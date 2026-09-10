"""CSRF protection feature tests.

Verifies that CSRF is enforced when WTF_CSRF_ENABLED is True:
- POST without token is rejected (400).
- POST with valid token is accepted.
"""

import pytest
from app import create_app
from app.models import User
from app.services.security import hash_password


@pytest.fixture
def csrf_app():
    """App factory with CSRF ENABLED (production-like)."""
    application = create_app(testing=True)
    application.config.update(
        {
            "TESTING": True,
            "SECRET_KEY": "test-csrf-secret-key-32-chars-min!!",
            "JWT_SECRET": "test-csrf-jwt-secret-key-32-chars!!",
            "WTF_CSRF_ENABLED": True,
            "WTF_CSRF_METHODS": ["POST"],
        }
    )
    with application.app_context():
        yield application


@pytest.fixture
def csrf_client(csrf_app):
    return csrf_app.test_client()


@pytest.fixture
def csrf_admin_user(csrf_app):
    """Create an admin user for CSRF tests."""
    with csrf_app.app_context():
        return User.create(
            username="csrfadmin",
            email="csrfadmin@test.local",
            name="CSRF Admin",
            role="administrador",
            password_hash=hash_password("test1234"),
            must_change_password=False,
            is_active=True,
        )


@pytest.fixture
def csrf_logged_in(csrf_app, csrf_admin_user):
    """Login an admin user and return (client, user)."""
    import re
    client = csrf_app.test_client()
    with csrf_app.app_context():
        # Fetch the login form to get a valid CSRF token
        resp = client.get("/login")
        assert resp.status_code == 200
        match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', resp.data.decode()
        )
        assert match is not None
        csrf_token = match.group(1)

        resp = client.post(
            "/login",
            data={
                "username": "csrfadmin",
                "password": "test1234",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
    return client, csrf_admin_user


class TestCSRFProtection:
    def test_post_without_csrf_token_is_rejected(self, csrf_app):
        """POST to a CSRF-protected route without token → 400."""
        client = csrf_app.test_client()
        with csrf_app.app_context():
            User.create(
                username="csrfuser",
                email="csrfuser@test.local",
                name="CSRF User",
                role="funcionario",
                password_hash=hash_password("test1234"),
                must_change_password=False,
                is_active=True,
            )
        resp = client.post(
            "/login",
            data={"username": "csrfuser", "password": "test1234"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_post_with_valid_csrf_token_is_accepted(self, csrf_logged_in):
        """POST with a valid CSRF token from the form succeeds."""
        client, user = csrf_logged_in
        # Fetch a GET to obtain a valid CSRF token from the page
        resp = client.get("/admin/plantillas")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="csrf_token"' in html

        # Extract the token value
        import re
        match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', html
        )
        assert match is not None, "CSRF token not found in form"
        csrf_token = match.group(1)

        # POST with the extracted token
        resp = client.post(
            "/admin/configuracion",
            data={
                "csrf_token": csrf_token,
                "key": "timezone",
                "value": "UTC",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_login_form_includes_csrf_token(self, csrf_app):
        """The login form renders a CSRF hidden input."""
        client = csrf_app.test_client()
        resp = client.get("/login")
        html = resp.data.decode()
        assert 'name="csrf_token"' in html
        assert 'value="' in html
