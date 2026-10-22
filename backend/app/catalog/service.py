"""SQL-backed catalog answers. Every number comes from a query; text is templated."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import psycopg

from app.catalog.limitations import STATIC_LIMITATIONS
from app.ingest.cob_listing import parse_fiscal_year, parse_period
from app.ingest.counties import BY_CODE, county_mentions, match_county

CatalogKind = Literal["overview", "have", "about", "examples", "freshness"]

EXAMPLE_QUESTIONS = [
    "What challenges did Kericho County face in the first nine months of FY 2025/26?",
    "Pending bills for Nairobi City (full year).",
    "Own source revenue target for Turkana in the first nine months.",
    "What data do you have?",
    "Do you have FY 2022/23?",
]

_FRONT_MATTER = re.compile(
    r"(executive\s+summary|introduction|\bforeword\b|\b1\s+introduction\b)",
    re.I,
)


@dataclass
class DocRow:
    doc_id: int
    title: str
    fiscal_year: str | None
    period_type: str | None
    page_count: int
    collection: str
    collection_visibility: str
    ingested_at: datetime | None
    source_url: str | None
    needs_ocr_pages: int


@dataclass
class CatalogPayload:
    kind: CatalogKind
    documents: list[DocRow] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    metrics: list[dict[str, str]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    about_body: str | None = None
    about_approved: bool = False
    front_matter: list[dict[str, Any]] = field(default_factory=list)
    have: dict[str, Any] | None = None
    freshness: dict[str, Any] | None = None


@dataclass
class CatalogAnswer:
    kind: CatalogKind
    text: str
    payload: CatalogPayload

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "payload": _payload_dict(self.payload)}


def _payload_dict(p: CatalogPayload) -> dict[str, Any]:
    d = asdict(p)
    for doc in d.get("documents", []):
        if isinstance(doc.get("ingested_at"), datetime):
            doc["ingested_at"] = doc["ingested_at"].isoformat()
    return d


def build_catalog(conn: psycopg.Connection, allowed: list[int]) -> CatalogPayload:
    if not allowed:
        return CatalogPayload(kind="overview", limitations=list(STATIC_LIMITATIONS), examples=list(EXAMPLE_QUESTIONS))

    docs = [
        DocRow(*r)
        for r in conn.execute(
            """
            SELECT d.doc_id, d.title, d.fiscal_year, d.period_type, d.page_count,
                   c.name, c.visibility, d.ingested_at, d.source_url, d.needs_ocr_pages
            FROM content.documents d
            JOIN app.collections c USING (collection_id)
            WHERE d.collection_id = ANY(%(allowed)s)
            ORDER BY d.fiscal_year NULLS LAST, d.period_type NULLS LAST, d.title
            """,
            {"allowed": allowed},
        ).fetchall()
    ]

    coverage = [
        {"fiscal_year": r[0], "period_type": r[1], "documents": r[2], "pages": r[3]}
        for r in conn.execute(
            """
            SELECT fiscal_year, period_type, count(*)::int, coalesce(sum(page_count), 0)::int
            FROM content.documents
            WHERE collection_id = ANY(%(allowed)s)
            GROUP BY fiscal_year, period_type
            ORDER BY fiscal_year NULLS LAST, period_type NULLS LAST
            """,
            {"allowed": allowed},
        ).fetchall()
    ]

    counts_row = conn.execute(
        """
        SELECT
          (SELECT count(*)::int FROM content.documents WHERE collection_id = ANY(%(allowed)s)),
          (SELECT coalesce(sum(page_count), 0)::int FROM content.documents WHERE collection_id = ANY(%(allowed)s)),
          (SELECT coalesce(sum(needs_ocr_pages), 0)::int FROM content.documents WHERE collection_id = ANY(%(allowed)s)),
          (SELECT count(*)::int FROM content.chunks WHERE collection_id = ANY(%(allowed)s)),
          (SELECT count(*)::int FROM content.chunks WHERE collection_id = ANY(%(allowed)s) AND kind = 'table'),
          (SELECT count(DISTINCT county_id)::int FROM content.chunks
             WHERE collection_id = ANY(%(allowed)s) AND county_id IS NOT NULL)
        """,
        {"allowed": allowed},
    ).fetchone()

    # SQL metrics are public_data (chapter-2). Only include if the user can see at least one
    # cob-public (or any) document that feeds those facts — still safe: facts have no titles
    # of restricted uploads. Counts of facts are global public_data; quarantine/validation
    # tied to source docs we can see via content join when possible.
    metric_count = conn.execute("SELECT count(*)::int FROM public_data.dim_metric").fetchone()[0]
    fact_count = conn.execute("SELECT count(*)::int FROM public_data.fact_county_budget").fetchone()[0]
    county_dim = conn.execute("SELECT count(*)::int FROM public_data.dim_county").fetchone()[0]

    quarantine = conn.execute("SELECT count(*)::int FROM public_data.quarantine_rows").fetchone()[0]
    cross_period_fails = conn.execute(
        "SELECT count(*)::int FROM public_data.validation_results "
        "WHERE check_name = 'cumulative_non_decreasing' AND status = 'fail'"
    ).fetchone()[0]

    counts = {
        "documents": counts_row[0],
        "pages": counts_row[1],
        "needs_ocr_pages": counts_row[2],
        "chunks": counts_row[3],
        "table_chunks": counts_row[4],
        "counties_in_chunks": counts_row[5],
        "counties_in_dim": county_dim,
        "sql_metrics": metric_count,
        "sql_facts": fact_count,
        "quarantine_rows": quarantine,
        "cross_period_decreases": cross_period_fails,
    }

    metrics = [
        {"metric": r[0], "description": r[1], "unit": r[2]}
        for r in conn.execute(
            "SELECT metric, description, unit FROM public_data.dim_metric ORDER BY metric"
        ).fetchall()
    ]

    about = conn.execute(
        "SELECT body, approved_at IS NOT NULL FROM app.dataset_about ORDER BY about_id LIMIT 1"
    ).fetchone()

    limitations = list(STATIC_LIMITATIONS)
    if counts["needs_ocr_pages"]:
        limitations.append(f"Image-only pages flagged in loaded reports: {counts['needs_ocr_pages']}.")
    if counts["quarantine_rows"]:
        limitations.append(
            f"Quarantined table rows (failed validation, not in published facts): {counts['quarantine_rows']}."
        )
    if counts["cross_period_decreases"]:
        limitations.append(
            f"Cross-period cumulative decreases flagged (values kept as printed): {counts['cross_period_decreases']}."
        )

    fresh = None
    if docs:
        latest = max(docs, key=lambda d: (d.ingested_at or datetime.min, d.doc_id))
        fresh = {
            "latest_ingested_at": latest.ingested_at.isoformat() if latest.ingested_at else None,
            "latest_title": latest.title,
            "latest_fiscal_year": latest.fiscal_year,
            "latest_period": latest.period_type,
        }

    return CatalogPayload(
        kind="overview",
        documents=docs,
        coverage=coverage,
        counts=counts,
        metrics=metrics,
        limitations=limitations,
        examples=list(EXAMPLE_QUESTIONS),
        about_body=about[0] if about else None,
        about_approved=bool(about[1]) if about else False,
        freshness=fresh,
    )


def have_coverage(
    conn: psycopg.Connection,
    allowed: list[int],
    *,
    fiscal_year: str | None = None,
    period: str | None = None,
    county_id: int | None = None,
) -> dict[str, Any]:
    """Deterministic presence check. Missing dimensions are not guessed."""
    if not allowed:
        return {
            "present": False,
            "fiscal_year": fiscal_year,
            "period": period,
            "county_id": county_id,
            "county_name": BY_CODE[county_id].name if county_id else None,
            "document_count": 0,
            "chunk_count": 0,
            "message": "No. You have access to no collections.",
        }

    doc_sql = ["collection_id = ANY(%(allowed)s)"]
    params: dict[str, Any] = {"allowed": allowed}
    if fiscal_year:
        doc_sql.append("fiscal_year = %(fy)s")
        params["fy"] = fiscal_year
    if period:
        doc_sql.append("period_type = %(period)s")
        params["period"] = period

    doc_count = conn.execute(
        f"SELECT count(*)::int FROM content.documents WHERE {' AND '.join(doc_sql)}", params
    ).fetchone()[0]

    chunk_count = 0
    if county_id is not None:
        chunk_params = dict(params)
        chunk_params["county"] = county_id
        chunk_sql = [
            "c.collection_id = ANY(%(allowed)s)",
            "c.county_id = %(county)s",
        ]
        if fiscal_year:
            chunk_sql.append("d.fiscal_year = %(fy)s")
        if period:
            chunk_sql.append("d.period_type = %(period)s")
        chunk_count = conn.execute(
            f"""SELECT count(*)::int FROM content.chunks c
                JOIN content.documents d USING (doc_id)
                WHERE {' AND '.join(chunk_sql)}""",
            chunk_params,
        ).fetchone()[0]
        present = chunk_count > 0
    else:
        present = doc_count > 0

    bits = []
    if fiscal_year:
        bits.append(f"FY {fiscal_year}")
    if period:
        bits.append(period)
    if county_id:
        bits.append(f"{BY_CODE[county_id].name} County")
    label = ", ".join(bits) if bits else "that slice"

    if present:
        msg = f"Yes. {label} is in the loaded reports you can access"
        if doc_count:
            msg += f" ({doc_count} document(s)"
            if county_id is not None:
                msg += f", {chunk_count} county chunk(s)"
            msg += ")."
        else:
            msg += "."
    else:
        msg = f"No. {label} is not in the loaded reports you can access."

    return {
        "present": present,
        "fiscal_year": fiscal_year,
        "period": period,
        "county_id": county_id,
        "county_name": BY_CODE[county_id].name if county_id else None,
        "document_count": doc_count,
        "chunk_count": chunk_count,
        "message": msg,
    }


def about_report(
    conn: psycopg.Connection,
    allowed: list[int],
    *,
    fiscal_year: str | None = None,
    period: str | None = None,
) -> CatalogPayload:
    base = build_catalog(conn, allowed)
    base.kind = "about"

    if not allowed:
        return base

    filters = ["d.collection_id = ANY(%(allowed)s)"]
    params: dict[str, Any] = {"allowed": allowed}
    if fiscal_year:
        filters.append("d.fiscal_year = %(fy)s")
        params["fy"] = fiscal_year
    if period:
        filters.append("d.period_type = %(period)s")
        params["period"] = period

    # Prefer front-matter narrative from the scoped reports.
    rows = conn.execute(
        f"""
        SELECT d.title, d.fiscal_year, d.period_type, c.page_start, c.page_end,
               c.section_title, c.context_header, left(c.text, 600)
        FROM content.chunks c
        JOIN content.documents d USING (doc_id)
        WHERE {' AND '.join(filters)}
          AND c.kind = 'narrative'
          AND (
                c.section_title ~* 'executive\\s+summary|^1\\.?\\s*introduction|\\bintroduction\\b|\\bforeword\\b'
             OR c.context_header ~* 'executive\\s+summary|,\\s*INTRODUCTION|,\\s*FOREWORD|,\\s*1 INTRODUCTION'
          )
        ORDER BY d.fiscal_year NULLS LAST, d.period_type NULLS LAST, c.page_start
        LIMIT 6
        """,
        params,
    ).fetchall()

    base.front_matter = [
        {
            "document": r[0],
            "fiscal_year": r[1],
            "period": r[2],
            "pages": [r[3], r[4]],
            "section_title": r[5],
            "context_header": r[6],
            "excerpt": r[7],
        }
        for r in rows
        if _FRONT_MATTER.search(" ".join(x for x in (r[5], r[6]) if x) or "")
    ]
    return base


def render_catalog_answer(payload: CatalogPayload) -> str:
    if payload.kind == "have" and payload.have:
        return payload.have["message"]

    lines: list[str] = []

    if payload.kind in ("overview", "about", "freshness", "examples"):
        if payload.about_body:
            flag = "" if payload.about_approved else " (draft — pending owner approval)"
            lines.append(payload.about_body.strip() + flag)
            lines.append("")

    if payload.kind == "freshness":
        f = payload.freshness or {}
        if not f:
            lines.append("No documents are loaded in the collections you can access.")
        else:
            lines.append(
                f"Most recently ingested report: {f.get('latest_title')} "
                f"({f.get('latest_fiscal_year')} {f.get('latest_period')}), "
                f"ingested_at={f.get('latest_ingested_at')}."
            )
            if payload.coverage:
                cov = ", ".join(
                    f"{c['fiscal_year']} {c['period_type']} ({c['documents']} doc, {c['pages']} pages)"
                    for c in payload.coverage
                )
                lines.append(f"Coverage in your collections: {cov}.")
        return "\n".join(lines).strip()

    if payload.kind == "examples":
        lines.append("Example questions you can ask:")
        for q in payload.examples:
            lines.append(f"- {q}")
        return "\n".join(lines).strip()

    if payload.kind == "about":
        if payload.front_matter:
            lines.append("From the report front matter (quoted, with pages):")
            for i, fm in enumerate(payload.front_matter, 1):
                pages = fm["pages"]
                p = f"p.{pages[0]}" if pages[0] == pages[1] else f"pp.{pages[0]}-{pages[1]}"
                lines.append(
                    f"[{i}] {fm['document']} ({fm['fiscal_year']} {fm['period']}), PDF {p}"
                    f" — {fm.get('section_title') or 'front matter'}"
                )
                lines.append(f"    {fm['excerpt']}")
        elif not payload.about_body:
            lines.append("No approved about-text and no front-matter sections found for that scope.")
        return "\n".join(lines).strip()

    # overview
    c = payload.counts
    lines.append(
        f"Loaded documents you can access: {c.get('documents', 0)} "
        f"({c.get('pages', 0)} pages, {c.get('chunks', 0)} chunks, "
        f"{c.get('table_chunks', 0)} table chunks)."
    )
    lines.append(
        f"Counties tagged in chunks: {c.get('counties_in_chunks', 0)}; "
        f"counties in directory: {c.get('counties_in_dim', 0)}; "
        f"table metrics: {c.get('sql_metrics', 0)}; table fact rows: {c.get('sql_facts', 0)}."
    )
    if payload.coverage:
        lines.append("Coverage by fiscal year and period:")
        for row in payload.coverage:
            lines.append(
                f"- {row['fiscal_year']} {row['period_type']}: "
                f"{row['documents']} document(s), {row['pages']} pages"
            )
    if payload.documents:
        lines.append("Documents:")
        for d in payload.documents:
            when = d.ingested_at.isoformat() if d.ingested_at else "?"
            url = d.source_url or "(no source URL)"
            lines.append(
                f"- {d.title} | {d.fiscal_year} {d.period_type} | {d.page_count} pages | "
                f"collection={d.collection} ({d.collection_visibility}) | ingested {when} | {url}"
            )
    if payload.metrics:
        lines.append("Metric dictionary (plain English):")
        for m in payload.metrics:
            lines.append(f"- {m['metric']}: {m['description']} [{m['unit']}]")
    if payload.limitations:
        lines.append("Known limitations:")
        for lim in payload.limitations:
            lines.append(f"- {lim}")
    if payload.examples:
        lines.append("Example questions:")
        for q in payload.examples:
            lines.append(f"- {q}")
    return "\n".join(lines).strip()


def _detect_kind(question: str) -> CatalogKind:
    q = question.lower()
    # Overview phrases that contain "do you have" must win before the presence check.
    if re.search(r"what data do you have|what documents|what sources|which (years|counties|periods)|what reports", q):
        return "overview"
    if re.search(r"\bdo you have\b|\bhave you got\b|\bis .{0,40}\b(available|loaded|in the (data|reports))\b", q):
        return "have"
    if re.search(r"what can i ask|example questions|starter questions", q):
        return "examples"
    if re.search(r"how up to date|how current|when (was|were).*(ingest|update)|latest report", q):
        return "freshness"
    if re.search(r"what is this report about|about this (report|dataset)|what (is|are) these reports", q):
        return "about"
    return "overview"


def answer_catalog(
    conn: psycopg.Connection,
    question: str,
    allowed: list[int],
) -> CatalogAnswer:
    kind = _detect_kind(question)

    if kind == "have":
        fy = parse_fiscal_year(question)
        period = parse_period(question)
        county = None
        mentioned = county_mentions(question)
        if len(mentioned) == 1:
            county = BY_CODE[next(iter(mentioned))]
        elif match_county(question):
            county = match_county(question)
        # Bare "do you have FY X" with no other signal still works via fy alone.
        have = have_coverage(
            conn, allowed, fiscal_year=fy, period=period, county_id=county.code if county else None,
        )
        payload = CatalogPayload(kind="have", have=have)
        return CatalogAnswer(kind="have", text=render_catalog_answer(payload), payload=payload)

    if kind == "about":
        payload = about_report(
            conn, allowed, fiscal_year=parse_fiscal_year(question), period=parse_period(question),
        )
        return CatalogAnswer(kind="about", text=render_catalog_answer(payload), payload=payload)

    payload = build_catalog(conn, allowed)
    payload.kind = kind
    if kind == "examples":
        payload.kind = "examples"
    elif kind == "freshness":
        payload.kind = "freshness"
    return CatalogAnswer(kind=payload.kind, text=render_catalog_answer(payload), payload=payload)
