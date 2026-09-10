"""Schema runner for PostgreSQL.

Applies numbered SQL files from ``migrations/`` in order, tracking
applied migrations in a ``schema_migrations`` table.

Usage:
    python migrate.py            # apply pending migrations
    python migrate.py --dry-run  # show what would run without applying
"""

import argparse
import os
import sys

import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _connect():
    """Connects to PostgreSQL using environment variables."""
    return psycopg2.connect(
        dbname=os.getenv("DATABASE_NAME", "asistencia"),
        user=os.getenv("DATABASE_USER", "asistencia"),
        password=os.getenv("DATABASE_PASSWORD", ""),
        host=os.getenv("DATABASE_HOST", "localhost"),
        port=os.getenv("DATABASE_PORT", "5432"),
    )


def _applied_names(conn):
    """Returns the set of migration names already applied."""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _pending_migrations(conn):
    """Returns pending migration file paths in filename order."""
    applied = _applied_names(conn)
    pending = []
    for filename in sorted(os.listdir(MIGRATIONS_DIR)):
        if not filename.endswith(".sql"):
            continue
        if filename not in applied:
            pending.append(os.path.join(MIGRATIONS_DIR, filename))
    return pending


def apply_migrations(conn, dry_run=False):
    """Applies every pending migration inside a transaction."""
    pending = _pending_migrations(conn)
    if not pending:
        print("No pending migrations.")
        return 0

    for path in pending:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        print(f"{'[dry-run] would apply' if dry_run else 'applying'}: {name}")
        if dry_run:
            continue
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (name) VALUES (%s)", (name,)
            )
        conn.commit()
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list pending migrations without applying them",
    )
    args = parser.parse_args()

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MIGRATIONS_SQL)
        conn.commit()
        count = apply_migrations(conn, dry_run=args.dry_run)
        print(f"{count} migration(s).")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())