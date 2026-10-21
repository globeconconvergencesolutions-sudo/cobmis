"""Apply db/migrations/*.sql in order, once each, as the database owner role.

Usage (from backend/):  python -m scripts.migrate
Uses POSTGRES_SUPERUSER / POSTGRES_SUPERUSER_PASSWORD from the environment or .env. Idempotent:
applied files are recorded in app.schema_migrations and skipped next time.
"""

from __future__ import annotations

import os
import sys

import psycopg

from app.core.config import REPO_ROOT, get_settings

MIGRATIONS = REPO_ROOT / "db" / "migrations"


def _env(name: str, default: str | None = None) -> str | None:
    if name in os.environ:
        return os.environ[name]
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def owner_conninfo() -> str:
    s = get_settings()
    user = _env("POSTGRES_SUPERUSER", "cob_owner")
    pw = _env("POSTGRES_SUPERUSER_PASSWORD")
    if not pw:
        raise SystemExit("POSTGRES_SUPERUSER_PASSWORD is not set (.env)")
    return f"host={s.db_host} port={s.db_port} dbname={s.db_name} user={user} password={pw}"


def migrate(conninfo: str | None = None) -> list[str]:
    applied_now = []
    with psycopg.connect(conninfo or owner_conninfo(), autocommit=False) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS app")
        conn.execute("CREATE TABLE IF NOT EXISTS app.schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM app.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO app.schema_migrations (name) VALUES (%s)", (path.name,))
            conn.commit()
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    names = migrate()
    print("applied:", ", ".join(names) if names else "nothing (up to date)")
    sys.exit(0)
