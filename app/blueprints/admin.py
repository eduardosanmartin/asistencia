"""Administration blueprint — users, shift templates, assignments, config.

Routes:
    /admin/usuarios        — user list + creation (administrador)
    /admin/plantillas      — ShiftTemplate list + creation (administrador)
    /admin/plantillas/<id>/toggle    — is_active toggle
    /admin/plantillas/<id>/eliminar  — delete (rejected with assignments)
    /admin/asignaciones    — AssignedShift list + creation (admin);
                             supervisors see only their own team
    /admin/asignaciones/<user_id>    — team-scoped view (403 for foreign teams)
    /admin/asignaciones/<id>/eliminar — deletes an assignment, preserving events
    /admin/configuracion   — SystemConfig update (administrador)

Messages follow the delta specs (e.g. «Nombre de plantilla duplicado»,
«Horario inválido», «No se puede eliminar: tiene asignaciones activas»).
"""

import re

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from peewee import prefetch

from ..models import (
    AssignedShift,
    AttendanceEvent,
    ShiftTemplate,
    SystemConfig,
    User,
    set_config_value,
)
from ..services.security import generate_temp_password, hash_password
from .auth import login_required, role_required

admin_bp = Blueprint("admin", __name__)

# SystemConfig keys the attendance service reads at runtime.
CONFIG_KEYS = (
    "kiosk_enabled",
    "late_grace_minutes",
    "qr_validity_seconds",
    "timezone",
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ── Users (foundation) ────────────────────────────────────────────────


@admin_bp.route("/usuarios", methods=["GET", "POST"])
@role_required("administrador")
def usuarios():
    """User list and creation (role administrador only)."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        name = (request.form.get("name") or "").strip()
        roles_raw = (request.form.get("roles") or "").strip()
        supervisor_id = (request.form.get("supervisor_id") or "").strip()

        if not username or not name:
            flash("Usuario y nombre son obligatorios.", "error")
        elif User.get_or_none(User.username == username):
            flash("Nombre de usuario duplicado.", "error")
        else:
            temp_password = generate_temp_password()
            roles = [r.strip() for r in roles_raw.split(",") if r.strip()] or [
                "funcionario"
            ]
            supervisor = None
            if supervisor_id:
                sid = supervisor_id
                if not sid.isdigit():
                    flash("Supervisor inválido.", "error")
                    return redirect(url_for("admin.usuarios"))
                supervisor = User.get_or_none(User.id == int(sid))
            User.create(
                username=username,
                email=email or f"{username}@asistencia.local",
                name=name,
                role=",".join(roles),
                password_hash=hash_password(temp_password),
                must_change_password=True,
                is_active=True,
                supervisor=supervisor,
            )
            flash(
                f"Usuario creado. Contraseña temporal: {temp_password}",
                "success",
            )
        return redirect(url_for("admin.usuarios"))

    users = prefetch(User.select().order_by(User.name), User)
    supervisors = User.select().where(
        User.role.contains("supervisor"), User.is_active == True  # noqa: E712
    )
    return render_template(
        "admin/usuarios.html", users=users, supervisors=supervisors
    )


# ── ShiftTemplate CRUD (task 2.1) ─────────────────────────────────────


def _validate_template(name, start_time, end_time, weekday_mask):
    """Returns an error message string, or None when the template is valid."""
    if not name:
        return "El nombre es obligatorio."
    if not _TIME_RE.match(start_time) or not _TIME_RE.match(end_time):
        return "Horario inválido"
    if start_time == end_time:
        return "Horario inválido"
    if len(weekday_mask) != 7 or not set(weekday_mask) <= {"0", "1"}:
        return "Datos de plantilla inválidos"
    return None


@admin_bp.route("/plantillas", methods=["GET", "POST"])
@role_required("administrador")
def plantillas():
    """Shift template list and creation (role administrador only)."""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        start_time = (request.form.get("start_time") or "").strip()
        end_time = (request.form.get("end_time") or "").strip()
        weekday_mask = (request.form.get("weekday_mask") or "1111100").strip()

        error = _validate_template(name, start_time, end_time, weekday_mask)
        if error:
            flash(error, "error")
        elif ShiftTemplate.get_or_none(ShiftTemplate.name == name):
            flash("Nombre de plantilla duplicado", "error")
        else:
            ShiftTemplate.create(
                name=name,
                start_time=start_time,
                end_time=end_time,
                weekday_mask=weekday_mask,
                is_active=True,
            )
            flash("Plantilla creada.", "success")
        return redirect(url_for("admin.plantillas"))

    templates = ShiftTemplate.select().order_by(ShiftTemplate.name)
    return render_template("admin/plantillas.html", templates=templates)


@admin_bp.route("/plantillas/<int:template_id>/toggle", methods=["POST"])
@role_required("administrador")
def plantilla_toggle(template_id):
    """Activates/deactivates a template (existing assignments untouched)."""
    template = ShiftTemplate.get_or_none(ShiftTemplate.id == template_id)
    if template is None:
        abort(404)
    template.is_active = not template.is_active
    template.save()
    state = "activada" if template.is_active else "desactivada"
    flash(f"Plantilla {state}.", "success")
    return redirect(url_for("admin.plantillas"))


@admin_bp.route("/plantillas/<int:template_id>/eliminar", methods=["POST"])
@role_required("administrador")
def plantilla_delete(template_id):
    """Deletes a template unless it has active assignments."""
    template = ShiftTemplate.get_or_none(ShiftTemplate.id == template_id)
    if template is None:
        abort(404)
    active_assignments = AssignedShift.select().where(
        AssignedShift.template == template,
        AssignedShift.is_active == True,  # noqa: E712
    )
    if active_assignments.exists():
        flash(
            "No se puede eliminar: tiene asignaciones activas", "error"
        )
        return redirect(url_for("admin.plantillas"))
    template.delete_instance()
    flash("Plantilla eliminada.", "success")
    return redirect(url_for("admin.plantillas"))


# ── AssignedShift CRUD (task 2.2) ─────────────────────────────────────


def _viewer():
    """Returns the logged-in user of the current session."""
    user = User.get_or_none(User.id == session.get("user_id"))
    if user is None:
        abort(401)
    return user


def _team_query(viewer):
    """Scope base query: admin sees all, supervisor sees own team."""
    if viewer.has_role("administrador"):
        return AssignedShift.select()
    return AssignedShift.select().join(User).where(
        User.supervisor == viewer
    )


@admin_bp.route("/asignaciones", methods=["GET", "POST"])
@role_required("supervisor", "administrador")
def asignaciones():
    """Assignment list (scoped) and creation (administrador only)."""
    viewer = _viewer()

    if request.method == "POST":
        if not viewer.has_role("administrador"):
            abort(403)
        user_id = (request.form.get("user_id") or "").strip()
        template_id = (request.form.get("template_id") or "").strip()

        if user_id and not user_id.isdigit():
            flash("Usuario inválido.", "error")
            return redirect(url_for("admin.asignaciones"))
        if template_id and not template_id.isdigit():
            flash("Plantilla inválida.", "error")
            return redirect(url_for("admin.asignaciones"))

        user = User.get_or_none(User.id == int(user_id)) if user_id else None
        template = (
            ShiftTemplate.get_or_none(ShiftTemplate.id == int(template_id))
            if template_id
            else None
        )
        if user is None or template is None:
            flash("Usuario o plantilla inexistentes.", "error")
        elif not template.is_active:
            flash("La plantilla está inactiva.", "error")
        else:
            AssignedShift.create(user=user, template=template, is_active=True)
            flash("Asignación creada.", "success")
        return redirect(url_for("admin.asignaciones"))

    assignments = prefetch(_team_query(viewer).order_by(AssignedShift.id), User, ShiftTemplate)
    if viewer.has_role("administrador"):
        users = User.select().where(User.is_active == True).order_by(User.name)  # noqa: E712
    else:
        # Supervisors only see (and could manage) their own team.
        users = User.select().where(
            User.supervisor == viewer, User.is_active == True  # noqa: E712
        ).order_by(User.name)
    templates = ShiftTemplate.select().where(
        ShiftTemplate.is_active == True  # noqa: E712
    ).order_by(ShiftTemplate.name)
    return render_template(
        "admin/asignaciones.html",
        assignments=assignments,
        users=users,
        templates=templates,
    )


@admin_bp.route("/asignaciones/<int:user_id>")
@role_required("supervisor", "administrador")
def asignaciones_usuario(user_id):
    """Team-scoped view of a user's assignments/attendance.

    Administradores may view anyone; Supervisors only their own team
    (or themselves). Foreign teams get 403 Forbidden.
    """
    viewer = _viewer()
    target = User.get_or_none(User.id == user_id)
    if target is None:
        abort(404)

    if not viewer.has_role("administrador"):
        if target.supervisor_id != viewer.id and target.id != viewer.id:
            abort(403)

    assignments = AssignedShift.select().where(
        AssignedShift.user == target
    ).order_by(AssignedShift.id)
    events = AttendanceEvent.select().where(
        AttendanceEvent.user == target
    ).order_by(AttendanceEvent.timestamp.desc())
    return render_template(
        "admin/asignaciones.html",
        assignments=assignments,
        users=[],
        templates=[],
        scope_user=target,
        events=events,
    )


@admin_bp.route("/asignaciones/<int:assignment_id>/eliminar", methods=["POST"])
@role_required("administrador")
def asignacion_delete(assignment_id):
    """Removes an assignment without affecting past attendance events.

    Past events keep their rows; the shift reference is cleared so the
    audit trail stays intact under PostgreSQL FK constraints.
    """
    assignment = AssignedShift.get_or_none(
        AssignedShift.id == assignment_id
    )
    if assignment is None:
        abort(404)

    AttendanceEvent.update(shift=None).where(
        AttendanceEvent.shift == assignment
    ).execute()
    assignment.delete_instance()
    flash("Asignación eliminada. Las marcas previas se conservan.", "success")
    return redirect(url_for("admin.asignaciones"))


# ── SystemConfig management (task 2.3) ────────────────────────────────


@admin_bp.route("/configuracion", methods=["GET", "POST"])
@role_required("administrador")
def configuracion():
    """SystemConfig settings page and runtime updates (administrador)."""
    if request.method == "POST":
        key = (request.form.get("key") or "").strip()
        value = (request.form.get("value") or "").strip()
        if key not in CONFIG_KEYS:
            flash("Clave de configuración no válida.", "error")
        else:
            set_config_value(key, value)
            flash(f"Configuración «{key}» actualizada.", "success")
        return redirect(url_for("admin.configuracion"))

    config_rows = SystemConfig.select().order_by(SystemConfig.key)
    return render_template(
        "admin/configuracion.html",
        config_rows=config_rows,
        config_keys=CONFIG_KEYS,
    )