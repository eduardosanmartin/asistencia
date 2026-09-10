"""Staff terminal blueprint — QR issuance and kiosk sign-in
(tasks 3.3, 4.1-4.3).

- /terminal: worker search + event type selector
- POST /terminal/qr: incoherent-sequence warning (Cancel/Continue),
  then JWT generation and QR rendering pointing to /marcar/<jwt>
- /terminal/kiosk: username/password sign-in with its own incoherent
  warning, recording the attempt under the worker's own identity
"""

import base64
import io

import qrcode
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.models import User
from app.services.attendance import (
    get_last_accepted_event,
    resolve_shift,
    validate_kiosk,
)
from app.services.hours import ACCEPTED_OUTCOMES
from app.services.security import verify_password
from app.services.token import generate_qr_token

from .auth import login_required

terminal_bp = Blueprint(
    "terminal", __name__, template_folder="../templates/terminal"
)

EVENT_TYPE_LABELS = {"entry": "Ingreso", "exit": "Salida"}

OUTCOME_MESSAGES = {
    "OK": "Marca registrada",
    "OK_extra": "Marca registrada",
    "INVALIDO_reuso": "Ya registró su marca",
    "INVALIDO_qr_expirado": "El enlace expiró o no es válido",
    "INVALIDO_persona_incorrecta": "La persona no es válida",
    "INVALIDO_fuera_de_horario": "Fuera del horario de la marca",
    "INVALIDO_kiosk_disabled": "El sistema de marcas está deshabilitado",
}


def _active_users():
    return User.select().where(User.is_active == True).order_by(User.name)  # noqa: E712


def _qr_image_data_uri(url):
    """PNG QR code for the marcar URL, as a base64 data URI."""
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(
        buf.getvalue()
    ).decode("ascii")


@terminal_bp.route("/terminal")
@login_required
def index():
    """Terminal home: worker search + event type selection."""
    return render_template(
        "terminal/index.html",
        users=_active_users(),
        warning=False,
        pending=None,
    )


@terminal_bp.route("/terminal/qr", methods=["POST"])
@login_required
def qr():
    """Generates a QR JWT for the selected worker and event type.

    Signs outside a matched shift (extra hours) with an unbound token.
    """
    user_id = request.form.get("user_id", type=int)
    event_type = request.form.get("event_type", "entry")
    if event_type not in ("entry", "exit"):
        flash("Tipo de marca no válido", "error")
        return redirect(url_for("terminal.index"))
    worker = User.get_or_none(
        User.id == user_id, User.is_active == True  # noqa: E712
    )
    if worker is None:
        flash("Seleccione un trabajador válido", "error")
        return redirect(url_for("terminal.index"))

    last = get_last_accepted_event(worker.id)
    incoherent = last is not None and last.event_type == event_type
    if incoherent and request.form.get("continue") != "1":
        return render_template(
            "terminal/index.html",
            users=_active_users(),
            warning=True,
            pending={
                "user_id": user_id,
                "event_type": event_type,
                "last_event_type": last.event_type,
            },
        )

    assignment = resolve_shift(worker.id)
    shift_id = assignment.id if assignment is not None else None
    token = generate_qr_token(worker.id, event_type, shift_id)
    marcar_url = current_app.config["APP_BASE_URL"] + url_for(
        "marcar.landing", jwt=token
    )
    return render_template(
        "terminal/qr.html",
        worker=worker,
        event_label=EVENT_TYPE_LABELS.get(event_type, event_type),
        marcar_url=marcar_url,
        qr_image=_qr_image_data_uri(marcar_url),
    )


@terminal_bp.route("/terminal/kiosk", methods=["GET", "POST"])
def kiosk():
    """Worker self-service sign-in with credentials (task 4.1)."""
    if request.method == "GET":
        return render_template(
            "terminal/kiosk.html", warning=False, pending=None
        )

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    event_type = request.form.get("event_type", "entry")
    user = User.get_or_none(User.username == username)
    if event_type not in ("entry", "exit"):
        flash("Tipo de marca no válido", "error")
        return redirect(url_for("terminal.kiosk"))
    if user is None or not user.is_active or not verify_password(
        password, user.password_hash
    ):
        # Record the failed attempt as INVALIDO_credenciales
        from app.models import AttendanceEvent as _AE
        _AE.create(
            user=user if user else None,
            event_type=event_type if event_type in ("entry", "exit") else "entry",
            source="kiosk",
            outcome="INVALIDO_credenciales",
        )
        flash("Credenciales inválidas", "error")
        return redirect(url_for("terminal.kiosk"))

    if user.must_change_password:
        flash("Debe iniciar sesión y cambiar su contraseña antes de usar el kiosco.", "warning")
        return redirect(url_for("auth.login"))

    last = get_last_accepted_event(user.id)
    incoherent = last is not None and last.event_type == event_type
    if incoherent and request.form.get("continue") != "1":
        return render_template(
            "terminal/kiosk.html",
            warning=True,
            pending={
                "username": username,
                "event_type": event_type,
                "last_event_type": last.event_type,
            },
        )

    result = validate_kiosk(user.id, event_type)
    outcome = result["error"] or result["event"].outcome
    return render_template(
        "terminal/result.html", result=result, username=user.name,
        message=OUTCOME_MESSAGES.get(outcome, "Marca registrada"),
    )