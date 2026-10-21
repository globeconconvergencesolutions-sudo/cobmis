"""Hybrid retrieval: pgvector cosine top-k + Postgres full-text top-k, fused by reciprocal rank.

Access control is applied in the query itself (collection_id = ANY(allowed)), never by
filtering results afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

RRF_K = 60


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
    context_header: str
    text: str
    source_url: str | None


SEARCH_SQL = """
WITH scope AS (
    SELECT c.chunk_id, c.embedding, c.tsv
    FROM content.chunks c JOIN content.documents d USING (doc_id)
    WHERE c.collection_id = ANY(%(allowed)s)
      AND (%(county)s::smallint IS NULL OR c.county_id = %(county)s OR c.county_id IS NULL AND %(include_national)s)
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
       c.county_id, c.table_ref, c.context_header, c.text, d.source_url
FROM fused f JOIN content.chunks c USING (chunk_id) JOIN content.documents d USING (doc_id)
ORDER BY f.score DESC LIMIT %(n)s
"""


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
) -> list[Hit]:
    if not allowed_collections:
        return []
    qvec = "[" + ",".join(f"{x:.7g}" for x in query_vector) + "]" if query_vector else None
    rows = conn.execute(SEARCH_SQL, {
        "allowed": allowed_collections, "county": county_id, "include_national": include_national,
        "fy": fiscal_year, "period": period, "qvec": qvec, "qtext": question, "k": k, "n": n, "rrf": RRF_K,
    }).fetchall()
    return [Hit(*r) for r in rows]
