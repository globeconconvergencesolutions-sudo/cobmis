"""Shared ask orchestration for CLI-parity HTTP/SSE endpoints."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.catalog import allowed_collections, answer_catalog
from app.core.config import get_settings
from app.ingest.cob_listing import parse_fiscal_year, parse_period
from app.ingest.counties import BY_CODE, county_mentions, match_county
from app.llm.ollama_client import OllamaClient, OllamaError
from app.orchestrator import answer_both, answer_rag, retrieve_hits, rewrite_standalone
from app.router import classify
from app.router.classify import has_narrative_intent
from app.sql_agent import answer_sql

ProgressFn = Callable[[str, str], None]


@dataclass
class AskResult:
    question: str
    route: str
    text: str
    citations: list[dict] = field(default_factory=list)
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    sql_citations: list[dict] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    error: str | None = None
    scope_label: str | None = None
    notices: list[str] = field(default_factory=list)


class AskCancelled(Exception):
    """Client aborted the stream; stop work without treating it as a hard failure."""


def _progress(cb: ProgressFn | None, step: str, label: str) -> None:
    if cb:
        cb(step, label)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise AskCancelled()


def format_scope_label(
    county_name: str | None,
    fiscal_year: str | None,
    period: str | None,
) -> str:
    parts: list[str] = []
    if county_name:
        parts.append(county_name)
    if fiscal_year:
        parts.append(f"FY {fiscal_year}")
    if period:
        parts.append(period)
    return " · ".join(parts)


def enrich_question_with_scope(
    question: str,
    *,
    county_name: str | None,
    fiscal_year: str | None,
    period: str | None,
) -> str:
    """Append filter scope so the model answers the same scope the DB search used."""
    label = format_scope_label(county_name, fiscal_year, period)
    if not label:
        return question
    return (
        f"{question.strip()}\n\n"
        f"(Answer only for this scope: {label}. Filters were set by the user.)"
    )


def resolve_scope(
    question: str,
    *,
    county: str | None = None,
    fy: str | None = None,
    period: str | None = None,
):
    c = match_county(county) if county else None
    if county and not c:
        raise ValueError(
            f"Unknown county {county!r}. Use a Kenyan county name (e.g. Machakos) "
            "or clear the County filter."
        )
    if c is None:
        mentioned = county_mentions(question)
        if len(mentioned) == 1:
            c = BY_CODE[next(iter(mentioned))]
    return c, fy or parse_fiscal_year(question), period or parse_period(question)


def clarify_if_underspecified(
    question: str,
    *,
    route: str,
    county_name: str | None,
) -> str | None:
    """Return a user-facing clarify message when we should not silently guess."""
    if route == "catalog":
        return None
    if county_name:
        return None
    mentioned = county_mentions(question)
    if mentioned:
        return None
    # Narrative / challenge questions without a county are too easy to answer wrong.
    if route in ("rag", "both") and has_narrative_intent(question):
        return (
            "I need a county to answer that.\n\n"
            "Set the **County** filter (e.g. Machakos) or name the county in your question, "
            "then ask again — for example: “What challenges did Machakos face in 9M FY 2025/26?”"
        )
    return None


def period_notice_for_route(
    question: str,
    *,
    route: str,
    period: str | None,
) -> str | None:
    if route not in ("rag", "both"):
        return None
    if period != "FY":
        return None
    if not has_narrative_intent(question):
        return None
    return (
        "Challenge write-ups are often richer in 9M or H1 reports than in the full-year (FY) volume. "
        "Searching FY as requested — if results are thin, try period 9M or H1."
    )


def run_ask(
    client: OllamaClient,
    conn: psycopg.Connection,
    question: str,
    *,
    user_id: int | None,
    county: str | None = None,
    fy: str | None = None,
    period: str | None = None,
    force_route: str | None = None,
    prior_question: str | None = None,
    prior_answer: str | None = None,
    on_progress: ProgressFn | None = None,
    skip_followup_rewrite: bool = False,
    cancel: threading.Event | None = None,
) -> AskResult:
    """Run a full ask. If prior_* set and not skip_followup_rewrite, rewrite first."""
    q = question.strip()
    notices: list[str] = []

    if prior_question and not skip_followup_rewrite:
        _check_cancel(cancel)
        _progress(on_progress, "rewrite", "Understanding your follow-up…")
        try:
            q = rewrite_standalone(
                client,
                q,
                prior_question=prior_question,
                prior_answer=prior_answer,
            )
        except OllamaError as exc:
            return AskResult(
                question=q,
                route="rag",
                text=(
                    "Could not expand that follow-up — the local model timed out or is busy. "
                    "Try a full question (county + period), or Cancel and retry in a moment.\n\n"
                    f"Detail: {exc}"
                ),
                error=str(exc),
            )

    _check_cancel(cancel)
    _progress(on_progress, "classify", "Choosing the best way to answer…")
    route = classify(q, force=force_route)  # type: ignore[arg-type]
    allowed = allowed_collections(conn, user_id)
    s = get_settings()

    if route == "catalog":
        _progress(on_progress, "catalog", "Checking what data is loaded…")
        ans = answer_catalog(conn, q, allowed)
        return AskResult(question=q, route=route, text=ans.text)

    try:
        county_obj, fiscal_year, period_type = resolve_scope(
            q, county=county, fy=fy, period=period
        )
    except ValueError as exc:
        return AskResult(question=q, route=route, text=str(exc), error=str(exc))

    county_name = county_obj.name if county_obj else None
    county_id = county_obj.code if county_obj else None
    scope_label = format_scope_label(county_name, fiscal_year, period_type) or None

    if scope_label:
        _progress(on_progress, "scope", f"Using filters: {scope_label}")

    clarify = clarify_if_underspecified(q, route=route, county_name=county_name)
    if clarify:
        return AskResult(
            question=q,
            route=route,
            text=clarify,
            scope_label=scope_label,
            error="needs_county",
        )

    tip = period_notice_for_route(q, route=route, period=period_type)
    if tip:
        notices.append(tip)
        _progress(on_progress, "notice", tip)

    q_for_model = enrich_question_with_scope(
        q, county_name=county_name, fiscal_year=fiscal_year, period=period_type
    )

    if route == "sql":
        _check_cancel(cancel)
        try:
            ans = answer_sql(
                client,
                q_for_model,
                fiscal_year=fiscal_year,
                period=period_type,
                county=county_name,
                on_progress=on_progress,
            )
        except OllamaError as exc:
            return AskResult(
                question=q,
                route=route,
                text=_ollama_fail_text(exc, scope_label),
                scope_label=scope_label,
                notices=notices,
                error=str(exc),
            )
        text = ans.text or ""
        if ans.error and not text:
            text = _pipeline_fail_text(ans.error, scope_label)
        return AskResult(
            question=q,
            route=route,
            text=text,
            sql=ans.sql,
            columns=ans.columns,
            rows=ans.rows,
            sql_citations=ans.citations,
            unsupported_numbers=ans.unsupported_numbers,
            error=ans.error,
            scope_label=scope_label,
            notices=notices,
        )

    _check_cancel(cancel)
    _progress(on_progress, "embed", "Understanding your question…")
    try:
        qvec = client.embed([q_for_model])[0]
    except OllamaError as exc:
        return AskResult(
            question=q,
            route=route,
            text=_ollama_fail_text(exc, scope_label),
            scope_label=scope_label,
            notices=notices,
            error=str(exc),
        )

    if route == "both":
        _check_cancel(cancel)
        try:
            both = answer_both(
                client,
                conn,
                q_for_model,
                allowed,
                qvec,
                county_id=county_id,
                county_name=county_name,
                fiscal_year=fiscal_year,
                period=period_type,
                k=s.retrieval_top_k,
                n=s.rerank_top_n,
                on_progress=on_progress,
            )
        except OllamaError as exc:
            return AskResult(
                question=q,
                route=route,
                text=_ollama_fail_text(exc, scope_label),
                scope_label=scope_label,
                notices=notices,
                error=str(exc),
            )
        text = both.text or ""
        if both.error and not text:
            text = _pipeline_fail_text(both.error, scope_label)
        return AskResult(
            question=q,
            route=route,
            text=text,
            citations=both.rag_answer.citations,
            sql=both.sql_answer.sql,
            columns=both.sql_answer.columns,
            rows=both.sql_answer.rows,
            sql_citations=both.sql_answer.citations,
            unsupported_numbers=both.unsupported_numbers,
            error=both.error,
            scope_label=scope_label,
            notices=notices,
        )

    # rag
    _check_cancel(cancel)
    scope_bit = f" for {scope_label}" if scope_label else ""
    _progress(on_progress, "retrieve", f"Searching report pages{scope_bit}…")
    hits = retrieve_hits(
        conn,
        q_for_model,
        qvec,
        allowed,
        county_id=county_id,
        fiscal_year=fiscal_year,
        period=period_type,
        k=s.retrieval_top_k,
        n=s.rerank_top_n,
    )
    if not hits:
        return AskResult(
            question=q,
            route=route,
            text=_empty_retrieval_text(scope_label, period_type, q),
            scope_label=scope_label,
            notices=notices,
        )

    _check_cancel(cancel)
    _progress(on_progress, "rag", "Writing the answer from cited pages…")
    try:
        rag = answer_rag(client, q_for_model, hits)
    except OllamaError as exc:
        return AskResult(
            question=q,
            route=route,
            text=_ollama_fail_text(exc, scope_label),
            citations=[],
            scope_label=scope_label,
            notices=notices,
            error=str(exc),
        )
    text = rag.text or ""
    if rag.error and not text:
        text = _ollama_fail_text(OllamaError(rag.error), scope_label)
    if notices and text and not text.startswith("Note:"):
        text = f"Note: {notices[0]}\n\n{text}"
    return AskResult(
        question=q,
        route=route,
        text=text,
        citations=rag.citations,
        unsupported_numbers=rag.unsupported_numbers,
        error=rag.error,
        scope_label=scope_label,
        notices=notices,
    )


def _ollama_fail_text(exc: BaseException, scope_label: str | None) -> str:
    scope = f" (scope: {scope_label})" if scope_label else ""
    return (
        f"Could not finish answering{scope} — the local model timed out or is busy.\n\n"
        "What to try:\n"
        "- Hit Cancel if a previous ask is still running\n"
        "- Ask a shorter, concrete question\n"
        "- Retry in a moment once the model is free\n\n"
        f"Detail: {exc}"
    )


def _pipeline_fail_text(detail: str, scope_label: str | None) -> str:
    scope = f" for {scope_label}" if scope_label else ""
    return (
        f"Could not complete that lookup{scope}.\n\n"
        f"{detail}\n\n"
        "Try rephrasing, adjusting filters, or a different period (9M / H1 / FY)."
    )


def _empty_retrieval_text(
    scope_label: str | None,
    period: str | None,
    question: str,
) -> str:
    scope = scope_label or "the current filters"
    lines = [
        f"No matching report passages for **{scope}**.",
        "",
        "What to try:",
        "- Confirm the county name spelling",
        "- Widen or change the period (Q1 / H1 / 9M / FY)",
        "- Ask about a topic covered in the loaded COB reports",
    ]
    if period == "FY" and has_narrative_intent(question):
        lines.append(
            "- For challenges / observations, try **9M** or **H1** — those volumes often have more narrative"
        )
    return "\n".join(lines)


def result_to_public(r: AskResult) -> dict[str, Any]:
    return {
        "question": r.question,
        "route": r.route,
        "text": r.text,
        "citations": r.citations,
        "sql": r.sql,
        "columns": r.columns,
        "rows": r.rows,
        "sql_citations": r.sql_citations,
        "unsupported_numbers": r.unsupported_numbers,
        "error": r.error,
        "scope_label": r.scope_label,
        "notices": r.notices,
    }


def chunk_text(text: str, size: int = 24) -> Iterator[str]:
    if not text:
        return
    for i in range(0, len(text), size):
        yield text[i : i + size]


def audit_ask(
    conn: psycopg.Connection,
    *,
    user_id: int | None,
    result: AskResult,
    conversation_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO app.audit_events (user_id, question, route, sql_text, sources_json, conversation_id)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
        """,
        (
            user_id,
            result.question,
            result.route,
            result.sql,
            json.dumps(result.citations or result.sql_citations or []),
            conversation_id,
        ),
    )
