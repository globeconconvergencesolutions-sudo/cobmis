from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import Engine, text

from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.llm.ollama_client import OllamaClient, OllamaError

router = APIRouter()


def get_ollama(settings: Settings = Depends(get_settings)) -> OllamaClient:
    return OllamaClient(settings)


def _db_status(engine: Engine) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
            exts = dict(
                conn.execute(
                    text("SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm')")
                ).all()
            )
            size = conn.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar()
        ok = "vector" in exts and "pg_trgm" in exts
        return {"ok": ok, "server_version": version, "extensions": exts, "size": size}
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        return {"ok": False, "error": type(exc).__name__ + ": " + str(exc).splitlines()[0]}


def _ollama_status(client: OllamaClient, settings: Settings) -> dict[str, Any]:
    try:
        version = client.version()
        available = client.list_models()
    except OllamaError as exc:
        return {"ok": False, "error": str(exc)}

    def present(tag: str) -> bool:
        # Ollama reports "nomic-embed-text:latest" for a pull of "nomic-embed-text".
        return tag in available or f"{tag}:latest" in available

    required = {
        "chat_model": settings.chat_model,
        "sql_model": settings.sql_model,
        "embed_model": settings.embed_model,
    }
    missing = sorted({tag for tag in required.values() if not present(tag)})
    return {"ok": not missing, "version": version, "required": required, "missing": missing}


@router.get("/health")
def health(
    response: Response,
    settings: Settings = Depends(get_settings),
    engine: Engine = Depends(get_engine),
    ollama: OllamaClient = Depends(get_ollama),
) -> dict[str, Any]:
    try:
        checks = {"database": _db_status(engine), "ollama": _ollama_status(ollama, settings)}
    finally:
        ollama.close()
    ok = all(c["ok"] for c in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", "profile": settings.profile, "llm_backend": settings.llm_backend, **checks}
