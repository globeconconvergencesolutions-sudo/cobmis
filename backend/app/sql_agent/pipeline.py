"""End-to-end text-to-SQL: generate → validate → execute → one repair → narrate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.llm.ollama_client import OllamaClient
from app.sql_agent.execute import QueryResult, execute_sql
from app.sql_agent.generate import generate_sql
from app.sql_agent.narrate import citations_from_rows, narrate_rows
from app.sql_agent.validate import ValidationError, validate_sql

ProgressFn = Callable[[str, str], None]


@dataclass
class SqlAnswer:
    text: str
    sql: str | None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    repaired: bool = False
    unsupported_numbers: list[str] = field(default_factory=list)
    error: str | None = None


def _try_once(
    client: OllamaClient,
    question: str,
    *,
    fiscal_year: str | None,
    period: str | None,
    county: str | None,
    repair_error: str | None,
    previous_sql: str | None,
    dbname: str | None,
    conn: psycopg.Connection | None,
) -> tuple[str | None, QueryResult | None, str | None]:
    """Returns (validated_sql, result, error_message)."""
    try:
        raw = generate_sql(
            client, question,
            fiscal_year=fiscal_year, period=period, county=county,
            repair_error=repair_error, previous_sql=previous_sql,
        )
    except Exception as exc:
        return None, None, f"SQL generation failed: {exc}"
    try:
        sql = validate_sql(raw)
    except ValidationError as exc:
        return raw, None, f"validation: {exc}"
    result = execute_sql(sql, dbname=dbname, conn=conn)
    if result.error:
        return sql, result, f"execution: {result.error}"
    return sql, result, None


def answer_sql(
    client: OllamaClient,
    question: str,
    *,
    fiscal_year: str | None = None,
    period: str | None = None,
    county: str | None = None,
    dbname: str | None = None,
    conn: psycopg.Connection | None = None,
    on_progress: ProgressFn | None = None,
) -> SqlAnswer:
    def prog(step: str, label: str) -> None:
        if on_progress:
            on_progress(step, label)

    prog("sql_generate", "Writing SQL against validated tables…")
    sql, result, err = _try_once(
        client, question,
        fiscal_year=fiscal_year, period=period, county=county,
        repair_error=None, previous_sql=None, dbname=dbname, conn=conn,
    )
    repaired = False
    if err or (result is not None and result.empty):
        prog("sql_repair", "Repairing SQL…")
        note = err or "query returned no rows"
        sql2, result2, err2 = _try_once(
            client, question,
            fiscal_year=fiscal_year, period=period, county=county,
            repair_error=note, previous_sql=sql, dbname=dbname, conn=conn,
        )
        repaired = True
        if result2 is not None and not result2.error and not result2.empty:
            sql, result, err = sql2, result2, None
        elif result2 is not None and not result2.error and result2.empty:
            sql, result, err = sql2, result2, None  # honest empty after repair
        else:
            # Keep best available
            if result2 is not None:
                sql, result, err = sql2, result2, err2
            else:
                err = err2 or err

    if result is None:
        return SqlAnswer(
            text="The loaded reports do not contain this information.",
            sql=sql,
            repaired=repaired,
            error=err,
        )

    if result.empty:
        return SqlAnswer(
            text="The loaded reports do not contain this information.",
            sql=sql,
            columns=result.columns,
            rows=[],
            repaired=repaired,
            error=None,
        )

    prog("sql_execute", "Querying validated tables…")
    # execute already happened in _try_once; label marks success path for UX
    prog("narrate", "Writing answer from the result rows…")
    try:
        text, bad = narrate_rows(client, question, result.columns, result.rows, sql or "")
    except Exception as exc:
        # Fall back to a plain table dump if narration fails.
        text = _fallback_table(result.columns, result.rows)
        bad = []
        err = f"narration failed: {exc}"

    return SqlAnswer(
        text=text,
        sql=sql,
        columns=result.columns,
        rows=result.rows,
        citations=citations_from_rows(result.columns, result.rows),
        repaired=repaired,
        unsupported_numbers=bad,
        error=err,
    )


def _fallback_table(columns: list[str], rows: list[list[Any]]) -> str:
    lines = [" | ".join(columns), " | ".join("---" for _ in columns)]
    for row in rows[:20]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)
