"""QR token service (task 3.1).

Issues short-lived JWT tokens bound to a worker, an event type and — when
an active shift matches — the matching AssignedShift. The token is the
credential the terminal prints as a QR code.

This service only ISSUES tokens and verifies their signature/expiry. Every
accept/reject decision and all AttendanceEvent writes belong to the single
attendance authority (app/services/attendance.py, outcome prefixes
INVALIDO_*). Never accept a token directly from here.

Token claims (HS256):
- uid        worker User id
- event_type "entry" | "exit"
- shift_id   matched AssignedShift id, or None (extra-shift signing)
- iat/nbf    issued at / not before (same value)
- exp        issued at + validity window (SystemConfig qr_validity_seconds)
- jti        unique id (single-use detection insurance)
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from flask import current_app

DEFAULT_VALIDITY_SECONDS = 60


def _default_now():
    """Aware-UTC clock hook; monkeypatched in tests."""
    return datetime.now(timezone.utc)


def generate_qr_token(user_id, event_type, shift_id, window_secs=None,
                      now=None):
    """Creates a QR payload token for the given worker and event.

    shift_id may be None — the worker signs outside any matched shift.
    window_secs defaults to the SystemConfig ``qr_validity_seconds`` value.
    """
    if window_secs is None:
        try:
            from app.models import get_config_value
            window_secs = int(get_config_value("qr_validity_seconds",
                                               str(DEFAULT_VALIDITY_SECONDS)))
        except (TypeError, ValueError):
            window_secs = DEFAULT_VALIDITY_SECONDS

    issued = now or _default_now()
    payload = {
        "uid": user_id,
        "event_type": event_type,
        "shift_id": shift_id,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int(issued.timestamp()) + int(window_secs),
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET"],
                      algorithm="HS256")


def verify_qr_token(token):
    """Verifies the JWT signature and expiry; returns the claims dict,
    or None when the token is invalid, expired, tampered or malformed."""
    if not token:
        return None
    try:
        return jwt.decode(token, current_app.config["JWT_SECRET"],
                          algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None