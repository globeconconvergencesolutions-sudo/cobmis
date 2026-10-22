"""Validate model-generated SQL with sqlglot before any execution."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DEFAULT_LIMIT = 500

# Relations the agent may touch. Prefer the friendly view; dims allowed for rare joins.
ALLOWED_TABLES = frozenset({
    "v_county_metrics",
    "dim_county",
    "dim_period",
    "dim_metric",
    "source_document",
    "fact_county_budget",
})
ALLOWED_SCHEMAS = frozenset({"public_data", ""})  # "" = unqualified (we rewrite to public_data)

# Postgres functions commonly needed for rankings / rounding. Anything else is rejected.
ALLOWED_FUNCTIONS = frozenset({
    "count", "sum", "avg", "min", "max", "coalesce", "nullif", "round", "abs",
    "upper", "lower", "trim", "length", "substring", "replace", "concat",
    "cast", "greatest", "least", "now", "date_trunc",
})

_FORBIDDEN_TYPES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.Command, exp.Grant, exp.Revoke, exp.TruncateTable, exp.Merge,
)


class ValidationError(ValueError):
    """Raised when generated SQL is unsafe or not a single SELECT."""


def _table_name(node: exp.Table) -> tuple[str, str]:
    schema = (node.db or node.catalog or "").lower()
    name = (node.name or "").lower()
    return schema, name


def _collect_tables(parsed: exp.Expression) -> list[tuple[str, str]]:
    return [_table_name(t) for t in parsed.find_all(exp.Table)]


def _collect_functions(parsed: exp.Expression) -> list[str]:
    names = []
    for fn in parsed.find_all(exp.Func):
        # sqlglot treats AND/OR/comparisons as Func subclasses — those are operators, not calls.
        if isinstance(fn, exp.Connector):
            continue
        if isinstance(fn, exp.Predicate) and not isinstance(fn, exp.Anonymous):
            continue
        # Anonymous (e.g. pg_sleep) stores the name on .this / .name; builtins use sql_name().
        if isinstance(fn, exp.Anonymous):
            name = str(fn.this or getattr(fn, "name", "") or "").lower()
        else:
            name = (fn.sql_name() if hasattr(fn, "sql_name") else fn.key or "").lower()
        if name and name not in ("anonymous", "and", "or", "not"):
            names.append(name)
    return names


def _ensure_limit(select: exp.Select, limit: int = DEFAULT_LIMIT) -> exp.Select:
    if select.args.get("limit") is None:
        return select.limit(limit)
    # Cap an oversized LIMIT
    lim = select.args["limit"]
    try:
        n = int(lim.expression.this) if lim and lim.expression else limit
    except (TypeError, ValueError, AttributeError):
        n = limit
    if n > limit:
        return select.limit(limit)
    return select


def validate_sql(sql: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """Parse and rewrite ``sql`` into a single safe Postgres SELECT string.

    Raises ValidationError on any violation. Never executes.
    """
    text = (sql or "").strip()
    if not text:
        raise ValidationError("empty SQL")
    # Strip markdown fences if the model wrapped the query.
    fence = re.match(r"^```(?:sql)?\s*([\s\S]*?)```\s*$", text, re.I)
    if fence:
        text = fence.group(1).strip()
    text = text.rstrip(";").strip()

    try:
        statements = sqlglot.parse(text, dialect="postgres")
    except ParseError as exc:
        raise ValidationError(f"parse error: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise ValidationError(f"expected exactly one statement, got {len(statements)}")

    root = statements[0]
    for bad in _FORBIDDEN_TYPES:
        if isinstance(root, bad) or list(root.find_all(bad)):
            raise ValidationError(f"forbidden statement type: {bad.__name__}")

    # Allow WITH … SELECT or plain SELECT (sqlglot wraps CTE as With or Select).
    select = root if isinstance(root, exp.Select) else root.find(exp.Select)
    if select is None:
        raise ValidationError("only SELECT (optionally WITH) queries are allowed")
    if not isinstance(root, (exp.Select, exp.With)):
        # e.g. Union at top level is ok if it's SELECTs only
        if not isinstance(root, exp.Union):
            raise ValidationError(f"unsupported root expression: {type(root).__name__}")

    for schema, name in _collect_tables(root):
        if not name:
            continue
        if schema and schema not in ALLOWED_SCHEMAS:
            raise ValidationError(f"schema not allowed: {schema}.{name}")
        if name not in ALLOWED_TABLES:
            raise ValidationError(f"table not allowed: {schema + '.' if schema else ''}{name}")

    for fn in _collect_functions(root):
        # sqlglot may report "anonymous" or dialect-specific names; allow cast aliases.
        base = fn.split(".")[-1]
        if base in ("", "anonymous"):
            continue
        if base not in ALLOWED_FUNCTIONS and not base.startswith("pg_catalog."):
            # Reject known dangerous names explicitly even if somehow aliased.
            if base in ("pg_sleep", "dblink", "lo_import", "lo_export", "pg_read_file"):
                raise ValidationError(f"function not allowed: {base}")
            if base not in ALLOWED_FUNCTIONS:
                raise ValidationError(f"function not allowed: {base}")

    # Inject / cap LIMIT on the outermost Select (not every subquery).
    outer = root if isinstance(root, exp.Select) else None
    if isinstance(root, exp.With):
        outer = root.this if isinstance(root.this, exp.Select) else root.find(exp.Select)
    if isinstance(root, exp.Union):
        # Apply limit to the union via wrap if needed
        if root.args.get("limit") is None:
            root = root.limit(limit)
        outer = None
    if outer is not None:
        new_select = _ensure_limit(outer, limit)
        if isinstance(root, exp.Select):
            root = new_select
        elif isinstance(root, exp.With):
            root.set("this", new_select)

    # Qualify bare allowlisted tables with public_data for clarity.
    for table in root.find_all(exp.Table):
        schema, name = _table_name(table)
        if name in ALLOWED_TABLES and not schema:
            table.set("db", exp.to_identifier("public_data"))

    out = root.sql(dialect="postgres")
    if not out.upper().lstrip().startswith(("SELECT", "WITH")):
        raise ValidationError("rewritten SQL must start with SELECT or WITH")
    return out
