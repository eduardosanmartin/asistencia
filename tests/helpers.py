"""Shared test helpers."""

from app.models import User
from app.services.security import hash_password


def create_user(role="funcionario", username=None, name=None,
                password="test1234", must_change=False, is_active=True):
    """Creates a user with a bcrypt password hash and returns it."""
    return User.create(
        username=username or f"{role}@test.local",
        email=f"{username or role}@test.local",
        name=name or f"Usuario {role}",
        role=role,
        password_hash=hash_password(password),
        must_change_password=must_change,
        is_active=is_active,
    )
