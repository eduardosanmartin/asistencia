"""App factory — centralized initialization (firmaDocs convention)."""

import os

from dotenv import load_dotenv

# Load environment variables BEFORE importing Config
load_dotenv()

from flask import Flask  # noqa: E402

from .config import Config  # noqa: E402


def _seed_superadmin(app):
    """Creates or updates the super administrator from environment variables.

    The super admin is the initial ``administrador`` account. It is only
    seeded in non-testing mode, from SUPERADMIN_USERNAME and
    SUPERADMIN_PASSWORD (bcrypt-hashed at rest).
    """
    username = app.config.get("SUPERADMIN_USERNAME", "admin").strip()
    password = app.config.get("SUPERADMIN_PASSWORD", "").strip()
    email = app.config.get("SUPERADMIN_EMAIL", "").strip()

    if not password:
        app.logger.warning(
            "Super admin no creado: definir SUPERADMIN_PASSWORD en .env"
        )
        return

    from .models import User
    from .services.security import hash_password, verify_password

    try:
        admin = User.get(User.username == username)
        changed = False
        if not verify_password(password, admin.password_hash):
            admin.password_hash = hash_password(password)
            admin.must_change_password = False
            changed = True
        if admin.name != app.config.get("SUPERADMIN_NAME"):
            admin.name = app.config.get("SUPERADMIN_NAME")
            changed = True
        if not admin.has_role("administrador"):
            admin.add_role("administrador")
            changed = True
        if not admin.is_active:
            admin.is_active = True
            changed = True
        if changed:
            admin.save()
            app.logger.info("Super admin actualizado: %s", username)
    except User.DoesNotExist:
        User.create(
            username=username,
            email=email or f"{username}@asistencia.local",
            name=app.config.get("SUPERADMIN_NAME", "Administrador"),
            role="administrador",
            password_hash=hash_password(password),
            must_change_password=False,
            is_active=True,
        )
        app.logger.info("Super admin creado: %s", username)


def create_app(testing: bool = False) -> Flask:
    """Creates and configures the Flask application.

    Args:
        testing: when True, uses SQLite :memory: and TESTING mode.

    Returns:
        A configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ── Configuration ────────────────────────────────────────────────
    app.config.from_object(Config)
    if testing:
        app.config["TESTING"] = True

    # ── Secret key guard ──────────────────────────────────────────────
    if not testing:
        _INSECURE_DEFAULTS = {"dev-key-insecure", "change-me-in-prod", "secret"}
        secret = app.config.get("SECRET_KEY", "")
        jwt_secret = app.config.get("JWT_SECRET", "")
        if not secret or secret in _INSECURE_DEFAULTS or len(secret) < 32:
            raise RuntimeError(
                "SECRET_KEY is missing or insecure. "
                "Set FLASK_SECRET_KEY to a unique, random string "
                "(>= 32 chars) in your environment."
            )
        if not jwt_secret or jwt_secret in _INSECURE_DEFAULTS or len(jwt_secret) < 32:
            raise RuntimeError(
                "JWT_SECRET is missing or insecure. "
                "Set JWT_SECRET to a unique, random string "
                "(>= 32 chars) in your environment."
            )

    # ── CSRF ─────────────────────────────────────────────────────────
    from flask_wtf.csrf import CSRFProtect

    csrf = CSRFProtect(app)
    app.extensions["csrf"] = csrf

    # ── Session security (secure cookie for HTTPS) ──────────────────
    if not testing:
        base_url = app.config.get("APP_BASE_URL", "")
        if base_url.lower().startswith("https"):
            app.config["SESSION_COOKIE_SECURE"] = True

    # ── Database ─────────────────────────────────────────────────────
    from .models import db_proxy, init_db, make_production_db, make_test_db

    if testing:
        database = make_test_db()
    else:
        database = make_production_db(app.config)

    db_proxy.initialize(database)

    with app.app_context():
        init_db()
        if not testing:
            _seed_superadmin(app)

    # ── Blueprints ───────────────────────────────────────────────────
    from .blueprints.admin import admin_bp
    from .blueprints.auth import auth_bp
    from .blueprints.marcar import marcar_bp
    from .blueprints.terminal import terminal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(terminal_bp)
    app.register_blueprint(marcar_bp)

    # ── Root routes ──────────────────────────────────────────────────
    from .routes import register_routes

    register_routes(app)

    return app