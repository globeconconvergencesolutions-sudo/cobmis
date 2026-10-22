"""Admin: health, jobs, users, ACL, audit."""

from __future__ import annotations

import csv
import io
import subprocess
import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import require_admin
from app.auth.passwords import hash_password
from app.auth.sessions import SessionUser
from app.core.config import REPO_ROOT, get_settings
from app.db.roles import connect
from app.llm.ollama_client import OllamaClient

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _csrf(user: SessionUser, token: str | None) -> None:
    if not token or token != user.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


@router.get("/health")
def admin_health(user: Annotated[SessionUser, Depends(require_admin)]):
    s = get_settings()
    client = OllamaClient(s)
    try:
        models = client.list_models()
        ollama_ok = True
        ollama_err = None
    except Exception as exc:
        models = []
        ollama_ok = False
        ollama_err = str(exc)
    finally:
        client.close()

    with connect("cob_app") as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM content.documents),
              (SELECT count(*) FROM content.chunks),
              (SELECT count(*) FROM public_data.fact_county_budget),
              (SELECT pg_size_pretty(pg_database_size(current_database())))
            """
        ).fetchone()
    return {
        "ollama_ok": ollama_ok,
        "ollama_error": ollama_err,
        "models": models,
        "required_models": [s.chat_model, s.embed_model, s.sql_model],
        "documents": counts[0],
        "chunks": counts[1],
        "fact_rows": counts[2],
        "db_size": counts[3],
        "profile": s.profile,
    }


class JobBody(BaseModel):
    kind: Literal["scrape", "ingest"]


@router.get("/jobs")
def list_jobs(user: Annotated[SessionUser, Depends(require_admin)]):
    with connect("cob_app") as conn:
        rows = conn.execute(
            """
            SELECT job_id, kind, status, created_by, created_at, started_at, finished_at, detail, log_tail
            FROM app.jobs ORDER BY job_id DESC LIMIT 50
            """
        ).fetchall()
    return {
        "jobs": [
            {
                "job_id": r[0],
                "kind": r[1],
                "status": r[2],
                "created_by": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "started_at": r[5].isoformat() if r[5] else None,
                "finished_at": r[6].isoformat() if r[6] else None,
                "detail": r[7],
                "log_tail": r[8],
            }
            for r in rows
        ]
    }


@router.post("/jobs")
def start_job(
    body: JobBody,
    user: Annotated[SessionUser, Depends(require_admin)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    _csrf(user, x_csrf_token)
    with connect("cob_app") as conn:
        job_id = conn.execute(
            """
            INSERT INTO app.jobs (kind, status, created_by)
            VALUES (%s, 'queued', %s) RETURNING job_id
            """,
            (body.kind, user.user_id),
        ).fetchone()[0]
        conn.commit()

    threading.Thread(target=_run_job, args=(job_id, body.kind), daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


def _run_job(job_id: int, kind: str) -> None:
    with connect("cob_app") as conn:
        conn.execute(
            "UPDATE app.jobs SET status = 'running', started_at = now() WHERE job_id = %s",
            (job_id,),
        )
        conn.commit()

    cmd = {
        "scrape": ["python", "-m", "scripts.scrape_cob", "--limit", "3"],
        "ingest": ["python", "-m", "scripts.ingest"],
    }[kind]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT / "backend"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
        )
        ok = proc.returncode == 0
        tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
        status = "succeeded" if ok else "failed"
        detail = f"exit {proc.returncode}"
    except Exception as exc:
        status = "failed"
        detail = str(exc)
        tail = detail

    with connect("cob_app") as conn:
        conn.execute(
            """
            UPDATE app.jobs
            SET status = %s, finished_at = now(), detail = %s, log_tail = %s
            WHERE job_id = %s
            """,
            (status, detail, tail, job_id),
        )
        conn.commit()


class CreateUserBody(BaseModel):
    email: str
    display_name: str
    password: str = Field(min_length=8)
    role: Literal["admin", "analyst", "viewer"] = "viewer"


@router.get("/users")
def list_users(user: Annotated[SessionUser, Depends(require_admin)]):
    with connect("cob_app") as conn:
        rows = conn.execute(
            """
            SELECT user_id, email, display_name, role, is_active, created_at,
                   (password_hash IS NOT NULL) AS has_password
            FROM app.users ORDER BY user_id
            """
        ).fetchall()
    return {
        "users": [
            {
                "user_id": r[0],
                "email": r[1],
                "display_name": r[2],
                "role": r[3],
                "is_active": r[4],
                "created_at": r[5].isoformat(),
                "has_password": r[6],
            }
            for r in rows
        ]
    }


@router.post("/users")
def create_user(
    body: CreateUserBody,
    user: Annotated[SessionUser, Depends(require_admin)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    _csrf(user, x_csrf_token)
    with connect("cob_app") as conn:
        try:
            uid = conn.execute(
                """
                INSERT INTO app.users (email, display_name, role, password_hash)
                VALUES (%s, %s, %s, %s) RETURNING user_id
                """,
                (body.email.strip().lower(), body.display_name.strip(), body.role, hash_password(body.password)),
            ).fetchone()[0]
            conn.commit()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user_id": uid}


class AccessBody(BaseModel):
    user_id: int
    collection_id: int
    can_read: bool = True
    can_write: bool = False


@router.put("/access")
def set_access(
    body: AccessBody,
    user: Annotated[SessionUser, Depends(require_admin)],
    x_csrf_token: Annotated[str | None, Header()] = None,
):
    _csrf(user, x_csrf_token)
    with connect("cob_app") as conn:
        conn.execute(
            """
            INSERT INTO app.collection_access (user_id, collection_id, can_read, can_write)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, collection_id) DO UPDATE
              SET can_read = EXCLUDED.can_read, can_write = EXCLUDED.can_write
            """,
            (body.user_id, body.collection_id, body.can_read, body.can_write),
        )
        conn.commit()
    return {"ok": True}


@router.get("/collections")
def list_collections(user: Annotated[SessionUser, Depends(require_admin)]):
    with connect("cob_app") as conn:
        rows = conn.execute(
            "SELECT collection_id, name, visibility FROM app.collections ORDER BY collection_id"
        ).fetchall()
    return {
        "collections": [
            {"collection_id": r[0], "name": r[1], "visibility": r[2]} for r in rows
        ]
    }


@router.get("/audit")
def audit(
    user: Annotated[SessionUser, Depends(require_admin)],
    limit: int = 200,
    csv_export: bool = False,
):
    with connect("cob_app") as conn:
        rows = conn.execute(
            """
            SELECT e.event_id, e.user_id, u.email, e.question, e.route, e.sql_text, e.created_at
            FROM app.audit_events e
            LEFT JOIN app.users u ON u.user_id = e.user_id
            ORDER BY e.event_id DESC
            LIMIT %s
            """,
            (min(limit, 2000),),
        ).fetchall()
    if csv_export:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["event_id", "user_id", "email", "question", "route", "sql", "created_at"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], r[5] or "", r[6].isoformat()])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit.csv"},
        )
    return {
        "events": [
            {
                "event_id": r[0],
                "user_id": r[1],
                "email": r[2],
                "question": r[3],
                "route": r[4],
                "sql": r[5],
                "created_at": r[6].isoformat(),
            }
            for r in rows
        ]
    }
