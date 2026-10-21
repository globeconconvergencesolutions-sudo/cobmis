"""Generic arithmetic checks on extracted tables, driven by the tables' own formula rows.

Many COB tables carry a row like  ["", "A", "B", "C=A+B", "D", "E", "F=D+E", "G=D/A 100", ...]
naming each numeric column and how derived columns are computed. Each data row can then
be checked against its own printed formulas, which catches extraction errors (a lost digit,
a shifted column) without knowing anything else about the table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM_RE = re.compile(r"^\(?-?\s*[\d,]*\.?\d+\)?%?$")
_LETTER_RE = re.compile(r"^[A-Za-z]$")
_FORMULA_RE = re.compile(r"^([A-Za-z])\s*=\s*(.+)$")
_TOTAL_RE = re.compile(r"^\s*(grand\s+)?total", re.I)


def parse_number(text: str | None) -> float | None:
    """'5,793.60' -> 5793.6; '(1,200)' and '-1,200' -> -1200; '-' or '' -> None."""
    if text is None:
        return None
    s = text.strip().replace(" ", "").replace("−", "-")
    if s in ("", "-", "–", "—", "N/A", "n/a", "NA"):
        return None
    if not _NUM_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = s.strip("()%").lstrip("-").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def find_formula_row(rows: list[list[str]], max_header_rows: int = 5) -> int | None:
    """Index of the row that labels columns with single letters / formulas, if any."""
    for i, row in enumerate(rows[:max_header_rows]):
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) >= 2 and all(_LETTER_RE.match(c) or _FORMULA_RE.match(c) for c in cells):
            if any(_FORMULA_RE.match(c) for c in cells):
                return i
    return None


def _compile(expr: str) -> str | None:
    """'D/A 100' / 'D/A*100' / 'B/D/6' / 'a-b-c+d' -> python expression over letters."""
    e = expr.strip().replace("x", "*").replace("×", "*")
    e = re.sub(r"\s*\*?\s*100\s*$", "*100", e) if re.search(r"/\s*[A-Za-z]\s*\*?\s*100\s*$", e) else e
    e = re.sub(r"\s+", "", e)
    if not re.fullmatch(r"[A-Za-z0-9+\-*/().]+", e):
        return None
    return e


@dataclass
class RowIssue:
    row_index: int
    label: str
    column: str
    expected: float
    actual: float


@dataclass
class TableCheck:
    formula_row: int | None = None
    rows_checked: int = 0
    formula_failures: list[RowIssue] = field(default_factory=list)
    total_failures: list[RowIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.formula_failures and not self.total_failures


def _tolerance(expected: float, is_pct: bool) -> float:
    # Percentages are printed rounded to whole numbers; amounts to 2 dp, sometimes rounded in
    # the source. Allow 1 point for percentages, 0.5% (min 1.0) for amounts.
    return 1.01 if is_pct else max(1.0, abs(expected) * 0.005)


def check_table(rows: list[list[str]]) -> TableCheck:
    out = TableCheck()
    fi = find_formula_row(rows)
    if fi is None:
        out.notes.append("no formula row")
        return out
    out.formula_row = fi
    labels: dict[str, int] = {}
    formulas: dict[str, str] = {}
    duplicated: set[str] = set()
    for col, cell in enumerate(rows[fi]):
        c = cell.strip()
        m = _FORMULA_RE.match(c)
        letter = c.upper() if _LETTER_RE.match(c) else (m.group(1).upper() if m else None)
        if letter is None:
            continue
        if letter in labels:
            duplicated.add(letter)
        labels[letter] = col
        if m and (expr := _compile(m.group(2).upper())):
            formulas[letter] = expr
    if duplicated:
        # A source labelling error (e.g. "F=D+E ... F=E/B*100"): any formula that defines or
        # uses a reused letter is ambiguous, so it is not checked.
        out.notes.append(f"source formula row reuses letter(s) {','.join(sorted(duplicated))}; those formulas skipped")
        formulas = {t: e for t, e in formulas.items() if t not in duplicated and not (set(re.findall(r"[A-Z]", e)) & duplicated)}

    # A printed formula that contradicts its own column header is a source labelling error
    # (e.g. "Revenue Performance (%)" labelled "E=A-D"): skip it rather than fail every row.
    for target in list(formulas):
        col = labels[target]
        head = " ".join(r[col] for r in rows[:fi] if col < len(r)).lower()
        is_pct_formula = "*100" in formulas[target]
        if (re.search(r"%|per\s*cent|performance", head) and not is_pct_formula) or (re.search(r"variance", head) and is_pct_formula):
            out.notes.append(f"source formula {target}={formulas[target]} contradicts column header {head.strip()!r}; skipped")
            del formulas[target]

    data_rows = [(i, r) for i, r in enumerate(rows) if i > fi]
    pct_letters = {t for t, e in formulas.items() if "*100" in e}

    def cell_value(letter: str, text: str) -> float | None:
        v = parse_number(text)
        if v is None and text.strip() in ("-", "–", "—") and letter not in pct_letters:
            return 0.0  # a dash is nil in amount columns
        return v

    for i, row in data_rows:
        vals = {k: cell_value(k, row[c]) if c < len(row) else None for k, c in labels.items()}
        label = row[0].strip() or (row[1].strip() if len(row) > 1 else "")
        checked = False
        for target, expr in formulas.items():
            needed = set(re.findall(r"[A-Z]", expr))
            target_text = row[labels[target]].strip() if labels[target] < len(row) else ""
            if parse_number(target_text) is None:
                continue  # a dash or blank result means "not computed", not zero
            if any(vals.get(n) is None for n in needed):
                continue
            try:
                expected = eval(expr, {"__builtins__": {}}, {k: vals[k] for k in needed})  # noqa: S307 - expr is [A-Z0-9+-*/().] only
            except ZeroDivisionError:
                continue
            checked = True
            is_pct = "*100" in expr
            if abs(expected - vals[target]) > _tolerance(expected, is_pct):
                # Some tables print subtracted amounts in brackets, e.g. arrears paid
                # "(4,740,887)" under "f=a-b-c+d". Accept the formula on absolute values.
                alt = eval(expr, {"__builtins__": {}}, {k: abs(vals[k]) for k in needed})  # noqa: S307
                if abs(alt - vals[target]) <= _tolerance(alt, is_pct):
                    if "sign convention: bracketed amounts" not in out.notes:
                        out.notes.append("sign convention: bracketed amounts")
                    continue
                out.formula_failures.append(RowIssue(i, label, target, round(expected, 2), vals[target]))
        out.rows_checked += checked

    # Total row = sum of the rows above it (for plain amount columns, i.e. not % columns).
    def header_text(col: int) -> str:
        return " ".join(r[col] for r in rows[:fi] if col < len(r)).lower()

    pct_cols = {t for t, e in formulas.items() if "*100" in e} | duplicated
    pct_cols |= {t for t, col in labels.items() if re.search(r"%|per\s*cent|performance|absorption|rate", header_text(col))}
    total_idx = [i for i, r in data_rows if _TOTAL_RE.match(r[0] or "") and not re.search(r"sub", r[0], re.I)]
    if total_idx:
        ti = total_idx[-1]
        body = [r for i, r in data_rows if i < ti and not _TOTAL_RE.match(r[0] or "") and not re.search(r"sub-?total", " ".join(r[:2]), re.I)]
        for letter, col in labels.items():
            if letter in pct_cols:
                continue
            nums = [parse_number(r[col]) for r in body if col < len(r)]
            nums = [n for n in nums if n is not None]
            total = parse_number(rows[ti][col]) if col < len(rows[ti]) else None
            if total is None or len(nums) < 2:
                continue
            if abs(sum(nums) - total) > max(1.0, abs(total) * 0.001):
                out.total_failures.append(RowIssue(ti, rows[ti][0], letter, round(sum(nums), 2), total))
    return out


@dataclass
class LogicalTable:
    """A table as printed, possibly spanning several pages, with the PDF page of every row."""

    key: str
    caption_number: str | None
    caption_title: str | None
    county_code: int | None
    header_rows: list[list[str]]
    body_rows: list[list[str]]
    row_pages: list[int]  # PDF page (1-based) of each body row
    fragment_ids: list[str]

    @property
    def rows(self) -> list[list[str]]:
        return self.header_rows + self.body_rows

    @property
    def pages(self) -> list[int]:
        return sorted(set(self.row_pages)) or []


def _header_len(rows: list[list[str]]) -> int:
    """Header = everything up to and including the formula row; else rows before the first
    row with a number in it (max 3)."""
    fi = find_formula_row(rows)
    if fi is not None:
        return fi + 1
    for i, r in enumerate(rows[:4]):
        if any(parse_number(c) is not None for c in r[1:]):
            return max(1, i) if i else 1
    return 1


def logical_tables(tables: list[dict]) -> list[LogicalTable]:
    """Merge continuation fragments (from parse.assemble) into whole tables."""
    out: list[LogicalTable] = []
    by_id: dict[str, LogicalTable] = {}
    last_on_page: dict[int, LogicalTable] = {}
    for t in tables:
        rows = t["rows"]
        hl = _header_len(rows)
        parent = last_on_page.get(t["continued_from_page"]) if t.get("continued_from_page") else None
        if parent is not None and parent.caption_number == t.get("caption_number"):
            # Continuation pages repeat the header; drop it if it matches.
            norm = lambda r: [re.sub(r"\s+", "", c).lower() for c in r]  # noqa: E731
            body = rows[hl:] if [norm(r) for r in rows[:hl]] == [norm(r) for r in parent.header_rows[:hl]] else rows
            parent.body_rows += body
            parent.row_pages += [t["page"]] * len(body)
            parent.fragment_ids.append(t["table_id"])
            lt = parent
        else:
            county = (t.get("county_section") or {}).get("code")
            lt = LogicalTable(
                key=t["table_id"], caption_number=t.get("caption_number"), caption_title=t.get("caption_title"),
                county_code=county, header_rows=rows[:hl], body_rows=rows[hl:],
                row_pages=[t["page"]] * (len(rows) - hl), fragment_ids=[t["table_id"]],
            )
            out.append(lt)
        by_id[t["table_id"]] = lt
        last_on_page[t["page"]] = lt
    return out
