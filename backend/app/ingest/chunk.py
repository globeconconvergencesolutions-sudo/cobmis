"""Turn a parsed report into retrievable chunks.

Narrative: ~600 tokens (configurable), ~12% overlap, broken at section headings and sentence
           ends, never mid-sentence. Text inside tables is excluded (it is in table chunks).
Tables:    one chunk per logical table (caption + header + rows as markdown). A table too big
           for one chunk is split between rows only, each part repeating the header.
Every chunk gets a short context header (report, FY, period, county, section/table) that is
prepended before embedding.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.ingest.counties import BY_CODE, match_county
from app.ingest.table_checks import logical_tables

PERIOD_NAMES = {"Q1": "First Quarter", "H1": "First Half", "9M": "First Nine Months", "FY": "Full Year"}
NOISE_LINE = re.compile(
    r"^\s*(source\s*:.*|(FY \d{4}/\d{2} )?COUNTY GOVERNMENTS BUDGET IMPLEMENTATION REVIEW REPORT.*|Figure\s+\d+(\.\d+)*\s*[:.].*)$",
    re.I,
)
SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z(“\"0-9])")


def estimate_tokens(text: str) -> int:
    # bge-m3 / Qwen tokenisers average ~1.3 tokens per English word; numbers split more.
    return int(len(text.split()) * 1.35) + 1


@dataclass
class Chunk:
    kind: str
    page_start: int
    page_end: int
    section_title: str | None
    county_id: int | None
    table_ref: str | None
    context_header: str
    text: str

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.context_header + " " + self.text)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.context_header}\n{self.text}".encode()).hexdigest()

    @property
    def embed_input(self) -> str:
        return f"{self.context_header}\n\n{self.text}"


def _context(fy: str | None, period: str | None, county_id: int | None, where: str | None) -> str:
    parts = ["County Governments Budget Implementation Review Report"]
    if fy:
        parts.append(f"FY {fy}")
    if period:
        parts.append(PERIOD_NAMES.get(period, period))
    if county_id:
        parts.append(f"{BY_CODE[county_id].name} County")
    if where:
        parts.append(where)
    return ", ".join(parts)


def _join_lines(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        else:
            out = f"{out} {ln}" if out else ln
    return out


def narrative_chunks(doc: dict, fy: str | None, period: str | None, target: int = 600, overlap: float = 0.12) -> list[Chunk]:
    # 1. Walk pages line by line, cutting a new "section run" at each heading.
    runs: list[dict] = []  # {section, county, sentences: [(text, page)]}
    cur = None
    for p in doc["pages"]:
        if p.get("needs_ocr"):
            continue
        headings = {h["title"].strip(): h for h in p["headings"]}
        county = (p.get("county_section") or {}).get("code")
        section = p.get("section_start")
        buf: list[str] = []

        def flush():
            nonlocal buf
            if buf and cur is not None:
                text = _join_lines(buf)
                for s in SENTENCE_END.split(text):
                    if s.strip():
                        cur["sentences"].append((s.strip(), p["page"]))
            buf = []

        if cur is None:
            cur = {"section": section, "county": county, "sentences": []}
            runs.append(cur)
        for line in p["text"].split("\n"):
            stripped = re.sub(r"^\d+(\.\d+)*\.?\s+", "", line.replace(" ", " ")).strip()
            if stripped in headings or line.strip() in headings:
                flush()
                h = headings.get(stripped) or headings.get(line.strip())
                title = f'{h["number"]} {h["title"]}' if h["number"] else h["title"]
                m = re.search(r"County Government of (.+)$", h["title"], re.I)
                if m and (c := match_county(m.group(1))):
                    county = c.code  # a new county section can start mid-page
                elif h["level"] <= 2 and not (h["number"] or "").startswith("3"):
                    county = None
                county_now = county
                cur = {"section": title, "county": county_now, "sentences": []}
                runs.append(cur)
                continue
            if re.match(r"^\s*Table\s+\d+(\.\d+)*\s*[:.]", line):
                continue  # captions belong to table chunks
            if "....." in line or "…" * 3 in line:
                continue  # contents / list-of-tables lines with dot leaders
            if NOISE_LINE.match(line):
                continue  # figure source notes and the running title repeated inside charts
            buf.append(line)
        flush()

    # 2. Pack sentences into chunks of ~target tokens with ~overlap carried forward.
    chunks: list[Chunk] = []
    for run in runs:
        sents = run["sentences"]
        if not sents:
            continue
        i = 0
        while i < len(sents):
            size, j = 0, i
            while j < len(sents) and (size == 0 or size + estimate_tokens(sents[j][0]) <= target):
                size += estimate_tokens(sents[j][0])
                j += 1
            body = " ".join(s for s, _ in sents[i:j])
            if estimate_tokens(body) >= 25 or (i == 0 and j == len(sents)):
                chunks.append(Chunk(
                    "narrative", sents[i][1], sents[j - 1][1], run["section"], run["county"], None,
                    _context(fy, period, run["county"], run["section"]), body,
                ))
            if j >= len(sents):
                break
            # step back so the next chunk starts with ~overlap of this one
            back, k = 0, j
            while k > i + 1 and back < target * overlap:
                k -= 1
                back += estimate_tokens(sents[k][0])
            i = k
    return chunks


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "/").replace("\n", " ").strip() for c in cells) + " |"


def table_chunks(doc: dict, fy: str | None, period: str | None, max_tokens: int = 900) -> list[Chunk]:
    out = []
    for lt in logical_tables(doc["tables"]):
        caption = f"Table {lt.caption_number}: {lt.caption_title}" if lt.caption_number else "Untitled table"
        width = max(len(r) for r in lt.rows)
        header = [_md_row(r + [""] * (width - len(r))) for r in lt.header_rows]
        header.insert(1, "|" + "---|" * width)
        parts: list[tuple[list[str], list[int]]] = [([], [])]
        base = estimate_tokens(caption + " ".join(header))
        size = base
        for row, page in zip(lt.body_rows, lt.row_pages):
            line = _md_row(row + [""] * (width - len(row)))
            t = estimate_tokens(line)
            if parts[-1][0] and size + t > max_tokens:
                parts.append(([], []))
                size = base
            parts[-1][0].append(line)
            parts[-1][1].append(page)
            size += t
        for n, (lines, pages) in enumerate(parts):
            if not lines and lt.body_rows:
                continue
            pages = pages or lt.pages or [0]
            label = caption + (f" (part {n + 1} of {len(parts)})" if len(parts) > 1 else "")
            out.append(Chunk(
                "table", min(pages), max(pages), None, lt.county_code, lt.caption_number,
                _context(fy, period, lt.county_code, caption), "\n".join([label, *header, *lines]),
            ))
    return out


def chunk_document(doc: dict, fy: str | None, period: str | None, target: int = 600) -> list[Chunk]:
    return narrative_chunks(doc, fy, period, target) + table_chunks(doc, fy, period)
