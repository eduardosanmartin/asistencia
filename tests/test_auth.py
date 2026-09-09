"""Auth tests — session login, role system, password change and security helpers.

Covers the auth-shift delta spec: Session Login, Role System,
Role-Based Access Control, Session Logout, plus the security service
unit tests (task 1.4).
"""

import pytest
from flask import session

from app.services.security import (
    generate_temp_password,
    hash_password,
    verify_password,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _create_user(app, username, password="test1234", role="funcionario",
                 must_change=False):
    """Creates a user with a bcrypt hash, returns the user."""
    from app.models import User

    with app.app_context():
        return User.create(
            username=username,
            email=f"{username}@test.local",
            name=f"Usuario {username}",
            role=role,
            password_hash=hash_password(password),
            must_change_password=must_change,
            is_active=True,
        )


def _login(client, username, password):
    """Helper: POST /login with username and password."""
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


# ── Security service (task 1.4) ───────────────────────────────────────


class TestHashPassword:
    def test_hash_returns_60_char_bcrypt_string(self):
        hashed = hash_password("test1234")
        assert isinstance(hashed, str)
        assert len(hashed) == 60
        assert hashed.startswith("$2")

    def test_hash_is_salted_but_both_verify(self):
        h1 = hash_password("test1234")
        h2 = hash_password("test1234")
        assert h1 != h2
        assert verify_password("test1234", h1) is True
        assert verify_password("test1234", h2) is True


class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        hashed = hash_password("test1234")
        assert verify_password("test1234", hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("test1234")
        assert verify_password("wrong-pass", hashed) is False

    def test_none_hash_returns_false(self):
        assert verify_password("test", None) is False

    def test_none_plain_returns_false(self):
        hashed = hash_password("test1234")
        assert verify_password(None, hashed) is False

    def test_malformed_hash_never_raises(self):
        assert verify_password("test1234", "not-a-bcrypt-hash") is False


class TestGenerateTempPassword:
    def test_default_length_is_8(self):
        assert len(generate_temp_password()) == 8

    def test_explicit_length_respected(self):
        assert len(generate_temp_password(length=12)) == 12

    def test_alphanumeric_charset(self):
        assert generate_temp_password().isalnum()

    def test_values_are_random(self):
        assert generate_temp_password() != generate_temp_password()

    def test_roundtrip_with_hash_password(self):
        pwd = generate_temp_password()
        assert verify_password(pwd, hash_password(pwd)) is True


# ── Session Login (REQ auth-shift) ────────────────────────────────────


class TestLoginPage:
    def test_login_renders_username_password_form(self, client):
        response = client.get("/login")
        assert response.status_code == 200
        html = response.data.decode()
        assert 'name="username"' in html
        assert 'name="password"' in html


class TestLogin:
    def test_valid_credentials_create_session_and_redirect_home(
        self, app, client, sample_user_password
    ):
        _create_user(app, "juan", password=sample_user_password,
                     role="funcionario")
        response = _login(client, "juan", sample_user_password)

        assert response.status_code == 302
        assert response.location == "/"
        with client.session_transaction() as sess:
            assert sess.get("user_id") is not None
            assert sess.get("name") == "Usuario juan"
            assert sess.get("role") == "funcionario"

    def test_invalid_credentials_show_generic_error(
        self, app, client, sample_user_password
    ):
        _create_user(app, "juan", password=sample_user_password)
        response = _login(client, "juan", "password-incorrecta")

        assert response.status_code == 200
        assert "Credenciales inválidas" in response.data.decode()
        with client.session_transaction() as sess:
            assert sess.get("user_id") is None

    def test_unknown_username_same_generic_error(self, client):
        response = _login(client, "ghost", "whatever")
        assert response.status_code == 200
        assert "Credenciales inválidas" in response.data.decode()

    def test_must_change_password_gate_forces_redirect(
        self, app, client, sample_user_password
    ):
        _create_user(app, "nuevo", password=sample_user_password,
                     must_change=True)
        response = _login(client, "nuevo", sample_user_password)

        assert response.status_code == 302
        assert "/cambiar-password" in response.location

    def test_must_change_password_blocks_other_routes(
        self, app, client, sample_user_password
    ):
        _create_user(app, "nuevo", password=sample_user_password,
                     must_change=True)
        _login(client, "nuevo", sample_user_password)

        response = client.get("/terminal", follow_redirects=False)
        assert response.status_code == 302
        assert "/cambiar-password" in response.location


# ── Role System (REQ auth-shift) ──────────────────────────────────────


class TestRoleSystem:
    def test_admin_has_full_access(self, auth_admin):
        client, _ = auth_admin
        response = client.get("/admin/usuarios")
        assert response.status_code == 200

    def test_funcionario_cannot_manage_users(self, auth_funcionario):
        client, _ = auth_funcionario
        response = client.get("/admin/usuarios")
        assert response.status_code == 403

    def test_supervisor_can_access_asignaciones_route(self, auth_supervisor):
        client, _ = auth_supervisor
        response = client.get("/admin/asignaciones")
        assert response.status_code == 200

    def test_funcionario_denied_admin_config_route(self, auth_funcionario):
        client, _ = auth_funcionario
        response = client.get("/admin/configuracion")
        assert response.status_code == 403

    def test_supervisor_func_roles_comma_separated(self, auth_supervisor_func):
        client, user = auth_supervisor_func
        assert user.get_roles() == ["supervisor", "funcionario"]
        assert user.has_any_role(["supervisor", "administrador"])


# ── Session Logout (REQ auth-shift) ───────────────────────────────────


class TestLogout:
    def test_logout_destroys_session(self, auth_admin):
        client, _ = auth_admin
        response = client.post("/logout", follow_redirects=False)

        assert response.status_code == 302
        assert "/login" in response.location
        with client.session_transaction() as sess:
            assert sess.get("user_id") is None

        # Protected route now redirects to login again
        response = client.get("/admin/usuarios", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.location

    def test_access_without_session_redirects_to_login(self, client):
        response = client.get("/terminal", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.location

        followed = client.get(response.location)
        assert "Debe iniciar sesión" in followed.data.decode()