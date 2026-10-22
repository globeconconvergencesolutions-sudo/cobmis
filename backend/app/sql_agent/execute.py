"""Execute validated SQL as cob_readonly only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from app.db.roles import connect
from app.sql_agent.validate import DEFAULT_LIMIT


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    error: str | None = None

    @property
    def empty(self) -> bool:
        return not self.rows and self.error is None


def _cell(v: Any) -> Any:
    if isinstance(v, Decimal):
        # Preserve integers cleanly for narration / number checks.
        if v == v.to_integral_value():
            return int(v)
        return float(v)
    return v


def execute_sql(
    sql: str,
    *,
    dbname: str | None = None,
    conn: psycopg.Connection | None = None,
    row_cap: int = DEFAULT_LIMIT,
) -> QueryResult:
    """Run ``sql`` with the read-only role. ``sql`` must already be validate_sql()'d."""
    own = conn is None
    try:
        c = conn or connect("cob_readonly", dbname)
    except Exception as exc:  # DatabaseSetupError is SystemExit subclass; still catch broadly
        return QueryResult(columns=[], rows=[], error=str(exc))
    try:
        cur = c.execute(sql)
        cols = [d.name for d in cur.description] if cur.description else []
        raw = cur.fetchmany(row_cap + 1)
        if len(raw) > row_cap:
            raw = raw[:row_cap]
        rows = [[_cell(v) for v in row] for row in raw]
        return QueryResult(columns=cols, rows=rows)
    except Exception as exc:
        return QueryResult(columns=[], rows=[], error=str(exc).strip())
    finally:
        if own:
            c.close()
