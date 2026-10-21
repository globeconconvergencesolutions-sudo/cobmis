"""Create/update the app's database roles, then apply db/migrations/*.sql in order, once each.

Usage (from backend/):  python -m scripts.migrate
Runs as the owner role (POSTGRES_SUPERUSER / POSTGRES_SUPERUSER_PASSWORD in .env). Role
passwords come from COB_APP_PASSWORD, COB_INGEST_PASSWORD and COB_READONLY_PASSWORD.
Idempotent: roles are created if missing (otherwise their password and attributes are
re-applied), and applied migrations are recorded in app.schema_migrations.
"""

from __future__ import annotations

import sys

import psycopg

from app.core.config import REPO_ROOT
from app.db.roles import DatabaseSetupError, connect_owner, ensure_roles, missing_roles

MIGRATIONS = REPO_ROOT / "db" / "migrations"


def migrate(conn: psycopg.Connection | None = None, dbname: str | None = None) -> dict[str, list[str]]:
    own = conn is None
    conn = conn or connect_owner(dbname)
    try:
        created = ensure_roles(conn)  # before any GRANT in the migrations
        conn.commit()
        if missing := missing_roles(conn):
            raise DatabaseSetupError(f"Database roles still missing after setup: {', '.join(missing)}")
        conn.execute("CREATE SCHEMA IF NOT EXISTS app")
        conn.execute("CREATE TABLE IF NOT EXISTS app.schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM app.schema_migrations")}
        conn.commit()
        applied = []
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            try:
                conn.execute(path.read_text(encoding="utf-8"))
            except psycopg.Error as exc:
                conn.rollback()
                raise DatabaseSetupError(f"Migration {path.name} failed: {str(exc).strip()}") from None
            conn.execute("INSERT INTO app.schema_migrations (name) VALUES (%s)", (path.name,))
            conn.commit()
            applied.append(path.name)
        return {"roles_created": created, "applied": applied}
    finally:
        if own:
            conn.close()


def main() -> int:
    try:
        res = migrate()
    except DatabaseSetupError as exc:
        print(f"ERROR: {exc.code}", file=sys.stderr)
        return 1
    print("roles created:", ", ".join(res["roles_created"]) or "none (all present; passwords re-applied)")
    print("migrations applied:", ", ".join(res["applied"]) or "nothing (up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
