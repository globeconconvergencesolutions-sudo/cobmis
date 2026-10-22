"""Hybrid retrieval: pgvector cosine top-k + Postgres full-text top-k, fused by reciprocal rank.

Access control is applied in the query itself (collection_id = ANY(allowed)), never by
filtering results afterwards.

When a county is set, ranking is strict to that county. County-less (national / chapter-2)
chunks are fetched separately as a capped, labelled fallback and are not fused into the
county scores. Observation-intent questions expand the FTS query with synonyms and boost
chunks whose section title matches those words — without touching stored embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import psycopg

from app.retrieval.intent import expand_query_for_fts, intent_score_bonus

RRF_K = 60
DEFAULT_NATIONAL_FALLBACK = 2


@dataclass
class Hit:
    chunk_id: int
    score: float
    kind: str
    doc_title: str
    fiscal_year: str | None
    period_type: str | None
    page_start: int
    page_end: int
    county_id: int | None
    table_ref: str | None
    section_title: str | None
    context_header: str
    text: str
    source_url: str | None
    is_national_fallback: bool = False


SEARCH_SQL = """
WITH scope AS (
    SELECT c.chunk_id, c.embedding, c.tsv
    FROM content.chunks c JOIN content.documents d USING (doc_id)
    WHERE c.collection_id = ANY(%(allowed)s)
      AND (
            %(county_mode)s = 'any'
         OR (%(county_mode)s = 'exact' AND c.county_id = %(county)s)
         OR (%(county_mode)s = 'null' AND c.county_id IS NULL)
      )
      AND (%(fy)s::text IS NULL OR d.fiscal_year = %(fy)s)
      AND (%(period)s::text IS NULL OR d.period_type = %(period)s)
),
vec AS (
    SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS r
    FROM scope WHERE embedding IS NOT NULL AND %(qvec)s::text IS NOT NULL
    ORDER BY embedding <=> %(qvec)s::vector LIMIT %(k)s
),
q AS (SELECT replace(plainto_tsquery('english', %(qtext)s)::text, ' & ', ' | ')::tsquery AS query),
fts AS (
    SELECT chunk_id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q.query) DESC) AS r
    FROM scope, q WHERE q.query::text <> '' AND tsv @@ q.query
    ORDER BY ts_rank_cd(tsv, q.query) DESC LIMIT %(k)s
),
fused AS (
    SELECT chunk_id, sum(1.0 / (%(rrf)s + r)) AS score
    FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) u GROUP BY chunk_id
)
SELECT f.chunk_id, f.score, c.kind, d.title, d.fiscal_year, d.period_type, c.page_start, c.page_end,
       c.county_id, c.table_ref, c.section_title, c.context_header, c.text, d.source_url
FROM fused f JOIN content.chunks c USING (chunk_id) JOIN content.documents d USING (doc_id)
ORDER BY f.score DESC LIMIT %(n)s
"""


def _rows_to_hits(rows: list, *, national_fallback: bool = False) -> list[Hit]:
    hits = []
    for r in rows:
        hits.append(Hit(
            chunk_id=r[0], score=float(r[1]), kind=r[2], doc_title=r[3], fiscal_year=r[4],
            period_type=r[5], page_start=r[6], page_end=r[7], county_id=r[8], table_ref=r[9],
            section_title=r[10], context_header=r[11], text=r[12], source_url=r[13],
            is_national_fallback=national_fallback,
        ))
    return hits


def _apply_intent_boost(question: str, hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    boosted = [
        replace(h, score=h.score + intent_score_bonus(question, h.section_title, h.context_header))
        for h in hits
    ]
    boosted.sort(key=lambda h: h.score, reverse=True)
    return boosted


def _run_search(
    conn: psycopg.Connection,
    *,
    qtext: str,
    query_vector: list[float] | None,
    allowed_collections: list[int],
    county_mode: str,
    county_id: int | None,
    fiscal_year: str | None,
    period: str | None,
    k: int,
    n: int,
) -> list[Hit]:
    if n <= 0 or not allowed_collections:
        return []
    qvec = "[" + ",".join(f"{x:.7g}" for x in query_vector) + "]" if query_vector else None
    rows = conn.execute(SEARCH_SQL, {
        "allowed": allowed_collections,
        "county_mode": county_mode,
        "county": county_id,
        "fy": fiscal_year,
        "period": period,
        "qvec": qvec,
        "qtext": qtext,
        "k": k,
        "n": n,
        "rrf": RRF_K,
    }).fetchall()
    return _rows_to_hits(rows, national_fallback=(county_mode == "null"))


def hits_for_answer(hits: list[Hit]) -> list[Hit]:
    """Chunks to send to the model: county hits when present, else labelled national fallback."""
    primary = [h for h in hits if not h.is_national_fallback]
    return primary if primary else [h for h in hits if h.is_national_fallback]


def search(
    conn: psycopg.Connection,
    question: str,
    query_vector: list[float] | None,
    allowed_collections: list[int],
    county_id: int | None = None,
    fiscal_year: str | None = None,
    period: str | None = None,
    k: int = 30,
    n: int = 8,
    include_national: bool = True,
    national_fallback: int | None = None,
) -> list[Hit]:
    """Hybrid search. When county_id is set, rank that county only; optionally append up to
    ``national_fallback`` county-less chunks labelled as fallback (default 2 when
    ``include_national`` is True, else 0)."""
    if not allowed_collections:
        return []

    if national_fallback is None:
        national_fallback = DEFAULT_NATIONAL_FALLBACK if (county_id is not None and include_national) else 0

    fts_text = expand_query_for_fts(question)
    # Fetch a wider fused window before intent re-rank so a boosted section can enter top-n.
    fetch_n = max(n * 3, n, min(k, 40)) if county_id is not None else n

    if county_id is None:
        hits = _run_search(
            conn, qtext=fts_text, query_vector=query_vector, allowed_collections=allowed_collections,
            county_mode="any", county_id=None, fiscal_year=fiscal_year, period=period, k=k, n=fetch_n,
        )
        return _apply_intent_boost(question, hits)[:n]

    county_hits = _run_search(
        conn, qtext=fts_text, query_vector=query_vector, allowed_collections=allowed_collections,
        county_mode="exact", county_id=county_id, fiscal_year=fiscal_year, period=period, k=k, n=fetch_n,
    )
    county_hits = _apply_intent_boost(question, county_hits)[:n]

    fallback: list[Hit] = []
    if national_fallback > 0:
        fallback = _run_search(
            conn, qtext=fts_text, query_vector=query_vector, allowed_collections=allowed_collections,
            county_mode="null", county_id=None, fiscal_year=fiscal_year, period=period, k=k,
            n=national_fallback,
        )
        fallback = _apply_intent_boost(question, fallback)[:national_fallback]
        # Relabel after boost (replace keeps the flag).
        fallback = [replace(h, is_national_fallback=True) for h in fallback]

    return county_hits + fallback
