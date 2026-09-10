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
        event = AttendanceEvent.get_by_id(result["event"].id)
        assert event.outcome == "INVALIDO_persona_incorrecta"
        assert event.user_id == inactive.id

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


# ── R2-2: IntegrityError not caused by token_hash reuse ───────────────


class TestRecordWithTokenIntegrity:
    def test_integrity_error_not_from_reuse_is_reraised(self, app):
        """An IntegrityError caused by something other than token_hash
        reuse must be re-raised, not silently converted to INVALIDO_reuso."""
        from unittest.mock import patch
        from peewee import IntegrityError as PWIntegrityError
        carlos = _make_user(app, username="integ")
        _set_config(app, "kiosk_enabled", "true")

        with app.app_context():
            # Monkeypatch _record to raise IntegrityError on the first
            # call (the real insert), then verify re-raise behavior.
            original_record = __import__(
                "app.services.attendance", fromlist=["_record"]
            )._record
            call_count = [0]

            def fake_record(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise PWIntegrityError("Simulated FK violation")
                return original_record(*args, **kwargs)

            with patch("app.services.attendance._record", side_effect=fake_record):
                try:
                    from app.services.attendance import _record_with_token
                    _record_with_token(
                        carlos, "entry", "qr", "OK",
                        datetime.now(timezone.utc),
                        token_hash="fake_hash_123")
                    assert False, "Should have raised IntegrityError"
                except PWIntegrityError:
                    pass  # Expected: non-reuse IntegrityError re-raised


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


# ── R2-3: overnight weekday mask uses shift-start weekday ─────────────


class TestOvernightWeekdayMask:
    def test_overnight_friday_to_saturday_entry_saturday_01(self, app):
        """Mon–Fri 22:00–06:00 overnight shift. Worker scans exit at
        Saturday 01:00 → window started Friday 22:00, mask[4]='1' → OK."""
        carlos = _make_user(app, username="nocturno")
        # Mon-Fri enabled: index 0..4 = '1', Sat/Sun = '0'
        template = _make_shift(app, start="22:00", end="06:00",
                               mask="1111100")
        _assign(app, carlos, template)

        # Saturday 01:00 UTC. local_now.weekday() = 5 (Saturday).
        # Window started Friday 22:00 (weekday 4) → mask[4] = '1' → match.
        with app.app_context():
            resolved = resolve_shift(
                carlos.id,
                now=datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc))
        assert resolved is not None

    def test_overnight_sunday_not_in_mon_fri_mask(self, app):
        """Mon–Fri 22:00–06:00 overnight shift. Sunday 01:00 → window
        started Saturday 22:00 (weekday 5), mask[5]='0' → no match."""
        carlos = _make_user(app, username="domingo")
        template = _make_shift(app, start="22:00", end="06:00",
                               mask="1111100")
        _assign(app, carlos, template)

        # Sunday 01:00 UTC. local_now.weekday() = 6 (Sunday).
        # Window started Saturday 22:00 (weekday 5) → mask[5] = '0' → skip.
        # Today-started window (Sunday 22:00 → Monday 06:00) → weekday 6, mask[6]='0' → skip.
        with app.app_context():
            resolved = resolve_shift(
                carlos.id,
                now=datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc))
        assert resolved is None

    def test_overnight_friday_to_saturday_qr_exit(self, app):
        """QR path: overnight Mon–Fri 22:00–06:00, exit at Saturday 01:00
        with QR bound to the shift → OK, not INVALIDO_fuera_de_horario."""
        carlos = _make_user(app, username="qrnocturno")
        template = _make_shift(app, start="22:00", end="06:00",
                               mask="1111100")
        assignment = _assign(app, carlos, template)

        with app.app_context():
            token = generate_qr_token(carlos.id, "exit", assignment.id, 3600)
            result = validate_qr(
                token,
                now=datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc))

        assert result["ok"] is True
        assert result["event"].outcome == "OK"


# ── R2-4: event_type validation ───────────────────────────────────────


class TestEventTypeValidation:
    def test_validate_kiosk_rejects_bogus_event_type(self, app):
        carlos = _make_user(app)
        _set_config(app, "kiosk_enabled", "true")

        with app.app_context():
            result = validate_kiosk(carlos.id, "bogus")

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_evento_invalido"
        event = AttendanceEvent.get_by_id(result["event"].id)
        assert event.outcome == "INVALIDO_evento_invalido"
        assert event.source == "kiosk"

    def test_validate_qr_rejects_bogus_event_type(self, app):
        carlos = _make_user(app)
        with app.app_context():
            # Craft a token with bogus event_type by generating a valid
            # one and monkeypatching the payload.
            from unittest.mock import patch
            token = generate_qr_token(carlos.id, "entry", None, 3600)
            fake_payload = {
                "uid": carlos.id,
                "event_type": "bogus",
                "exp": 9999999999,
                "nbf": 1000000000,
                "iat": 1000000000,
                "jti": "test-jti",
            }
            with patch("app.services.token.verify_qr_token",
                       return_value=fake_payload):
                result = validate_qr(token)

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_evento_invalido"
        event = AttendanceEvent.get_by_id(result["event"].id)
        assert event.outcome == "INVALIDO_evento_invalido"


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


# ── F3-1: non-overnight weekday mask check ────────────────────────────


class TestNonOvernightWeekdayMask:
    def test_weekend_scan_rejected_for_mon_fri_shift(self, app):
        """Non-overnight Mon–Fri shift: QR scanned on Saturday within
        the clock window → INVALIDO_fuera_de_horario, not OK."""
        carlos = _make_user(app, username="finde_qr")
        template = _make_shift(app, start="06:00", end="14:00",
                               mask="1111100")
        assignment = _assign(app, carlos, template)

        # Saturday 2026-09-12 10:00 UTC — inside 06:00–14:00 window but
        # Saturday (weekday=5) is disabled in mask "1111100".
        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", assignment.id, 3600)
            result = validate_qr(
                token,
                now=datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc))

        assert result["ok"] is False
        assert result["error"] == "INVALIDO_fuera_de_horario"
        event = AttendanceEvent.get_by_id(result["event"].id)
        assert event.outcome == "INVALIDO_fuera_de_horario"

    def test_weekday_scan_accepted_for_mon_fri_shift(self, app):
        """Non-overnight Mon–Fri shift: QR scanned on Monday within
        the clock window → OK (control case)."""
        carlos = _make_user(app, username="lunes_qr")
        template = _make_shift(app, start="06:00", end="14:00",
                               mask="1111100")
        assignment = _assign(app, carlos, template)

        # Monday 2026-09-07 08:00 UTC — inside 06:00–14:00, Monday (weekday=0)
        # is enabled in mask "1111100".
        with app.app_context():
            token = generate_qr_token(carlos.id, "entry", assignment.id, 3600)
            result = validate_qr(
                token,
                now=datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc))

        assert result["ok"] is True
        assert result["event"].outcome == "OK"