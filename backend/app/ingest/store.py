"""Write chunks to content.chunks and embed them in resumable batches.

Resumability: chunks are upserted by (doc_id, content_hash), so re-chunking an unchanged
report changes nothing; embedding only touches rows with embedding IS NULL and commits after
every batch, so an interrupted run continues where it stopped. A chunk whose exact text was
already embedded with the same model (in any document) reuses that vector.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg

from app.ingest.chunk import Chunk

log = logging.getLogger("cob.store")


def upsert_document(conn: psycopg.Connection, rec: dict, page_count: int, parse_version: int, collection: str = "cob-public") -> int:
    coll = conn.execute("SELECT collection_id FROM app.collections WHERE name = %s", (collection,)).fetchone()
    if not coll:
        raise ValueError(f"unknown collection {collection!r}")
    return conn.execute(
        "INSERT INTO content.documents (collection_id, title, fiscal_year, period_type, source_url, file_name, sha256, page_count, parse_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (sha256) DO UPDATE SET title = EXCLUDED.title, fiscal_year = EXCLUDED.fiscal_year, period_type = EXCLUDED.period_type, "
        "  source_url = EXCLUDED.source_url, file_name = EXCLUDED.file_name, page_count = EXCLUDED.page_count, parse_version = EXCLUDED.parse_version "
        "RETURNING doc_id",
        (coll[0], rec["title"], rec.get("fiscal_year"), rec.get("period"), rec.get("url"), rec["file"], rec["sha256"], page_count, parse_version),
    ).fetchone()[0]


def sync_chunks(conn: psycopg.Connection, doc_id: int, chunks: list[Chunk]) -> dict[str, int]:
    """Make the stored chunks of a document equal to `chunks`, keeping existing embeddings."""
    collection_id = conn.execute("SELECT collection_id FROM content.documents WHERE doc_id = %s", (doc_id,)).fetchone()[0]
    existing = {h for (h,) in conn.execute("SELECT content_hash FROM content.chunks WHERE doc_id = %s", (doc_id,))}
    wanted = {c.content_hash: c for c in chunks}
    stale = existing - wanted.keys()
    if stale:
        conn.execute("DELETE FROM content.chunks WHERE doc_id = %s AND content_hash = ANY(%s)", (doc_id, list(stale)))
    new = [c for h, c in wanted.items() if h not in existing]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO content.chunks (doc_id, collection_id, kind, page_start, page_end, section_title, county_id, table_ref, "
            "context_header, text, token_estimate, content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (doc_id, content_hash) DO NOTHING",
            [(doc_id, collection_id, c.kind, c.page_start, c.page_end, c.section_title, c.county_id, c.table_ref,
              c.context_header, c.text, c.token_estimate, c.content_hash) for c in new],
        )
    return {"kept": len(existing & wanted.keys()), "added": len(new), "removed": len(stale)}


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def embed_pending(
    conn: psycopg.Connection,
    embed: Callable[[list[str]], list[list[float]]],
    model: str,
    batch_size: int = 16,
    doc_ids: list[int] | None = None,
    limit: int | None = None,
    progress: Callable[[int, int, float], None] | None = None,
) -> int:
    """Embed every chunk with no vector (optionally only some documents). Returns count embedded."""
    where = "embedding IS NULL" + (" AND doc_id = ANY(%(docs)s)" if doc_ids else "")
    total = conn.execute(f"SELECT count(*) FROM content.chunks WHERE {where}", {"docs": doc_ids}).fetchone()[0]
    if limit:
        total = min(total, limit)

    # Reuse vectors for identical text already embedded with this model.
    reused = conn.execute(
        """UPDATE content.chunks c SET embedding = src.embedding, embed_model = src.embed_model
           FROM (SELECT DISTINCT ON (content_hash) content_hash, embedding, embed_model FROM content.chunks
                 WHERE embedding IS NOT NULL AND embed_model = %(model)s) src
           WHERE c.embedding IS NULL AND c.content_hash = src.content_hash
             AND (%(docs)s::int[] IS NULL OR c.doc_id = ANY(%(docs)s::int[]))""",
        {"model": model, "docs": doc_ids},
    ).rowcount
    conn.commit()
    done = reused
    t0 = time.time()
    while done < total:
        rows = conn.execute(
            f"SELECT chunk_id, context_header, text FROM content.chunks WHERE {where} ORDER BY chunk_id LIMIT %(n)s",
            {"docs": doc_ids, "n": min(batch_size, total - done)},
        ).fetchall()
        if not rows:
            break
        vectors = embed([f"{h}\n\n{t}" for _, h, t in rows])
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE content.chunks SET embedding = %s::vector, embed_model = %s WHERE chunk_id = %s",
                [(_vec(v), model, cid) for (cid, _, _), v in zip(rows, vectors)],
            )
        conn.commit()  # the resume point: everything up to here is saved
        done += len(rows)
        if progress:
            progress(done, total, time.time() - t0)
    return done
