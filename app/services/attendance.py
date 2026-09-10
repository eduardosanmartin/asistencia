"""Attendance validation authority (task 3.2).

Single place that decides whether an attendance attempt is accepted, and
the ONLY module that writes AttendanceEvent rows. Every attempt — valid
or not — produces a row so the audit trail always has a trace (traceability
spec).

Outcome codes (stored as-is for traceability across all views):
- OK                     valid sign inside a matched shift window
- OK_extra               valid sign with no matched shift (extra hours)
- INVALIDO_reuso         the same token was already used (token_hash)
- INVALIDO_qr_expirado   token expired or malformed
- INVALIDO_persona_incorrecta  JWT-bound worker missing or inactive
- INVALIDO_fuera_de_horario    token bound to a shift but submitted
                               outside that shift's window
- INVALIDO_kiosk_disabled      the kiosk_enabled toggle is off

Windows: shift [start - late_grace_minutes, end] in the org timezone
(SystemConfig ``timezone``), overnight shifts roll the end to the next
day. Delay = full minutes past the shift start (0 when early).
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jwt
from flask import current_app
from peewee import IntegrityError

from app.models import (
    AssignedShift,
    AttendanceEvent,
    ShiftTemplate,
    User,
    get_config_value,
)


def _default_now():
    """Aware-UTC clock hook; monkeypatched in tests."""
    return datetime.now(timezone.utc)


def _org_timezone():
    """Org timezone from SystemConfig; falls back to UTC on bad input."""
    try:
        return ZoneInfo(get_config_value("timezone", "UTC"))
    except (TypeError, ValueError):
        return timezone.utc


def _grace_minutes():
    """Late-grace window (minutes) from SystemConfig; 5 by default."""
    try:
        return max(0, int(get_config_value("late_grace_minutes", "5")))
    except (TypeError, ValueError):
        return 5


def _kiosk_enabled():
    """kiosk_enabled toggle from SystemConfig; enabled by default."""
    return get_config_value("kiosk_enabled", "true").strip().lower() in (
        "true", "1", "yes", "on",
    )


def _window(start_time, end_time, local_now):
    """Shift window for the local day; overnight shifts roll the end."""
    start_hour, start_min = (int(p) for p in start_time.split(":"))
    end_hour, end_min = (int(p) for p in end_time.split(":"))
    start = local_now.replace(hour=start_hour, minute=start_min,
                              second=0, microsecond=0)
    end = local_now.replace(hour=end_hour, minute=end_min,
                            second=0, microsecond=0)
    if end <= start:  # overnight shift
        end += timedelta(days=1)
    return start, end


def _overnight_window(start_time, end_time, local_now):
    """Compute both possible windows for an overnight shift:
    1. Started today (22:00 today → 06:00 tomorrow)
    2. Started yesterday (22:00 yesterday → 06:00 today)

    Returns both (start, end) pairs so callers can check which one
    the event falls into."""
    today_start, today_end = _window(start_time, end_time, local_now)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_end - timedelta(days=1)
    return [(today_start, today_end), (yesterday_start, yesterday_end)]


def compute_delay_minutes(shift_start, now):
    """Full minutes past the shift start; 0 when arriving early."""
    delta = (now - shift_start).total_seconds() // 60
    return max(0, int(delta))


def _unverified_claims(jwt_token):
    """Best-effort claims for audit rows. NEVER trusted for decisions."""
    try:
        return jwt.decode(jwt_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return {}


def get_last_event(user_id):
    """Latest AttendanceEvent for a worker, or None."""
    return (
        AttendanceEvent.select()
        .where(AttendanceEvent.user_id == user_id)
        .order_by(AttendanceEvent.timestamp.desc())
        .first()
    )


def get_last_accepted_event(user_id):
    """Latest accepted (OK/OK_extra) AttendanceEvent for a worker, or None."""
    return (
        AttendanceEvent.select()
        .where(
            AttendanceEvent.user_id == user_id,
            AttendanceEvent.outcome.in_(("OK", "OK_extra")),
        )
        .order_by(AttendanceEvent.timestamp.desc())
        .first()
    )


def resolve_shift(user_id, now=None):
    """Best active assignment whose window covers ``now``.

    Multiple matches: the earliest window start wins. No match: None,
    which callers interpret as an extra-hours sign.
    """
    now = now or _default_now()
    user = User.get_or_none(User.id == user_id)
    if user is None:
        return None

    local_now = now.astimezone(_org_timezone())
    grace = _grace_minutes()
    matches = []
    for assignment in (
        AssignedShift.select()
        .join(ShiftTemplate)
        .where(
            AssignedShift.user == user,
            AssignedShift.is_active == True,  # noqa: E712
            ShiftTemplate.is_active == True,  # noqa: E712
        )
    ):
        template = assignment.template
        mask = template.weekday_mask or "1111111"

        if template.is_overnight:
            # For overnight shifts, check both today-started and
            # yesterday-started windows.  The weekday mask must be
            # checked against the START date of each window, not today.
            windows = _overnight_window(template.start_time,
                                        template.end_time, local_now)
            for start, end in windows:
                window_weekday = start.weekday()
                if len(mask) > window_weekday and \
                        mask[window_weekday] == "0":
                    continue
                effective_start = start - timedelta(minutes=grace)
                if effective_start <= local_now <= end:
                    matches.append((start, assignment))
                    break
        else:
            if len(mask) > local_now.weekday() and \
                    mask[local_now.weekday()] == "0":
                continue
            start, end = _window(template.start_time, template.end_time,
                                 local_now)
            effective_start = start - timedelta(minutes=grace)
            if effective_start <= local_now <= end:
                matches.append((start, assignment))

    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0])
    return matches[0][1]


def _record(user, event_type, source, outcome, now, token_hash=None,
            shift=None, is_extra=False, delay_minutes=0):
    """Writes one AttendanceEvent row — the only writer."""
    return AttendanceEvent.create(
        user=user,
        event_type=event_type,
        source=source,
        outcome=outcome,
        timestamp=now,
        token_hash=token_hash,
        shift=shift,
        is_extra=is_extra,
        delay_minutes=delay_minutes,
    )


def _record_with_token(user, event_type, source, outcome, now, token_hash,
                       shift=None, is_extra=False, delay_minutes=0):
    """Records an event with a token_hash, handling TOCTOU races.

    If another concurrent request already inserted a row with the same
    token_hash, we catch the IntegrityError and record as INVALIDO_reuso.
    Non-token-hash integrity errors (FK, NOT NULL, CHECK) are re-raised.
    """
    try:
        return _record(user, event_type, source, outcome, now,
                       token_hash=token_hash, shift=shift,
                       is_extra=is_extra, delay_minutes=delay_minutes)
    except IntegrityError:
        logging.exception("IntegrityError during _record_with_token")
        # Verify the conflict is actually a same-token reuse, not some
        # other constraint violation (FK, NOT NULL, CHECK, etc.).
        existing = AttendanceEvent.select().where(
            AttendanceEvent.token_hash == token_hash
        ).first()
        if existing is None:
            raise
        # The existing row is a valid prior use of this token → record reuse.
        return _record(user, event_type, source, "INVALIDO_reuso", now,
                       token_hash=None, shift=shift,
                       is_extra=is_extra, delay_minutes=delay_minutes)


# ── QR validation ─────────────────────────────────────────────────────


def validate_qr(jwt_token, now=None):
    """Validates a QR JWT and records the attempt.

    Returns a dict: {"ok": bool, "event": row, "error": outcome-code-or-None}
    """
    now = now or _default_now()
    token_hash = hashlib.sha256(jwt_token.encode("utf-8")).hexdigest()

    payload = None
    from app.services.token import verify_qr_token
    payload = verify_qr_token(jwt_token)

    if payload is not None:
        event_type = payload.get("event_type")
        if event_type not in AttendanceEvent.EVENT_TYPES:
            event = _record(None, event_type or "unknown", "qr",
                            "INVALIDO_evento_invalido", now,
                            token_hash=token_hash)
            return {"ok": False, "event": event,
                    "error": "INVALIDO_evento_invalido"}

    # Reuse check first: a consumed token must never succeed again, even
    # when it has since expired.
    previously_used = AttendanceEvent.select().where(
        AttendanceEvent.token_hash == token_hash
    ).first()

    if payload is None:
        if previously_used is not None:
            # Distinguish actual reuse (token was consumed for OK) from
            # a second scan of an expired token that was never consumed.
            if previously_used.outcome.startswith("OK"):
                event = _record(previously_used.user, previously_used.event_type,
                                "qr", "INVALIDO_reuso", now, token_hash=None)
                return {"ok": False, "event": event, "error": "INVALIDO_reuso"}
            # Expired token scanned again → both rows are INVALIDO_qr_expirado.
            event = _record(previously_used.user, previously_used.event_type,
                            "qr", "INVALIDO_qr_expirado", now, token_hash=None)
            return {"ok": False, "event": event, "error": "INVALIDO_qr_expirado"}
        # Audit metadata from an unverified decode — the outcome is still
        # INVALIDO_qr_expirado; the claims are never trusted.
        claims = _unverified_claims(jwt_token)
        audit_user = None
        if claims.get("uid"):
            audit_user = User.get_or_none(User.id == claims["uid"])
        event_type = claims.get("event_type") or "unknown"
        event = _record_with_token(audit_user, event_type, "qr",
                                    "INVALIDO_qr_expirado", now,
                                    token_hash=token_hash)
        return {"ok": False, "event": event, "error": "INVALIDO_qr_expirado"}

    user = User.get_or_none(User.id == payload["uid"])
    event_type = payload["event_type"]

    if previously_used is not None:
        event = _record(user or previously_used.user, event_type, "qr",
                        "INVALIDO_reuso", now, token_hash=None)
        return {"ok": False, "event": event, "error": "INVALIDO_reuso"}

    if user is None or not user.is_active:
        event = _record(user, event_type, "qr",
                        "INVALIDO_persona_incorrecta", now,
                        token_hash=token_hash)
        return {"ok": False, "event": event,
                "error": "INVALIDO_persona_incorrecta"}

    shift_id = payload.get("shift_id")
    if shift_id is not None:
        assignment = AssignedShift.get_or_none(
            AssignedShift.id == shift_id,
            AssignedShift.user == user,
            AssignedShift.is_active == True,  # noqa: E712
        )
        if assignment is None:
            event = _record(user, event_type, "qr",
                            "INVALIDO_fuera_de_horario", now,
                            token_hash=token_hash)
            return {"ok": False, "event": event,
                    "error": "INVALIDO_fuera_de_horario"}

        local_now = now.astimezone(_org_timezone())

        if assignment.template.is_overnight:
            # Check both today-started and yesterday-started windows
            windows = _overnight_window(assignment.template.start_time,
                                        assignment.template.end_time,
                                        local_now)
            mask = assignment.template.weekday_mask or "1111111"
            in_window = False
            matched_start = None
            for start, end in windows:
                window_weekday = start.weekday()
                if len(mask) > window_weekday and \
                        mask[window_weekday] == "0":
                    continue
                effective_start = start - timedelta(minutes=_grace_minutes())
                if effective_start <= local_now <= end:
                    in_window = True
                    matched_start = start
                    break
            if not in_window:
                event = _record(user, event_type, "qr",
                                "INVALIDO_fuera_de_horario", now,
                                token_hash=token_hash)
                return {"ok": False, "event": event,
                        "error": "INVALIDO_fuera_de_horario"}
        else:
            mask = assignment.template.weekday_mask or "1111111"
            if len(mask) > local_now.weekday() and \
                    mask[local_now.weekday()] == "0":
                event = _record(user, event_type, "qr",
                                "INVALIDO_fuera_de_horario", now,
                                token_hash=token_hash)
                return {"ok": False, "event": event,
                        "error": "INVALIDO_fuera_de_horario"}
            start, end = _window(assignment.template.start_time,
                                 assignment.template.end_time, local_now)
            matched_start = start
            effective_start = start - timedelta(minutes=_grace_minutes())
            if not (effective_start <= local_now <= end):
                event = _record(user, event_type, "qr",
                                "INVALIDO_fuera_de_horario", now,
                                token_hash=token_hash)
                return {"ok": False, "event": event,
                        "error": "INVALIDO_fuera_de_horario"}

        delay = compute_delay_minutes(matched_start.astimezone(timezone.utc),
                                      now.astimezone(timezone.utc))
        event = _record_with_token(user, event_type, "qr", "OK", now,
                                   token_hash=token_hash, shift=assignment,
                                   is_extra=False,
                                   delay_minutes=delay if event_type == "entry" else None)
        return {"ok": True, "event": event, "error": None}

    # Unbound token → extra-hours sign.
    event = _record_with_token(user, event_type, "qr", "OK_extra", now,
                               token_hash=token_hash, shift=None,
                               is_extra=True)
    return {"ok": True, "event": event, "error": None}


# ── Kiosk validation ──────────────────────────────────────────────────


def validate_kiosk(user_id, event_type, now=None):
    """Validates a username/password kiosk sign (task 4.2).

    The caller already checked credentials; this authority checks the
    kiosk_enabled toggle and the shift window, then records the attempt.
    """
    now = now or _default_now()

    if event_type not in AttendanceEvent.EVENT_TYPES:
        event = _record(None, event_type or "unknown", "kiosk",
                        "INVALIDO_evento_invalido", now)
        return {"ok": False, "event": event,
                "error": "INVALIDO_evento_invalido"}

    user = User.get_or_none(User.id == user_id)

    if user is None or not user.is_active:
        event = _record(user, event_type, "kiosk",
                        "INVALIDO_persona_incorrecta", now)
        return {"ok": False, "event": event,
                "error": "INVALIDO_persona_incorrecta"}

    if not _kiosk_enabled():
        event = _record(user, event_type, "kiosk", "INVALIDO_kiosk_disabled",
                        now)
        return {"ok": False, "event": event,
                "error": "INVALIDO_kiosk_disabled"}

    assignment = resolve_shift(user_id, now=now)
    if assignment is not None:
        local_now = now.astimezone(_org_timezone())
        if assignment.template.is_overnight:
            windows = _overnight_window(assignment.template.start_time,
                                        assignment.template.end_time,
                                        local_now)
            matched_start = None
            for start, end in windows:
                effective_start = start - timedelta(minutes=_grace_minutes())
                if effective_start <= local_now <= end:
                    matched_start = start
                    break
            if matched_start is None:
                matched_start = _window(
                    assignment.template.start_time,
                    assignment.template.end_time, local_now)[0]
        else:
            matched_start, _end = _window(
                assignment.template.start_time,
                assignment.template.end_time, local_now)
        delay = compute_delay_minutes(matched_start.astimezone(timezone.utc),
                                      now.astimezone(timezone.utc))
        event = _record(user, event_type, "kiosk", "OK", now,
                        shift=assignment, is_extra=False,
                        delay_minutes=delay if event_type == "entry" else None)
        return {"ok": True, "event": event, "error": None}

    event = _record(user, event_type, "kiosk", "OK_extra", now,
                    shift=None, is_extra=True)
    return {"ok": True, "event": event, "error": None}