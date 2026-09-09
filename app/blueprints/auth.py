"""Authentication blueprint — session login, roles, password change.

Routes:
    GET  /login              — login form
    POST /login              — verifies credentials, establishes session
    GET  /cambiar-password   — password change form
    POST /cambiar-password   — applies the new password
    POST /logout             — destroys the session

Decorators:
    login_required   — requires an active session; enforces the
                       must_change_password gate.
    role_required    — composes login_required with role verification;
                       unauthorized users get 403 Forbidden.
"""

from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.models import User
from app.services.security import hash_password, verify_password

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")

MIN_PASSWORD_LENGTH = 8


# ── Decorators ────────────────────────────────────────────────────────


def login_required(f):
    """Requires an active session and an active user.

    Redirects to /cambiar-password when must_change_password is True
    (except when already on that route), forcing the password change
    before the rest of the application is reachable.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("Debe iniciar sesión", "warning")
            return redirect(url_for("auth.login"))
        user = User.get_or_none(User.id == user_id)
        if not user or not user.is_active:
            session.clear()
            flash("Su cuenta está desactivada.", "error")
            return redirect(url_for("auth.login"))
        if user.must_change_password and request.endpoint != "auth.cambiar_password":
            return redirect(url_for("auth.cambiar_password"))
        return f(*args, **kwargs)

    return decorated


def role_required(*roles):
    """Composes login_required with role verification.

    Returns 403 Forbidden when the logged-in user holds none of the
    required roles (spec: Funcionario requesting /admin/usuarios → 403).
    """

    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            user_id = session.get("user_id")
            user = User.get_or_none(User.id == user_id)
            if not user.has_any_role(roles):
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


# ── Session helpers ───────────────────────────────────────────────────


def _establish_session(user):
    """Creates the Flask session for an authenticated user."""
    session["user_id"] = user.id
    session["name"] = user.name
    session["role"] = user.role


# ── Routes ────────────────────────────────────────────────────────────


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Session login with username and password (bcrypt).

    Failure always shows the generic «Credenciales inválidas» message
    so the system never reveals whether a username exists.
    """
    if request.method == "GET":
        return render_template("auth/login.html")

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = User.get_or_none(User.username == username)

    if not user or not user.is_active or not verify_password(
        password, user.password_hash
    ):
        flash("Credenciales inválidas", "error")
        return render_template("auth/login.html"), 200

    _establish_session(user)

    if user.must_change_password:
        flash("Debe cambiar su contraseña antes de continuar.", "warning")
        return redirect(url_for("auth.cambiar_password"))

    flash("Inicio de sesión exitoso.", "success")
    return redirect(url_for("index"))


@auth_bp.route("/cambiar-password", methods=["GET", "POST"])
@login_required
def cambiar_password():
    """Password change (forced after first login or voluntary).

    Validates the current password, requires a new one of at least
    8 characters and clears must_change_password on success.
    """
    user = User.get_by_id(session.get("user_id"))

    if request.method == "GET":
        return render_template("auth/cambiar_password.html")

    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not verify_password(current_password, user.password_hash):
        flash("La contraseña actual es incorrecta.", "error")
        return render_template("auth/cambiar_password.html"), 200

    if len(new_password) < MIN_PASSWORD_LENGTH:
        flash(
            f"La nueva contraseña debe tener al menos "
            f"{MIN_PASSWORD_LENGTH} caracteres.",
            "error",
        )
        return render_template("auth/cambiar_password.html"), 200

    if new_password != confirm_password:
        flash("Las contraseñas no coinciden.", "error")
        return render_template("auth/cambiar_password.html"), 200

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.save()

    flash("Contraseña actualizada correctamente.", "success")
    return redirect(url_for("index"))


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Destroys the session and redirects to /login."""
    session.clear()
    flash("Sesión cerrada exitosamente.", "info")
    return redirect(url_for("auth.login"))