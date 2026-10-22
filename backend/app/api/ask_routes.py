"""Ask + SSE stream."""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.ask_service import (
    AskCancelled,
    AskResult,
    audit_ask,
    chunk_text,
    format_scope_label,
    result_to_public,
    run_ask,
)
from app.auth.deps import require_user
from app.auth.sessions import SessionUser as SU
from app.core.config import get_settings
from app.db.roles import connect
from app.ingest.counties import match_county
from app.llm.ollama_client import OllamaClient
from app.orchestrator import rewrite_standalone
from app.router import classify

router = APIRouter(prefix="/api/ask", tags=["ask"])


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: int | None = None
    county: str | None = None
    fy: str | None = None
    period: Literal["Q1", "H1", "9M", "FY"] | None = None
    route: Literal["catalog", "sql", "rag", "both", "other"] | None = None
    prior_question: str | None = None
    prior_answer: str | None = None


def _persist_turn(
    conn,
    *,
    user: SU,
    body: AskBody,
    result: AskResult,
) -> dict[str, Any]:
    conv_id = body.conversation_id
    if conv_id is None:
        title = (result.question or body.question)[:80]
        conv_id = conn.execute(
            """
            INSERT INTO app.conversations (user_id, title)
            VALUES (%s, %s) RETURNING conversation_id
            """,
            (user.user_id, title),
        ).fetchone()[0]
    else:
        own = conn.execute(
            "SELECT 1 FROM app.conversations WHERE conversation_id = %s AND user_id = %s",
            (conv_id, user.user_id),
        ).fetchone()
        if not own:
            raise HTTPException(status_code=404, detail="Conversation not found")

    conn.execute(
        """
        INSERT INTO app.messages (conversation_id, role, content)
        VALUES (%s, 'user', %s)
        """,
        (conv_id, body.question),
    )
    mid = conn.execute(
        """
        INSERT INTO app.messages (
            conversation_id, role, content, route, citations_json, sql_json, unsupported_numbers
        ) VALUES (%s, 'assistant', %s, %s, %s::jsonb, %s::jsonb, %s)
        RETURNING message_id
        """,
        (
            conv_id,
            result.text,
            result.route,
            json.dumps(result.citations),
            json.dumps({
                "sql": result.sql,
                "columns": result.columns,
                "rows": result.rows,
                "citations": result.sql_citations,
            }),
            result.unsupported_numbers or None,
        ),
    ).fetchone()[0]
    conn.execute(
        "UPDATE app.conversations SET updated_at = now() WHERE conversation_id = %s",
        (conv_id,),
    )
    audit_ask(conn, user_id=user.user_id, result=result, conversation_id=conv_id)
    return {"conversation_id": conv_id, "message_id": mid}


def _preview_scope(body: AskBody) -> str | None:
    """Best-effort scope label before full resolve (for immediate SSE)."""
    county_name = None
    if body.county:
        try:
            c = match_county(body.county)
            county_name = c.name if c else body.county.strip()
        except Exception:
            county_name = body.county.strip()
    label = format_scope_label(county_name, body.fy, body.period)
    return label or None


@router.post("")
def ask(
    body: AskBody,
    user: Annotated[SU, Depends(require_user)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    if not x_csrf_token or x_csrf_token != user.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    s = get_settings()
    client = OllamaClient(s)
    try:
        with connect("cob_app") as conn:
            result = run_ask(
                client,
                conn,
                body.question,
                user_id=user.user_id,
                county=body.county,
                fy=body.fy,
                period=body.period,
                force_route=body.route,
                prior_question=body.prior_question,
                prior_answer=body.prior_answer,
            )
            meta = _persist_turn(conn, user=user, body=body, result=result)
            conn.commit()
    finally:
        client.close()
    out = result_to_public(result)
    out.update(meta)
    return out


@router.post("/stream")
def ask_stream(
    body: AskBody,
    user: Annotated[SU, Depends(require_user)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    if not x_csrf_token or x_csrf_token != user.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")

    def events():
        qbox: queue.Queue[tuple[str, Any]] = queue.Queue()
        cancel = threading.Event()
        client_holder: dict[str, OllamaClient | None] = {"client": None}
        started = time.monotonic()

        def on_progress(step: str, label: str) -> None:
            qbox.put(("progress", {"step": step, "label": label}))

        def heartbeat() -> None:
            while not cancel.is_set():
                if cancel.wait(8.0):
                    break
                elapsed = int(time.monotonic() - started)
                on_progress(
                    "heartbeat",
                    f"Still working… {elapsed}s — local model can take a minute on this machine",
                )

        def worker() -> None:
            client = OllamaClient(get_settings())
            client_holder["client"] = client
            try:
                with connect("cob_app") as conn:
                    if cancel.is_set():
                        raise AskCancelled()

                    q = body.question.strip()
                    rewritten = False
                    if body.prior_question:
                        on_progress("rewrite", "Understanding your follow-up…")
                        try:
                            q = rewrite_standalone(
                                client,
                                q,
                                prior_question=body.prior_question,
                                prior_answer=body.prior_answer,
                            )
                            qbox.put(("rewritten", {"question": q}))
                            rewritten = True
                        except Exception as exc:
                            # Persist a clear failure so the chat isn't left blank.
                            fail = AskResult(
                                question=body.question.strip(),
                                route="rag",
                                text=(
                                    "Could not expand that follow-up — the local model timed out "
                                    "or is busy. Try a full question with county and period.\n\n"
                                    f"Detail: {exc}"
                                ),
                                error=str(exc),
                            )
                            try:
                                meta = _persist_turn(conn, user=user, body=body, result=fail)
                                conn.commit()
                                qbox.put(("result", (fail, meta)))
                            except Exception:
                                qbox.put(("error", {"detail": str(exc)}))
                            return

                    if cancel.is_set():
                        raise AskCancelled()

                    on_progress("classify", "Choosing the best way to answer…")
                    route = classify(q, force=body.route)  # type: ignore[arg-type]
                    qbox.put(("route", {"route": route}))

                    result = run_ask(
                        client,
                        conn,
                        q if rewritten else body.question,
                        user_id=user.user_id,
                        county=body.county,
                        fy=body.fy,
                        period=body.period,
                        force_route=body.route,
                        prior_question=None if rewritten else body.prior_question,
                        prior_answer=None if rewritten else body.prior_answer,
                        on_progress=on_progress,
                        skip_followup_rewrite=rewritten,
                        cancel=cancel,
                    )

                    if cancel.is_set():
                        raise AskCancelled()

                    # Always persist something the user can read (including clarify / errors).
                    if not (result.text or "").strip() and result.error:
                        result.text = (
                            f"Could not finish answering. {result.error}\n\n"
                            "Cancel any stuck ask, then retry with a concrete question."
                        )

                    meta = _persist_turn(conn, user=user, body=body, result=result)
                    conn.commit()
                    qbox.put(("result", (result, meta)))
            except AskCancelled:
                qbox.put(("error", {"detail": "cancelled"}))
            except Exception as exc:
                qbox.put(("error", {"detail": str(exc)}))
            finally:
                client.close()
                client_holder["client"] = None
                cancel.set()
                qbox.put(("end", None))

        # Immediate feedback so the UI never sits on a silent local placeholder.
        on_progress("accepted", "Request received — starting…")
        scope = _preview_scope(body)
        if scope:
            on_progress("scope", f"Using filters: {scope}")
            qbox.put(("scope", {"label": scope}))

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=heartbeat, daemon=True).start()

        try:
            while True:
                try:
                    kind, payload = qbox.get(timeout=1.0)
                except queue.Empty:
                    # Keep proxies / browsers from buffering the stream dead.
                    yield ": keepalive\n\n"
                    continue
                if kind == "end":
                    break
                if kind == "progress":
                    yield _sse("progress", payload)
                elif kind == "scope":
                    yield _sse("scope", payload)
                elif kind == "rewritten":
                    yield _sse("rewritten", payload)
                elif kind == "route":
                    yield _sse("route", payload)
                elif kind == "error":
                    yield _sse("error", payload)
                elif kind == "result":
                    result, meta = payload
                    if result.scope_label:
                        yield _sse("scope", {"label": result.scope_label})
                    if result.notices:
                        yield _sse("notice", {"notices": result.notices})
                    for piece in chunk_text(result.text or ""):
                        yield _sse("token", {"text": piece})
                    yield _sse("sources", {
                        "citations": result.citations,
                        "sql_citations": result.sql_citations,
                    })
                    yield _sse("sql", {
                        "sql": result.sql,
                        "columns": result.columns,
                        "rows": result.rows,
                    })
                    if result.unsupported_numbers:
                        yield _sse("warning", {"unsupported_numbers": result.unsupported_numbers})
                    yield _sse("done", {**result_to_public(result), **meta})
        finally:
            # Client disconnected or generator closed — kill in-flight Ollama work.
            cancel.set()
            c = client_holder.get("client")
            if c is not None:
                c.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
