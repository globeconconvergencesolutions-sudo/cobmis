"""Library: document list + PDF bytes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth.deps import require_user
from app.auth.sessions import SessionUser
from app.catalog import allowed_collections
from app.core.config import get_settings
from app.db.roles import connect

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("")
def list_documents(user: Annotated[SessionUser, Depends(require_user)]):
    with connect("cob_app") as conn:
        allowed = allowed_collections(conn, user.user_id)
        rows = conn.execute(
            """
            SELECT d.doc_id, d.title, d.fiscal_year, d.period_type, d.page_count,
                   d.file_name, d.source_url, d.ingested_at, d.needs_ocr_pages,
                   c.name, c.visibility
            FROM content.documents d
            JOIN app.collections c USING (collection_id)
            WHERE d.collection_id = ANY(%s)
            ORDER BY d.fiscal_year NULLS LAST, d.period_type NULLS LAST, d.title
            """,
            (allowed,),
        ).fetchall()
    return {
        "documents": [
            {
                "doc_id": r[0],
                "title": r[1],
                "fiscal_year": r[2],
                "period_type": r[3],
                "page_count": r[4],
                "file_name": r[5],
                "source_url": r[6],
                "ingested_at": r[7].isoformat() if r[7] else None,
                "needs_ocr_pages": r[8],
                "collection": r[9],
                "visibility": r[10],
            }
            for r in rows
        ]
    }


@router.get("/{doc_id}/file")
def document_file(doc_id: int, user: Annotated[SessionUser, Depends(require_user)]):
    s = get_settings()
    with connect("cob_app") as conn:
        allowed = allowed_collections(conn, user.user_id)
        row = conn.execute(
            """
            SELECT d.file_name, d.title
            FROM content.documents d
            WHERE d.doc_id = %s AND d.collection_id = ANY(%s)
            """,
            (doc_id, allowed),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(s.data_dir) / "raw" / row[0]
    if not path.is_file():
        # try nested layout
        matches = list((Path(s.data_dir) / "raw").rglob(row[0]))
        path = matches[0] if matches else path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="PDF file missing on disk")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=row[0],
        headers={"Content-Disposition": f'inline; filename="{row[0]}"'},
    )
