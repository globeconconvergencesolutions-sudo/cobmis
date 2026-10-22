"""FastAPI dependencies for session auth."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request

from app.auth.sessions import COOKIE_NAME, SessionUser, get_session_user
from app.db.roles import connect

CurrentUser = SessionUser


def _conn():
    return connect("cob_app")


def require_user(
    cob_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> SessionUser:
    with _conn() as conn:
        user = get_session_user(conn, cob_session)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: Annotated[SessionUser, Depends(require_user)]) -> SessionUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def optional_user(
    cob_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> SessionUser | None:
    with _conn() as conn:
        return get_session_user(conn, cob_session)


def check_csrf(
    request: Request,
    user: SessionUser,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    token = x_csrf_token or request.headers.get("X-CSRF-Token")
    if not token or token != user.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
