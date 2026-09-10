"""Data models — Peewee ORM.

Uses a DatabaseProxy so the app factory can switch between PostgreSQL
(production) and SQLite :memory: (tests), replicating firmaDocs.

Models:
    User             — worker/administrator with comma-separated roles and
                       an optional supervisor (self-FK, defines teams).
    ShiftTemplate    — reusable shift pattern (name, times, weekday mask).
    AssignedShift    — links a user to a template (the assignment).
    AttendanceEvent  — unified audit log: every attempt (OK_* / INVALIDO_*)
                       writes exactly one row.
    SystemConfig     — key/value store for runtime-adjustable settings.
"""

import logging
from datetime import datetime, timezone

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    DatabaseProxy,
    ForeignKeyField,
    IntegerField,
    Model,
    PostgresqlDatabase,
    SqliteDatabase,
)

# ── Database proxy ────────────────────────────────────────────────────

db_proxy = DatabaseProxy()


def _utcnow():
    """Current UTC time as an aware datetime (storage convention)."""
    return datetime.now(timezone.utc)


class BaseModel(Model):
    """Base model bound to the database proxy."""

    class Meta:
        database = db_proxy


# ── User ──────────────────────────────────────────────────────────────


class User(BaseModel):
    """Worker or administrator of the single organization.

    ``role`` is a comma-separated string allowing multiple roles, e.g.
    ``supervisor,funcionario``. ``supervisor_id`` is a self-FK that
    defines team membership for supervisors.
    """

    username = CharField(max_length=128, unique=True)
    email = CharField(max_length=255)
    name = CharField(max_length=255)
    role = CharField(max_length=64, default="funcionario")
    password_hash = CharField(max_length=60, null=True)  # bcrypt; None = no credentials
    must_change_password = BooleanField(default=False)
    is_active = BooleanField(default=True)
    supervisor = ForeignKeyField(
        "self", null=True, backref="team", column_name="supervisor_id"
    )
    created_at = DateTimeField(default=_utcnow)

    def get_roles(self):
        """Returns the list of roles for this user."""
        if not self.role:
            return []
        return [r.strip() for r in self.role.split(",") if r.strip()]

    def has_role(self, role):
        """True if the user holds the given role."""
        return role in self.get_roles()

    def has_any_role(self, roles):
        """True if the user holds at least one of the given roles."""
        user_roles = self.get_roles()
        return any(r in user_roles for r in roles)

    def set_roles(self, roles_list):
        """Replaces the roles of this user from a list."""
        self.role = ",".join(roles_list) if roles_list else "funcionario"

    def add_role(self, role):
        """Adds a role to this user if not already present."""
        roles = self.get_roles()
        if role not in roles:
            roles.append(role)
            self.role = ",".join(roles)

    def remove_role(self, role):
        """Removes a role from this user if present."""
        roles = self.get_roles()
        if role in roles:
            roles.remove(role)
            self.role = ",".join(roles) if roles else "funcionario"

    class Meta:
        table_name = "users"


# ── ShiftTemplate ─────────────────────────────────────────────────────


class ShiftTemplate(BaseModel):
    """Reusable shift pattern managed by Administradores.

    ``weekday_mask`` is a 7-character string, index 0 = Monday through
    index 6 = Sunday (e.g. ``1111100`` for Monday–Friday).
    """

    name = CharField(max_length=128, unique=True)
    start_time = CharField(max_length=5)  # "HH:MM"
    end_time = CharField(max_length=5)    # "HH:MM"
    weekday_mask = CharField(max_length=7, default="1111100")
    is_active = BooleanField(default=True)
    created_at = DateTimeField(default=_utcnow)

    @property
    def is_overnight(self):
        """True when the shift crosses midnight (end <= start)."""
        return self.end_time <= self.start_time

    class Meta:
        table_name = "shift_templates"


# ── AssignedShift ─────────────────────────────────────────────────────


class AssignedShift(BaseModel):
    """Links a user to a template: the user is assigned that shift.

    Weekday applicability lives on the template's ``weekday_mask``.
    A user may hold several assignments, including overlapping days.
    """

    user = ForeignKeyField(User, backref="assigned_shifts")
    template = ForeignKeyField(ShiftTemplate, backref="assigned_shifts")
    is_active = BooleanField(default=True)
    created_at = DateTimeField(default=_utcnow)

    class Meta:
        table_name = "assigned_shifts"


# ── AttendanceEvent ───────────────────────────────────────────────────


class AttendanceEvent(BaseModel):
    """Unified audit log: one row per attendance attempt.

    Every attempt is recorded: valid events use ``OK``/``OK_extra``,
    invalid ones use ``INVALIDO_*`` outcome prefixes. The nullable user
    FK allows logging attempts where the person could not be identified.
    ``token_hash`` is a plain unique index enforcing single-use QR tokens
    (check-then-insert at the app level, index as backstop).
    """

    EVENT_TYPES = ("entry", "exit")

    user = ForeignKeyField(User, null=True, backref="events")
    event_type = CharField(max_length=16)  # entry | exit
    source = CharField(max_length=16, default="qr")  # qr | kiosk
    outcome = CharField(max_length=32)  # OK | OK_extra | INVALIDO_*
    timestamp = DateTimeField(default=_utcnow)  # stored in UTC
    token_hash = CharField(max_length=64, null=True, unique=True)
    shift = ForeignKeyField(
        AssignedShift, null=True, backref="events", column_name="shift_id"
    )
    is_extra = BooleanField(default=False)
    delay_minutes = IntegerField(null=True)

    class Meta:
        table_name = "attendance_events"

    @property
    def is_valid(self):
        return self.outcome.startswith("OK")


# ── SystemConfig ──────────────────────────────────────────────────────


class SystemConfig(BaseModel):
    """Key/value store for runtime-adjustable settings.

    Keys used by the attendance service:
        kiosk_enabled       — "true"/"false" (default "true")
        late_grace_minutes  — int (default 5)
        qr_validity_seconds — int (default 45)
        timezone            — IANA name (default "UTC")
    """

    key = CharField(max_length=64, primary_key=True)
    value = CharField(max_length=255, default="")

    class Meta:
        table_name = "system_config"


# ── Config helpers ────────────────────────────────────────────────────


def get_config_value(key, default=None):
    """Reads a SystemConfig value, falling back to ``default``."""
    row = SystemConfig.get_or_none(SystemConfig.key == key)
    return row.value if row is not None else default


def set_config_value(key, value):
    """Upserts a SystemConfig value at runtime."""
    row, _ = SystemConfig.get_or_create(key=key, defaults={"value": str(value)})
    if row.value != str(value):
        row.value = str(value)
        row.save()
    return row


# ── Database initialization ───────────────────────────────────────────


def init_db():
    """Connects and creates tables (safe=True never drops existing)."""
    db_proxy.connect()
    db_proxy.create_tables(
        [User, ShiftTemplate, AssignedShift, AttendanceEvent, SystemConfig],
        safe=True,
    )
    _create_indexes()


def _create_indexes():
    """Creates the named indexes referenced by design.md.

    ``create_tables(safe=True)`` does not recreate indexes on existing
    tables, so they are applied explicitly. Idempotent on both SQLite
    and PostgreSQL.
    """
    database = db_proxy.obj
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_events_user_ts "
        "ON attendance_events (user_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_events_outcome "
        "ON attendance_events (outcome)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_token_hash "
        "ON attendance_events (token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_users_supervisor "
        "ON users (supervisor_id)",
    )
    for statement in statements:
        try:
            database.execute_sql(statement)
        except Exception:  # noqa: BLE001 - do not block startup on index errors
            logging.warning("Failed to create index: %s", statement,
                            exc_info=True)


def make_production_db(config):
    """Builds the PostgreSQL database from the app configuration."""
    return PostgresqlDatabase(
        config.get("DATABASE_NAME", "asistencia"),
        user=config.get("DATABASE_USER", "asistencia"),
        password=config.get("DATABASE_PASSWORD", ""),
        host=config.get("DATABASE_HOST", "localhost"),
        port=config.get("DATABASE_PORT", 5432),
    )


def make_test_db():
    """Builds the in-memory SQLite database used by the test suite."""
    return SqliteDatabase(":memory:")


def make_dev_db():
    """Builds a file-based SQLite database for local development."""
    return SqliteDatabase("dev.db")