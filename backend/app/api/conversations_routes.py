"""Conversation history + feedback."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import require_user
from app.auth.sessions import SessionUser
from app.db.roles import connect

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
def list_conversations(user: Annotated[SessionUser, Depends(require_user)]):
    with connect("cob_app") as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, title, created_at, updated_at
            FROM app.conversations
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT 100
            """,
            (user.user_id,),
        ).fetchall()
    return {
        "conversations": [
            {
                "conversation_id": r[0],
                "title": r[1],
                "created_at": r[2].isoformat(),
                "updated_at": r[3].isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/{conversation_id}")
def get_conversation(conversation_id: int, user: Annotated[SessionUser, Depends(require_user)]):
    with connect("cob_app") as conn:
        head = conn.execute(
            """
            SELECT conversation_id, title FROM app.conversations
            WHERE conversation_id = %s AND user_id = %s
            """,
            (conversation_id, user.user_id),
        ).fetchone()
        if not head:
            raise HTTPException(status_code=404, detail="Not found")
        msgs = conn.execute(
            """
            SELECT message_id, role, content, route, citations_json, sql_json,
                   unsupported_numbers, created_at
            FROM app.messages WHERE conversation_id = %s ORDER BY message_id
            """,
            (conversation_id,),
        ).fetchall()
    return {
        "conversation_id": head[0],
        "title": head[1],
        "messages": [
            {
                "message_id": m[0],
                "role": m[1],
                "content": m[2],
                "route": m[3],
                "citations": m[4] or [],
                "sql": m[5],
                "unsupported_numbers": m[6] or [],
                "created_at": m[7].isoformat(),
            }
            for m in msgs
        ],
    }


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    user: Annotated[SessionUser, Depends(require_user)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    if not x_csrf_token or x_csrf_token != user.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    with connect("cob_app") as conn:
        cur = conn.execute(
            "DELETE FROM app.conversations WHERE conversation_id = %s AND user_id = %s",
            (conversation_id, user.user_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        conn.commit()
    return {"ok": True}


class FeedbackBody(BaseModel):
    message_id: int
    rating: int = Field(..., description="1 or -1")
    note: str | None = None


@router.post("/feedback")
def feedback(
    body: FeedbackBody,
    user: Annotated[SessionUser, Depends(require_user)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    if body.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")
    if not x_csrf_token or x_csrf_token != user.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    with connect("cob_app") as conn:
        own = conn.execute(
            """
            SELECT 1 FROM app.messages m
            JOIN app.conversations c USING (conversation_id)
            WHERE m.message_id = %s AND c.user_id = %s
            """,
            (body.message_id, user.user_id),
        ).fetchone()
        if not own:
            raise HTTPException(status_code=404, detail="Message not found")
        conn.execute(
            """
            INSERT INTO app.feedback (message_id, user_id, rating, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (message_id, user_id) DO UPDATE
              SET rating = EXCLUDED.rating, note = EXCLUDED.note
            """,
            (body.message_id, user.user_id, body.rating, body.note),
        )
        conn.commit()
    return {"ok": True}
