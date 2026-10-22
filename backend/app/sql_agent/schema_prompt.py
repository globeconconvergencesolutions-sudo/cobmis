"""Compact schema + few-shot examples for the SQL generation model."""

from __future__ import annotations

from app.ingest.tables_to_sql import METRICS

VIEW_COLUMNS = (
    "county text — official county name (e.g. 'Nairobi City', 'Turkana', not 'Nairobi')",
    "region text — former province",
    "fiscal_year text — e.g. '2025/26'",
    "period_type text — one of 'Q1', 'H1', '9M', 'FY'",
    "months smallint — cumulative months covered (3/6/9/12)",
    "metric text — snake_case metric key (see dictionary)",
    "unit text — 'KES' or 'percent' or 'count'",
    "value numeric — amounts already converted to full Kenya Shillings (not millions); percents as e.g. 22.00",
    "source_title text — report title",
    "source_page integer — 1-based PDF page",
    "source_table text — printed table caption",
    "source_url text — cob.go.ke download URL",
)

FEW_SHOTS = [
    (
        "Top 5 counties by pending bills in FY 2025/26 full year",
        """SELECT county, value, source_page, source_title, source_table
FROM public_data.v_county_metrics
WHERE metric = 'pending_bills_total' AND fiscal_year = '2025/26' AND period_type = 'FY'
ORDER BY value DESC
LIMIT 5""",
    ),
    (
        "Turkana development absorption in the first nine months of FY 2025/26",
        """SELECT county, metric, value, unit, period_type, source_page, source_title
FROM public_data.v_county_metrics
WHERE county = 'Turkana' AND metric = 'absorption_development_pct'
  AND fiscal_year = '2025/26' AND period_type = '9M'
LIMIT 10""",
    ),
    (
        "Nairobi City own source revenue performance H1 FY 2025/26",
        """SELECT county, metric, value, unit, source_page, source_table
FROM public_data.v_county_metrics
WHERE county = 'Nairobi City' AND metric LIKE 'osr_%'
  AND fiscal_year = '2025/26' AND period_type = 'H1'
ORDER BY metric
LIMIT 20""",
    ),
    (
        "Compare Nairobi City and Mombasa total OSR performance in H1 2025/26",
        """SELECT county, value, unit, source_page, source_title
FROM public_data.v_county_metrics
WHERE metric = 'osr_performance_total_pct'
  AND fiscal_year = '2025/26' AND period_type = 'H1'
  AND county IN ('Nairobi City', 'Mombasa')
ORDER BY county
LIMIT 10""",
    ),
    (
        "Baringo total approved budget first half FY 2025/26",
        """SELECT county, metric, value, unit, source_page, source_title
FROM public_data.v_county_metrics
WHERE county = 'Baringo' AND metric = 'budget_total'
  AND fiscal_year = '2025/26' AND period_type = 'H1'
LIMIT 5""",
    ),
    (
        "Which county had the highest total absorption rate in 9M FY 2025/26?",
        """SELECT county, value, unit, source_page, source_title
FROM public_data.v_county_metrics
WHERE metric = 'absorption_total_pct' AND fiscal_year = '2025/26' AND period_type = '9M'
ORDER BY value DESC
LIMIT 1""",
    ),
    (
        "Kericho ordinary own-source revenue target FY 2025/26 nine months",
        """SELECT county, metric, value, unit, source_page, source_table
FROM public_data.v_county_metrics
WHERE county = 'Kericho' AND metric = 'osr_target_ordinary'
  AND fiscal_year = '2025/26' AND period_type = '9M'
LIMIT 5""",
    ),
    (
        "Total pending bills for all counties end of FY 2025/26",
        """SELECT sum(value) AS total_pending_bills_kes, count(*) AS county_rows
FROM public_data.v_county_metrics
WHERE metric = 'pending_bills_total' AND fiscal_year = '2025/26' AND period_type = 'FY'
LIMIT 5""",
    ),
]


def metric_dictionary_text() -> str:
    lines = []
    for m in sorted(METRICS.values(), key=lambda x: x.name):
        cum = "cumulative YTD" if m.cumulative else "point-in-time / rate"
        lines.append(f"- {m.name}: {m.description} [{m.unit}] ({cum})")
    return "\n".join(lines)


def build_system_prompt() -> str:
    cols = "\n".join(f"  - {c}" for c in VIEW_COLUMNS)
    shots = []
    for q, sql in FEW_SHOTS:
        shots.append(f"Q: {q}\nSQL:\n{sql}")
    return f"""You write a single PostgreSQL SELECT for Kenyan county budget metrics.

Rules:
- Output ONLY SQL (no markdown fences, no commentary).
- One statement only. No INSERT/UPDATE/DELETE/DDL. No semicolons needed.
- Query public_data.v_county_metrics (preferred). You may also use public_data.dim_county,
  dim_period, dim_metric, fact_county_budget, source_document if needed.
- Always filter fiscal_year and period_type when the question names them.
- County names must match exactly (use 'Nairobi City' not 'Nairobi').
- value is already in full KES for money metrics (not millions). Percents are numeric e.g. 22.
- Include source_page and source_title (and source_table when useful) in SELECT lists for fact rows.
- Add LIMIT (max 500). Prefer ORDER BY for rankings.
- If the question cannot be answered from this schema, still write a SELECT that returns zero rows
  with the most restrictive sensible filters (do not invent tables).

View public_data.v_county_metrics columns:
{cols}

Metric dictionary:
{metric_dictionary_text()}

Examples:
""" + "\n\n".join(shots)


def build_user_prompt(
    question: str,
    *,
    fiscal_year: str | None = None,
    period: str | None = None,
    county: str | None = None,
    repair_error: str | None = None,
    previous_sql: str | None = None,
) -> str:
    hints = []
    if fiscal_year:
        hints.append(f"Prefer fiscal_year = '{fiscal_year}' if that year exists in the data.")
    if period:
        hints.append(f"Prefer period_type = '{period}'.")
    if county:
        hints.append(f"Prefer county = '{county}'.")
    hint_block = ("\n".join(hints) + "\n") if hints else ""
    if repair_error:
        return (
            f"The previous SQL failed or returned nothing.\n"
            f"Error / note: {repair_error}\n"
            f"Previous SQL:\n{previous_sql or '(none)'}\n"
            f"{hint_block}"
            f"Question: {question}\n"
            f"Write a corrected single SELECT:"
        )
    return f"{hint_block}Question: {question}\nSQL:"
