"""Cookie session store in app.sessions."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

SESSION_DAYS = 14
COOKIE_NAME = "cob_session"


@dataclass
class SessionUser:
    user_id: int
    email: str
    display_name: str
    role: str
    csrf_token: str
    session_id: str


def create_session(conn: psycopg.Connection, user_id: int) -> tuple[str, str, datetime]:
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn.execute(
        """
        INSERT INTO app.sessions (session_id, user_id, expires_at, csrf_token)
        VALUES (%s, %s, %s, %s)
        """,
        (sid, user_id, expires, csrf),
    )
    return sid, csrf, expires


def destroy_session(conn: psycopg.Connection, session_id: str | None) -> None:
    if not session_id:
        return
    conn.execute("DELETE FROM app.sessions WHERE session_id = %s", (session_id,))


def get_session_user(conn: psycopg.Connection, session_id: str | None) -> SessionUser | None:
    if not session_id:
        return None
    row = conn.execute(
        """
        SELECT s.session_id, s.csrf_token, u.user_id, u.email, u.display_name, u.role
        FROM app.sessions s
        JOIN app.users u ON u.user_id = s.user_id
        WHERE s.session_id = %s
          AND s.expires_at > now()
          AND u.is_active
        """,
        (session_id,),
    ).fetchone()
    if not row:
        return None
    return SessionUser(
        session_id=row[0],
        csrf_token=row[1],
        user_id=row[2],
        email=row[3],
        display_name=row[4],
        role=row[5],
    )
