"""Catalog JSON for About page and starters."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.deps import require_user
from app.auth.sessions import SessionUser
from app.catalog import allowed_collections, build_catalog
from app.db.roles import connect

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("")
def catalog(user: Annotated[SessionUser, Depends(require_user)]):
    with connect("cob_app") as conn:
        allowed = allowed_collections(conn, user.user_id)
        payload = build_catalog(conn, allowed)
    d = asdict(payload)
    for doc in d.get("documents", []):
        if isinstance(doc.get("ingested_at"), datetime):
            doc["ingested_at"] = doc["ingested_at"].isoformat()
    return d
