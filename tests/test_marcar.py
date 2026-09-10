"""Marcar flow tests (tasks 3.4, 3.6).

Covers the worker landing page (person + event + Confirmar) and the
confirm POST: OK recording, double-submit rejection (INVALIDO_reuso ->
"Ya registró su marca"), expired links, inactive persons, and
out-of-window submissions (INVALIDO_fuera_de_horario).
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models import AttendanceEvent, ShiftTemplate, SystemConfig, User
from app.services.attendance import _default_now
from app.services.security import hash_password
from app.services.token import generate_qr_token

from tests.helpers import create_user


@pytest.fixture(autouse=True)
def _token_generation_clock(monkeypatch):
    """Fresh tokens: issue them a few seconds ago in real time so they
    are always valid at the real validation instant."""
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    monkeypatch.setattr(
        "app.services.token._default_now", lambda: fresh
    )
    return fresh


def _worker(username="carlos"):
    return create_user(role="funcionario", username=username,
                       name=f"Usuario {username}")


def _token_for(user, event_type="entry", shift_id=None, window_secs=3600):
    return generate_qr_token(user.id, event_type, shift_id, window_secs)


def _assign_shift(app, user, start="06:00", end="14:00", name="Turno Mañana"):
    template = ShiftTemplate.create(name=name, start_time=start,
                                    end_time=end, weekday_mask="1111100")
    from app.models import AssignedShift
    return AssignedShift.create(user=user, template=template)


# ── Landing page (task 3.4) ───────────────────────────────────────────


class TestMarcarLanding:
    def test_landing_shows_person_event_and_confirm(self, app, client):
        carlos = _worker()
        token = _token_for(carlos)
        page = client.get(f"/marcar/{token}").data.decode()
        assert "Usuario carlos" in page
        assert "Ingreso" in page
        assert "Confirmar" in page

    def test_invalid_link_shows_error(self, client):
        page = client.get("/marcar/not-a-token").data.decode()
        assert "inválido" in page or "expirado" in page
        assert "Confirmar" not in page

    def test_expired_link_shows_error(self, app, client):
        carlos = _worker()
        token = _token_for(carlos, window_secs=-60)
        page = client.get(f"/marcar/{token}").data.decode()
        assert "inválido" in page or "expirado" in page


# ── Confirm POST (task 3.4) ───────────────────────────────────────────


class TestMarcarConfirm:
    def test_confirm_records_ok(self, app, client):
        carlos = _worker()
        token = _token_for(carlos)
        page = client.post(f"/marcar/{token}").data.decode()
        assert "Marca registrada" in page
        event = AttendanceEvent.select().order_by(
            AttendanceEvent.id.desc()).get()
        assert "OK" in event.outcome  # OK when shift-bound, OK_extra otherwise
        assert event.user_id == carlos.id
        assert event.event_type == "entry"
        assert event.source == "qr"

    def test_double_submit_rejected_as_reuso(self, app, client):
        carlos = _worker()
        token = _token_for(carlos)
        first = client.post(f"/marcar/{token}").data.decode()
        assert "Marca registrada" in first

        second = client.post(f"/marcar/{token}").data.decode()
        assert "Ya registró su marca" in second

        rows = list(AttendanceEvent.select().order_by(AttendanceEvent.id))
        assert len(rows) == 2
        assert "OK" in rows[0].outcome
        assert rows[1].outcome == "INVALIDO_reuso"
        assert rows[1].token_hash is None

    def test_expired_confirm_records_expirado(self, app, client):
        carlos = _worker()
        token = _token_for(carlos, window_secs=-60)
        page = client.post(f"/marcar/{token}").data.decode()
        assert "INVALIDO_qr_expirado" in page
        event = AttendanceEvent.select().order_by(
            AttendanceEvent.id.desc()).get()
        assert event.outcome == "INVALIDO_qr_expirado"

    def test_inactive_person_records_persona_incorrecta(self, app, client):
        carlos = _worker(username="fantasma")
        carlos.is_active = False
        carlos.save()
        token = _token_for(carlos)
        page = client.post(f"/marcar/{token}").data.decode()
        assert "INVALIDO_persona_incorrecta" in page

    def test_out_of_window_records_fuera_de_horario(self, app, client,
                                                    monkeypatch):
        carlos = _worker(username="trasnoche")
        assignment = _assign_shift(app, carlos)
        # Token bound to the shift; issued fresh with a 10h window so it
        # never expires during the test.
        token = _token_for(carlos, shift_id=assignment.id, window_secs=36000)
        # Shift window [05:55, 14:00] UTC — freeze validation at 15:00.
        monkeypatch.setattr(
            "app.services.attendance._default_now",
            lambda: datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc),
        )
        page = client.post(f"/marcar/{token}").data.decode()
        assert "INVALIDO_fuera_de_horario" in page
        event = AttendanceEvent.select().order_by(
            AttendanceEvent.id.desc()).get()
        assert event.outcome == "INVALIDO_fuera_de_horario"