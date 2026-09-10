"""Centralized configuration read from environment variables."""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    """Application configuration for the attendance control system.

    Every value is read from the environment. In development,
    python-dotenv loads the .env file automatically.
    """

    # ── Flask ──
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-key-insecure")
    PORT: int = int(os.getenv("FLASK_PORT", "5000"))

    # ── Database ──
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "asistencia")
    DATABASE_USER: str = os.getenv("DATABASE_USER", "asistencia")
    DATABASE_PASSWORD: str = os.getenv("DATABASE_PASSWORD", "")
    DATABASE_HOST: str = os.getenv("DATABASE_HOST", "localhost")
    DATABASE_PORT: int = int(os.getenv("DATABASE_PORT", "5432"))

    # ── JWT for QR signing URLs ──
    JWT_SECRET: str = os.getenv(
        "JWT_SECRET", os.getenv("FLASK_SECRET_KEY", "dev-key-insecure")
    )

    # ── Public base URL (QRs encode full /marcar/<jwt> URLs) ──
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:5000")

    # ── CSRF ──
    WTF_CSRF_TIME_LIMIT = None  # No CSRF token expiration

    # ── Session security ──
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # SESSION_COOKIE_SECURE is set dynamically in create_app when
    # APP_BASE_URL uses HTTPS. Not set by default (local HTTP dev).

    # ── Super Admin seeding ──
    SUPERADMIN_USERNAME: str = os.getenv("SUPERADMIN_USERNAME", "admin")
    SUPERADMIN_PASSWORD: str = os.getenv("SUPERADMIN_PASSWORD", "")
    SUPERADMIN_EMAIL: str = os.getenv(
        "SUPERADMIN_EMAIL", "admin@asistencia.local"
    )
    SUPERADMIN_NAME: str = os.getenv("SUPERADMIN_NAME", "Administrador")