"""Shared fixtures for the whole test suite.

Replicates the firmaDocs test conventions: SQLite :memory: database,
app factory with testing=True, session-based role fixtures planted via
``session_transaction()``.
"""

import pytest

from app import create_app
from app.models import User
from app.services.security import hash_password

from tests.helpers import create_user


# ── App and client ────────────────────────────────────────────────────


@pytest.fixture
def app():
    """App factory with SQLite :memory: and testing mode."""
    application = create_app(testing=True)
    application.config.update(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "JWT_SECRET": "test-jwt-secret-key-0123456789abcdef",
            "WTF_CSRF_ENABLED": False,
        }
    )
    with application.app_context():
        yield application


@pytest.fixture
def client(app):
    """Flask test client bound to the app fixture."""
    return app.test_client()


@pytest.fixture
def sample_user_password():
    """Standard password used by the user fixtures."""
    return "test1234"


def _plant_session(client, user):
    """Plants a Flask session for a user (firmaDocs pattern)."""
    with client.session_transaction() as sess:
        sess["user_id"] = user.id
        sess["name"] = user.name
        sess["role"] = user.role


# ── Authenticated role fixtures ───────────────────────────────────────


@pytest.fixture
def auth_admin(client):
    """Session authenticated as an Administrador."""
    with client.application.app_context():
        user = create_user(role="administrador", username="admin",
                           name="Admin")
    _plant_session(client, user)
    yield client, user
    with client.session_transaction() as sess:
        sess.clear()


@pytest.fixture
def auth_supervisor(client):
    """Session authenticated as a Supervisor (with own team)."""
    with client.application.app_context():
        supervisor = create_user(role="supervisor", username="super",
                                 name="Supervisor")
        # Supervisors are the root of their own team (spec scenario).
        supervisor.supervisor_id = supervisor.id
        supervisor.save()
    _plant_session(client, supervisor)
    yield client, supervisor
    with client.session_transaction() as sess:
        sess.clear()


@pytest.fixture
def auth_funcionario(client):
    """Session authenticated as a Funcionario."""
    with client.application.app_context():
        user = create_user(role="funcionario", username="func",
                           name="Funcionario")
    _plant_session(client, user)
    yield client, user
    with client.session_transaction() as sess:
        sess.clear()


@pytest.fixture
def auth_supervisor_func(client):
    """Session authenticated as a user with roles supervisor,funcionario."""
    with client.application.app_context():
        user = create_user(role="supervisor,funcionario",
                           username="superfunc", name="Supervisor Funcionario")
    _plant_session(client, user)
    yield client, user
    with client.session_transaction() as sess:
        sess.clear()