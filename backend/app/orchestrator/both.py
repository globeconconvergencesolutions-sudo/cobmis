"""Run SQL + RAG and merge into one answer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg

from app.llm.ollama_client import OllamaClient
from app.orchestrator.merge import merge_both
from app.orchestrator.rag_path import RagAnswer, answer_rag, retrieve_hits
from app.sql_agent.pipeline import SqlAnswer, answer_sql

ProgressFn = Callable[[str, str], None]


@dataclass
class BothAnswer:
    text: str
    sql_answer: SqlAnswer
    rag_answer: RagAnswer
    unsupported_numbers: list[str] = field(default_factory=list)
    error: str | None = None


def answer_both(
    client: OllamaClient,
    conn: psycopg.Connection,
    question: str,
    allowed: list[int],
    query_vector: list[float] | None,
    *,
    county_id: int | None = None,
    county_name: str | None = None,
    fiscal_year: str | None = None,
    period: str | None = None,
    k: int = 30,
    n: int = 8,
    on_progress: ProgressFn | None = None,
) -> BothAnswer:
    def prog(step: str, label: str) -> None:
        if on_progress:
            on_progress(step, label)

    # Sequential on small hardware (one Ollama model at a time).
    sql_ans = answer_sql(
        client, question,
        fiscal_year=fiscal_year, period=period, county=county_name,
        on_progress=on_progress,
    )
    prog("retrieve", "Searching report pages…")
    hits = retrieve_hits(
        conn, question, query_vector, allowed,
        county_id=county_id, fiscal_year=fiscal_year, period=period, k=k, n=n,
    )
    prog("rag", "Writing narrative from cited passages…")
    rag_ans = answer_rag(client, question, hits)
    if rag_ans.error and not sql_ans.rows:
        return BothAnswer(
            text="Answer generation failed.",
            sql_answer=sql_ans,
            rag_answer=rag_ans,
            error=rag_ans.error,
        )
    prog("merge", "Merging figures with narrative…")
    text, bad = merge_both(client, question, sql_ans, rag_ans)
    return BothAnswer(
        text=text,
        sql_answer=sql_ans,
        rag_answer=rag_ans,
        unsupported_numbers=bad,
        error=rag_ans.error,
    )
