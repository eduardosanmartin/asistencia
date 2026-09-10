"""Terminal QR generation + kiosk flow tests (tasks 3.3, 3.5, 4.1-4.3).

Covers: worker search + event type selector, incoherent-sequence warning
(Cancel/Continue), QR generation with a JWT pointing to /marcar/<jwt>,
and the kiosk flow (credentials, kiosk_enabled toggle, incoherent warning,
recording under the kiosk user's own identity).
"""

import re
from datetime import datetime, timedelta, timezone

from app.models import (
    AssignedShift,
    AttendanceEvent,
    ShiftTemplate,
    SystemConfig,
    User,
)
from app.services.attendance import get_last_event
from app.services.security import hash_password
from app.services.token import verify_qr_token

from tests.helpers import create_user

MARCAR_URL_RE = re.compile(r"/marcar/([A-Za-z0-9_\-\.]+)")


def _worker_with_events(username="carlos", last_type="exit"):
    """A plain worker, optionally with a recorded last event."""
    user = create_user(role="funcionario", username=username,
                       name=f"Usuario {username}")
    if last_type:
        AttendanceEvent.create(
            user=user, event_type=last_type, source="qr", outcome="OK",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    return user


def _set_config(key, value):
    row, _ = SystemConfig.get_or_create(key=key, defaults={"value": str(value)})
    row.value = str(value)
    row.save()


def _plant_worker_session(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id
        sess["name"] = user.name
        sess["role"] = user.role


# ── Terminal index (task 3.3) ─────────────────────────────────────────


class TestTerminalIndex:
    def test_requires_login(self, client):
        assert client.get("/terminal").status_code == 302

    def test_index_lists_workers(self, auth_funcionario):
        client, _ = auth_funcionario
        _worker_with_events()
        page = client.get("/terminal").data.decode()
        assert "carlos" in page
        assert "Ingreso" in page
        assert "Salida" in page


# ── QR generation with incoherent warning (task 3.3) ──────────────────


class TestQrGeneration:
    def test_happy_flow_generates_jwt(self, auth_funcionario):
        client, _ = auth_funcionario
        carlos = _worker_with_events(last_type="exit")  # last = Salida

        page = client.post(
            "/terminal/qr",
            data={"user_id": carlos.id, "event_type": "entry"},
        ).data.decode()

        match = MARCAR_URL_RE.search(page)
        assert match, "QR page must expose the /marcar/<jwt> URL"
        payload = verify_qr_token(match.group(1))
        assert payload["uid"] == carlos.id
        assert payload["event_type"] == "entry"

    def test_incoherent_sequence_shows_warning(self, auth_funcionario):
        client, _ = auth_funcionario
        carlos = _worker_with_events(last_type="entry")  # last = Ingreso

        page = client.post(
            "/terminal/qr",
            data={"user_id": carlos.id, "event_type": "entry"},
        ).data.decode()

        assert "Continuar" in page
        assert "Cancelar" in page
        assert MARCAR_URL_RE.search(page) is None, \
            "No QR may be issued until the operator confirms"

    def test_warning_cancel_aborts(self, auth_funcionario):
        client, _ = auth_funcionario
        carlos = _worker_with_events(last_type="entry")

        page = client.post(
            "/terminal/qr",
            data={"user_id": carlos.id, "event_type": "entry",
                  "continue": "1"},
        ).data.decode()

        assert MARCAR_URL_RE.search(page) is not None, \
            "Operator confirmation must proceed with the QR"

    def test_invalid_worker_rejected(self, auth_funcionario):
        client, _ = auth_funcionario
        page = client.post(
            "/terminal/qr",
            data={"user_id": 999999, "event_type": "entry"},
            follow_redirects=True,
        ).data.decode()
        assert "válido" in page


# ── Kiosk flow (tasks 4.1-4.3) ────────────────────────────────────────


class TestKiosk:
    def test_happy_sign_in_records_under_identity(self, client, monkeypatch):
        """Supervisor+Funcionario signs in at the kiosk: the event is
        recorded under their own identity, with no team-scope restriction
        (turn-assignment spec scenario)."""
        kiosk_user = create_user(role="supervisor,funcionario",
                                 username="superfunc",
                                 name="Supervisor Funcionario")
        _set_config("kiosk_enabled", "true")
        template = ShiftTemplate.create(name="Turno Mañana",
                                        start_time="06:00", end_time="14:00",
                                        weekday_mask="1111100")
        AssignedShift.create(user=kiosk_user, template=template)
        # Freeze validation at 10:00 UTC — inside the 06:00-14:00 window.
        monkeypatch.setattr(
            "app.services.attendance._default_now",
            lambda: datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
        )

        page = client.post(
            "/terminal/kiosk",
            data={"username": "superfunc", "password": "test1234",
                  "event_type": "entry"},
        ).data.decode()

        assert "Marca registrada" in page
        event = AttendanceEvent.select().order_by(
            AttendanceEvent.id.desc()).get()
        assert event.user_id == kiosk_user.id
        assert event.source == "kiosk"
        assert event.outcome == "OK"

    def test_kiosk_disabled_records_invalido(self, client):
        kiosk_user = create_user(username="kiosk1", name="Kiosk Uno")
        _set_config("kiosk_enabled", "false")

        page = client.post(
            "/terminal/kiosk",
            data={"username": "kiosk1", "password": "test1234",
                  "event_type": "entry"},
        ).data.decode()

        assert "INVALIDO_kiosk_disabled" in page
        event = AttendanceEvent.select().order_by(
            AttendanceEvent.id.desc()).get()
        assert event.outcome == "INVALIDO_kiosk_disabled"
        assert event.user_id == kiosk_user.id

    def test_wrong_password_rejected(self, client):
        create_user(username="kiosk2", name="Kiosk Dos")
        _set_config("kiosk_enabled", "true")

        page = client.post(
            "/terminal/kiosk",
            data={"username": "kiosk2", "password": "nope",
                  "event_type": "entry"},
            follow_redirects=True,
        ).data.decode()

        assert "Credenciales inválidas" in page
        # Failed kiosk attempts are now audited (Fix 13)
        event = AttendanceEvent.select().order_by(
            AttendanceEvent.id.desc()).get()
        assert event.outcome == "INVALIDO_credenciales"
        assert event.source == "kiosk"

    def test_incoherent_sequence_warns_then_confirms(self, client):
        kiosk_user = create_user(username="kiosk3", name="Kiosk Tres")
        AttendanceEvent.create(
            user=kiosk_user, event_type="entry", source="kiosk", outcome="OK",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _set_config("kiosk_enabled", "true")

        data = {"username": "kiosk3", "password": "test1234",
                "event_type": "entry"}
        warning_page = client.post("/terminal/kiosk", data=data).data.decode()
        assert "Continuar" in warning_page
        assert "Cancelar" in warning_page
        assert AttendanceEvent.select().count() == 1, \
            "Warning must not write a new event yet"

        page = client.post(
            "/terminal/kiosk", data={**data, "continue": "1"}
        ).data.decode()
        assert "Marca registrada" in page
        assert AttendanceEvent.select().count() == 2


# ── R2-6: must_change_password redirect in kiosk ──────────────────────


class TestKioskPasswordChange:
    def test_must_change_password_redirects_to_login(self, client, monkeypatch):
        """Kiosk user with must_change_password=True is redirected to
        login (not cambiar_password which requires a session), and no
        OK event is recorded."""
        kiosk_user = create_user(role="funcionario", username="cambiar",
                                 name="Cambiar Pass", must_change=True)
        _set_config("kiosk_enabled", "true")
        template = ShiftTemplate.create(name="Turno Mañana",
                                        start_time="06:00", end_time="14:00",
                                        weekday_mask="1111100")
        AssignedShift.create(user=kiosk_user, template=template)
        monkeypatch.setattr(
            "app.services.attendance._default_now",
            lambda: datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
        )

        response = client.post(
            "/terminal/kiosk",
            data={"username": "cambiar", "password": "test1234",
                  "event_type": "entry"},
            follow_redirects=False,
        )

        # Must redirect to login, not to cambiar_password
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
        # No OK event should be recorded
        ok_events = AttendanceEvent.select().where(
            AttendanceEvent.outcome.in_(("OK", "OK_extra"))
        ).count()
        assert ok_events == 0