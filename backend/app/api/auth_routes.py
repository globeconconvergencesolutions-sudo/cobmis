"""Login / logout / me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth.deps import require_user
from app.auth.passwords import verify_password
from app.auth.sessions import COOKIE_NAME, SessionUser, create_session, destroy_session
from app.db.roles import connect

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str = Field(min_length=1)


@router.post("/login")
def login(body: LoginBody, response: Response):
    with connect("cob_app") as conn:
        row = conn.execute(
            """
            SELECT user_id, email, display_name, role, password_hash, is_active
            FROM app.users WHERE lower(email) = lower(%s)
            """,
            (body.email.strip(),),
        ).fetchone()
        if not row or not row[5] or not verify_password(row[4], body.password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        sid, csrf, expires = create_session(conn, row[0])
        conn.commit()
    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        httponly=True,
        samesite="lax",
        secure=False,  # loopback HTTP PoC
        max_age=14 * 24 * 3600,
        path="/",
    )
    return {
        "user": {
            "user_id": row[0],
            "email": row[1],
            "display_name": row[2],
            "role": row[3],
        },
        "csrf_token": csrf,
        "expires_at": expires.isoformat(),
    }


@router.post("/logout")
def logout(
    response: Response,
    cob_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
):
    with connect("cob_app") as conn:
        destroy_session(conn, cob_session)
        conn.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: Annotated[SessionUser, Depends(require_user)]):
    return {
        "user": {
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
        },
        "csrf_token": user.csrf_token,
    }
