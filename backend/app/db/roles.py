"""Database login roles and connections.

The three least-privilege roles are created (or have their password and attributes
re-applied) by the migration runner on every run, as the owner role, before any GRANT.
Nothing depends on Docker's one-time init scripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from psycopg import sql

from app.core.config import REPO_ROOT, get_settings


@dataclass(frozen=True)
class RoleSpec:
    name: str
    password_var: str
    purpose: str
    settings: tuple[tuple[str, str], ...] = ()


ROLES = (
    RoleSpec("cob_app", "COB_APP_PASSWORD", "API runtime"),
    RoleSpec("cob_ingest", "COB_INGEST_PASSWORD", "ingestion writer"),
    RoleSpec(
        "cob_readonly",
        "COB_READONLY_PASSWORD",
        "text-to-SQL executor",
        (
            ("statement_timeout", "10s"),
            ("default_transaction_read_only", "on"),
            ("idle_in_transaction_session_timeout", "30s"),
        ),
    ),
)
BY_NAME = {r.name: r for r in ROLES}


class DatabaseSetupError(SystemExit):
    """Raised with a one-paragraph explanation instead of a traceback."""


def env(name: str, default: str | None = None) -> str | None:
    """Value from the environment, else from the repo's .env file."""
    if name in os.environ:
        return os.environ[name]
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def _password(var: str) -> str:
    pw = env(var)
    if not pw:
        raise DatabaseSetupError(f"{var} is not set. Add it to .env (see .env.example) and re-run.")
    return pw


def conninfo(user: str, password: str, dbname: str | None = None) -> str:
    s = get_settings()
    return psycopg.conninfo.make_conninfo(
        host=s.db_host, port=s.db_port, dbname=dbname or s.db_name, user=user, password=password, connect_timeout=5
    )


def owner_conninfo(dbname: str | None = None) -> str:
    return conninfo(env("POSTGRES_SUPERUSER", "cob_owner"), _password("POSTGRES_SUPERUSER_PASSWORD"), dbname)


def role_conninfo(role: str, dbname: str | None = None) -> str:
    return conninfo(role, _password(BY_NAME[role].password_var), dbname)


def ensure_roles(conn: psycopg.Connection) -> list[str]:
    """Create missing roles; reset password and attributes of existing ones. Idempotent.

    Returns the roles that were created. Must run as a role allowed to manage roles
    (the compose owner role is a superuser).
    """
    me = conn.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user").fetchone()
    if not me or not me[0]:
        raise DatabaseSetupError(
            "The migration role cannot create roles. Run migrations as the database owner "
            "(POSTGRES_SUPERUSER in .env, cob_owner by default)."
        )
    created = []
    for role in ROLES:
        pw = _password(role.password_var)
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role.name,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(
                "{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.SQL(verb), sql.Identifier(role.name), sql.Literal(pw))
        )
        for key, value in role.settings:
            conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(sql.Identifier(role.name), sql.Identifier(key), sql.Literal(value)))
        if not exists:
            created.append(role.name)
    return created


def _role_exists(role: str) -> bool | None:
    """Ask the owner whether a role exists (None if the owner cannot connect either).

    Needed because, with password authentication, Postgres answers "password authentication
    failed" for a role that does not exist, so the client error alone cannot tell them apart.
    """
    try:
        with psycopg.connect(owner_conninfo("postgres")) as c:
            return c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None
    except (psycopg.Error, DatabaseSetupError):
        return None


def connect(role: str, dbname: str | None = None, **kwargs) -> psycopg.Connection:
    """Connect as one of the app roles, turning setup problems into a clear message."""
    try:
        return psycopg.connect(role_conninfo(role, dbname), **kwargs)
    except psycopg.OperationalError as exc:
        if "password authentication failed" in str(exc) and _role_exists(role) is False:
            exc = psycopg.OperationalError(f'role "{role}" does not exist')
        raise DatabaseSetupError(explain_connect_error(role, exc)) from None


def connect_owner(dbname: str | None = None, **kwargs) -> psycopg.Connection:
    user = env("POSTGRES_SUPERUSER", "cob_owner")
    try:
        return psycopg.connect(owner_conninfo(dbname), **kwargs)
    except psycopg.OperationalError as exc:
        raise DatabaseSetupError(explain_connect_error(user, exc)) from None


def explain_connect_error(role: str, exc: Exception) -> str:
    msg = str(exc)
    s = get_settings()
    if f'role "{role}" does not exist' in msg:
        hint = "Run `python -m scripts.migrate` (it creates the app roles)." if role in BY_NAME else (
            "This is the owner role from POSTGRES_SUPERUSER in .env; it is created when the Postgres volume is "
            "first initialised. If you changed it later, recreate the volume or restore the old value."
        )
        return f"Database role {role!r} does not exist. {hint}"
    if "password authentication failed" in msg:
        var = BY_NAME[role].password_var if role in BY_NAME else "POSTGRES_SUPERUSER_PASSWORD"
        hint = "Run `python -m scripts.migrate` to apply the passwords in .env." if role in BY_NAME else (
            "The owner password is fixed when the Postgres volume is created; .env must match it."
        )
        return f"Password for database role {role!r} was rejected ({var} in .env). {hint}"
    if "Connection refused" in msg or "could not connect" in msg or "timeout" in msg.lower():
        return (
            f"Cannot reach Postgres at {s.db_host}:{s.db_port}. Is it running? Start it with `docker compose up -d` "
            "and check `docker compose ps`."
        )
    return f"Could not connect to Postgres as {role!r}: {msg.strip()}"


def missing_roles(conn: psycopg.Connection) -> list[str]:
    have = {r for (r,) in conn.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", ([r.name for r in ROLES],))}
    return [r.name for r in ROLES if r.name not in have]
