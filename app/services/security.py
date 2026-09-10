"""Security service — bcrypt password hashing helpers.

Helpers:
    hash_password         — bcrypt hash generation.
    verify_password       — bcrypt verification; never raises.
    generate_temp_password — provisioning of temporary passwords
                             (must_change_password flow).
"""

import secrets
import string

import bcrypt

# Same length standard as MIN_PASSWORD_LENGTH enforced in auth routes.
TEMP_PASSWORD_LENGTH = 8

# Alphanumeric: safe to show in flash messages and easy to type.
_TEMP_PASSWORD_CHARSET = string.ascii_letters + string.digits


def hash_password(plain: str) -> str:
    """Generates a bcrypt hash for a plain-text password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str | None, hashed: str | None) -> bool:
    """Verifies a plain password against a bcrypt hash.

    Returns False (never raises) when either argument is None, enabling
    credential-less accounts.
    """
    if plain is None or hashed is None:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash: treat as invalid credentials.
        return False


def generate_temp_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    """Generates a secure alphanumeric temporary password.

    Used when provisioning users: the temp password is hashed and the
    user is forced to change it on first login.
    """
    if length < 1:
        raise ValueError("length must be greater than 0")
    return "".join(
        secrets.choice(_TEMP_PASSWORD_CHARSET) for _ in range(length)
    )