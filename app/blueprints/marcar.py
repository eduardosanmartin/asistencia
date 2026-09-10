"""Phone landing blueprint — /marcar/<jwt> QR confirm flow (task 3.4).

The worker opens the QR link; the landing page shows person + event and
asks for confirmation. The confirm POST delegates to the single
attendance authority (validate_qr), which records every attempt.
"""

from flask import Blueprint, render_template

from app.models import User
from app.services.attendance import validate_qr
from app.services.token import verify_qr_token

marcar_bp = Blueprint("marcar", __name__)

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


@marcar_bp.route("/marcar/<jwt>", methods=["GET"])
def landing(jwt):
    """Landing page: verifies the link and shows the confirm button."""
    payload = verify_qr_token(jwt)
    if payload is None:
        return render_template(
            "marcar/landing.html",
            valid=False,
            person=None,
            event_label=None,
            jwt=jwt,
            error="El enlace es inválido o expiró.",
        )
    person = User.get_or_none(User.id == payload["uid"])
    return render_template(
        "marcar/landing.html",
        valid=True,
        person=person,
        event_label=EVENT_TYPE_LABELS.get(
            payload["event_type"], payload["event_type"]
        ),
        jwt=jwt,
        error=None,
    )


@marcar_bp.route("/marcar/<jwt>", methods=["POST"])
def confirm(jwt):
    """Confirms the attendance attempt via the validation authority."""
    result = validate_qr(jwt)
    outcome = result["error"] or result["event"].outcome
    return render_template(
        "marcar/result.html",
        result=result,
        message=OUTCOME_MESSAGES.get(outcome, "Marca registrada"),
    )