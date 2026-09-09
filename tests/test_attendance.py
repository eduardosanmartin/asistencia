"""Attendance authority tests — token service + validation (tasks 3.1, 3.2).

Unit tests for app/services/token.py and integration tests for the
single validation authority in app/services/attendance.py against the
SQLite :memory: database.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from app.models import (
    AssignedShift,
    AttendanceEvent,
    ShiftTemplate,
    SystemConfig,
    User,
)
from app.services.attendance import (
    compute_delay_minutes,
    get_last_event,
    resolve_shift,
    validate_kiosk,
    validate_qr,
)
from app.services.token import generate_qr_token, verify_qr_token
from app.services.security import hash_password


# ── Helpers ───────────────────────────────────────────────────────────


def _make_user(app, username="carlos", role="funcionario", active=True):
    with app.app_context():
        return User.create(
            username=username,
            email=f"{username}@test.local",
            name=f"Usuario {username}",
            role=role,
            password_hash=hash_password("test1234"),
            must_change_password=False,
            is_active=active,
        )


def _make_shift(app, start="06:00", end="14:00", mask="1111100",
                name="Turno Mañana"):
    with app.app_context():
        return ShiftTemplate.create(
            name=name, start_time=start, end_time=end, weekday_mask=mask
        )


def _assign(app, user, template):
    with app.app_context():
        return AssignedShift.create(user=user, template=template)


def _record(app, user, event_type, outcome="OK", source="qr", ts=None):
    with app.app_context():
        return AttendanceEvent.create(
            user=user,
            event_type=event_type,
            source=source,
            outcome=outcome,
            timestamp=ts or datetime.now(timezone.utc),
        )


def _set_config(app, key, value):
    with app.app_context():
        row, _ = SystemConfig.get_or_create(
            key=key, defaults={"value": str(value)}
        )
        row.value = str(value)
        row.save()


# ── Token service (task 3.1) ──────────────────────────────────────────


class TestTokenService:
    def test_generate_returns_jwt_with_full_payload(self, app, client):
        with app.app_context():
            token = generate_qr_token(
                user_id=7, event_type="entry", shift_id=3, window_secs=45
            )
            payload = verify_qr_token(token)
            assert payload is not None
            assert payload["uid"] == 7
            assert payload["event_type"] == "entry"
            assert payload["shift_id"] == 3
            assert payload["exp"] > payload["nbf"]
            assert payload["iat"] <= payload["nbf"] + 1
            assert payload["jti"]

    def test_verify_roundtrip_same_payload(self, app):
        with app.app_context():
            token = generate_qr_token(user_id=1, event_type="exit",
                                      shift_id=None, window_secs=45)
            payload = verify_qr_token(token)
            assert payload["uid"] == 1
            assert payload["event_type"] == "exit"
            assert payload["shift_id"] is None

    def test_expired_token_returns_none(self, app):
        with app.app_context():
            token = generate_qr_token(user_id=1, event_type="entry",
                                      shift_id=None, window_secs=-60)
            assert verify_qr_token(token) is None

    def test_tampered_token_returns_none(self, app):
        with app.app_context():
            token = generate_qr_token(user_id=1, event_type="entry",
                                      shift_id=None, window_secs=45)
            assert verify_qr_token(token + "x") is None

    def test_garbage_returns_none(self, app):
        with app.app_context():
            assert verify_qr_token("not-a-jwt") is None


# ── validate_qr integration (task 3.2) ────────────────────────────────


class TestValidateQR:
    def test_happy_entry_records_ok(self, app):
        carlos = _make_user(app)
        template = _make_shift(app)
        _assign(app, carlos, template)
        _record(app, carlos, "exit", ts=datetime.now(timezone.utc) - timedelta(hours=1))

        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", template.id, 3600)
            result = validate_qr(token, now=datetime(2026, 9, 9, 5, 58, tzinfo=timezone.utc))

        assert result["ok"] is True
        event = result["event"]
        assert event.outcome == "OK"
        assert event.source == "qr"
        assert event.event_type == "entry"
        assert event.is_extra is False
        assert event.shift_id == template.id
        assert event.delay_minutes == 0
        expected_hash = hashlib.sha256(token.encode()).hexdigest()
        assert event.token_hash == expected_hash

    def test_second_use_records_reuso(self, app):
        carlos = _make_user(app)
        template = _make_shift(app)
        _assign(app, carlos, template)

        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", template.id, 3600)
            first = validate_qr(token, now=datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc))
            second = validate_qr(token, now=datetime(2026, 9, 9, 6, 1, tzinfo=timezone.utc))

        assert first["ok"] is True
        assert second["ok"] is False
        assert second["error"] == "INVALIDO_reuso"
        second_event = AttendanceEvent.get_by_id(second["event"].id)
        assert second_event.outcome == "INVALIDO_reuso"
        # Reuse rows never carry the hash (plain unique index)
        assert second_event.token_hash is None
        rows = AttendanceEvent.select().count()
        assert rows == 2

    def test_expired_token_records_expirado(self, app):
        carlos = _make_user(app)
        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", None, -60)
            result = validate_qr(token)

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_qr_expirado"
        row = AttendanceEvent.get_by_id(result["event"].id)
        assert row.outcome == "INVALIDO_qr_expirado"

    def test_inactive_person_records_persona_incorrecta(self, app):
        inactive = _make_user(app, username="inactivo", active=False)
        with app.app_context():
            token = generate_qr_token(inactive.id, "entry", None, 3600)
            result = validate_qr(token)

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_persona_incorrecta"
        assert AttendanceEvent.get_by_id(result["event"].id).outcome == \
            "INVALIDO_persona_incorrecta"

    def test_out_of_window_records_fuera_de_horario(self, app):
        carlos = _make_user(app)
        template = _make_shift(app, start="06:00", end="14:00")
        _assign(app, carlos, template)

        # QR bound to the shift, generated at 07:00 with a 10h window;
        # submitted at 15:00 — still within exp, but outside the window.
        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", template.id, 36000)
            result = validate_qr(token, now=datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc))

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_fuera_de_horario"
        assert AttendanceEvent.get_by_id(result["event"].id).outcome == \
            "INVALIDO_fuera_de_horario"

    def test_extra_shift_records_ok_extra(self, app):
        carlos = _make_user(app, username="extra")
        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", None, 3600)
            result = validate_qr(token, now=datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc))

        assert result["ok"] is True
        event = AttendanceEvent.get_by_id(result["event"].id)
        assert event.outcome == "OK_extra"
        assert event.shift_id is None
        assert event.is_extra is True

    def test_lateness_delay_is_recorded(self, app):
        carlos = _make_user(app, username="tarde")
        template = _make_shift(app, start="06:00", end="14:00")
        _assign(app, carlos, template)

        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", template.id, 3600)
            result = validate_qr(token, now=datetime(2026, 9, 9, 6, 6, tzinfo=timezone.utc))

        assert result["ok"] is True
        assert result["event"].delay_minutes == 6

    def test_within_grace_delay_still_recorded(self, app):
        carlos = _make_user(app, username="cuatro")
        template = _make_shift(app, start="06:00", end="14:00")
        _assign(app, carlos, template)

        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", template.id, 3600)
            result = validate_qr(token, now=datetime(2026, 9, 9, 6, 4, tzinfo=timezone.utc))

        assert result["ok"] is True
        assert result["event"].delay_minutes == 4


# ── validate_kiosk integration (task 3.2) ─────────────────────────────


class TestValidateKiosk:
    def test_happy_kiosk_records_ok(self, app):
        carlos = _make_user(app)
        template = _make_shift(app)
        _assign(app, carlos, template)
        _set_config(app, "kiosk_enabled", "true")

        with app.app_context():
            result = validate_kiosk(carlos.id, "entry", now=datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc))

        assert result["ok"] is True
        event = AttendanceEvent.get_by_id(result["event"].id)
        assert event.outcome == "OK"
        assert event.source == "kiosk"

    def test_kiosk_disabled_records_invalido(self, app):
        carlos = _make_user(app)
        _set_config(app, "kiosk_enabled", "false")

        with app.app_context():
            result = validate_kiosk(carlos.id, "entry")

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_kiosk_disabled"
        row = AttendanceEvent.get_by_id(result["event"].id)
        assert row.outcome == "INVALIDO_kiosk_disabled"
        assert row.source == "kiosk"

    def test_kiosk_extra_records_ok_extra(self, app):
        carlos = _make_user(app, username="sin-turno")
        _set_config(app, "kiosk_enabled", "true")

        with app.app_context():
            result = validate_kiosk(carlos.id, "exit")

        assert result["ok"] is True
        row = AttendanceEvent.get_by_id(result["event"].id)
        assert row.outcome == "OK_extra"
        assert row.shift_id is None


# ── resolve_shift (task 3.2) ──────────────────────────────────────────


class TestResolveShift:
    def test_inside_window_matches_assignment(self, app):
        carlos = _make_user(app)
        template = _make_shift(app, start="06:00", end="14:00")
        assignment = _assign(app, carlos, template)

        with app.app_context():
            resolved = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc))
        assert resolved is not None
        assert resolved.id == assignment.id

    def test_early_arrival_within_grace_matches(self, app):
        carlos = _make_user(app)
        template = _make_shift(app, start="06:00", end="14:00")
        _assign(app, carlos, template)
        _set_config(app, "late_grace_minutes", "5")

        with app.app_context():
            resolved = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 5, 58, tzinfo=timezone.utc))
        assert resolved is not None

    def test_outside_window_returns_none(self, app):
        carlos = _make_user(app)
        template = _make_shift(app, start="06:00", end="14:00")
        _assign(app, carlos, template)

        with app.app_context():
            resolved = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc))
        assert resolved is None

    def test_overnight_shift_matches_nightly_window(self, app):
        carlos = _make_user(app, username="noche")
        template = _make_shift(app, start="22:00", end="06:00")
        _assign(app, carlos, template)

        with app.app_context():
            at_night = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc))
        assert at_night is not None

        with app.app_context():
            at_midday = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc))
        assert at_midday is None

    def test_inactive_assignment_not_resolved(self, app):
        carlos = _make_user(app)
        template = _make_shift(app)
        assignment = _assign(app, carlos, template)
        with app.app_context():
            assignment.is_active = False
            assignment.save()
        with app.app_context():
            resolved = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc))
        assert resolved is None

    def test_weekday_mask_filters_out_missing_days(self, app):
        carlos = _make_user(app, username="finde")
        # Only Monday (index 0) enabled; today (Wed, index 2) → no match.
        template = _make_shift(app, mask="1000000")
        _assign(app, carlos, template)

        with app.app_context():
            resolved = resolve_shift(carlos.id, now=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc))
        assert resolved is None


# ── get_last_event (task 3.2) ─────────────────────────────────────────


class TestGetLastEvent:
    def test_returns_latest_event(self, app):
        carlos = _make_user(app)
        earlier = _record(app, carlos, "entry", ts=datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc))
        later = _record(app, carlos, "exit", ts=datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc))

        with app.app_context():
            last = get_last_event(carlos.id)
        assert last is not None
        assert last.id == later.id
        assert last.event_type == "exit"

    def test_none_when_no_events(self, app):
        carlos = _make_user(app, username="nuevo")
        with app.app_context():
            last = get_last_event(carlos.id)
        assert last is None


# ── compute_delay_minutes (task 3.2) ──────────────────────────────────


class TestComputeDelayMinutes:
    def test_early_arrival_is_zero(self, app):
        with app.app_context():
            start = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
            now = datetime(2026, 9, 9, 5, 58, tzinfo=timezone.utc)
            assert compute_delay_minutes(start, now) == 0

    def test_within_grace_records_minutes(self, app):
        with app.app_context():
            start = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
            now = datetime(2026, 9, 9, 6, 4, tzinfo=timezone.utc)
            assert compute_delay_minutes(start, now) == 4

    def test_beyond_grace_records_minutes(self, app):
        with app.app_context():
            start = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
            now = datetime(2026, 9, 9, 6, 6, tzinfo=timezone.utc)
            assert compute_delay_minutes(start, now) == 6