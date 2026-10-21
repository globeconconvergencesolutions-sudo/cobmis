"""Load the all-county (chapter 2) tables of parsed reports into public_data, with validation.

Tables are recognised by caption title (numbering differs between reports: "2.4" is the
absorption table in the H1 report and the economic-classification table in the annual one).
Columns are mapped to metrics from their header wording, never from their position.

Per row:  county name must resolve to one of the 47; every printed formula must hold
          (table_checks); values must parse. Failing rows go to quarantine_rows, not facts.
Per table: 47 county rows expected; Total row must equal the column sums.
Per doc:   the same metric printed in two tables (e.g. total expenditure in the absorption and
           the economic-classification tables) must agree.
Per year:  cumulative metrics must not decrease H1 -> 9M -> FY (consistency_check()).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.ingest.counties import COUNTIES, match_county
from app.ingest.table_checks import LogicalTable, check_table, find_formula_row, logical_tables, parse_number

log = logging.getLogger("cob.tables_to_sql")

PERIOD_MONTHS = {"Q1": 3, "H1": 6, "9M": 9, "FY": 12}


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    unit: str  # KES | percent | count
    cumulative: bool


METRICS = {m.name: m for m in [
    Metric("budget_recurrent", "Approved recurrent budget estimates", "KES", False),
    Metric("budget_development", "Approved development budget estimates", "KES", False),
    Metric("budget_total", "Approved total budget estimates", "KES", False),
    Metric("expenditure_recurrent", "Recurrent expenditure, year to date", "KES", True),
    Metric("expenditure_development", "Development expenditure, year to date", "KES", True),
    Metric("expenditure_total", "Total expenditure, year to date", "KES", True),
    Metric("expenditure_compensation_employees", "Expenditure on compensation to employees (personnel emoluments), year to date", "KES", True),
    Metric("expenditure_operations_maintenance", "Expenditure on operations and maintenance, year to date", "KES", True),
    Metric("absorption_recurrent_pct", "Recurrent expenditure as % of recurrent budget", "percent", False),
    Metric("absorption_development_pct", "Development expenditure as % of development budget", "percent", False),
    Metric("absorption_total_pct", "Total expenditure as % of total budget", "percent", False),
    Metric("osr_target_ordinary", "Annual target, ordinary own-source revenue", "KES", False),
    Metric("osr_target_fif", "Annual target, Facility Improvement Fund / A-I-A revenue", "KES", False),
    Metric("osr_target_total", "Annual target, total own-source revenue", "KES", False),
    Metric("osr_actual_ordinary", "Ordinary own-source revenue collected, year to date", "KES", True),
    Metric("osr_actual_fif", "FIF / A-I-A revenue collected, year to date", "KES", True),
    Metric("osr_actual_total", "Total own-source revenue collected, year to date", "KES", True),
    Metric("osr_performance_ordinary_pct", "Ordinary OSR collected as % of annual target", "percent", False),
    Metric("osr_performance_fif_pct", "FIF / A-I-A collected as % of annual target", "percent", False),
    Metric("osr_performance_total_pct", "Total OSR collected as % of annual target", "percent", False),
    Metric("exchequer_issues_recurrent", "Exchequer issues for recurrent expenditure, year to date", "KES", True),
    Metric("exchequer_issues_development", "Exchequer issues for development expenditure, year to date", "KES", True),
    Metric("exchequer_issues_total", "Total exchequer issues, year to date", "KES", True),
    Metric("pending_bills_total", "Trade payables (pending bills) outstanding at period end", "KES", False),
    Metric("pending_bills_executive", "County Executive trade payables outstanding at period end", "KES", False),
    Metric("pending_bills_assembly", "County Assembly trade payables outstanding at period end", "KES", False),
]}


@dataclass
class TableSpec:
    key: str
    title_re: re.Pattern[str]
    # (metric, regex over the column's full header text) - first match wins per column
    columns: list[tuple[str, re.Pattern[str]]]
    optional: bool = False  # not printed in every report (the exchequer table is annual-only)


def _r(p: str) -> re.Pattern[str]:
    return re.compile(p, re.I)


SPECS = [
    TableSpec("absorption", _r(r"^County Budget Allocation, Expenditure,? and Absorption Rate"), [
        ("budget_recurrent", _r(r"budget.*\brec")), ("budget_development", _r(r"budget.*\bdev")), ("budget_total", _r(r"budget.*\btotal")),
        ("expenditure_recurrent", _r(r"expenditure.*\brec")), ("expenditure_development", _r(r"expenditure.*\bdev")), ("expenditure_total", _r(r"expenditure.*\btotal")),
        ("absorption_recurrent_pct", _r(r"absorption.*\brec")), ("absorption_development_pct", _r(r"absorption.*\bdev")), ("absorption_total_pct", _r(r"absorption.*\btotal")),
    ]),
    TableSpec("economic_classification", _r(r"^Expenditure by Major Economic Classification"), [
        ("expenditure_compensation_employees", _r(r"compensation")), ("expenditure_operations_maintenance", _r(r"operations")),
        ("expenditure_recurrent", _r(r"total recurrent")), ("expenditure_development", _r(r"development expenditure")),
        ("expenditure_total", _r(r"total expenditure")),
    ]),
    TableSpec("own_source_revenue", _r(r"^Own[- ]Source Revenue Collection"), [
        ("osr_target_ordinary", _r(r"target.*ordinary")), ("osr_target_fif", _r(r"target.*(fif|a-?i-?a)")), ("osr_target_total", _r(r"target.*total")),
        ("osr_actual_ordinary", _r(r"(reported|actual|collect).*ordinary")), ("osr_actual_fif", _r(r"(reported|actual|collect).*(fif|a-?i-?a)")),
        ("osr_actual_total", _r(r"(reported|actual|collect).*total")),
        ("osr_performance_ordinary_pct", _r(r"performance.*ordinary")), ("osr_performance_fif_pct", _r(r"performance.*(fif|a-?i-?a)")),
        ("osr_performance_total_pct", _r(r"performance.*total")),
    ]),
    TableSpec("exchequer", _r(r"Approved Budget Estimates and Exchequer Issues"), [
        ("budget_recurrent", _r(r"^recurrent estimates")), ("budget_development", _r(r"^development estimates")),
        ("budget_total", _r(r"^total (approved )?estimates")),
        ("exchequer_issues_recurrent", _r(r"^recurrent exchequer")), ("exchequer_issues_development", _r(r"^development exchequer")),
        ("exchequer_issues_total", _r(r"^totals? (exchequer )?issues")),
    ], optional=True),
    TableSpec("trade_payables", _r(r"^Trade Payables for the Counties"), [
        ("pending_bills_executive", _r(r"county executive.*sub-?total")), ("pending_bills_assembly", _r(r"county assembly.*sub-?total")),
        ("pending_bills_total", _r(r"^grand total")),
    ]),
]


def column_headers(rows: list[list[str]], n_header: int) -> list[str]:
    """Full header text per column. Group headers in the first row span right over blank
    (merged) cells, so 'Budget Estimates | | |' labels three sub-columns."""
    width = max(len(r) for r in rows[:n_header]) if n_header else 0
    out = [""] * width
    for ri, row in enumerate(rows[:n_header]):
        carry = ""
        for ci in range(width):
            cell = row[ci].strip() if ci < len(row) else ""
            if ri == 0:
                carry = cell or carry
                cell = cell or carry
            out[ci] = f"{out[ci]} {cell}".strip()
    return out


def unit_multiplier(header: str, default: tuple[float, str] | None = None) -> tuple[float, str]:
    """Unit printed in a column header; else the table's default; else plain shillings."""
    h = header.lower().replace(" ", "")
    if "billion" in h or re.search(r"kshs?\.?bn\b", h):
        return 1e9, "Kshs. Billion"
    if "million" in h or re.search(r"kshs?\.?mn\b", h):
        return 1e6, "Kshs. Million"
    if re.search(r"\(kshs?\.?\)", h):
        return 1.0, "Kshs."
    return default or (1.0, "Kshs.")


@dataclass
class ExtractedRow:
    county_code: int | None
    label: str
    page: int
    raw: list[str]
    values: dict[str, tuple[float, str, str]]  # metric -> (value, printed text, printed unit)
    problems: list[tuple[str, str]]  # (check_name, reason)


def extract(lt: LogicalTable, spec: TableSpec) -> tuple[list[ExtractedRow], dict[str, Any]]:
    """Map a logical table onto metrics. Returns rows plus table-level check results."""
    rows = lt.rows
    fi = find_formula_row(rows)
    n_header = len(lt.header_rows)
    headers = column_headers(rows, fi if fi is not None else n_header)
    mapping: dict[int, str] = {}
    for ci, h in enumerate(headers):
        for metric, pat in spec.columns:
            if pat.search(h) and metric not in mapping.values():
                mapping[ci] = metric
                break
    # Some tables print the unit on only some columns (2.3: "Recurrent Estimates" has none,
    # "Recurrent Exchequer Issues (Kshs.Mn)" does); use the table's printed unit for the rest.
    printed_units = {unit_multiplier(h) for h in headers if unit_multiplier(h, (0, "")) != (0, "")}
    table_unit = printed_units.pop() if len(printed_units) == 1 else None
    check = check_table(rows)
    bad_rows: dict[int, list[str]] = {}
    for issue in check.formula_failures:
        bad_rows.setdefault(issue.row_index, []).append(
            f"printed formula for column {issue.column} gives {issue.expected:,.2f}, table shows {issue.actual:,.2f}"
        )

    out: list[ExtractedRow] = []
    total_row = None
    for bi, row in enumerate(lt.body_rows):
        ri = n_header + bi
        label = row[0].strip()
        if re.match(r"^(grand\s+)?total", label, re.I):
            total_row = (ri, row)
            continue
        if not label and not any(c.strip() for c in row[1:]):
            continue
        county = match_county(label)
        values, problems = {}, []
        if county is None:
            problems.append(("county_name", f"row label {label!r} is not one of the 47 counties"))
        for ci, metric in mapping.items():
            text = row[ci].strip() if ci < len(row) else ""
            mult, unit_txt = (1.0, "%") if METRICS[metric].unit == "percent" else unit_multiplier(headers[ci], table_unit)
            v = parse_number(text)
            if v is None and text in ("-", "–", "—") and METRICS[metric].unit == "KES":
                v = 0.0  # accounting convention in these tables: a dash is nil
            if v is None:
                if text.lower() not in ("", "-", "–", "—", "n/a", "na", "n.a."):  # "n/a": not applicable, skip the cell
                    problems.append(("number_format", f"{metric}: cannot read {text!r} as a number"))
                continue
            values[metric] = (round(v * mult, 2), text, unit_txt)
        for reason in bad_rows.get(ri, []):
            problems.append(("row_formula", reason))
        out.append(ExtractedRow(county.code if county else None, label, lt.row_pages[bi], row, values, problems))

    counties = [r.county_code for r in out if r.county_code]
    dupes = sorted({c for c in counties if counties.count(c) > 1})
    for r in out:
        if r.county_code in dupes:
            r.problems.append(("duplicate_county", f"county appears {counties.count(r.county_code)} times in this table"))
    table_checks = {
        "mapped_columns": {headers[c]: m for c, m in mapping.items()},
        "unmapped_metrics": [m for m, _ in spec.columns if m not in mapping.values()],
        "county_rows": len(set(counties)),
        "missing_counties": sorted({c.name for c in COUNTIES} - {c.name for c in COUNTIES if c.code in counties}),
        "total_row_found": total_row is not None,
        "total_failures": [
            {"column": i.column, "sum_of_rows": i.expected, "printed_total": i.actual} for i in check.total_failures
        ],
        "formula_notes": check.notes,
    }
    return out, table_checks


# ------------------------------------------------------------------ loading


def _period_end(fiscal_year: str, period: str) -> date:
    start = int(fiscal_year[:4])
    return {"Q1": date(start, 9, 30), "H1": date(start, 12, 31), "9M": date(start + 1, 3, 31), "FY": date(start + 1, 6, 30)}[period]


def seed_dimensions(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO public_data.dim_county (county_id, name, region) VALUES (%s, %s, %s) "
            "ON CONFLICT (county_id) DO UPDATE SET name = EXCLUDED.name, region = EXCLUDED.region",
            [(c.code, c.name, c.region) for c in COUNTIES],
        )
        cur.executemany(
            "INSERT INTO public_data.dim_metric (metric, description, unit, cumulative) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (metric) DO UPDATE SET description = EXCLUDED.description, unit = EXCLUDED.unit, cumulative = EXCLUDED.cumulative",
            [(m.name, m.description, m.unit, m.cumulative) for m in METRICS.values()],
        )


def _period_id(conn: psycopg.Connection, fiscal_year: str, period: str) -> int:
    row = conn.execute(
        "INSERT INTO public_data.dim_period (fiscal_year, period_type, months, period_end) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (fiscal_year, period_type) DO UPDATE SET months = EXCLUDED.months RETURNING period_id",
        (fiscal_year, period, PERIOD_MONTHS[period], _period_end(fiscal_year, period)),
    ).fetchone()
    return row[0]


def load_document(conn: psycopg.Connection, document_json: Path, manifest_rec: dict[str, Any]) -> dict[str, Any]:
    """(Re)load one parsed report. Replaces anything previously loaded from the same file."""
    doc = json.loads(document_json.read_text(encoding="utf-8"))
    fy, period = manifest_rec["fiscal_year"], manifest_rec["period"]
    if not fy or period not in PERIOD_MONTHS:
        raise ValueError(f"{manifest_rec['file']}: fiscal year/period unknown ({fy}, {period})")
    seed_dimensions(conn)
    conn.execute("DELETE FROM public_data.source_document WHERE sha256 = %s", (manifest_rec["sha256"],))
    doc_id = conn.execute(
        "INSERT INTO public_data.source_document (title, fiscal_year, period_type, source_url, file_name, sha256, page_count, parse_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING doc_id",
        (manifest_rec["title"], fy, period, manifest_rec.get("url"), manifest_rec["file"], manifest_rec["sha256"], doc["page_count"], doc["parse_version"]),
    ).fetchone()[0]
    period_id = _period_id(conn, fy, period)

    stats = {"doc_id": doc_id, "tables": {}, "facts": 0, "quarantined": 0, "cross_table_mismatches": 0}
    loaded: dict[tuple[int, str], tuple[float, str, int]] = {}  # (county, metric) -> (value, table, page)
    for lt in logical_tables(doc["tables"]):
        title = lt.caption_title or ""
        spec = next((s for s in SPECS if s.title_re.search(title)), None)
        if spec is None or lt.county_code is not None:
            continue
        table_name = f"{lt.caption_number} {title}"
        rows, tchecks = extract(lt, spec)
        first_page = lt.pages[0] if lt.pages else None
        facts = quarantined = 0
        for r in rows:
            if r.problems:
                for check_name, reason in r.problems:
                    conn.execute(
                        "INSERT INTO public_data.quarantine_rows (source_doc_id, source_page, source_table, row_label, raw_row, check_name, reason) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (doc_id, r.page, table_name, r.label, Jsonb(r.raw), check_name, reason),
                    )
                quarantined += 1
                continue
            for metric, (value, printed, unit) in r.values.items():
                key = (r.county_code, metric)
                if key in loaded:
                    prev_value, prev_table, prev_page = loaded[key]
                    tol = 1.01 if METRICS[metric].unit == "percent" else max(1.0, abs(prev_value) * 0.001)
                    same = abs(prev_value - value) <= tol
                    conn.execute(
                        "INSERT INTO public_data.validation_results (check_name, scope, status, details, source_doc_id, source_page) "
                        "VALUES ('cross_table_agreement', %s, %s, %s, %s, %s)",
                        (f"{metric} / county {r.county_code}", "pass" if same else "fail",
                         Jsonb({"first": {"table": prev_table, "page": prev_page, "value": prev_value},
                                "second": {"table": table_name, "page": r.page, "value": value}}), doc_id, r.page),
                    )
                    stats["cross_table_mismatches"] += not same
                    continue
                conn.execute(
                    "INSERT INTO public_data.fact_county_budget (county_id, period_id, metric, value, source_doc_id, source_page, source_table, source_row, printed_value, printed_unit) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r.county_code, period_id, metric, value, doc_id, r.page, table_name, r.label, printed, unit),
                )
                loaded[key] = (value, table_name, r.page)
                facts += 1
        status = "pass" if tchecks["county_rows"] == 47 and not tchecks["total_failures"] and tchecks["total_row_found"] and not tchecks["unmapped_metrics"] else "fail"
        conn.execute(
            "INSERT INTO public_data.validation_results (check_name, scope, status, details, source_doc_id, source_page) "
            "VALUES ('table_structure', %s, %s, %s, %s, %s)",
            (table_name, status, Jsonb(tchecks), doc_id, first_page),
        )
        stats["tables"][table_name] = {"pages": lt.pages, "facts": facts, "quarantined_rows": quarantined, **{k: tchecks[k] for k in ("county_rows", "unmapped_metrics", "total_failures")}}
        stats["facts"] += facts
        stats["quarantined"] += quarantined
    missing_specs = [s.key for s in SPECS if not s.optional and not any(s.title_re.search(t.split(" ", 1)[1]) for t in stats["tables"])]
    if missing_specs:
        conn.execute(
            "INSERT INTO public_data.validation_results (check_name, scope, status, details, source_doc_id) VALUES ('expected_tables', %s, 'warn', %s, %s)",
            (manifest_rec["file"], Jsonb({"not_found": missing_specs}), doc_id),
        )
    stats["missing_specs"] = missing_specs
    return stats


CONSISTENCY_SQL = """
WITH x AS (
    SELECT f.county_id, c.name AS county, f.metric, p.fiscal_year, p.period_type, p.months, f.value,
           f.source_doc_id, f.source_page,
           lag(f.value)       OVER w AS prev_value,
           lag(p.period_type) OVER w AS prev_period,
           lag(f.source_page) OVER w AS prev_page,
           lag(f.source_doc_id) OVER w AS prev_doc
    FROM public_data.fact_county_budget f
    JOIN public_data.dim_period p USING (period_id)
    JOIN public_data.dim_metric m USING (metric)
    JOIN public_data.dim_county c USING (county_id)
    WHERE m.cumulative
    WINDOW w AS (PARTITION BY f.county_id, f.metric, p.fiscal_year ORDER BY p.months)
)
SELECT county_id, county, metric, fiscal_year, prev_period, period_type, prev_value, value,
       prev_doc, prev_page, source_doc_id, source_page
FROM x WHERE prev_value IS NOT NULL
"""


def consistency_check(conn: psycopg.Connection) -> dict[str, Any]:
    """Cumulative (year-to-date) metrics must not decrease H1 -> 9M -> FY within a fiscal year."""
    conn.execute("DELETE FROM public_data.validation_results WHERE check_name = 'cumulative_non_decreasing'")
    pairs = conn.execute(CONSISTENCY_SQL).fetchall()
    fails = []
    for (cid, county, metric, fy, p0, p1, v0, v1, d0, pg0, d1, pg1) in pairs:
        v0, v1 = float(v0), float(v1)
        ok = v1 >= v0 - max(1.0, abs(v0) * 0.0005)
        if not ok:
            fails.append((county, metric, fy, p0, p1, float(v0), float(v1), pg0, pg1))
        conn.execute(
            "INSERT INTO public_data.validation_results (check_name, scope, status, details, source_doc_id, source_page) "
            "VALUES ('cumulative_non_decreasing', %s, %s, %s, %s, %s)",
            (f"{fy} {metric} {county} {p0}->{p1}", "pass" if ok else "fail",
             Jsonb({"earlier": {"period": p0, "value": float(v0), "doc_id": d0, "page": pg0},
                    "later": {"period": p1, "value": float(v1), "doc_id": d1, "page": pg1}}), d1, pg1),
        )
    return {"pairs_checked": len(pairs), "failures": fails}
