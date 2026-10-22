"""Turn SQL result rows into prose; forbid numbers not present in the result set."""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.ollama_client import OllamaClient

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

NARRATE_SYSTEM = """You answer questions about Kenyan county budget figures using ONLY the table result rows provided.

Rules:
- Use only values that appear in the rows. Never invent, round-invent, or estimate numbers.
- Copy figures as shown (you may add thousand separators). Do not compute new totals unless that total is already a row value.
- Cite source_title and source_page from the rows when present, like (source_title, p.N).
- Never mention SQL, databases, queries, or technical implementation — speak only about county figures and report sources.
- If the rows are empty, reply exactly: The loaded reports do not contain this information.
- Be concise: a short paragraph or a few bullets."""


def numbers_in_rows(columns: list[str], rows: list[list[Any]]) -> set[str]:
    """Normalised number tokens present in the result (commas stripped)."""
    out: set[str] = set()
    for row in rows:
        for v in row:
            if v is None:
                continue
            if isinstance(v, (int, float)):
                # Both int and float string forms for matching.
                out.add(str(int(v)) if float(v) == int(v) else str(v))
                out.add(f"{v:.2f}" if isinstance(v, float) else str(v))
                # Also plain str without trailing .0
                s = str(v)
                out.add(s.replace(",", ""))
                if "." in s:
                    out.add(s.rstrip("0").rstrip("."))
            else:
                for n in _NUM.findall(str(v)):
                    out.add(n.replace(",", ""))
    return {x for x in out if x}


def unsupported_numbers_in_text(text: str, columns: list[str], rows: list[list[Any]]) -> list[str]:
    allowed = numbers_in_rows(columns, rows)
    # Drop page cites and fiscal-year tokens (2024/25, FY 2025/26) from the scan.
    scrubbed = re.sub(r"\(p\.\d+\)", "", text)
    scrubbed = re.sub(r"\b20\d{2}\s*/\s*\d{2,4}\b", "", scrubbed)
    scrubbed = re.sub(r"\b20\d{2}\b", "", scrubbed)
    bad = []
    for n in _NUM.findall(scrubbed):
        key = n.replace(",", "")
        if len(key) <= 1:
            continue
        if key in allowed:
            continue
        try:
            fv = float(key)
        except ValueError:
            bad.append(n)
            continue
        if not _matches_allowed(fv, allowed):
            bad.append(n)
    return bad


def _num_eq(a: float, b_str: str) -> bool:
    try:
        return abs(a - float(b_str)) < 1e-6
    except ValueError:
        return False


def _matches_allowed(fv: float, allowed: set[str]) -> bool:
    """Exact match, or fv as millions/thousands of an allowed full-KES amount."""
    if any(_num_eq(fv, a) for a in allowed):
        return True
    for a in allowed:
        try:
            av = float(a)
        except ValueError:
            continue
        # Scaled forms common in COB prose (rounded to 2–3 decimals).
        if av >= 1_000_000 and abs(fv * 1_000_000_000 - av) / av < 0.002:
            return True
        if av >= 1000 and abs(fv * 1_000_000 - av) / av < 0.002:
            return True
        if av >= 1000 and abs(fv * 1_000 - av) / av < 0.002:
            return True
    return False


def narrate_rows(
    client: OllamaClient,
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    sql: str,
) -> tuple[str, list[str]]:
    """Return (answer_text, unsupported_number_tokens)."""
    if not rows:
        return "The loaded reports do not contain this information.", []

    table = {
        "columns": columns,
        "rows": rows[:50],
        "sql": sql,
    }
    messages = [
        {"role": "system", "content": NARRATE_SYSTEM},
        {
            "role": "user",
            "content": f"Question: {question}\n\nTable result (JSON):\n{json.dumps(table, default=str)}",
        },
    ]
    text = client.chat(messages, model=client.settings.sql_model, temperature=0.0).strip()
    bad = unsupported_numbers_in_text(text, columns, rows)
    return text, bad


def citations_from_rows(columns: list[str], rows: list[list[Any]]) -> list[dict]:
    idx = {c: i for i, c in enumerate(columns)}
    cites = []
    seen = set()
    for row in rows:
        title = row[idx["source_title"]] if "source_title" in idx else None
        page = row[idx["source_page"]] if "source_page" in idx else None
        table = row[idx["source_table"]] if "source_table" in idx else None
        url = row[idx["source_url"]] if "source_url" in idx else None
        key = (title, page, table)
        if title is None and page is None:
            continue
        if key in seen:
            continue
        seen.add(key)
        cites.append({
            "document": title,
            "pages": [page, page] if page is not None else None,
            "table": table,
            "url": url,
        })
    return cites
