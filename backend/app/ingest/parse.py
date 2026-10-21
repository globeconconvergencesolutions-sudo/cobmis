"""Phase 2 PDF parser for COB budget implementation review reports.

Two passes:
  1. parse_page(): stateless, per page, run in parallel worker processes, in batches.
     Extracts narrative text (outside tables, without the running footer), headings,
     table captions and tables (PyMuPDF ruling-line detection for the cell grid, with
     cell text rebuilt from character positions; PyMuPDF's own cell text drops word
     spaces and splits "5,793.60" into "579360\\n, .").
  2. assemble(): sequential, adds context that crosses pages: the current section and
     county (from "3.N County Government of X" headings), and links caption-less tables
     on following pages to the table they continue.

Output, per document, under data/parsed/<doc_key>/:
  batches/<first>-<last>.json   one file per batch of pages (the resume unit)
  document.json                 assembled pages + tables, written once all batches exist
Re-running skips finished batches; a different PARSE_VERSION invalidates them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pymupdf

from app.ingest.counties import county_mentions, match_county

log = logging.getLogger("cob.parse")

PARSE_VERSION = 3  # 3: typographic ligatures expanded
FOOTER_BAND = 50  # points from the bottom edge: page number + running title live here
MIN_TEXT_CHARS = 200  # below this, a page is treated as image-only (OCR candidate)

CAPTION_RE = re.compile(r"^\s*Table\s+(\d+(?:\.\d+)*)\s*[:.]\s*(.+)$")
NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
COUNTY_SECTION_RE = re.compile(r"County\s+Government\s+of\s+(.+)$", re.I)
ROMAN_RE = re.compile(r"^[ivxlc]+$", re.I)
# InDesign emits ligature glyphs ("Ofﬁce"): expand them so search and embeddings see plain words.
LIGATURES = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"})
DEFAULT_MAX_WORKERS = 4  # each worker holds a full report in memory; more rarely helps on a laptop


# ---------------------------------------------------------------- character helpers


def _page_chars(page: pymupdf.Page) -> list[tuple[str, float, float, float, float, float]]:
    """(char, x0, y0, x1, origin_y, size) for every glyph on the page."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                size = span["size"]
                for ch in span["chars"]:
                    x0, y0, x1, _ = ch["bbox"]
                    out.append((ch["c"].translate(LIGATURES), x0, y0, x1, ch["origin"][1], size))
    return out


def _group_lines(chars) -> list[list]:
    """Group glyphs into lines by baseline; each line sorted left to right."""
    chars = sorted(chars, key=lambda c: (c[4], c[1]))
    lines: list[list] = []
    for c in chars:
        if lines and abs(lines[-1][0][4] - c[4]) <= max(1.5, 0.25 * c[5]):
            lines[-1].append(c)
        else:
            lines.append([c])
    for line in lines:
        line.sort(key=lambda c: c[1])
    return lines


def _line_words(line: list) -> list[tuple[str, float, float, float, float]]:
    """Split a line of glyphs into words: (text, x0, y0, x1, baseline).

    InDesign output often has no space glyphs between words, so a horizontal gap wider than
    ~18% of the font size is read as a word break.
    """
    words = []
    cur: list = []
    for c in line:
        if c[0].isspace():
            if cur:
                words.append(cur)
            cur = []
            continue
        if cur and c[1] - cur[-1][3] > 0.18 * c[5]:
            words.append(cur)
            cur = []
        cur.append(c)
    if cur:
        words.append(cur)
    return [("".join(g[0] for g in w), w[0][1], min(g[2] for g in w), w[-1][3], w[0][4]) for w in words]


def _join_lines(texts: Iterable[str]) -> str:
    out = ""
    for t in texts:
        if not t:
            continue
        if out.endswith("-") and t[:1].islower():
            out = out[:-1] + t  # re-join a hyphenated line break: "Per-" + "formance"
        else:
            out = f"{out} {t}" if out else t
    return out


def chars_to_text(chars: Iterable[tuple[str, float, float, float, float, float]]) -> str:
    """Rebuild text from glyphs: lines by baseline, words by gaps, hyphenated breaks re-joined."""
    return _join_lines(" ".join(w[0] for w in _line_words(line)) for line in _group_lines(chars))


def table_cell_texts(chars, cells: list[list[tuple | None]]) -> list[list[str]]:
    """Fill a cell grid with text, assigning whole words to the cell holding the word's centre.

    Assigning words rather than glyphs matters when a PDF's ruling lines are drawn slightly
    offset from its text: glyph-level assignment then splits "865,750,217" into
    "865,750,2" | "17".
    """
    words = [w for line in _group_lines(chars) for w in _line_words(line)]
    grid: list[list[list]] = [[[] for _ in row] for row in cells]
    flat = [(ri, ci, cell) for ri, row in enumerate(cells) for ci, cell in enumerate(row) if cell]
    for w in words:
        cx, cy = (w[1] + w[3]) / 2, (w[2] + w[4]) / 2
        hit = next((
            (ri, ci) for ri, ci, cell in flat if cell[0] <= cx <= cell[2] and cell[1] <= cy <= cell[3]
        ), None)
        if hit:
            grid[hit[0]][hit[1]].append(w)
    out = []
    for row in grid:
        texts = []
        for ws in row:
            lines: list[list] = []
            for w in sorted(ws, key=lambda w: (w[4], w[1])):
                if lines and abs(lines[-1][0][4] - w[4]) <= 2.0:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            texts.append(_join_lines(" ".join(x[0] for x in sorted(line, key=lambda x: x[1])) for line in lines))
        out.append(texts)
    return out


def _in_rect(c, rect) -> bool:
    cx, cy = (c[1] + c[3]) / 2, (c[2] + c[4]) / 2
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


# ---------------------------------------------------------------- per-page parse


def _lines(page: pymupdf.Page, footer_y: float) -> list[dict[str, Any]]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = re.sub(r"\s+", " ", "".join(s["text"] for s in line["spans"])).strip().translate(LIGATURES)
            bold = all(("Bold" in s["font"]) or (s["flags"] & 16) for s in spans)
            size = max(s["size"] for s in spans)
            out.append({"text": text, "bbox": line["bbox"], "bold": bool(bold), "size": size, "footer": line["bbox"][1] >= footer_y})
    return out


def _heading(line: dict[str, Any], body_size: float) -> dict[str, Any] | None:
    text = line["text"].replace(" ", " ")
    if not line["bold"] or line["size"] < body_size + 0.5 or len(text) > 160 or "...." in text:
        return None
    m = NUMBERED_HEADING_RE.match(text)
    if m and not CAPTION_RE.match(text):
        number = m.group(1)
        return {"number": number, "level": number.count(".") + 1, "title": m.group(2).strip()}
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) >= 4 and letters.isupper():
        return {"number": None, "level": 1, "title": text}
    return None


def parse_page(page: pymupdf.Page) -> dict[str, Any]:
    height = page.rect.height
    footer_y = height - FOOTER_BAND
    lines = _lines(page, footer_y)
    body_lines = [ln for ln in lines if not ln["footer"]]

    sizes = sorted(round(ln["size"], 1) for ln in body_lines if not ln["bold"])
    body_size = sizes[len(sizes) // 2] if sizes else 10.0

    printed = None
    for ln in lines:
        if ln["footer"] and (ln["text"].isdigit() or ROMAN_RE.match(ln["text"])):
            printed = ln["text"]

    chars = [c for c in _page_chars(page) if c[2] < footer_y]

    tables = []
    try:
        found = page.find_tables(strategy="lines").tables
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop a 900-page parse
        log.warning("page %d: table detection failed: %s", page.number + 1, exc)
        found = []
    for t in found:
        tb = t.bbox
        in_table = [c for c in chars if _in_rect(c, (tb[0] - 1, tb[1] - 1, tb[2] + 1, tb[3] + 1))]
        rows = table_cell_texts(in_table, [r.cells for r in t.rows])
        rows = [r for r in rows if any(x.strip() for x in r)]
        if len(rows) < 2 or t.col_count < 2:
            continue  # boxes and call-outs, not tables
        tables.append({"bbox": [round(v, 1) for v in t.bbox], "n_rows": len(rows), "n_cols": t.col_count, "rows": rows})
    tables.sort(key=lambda t: t["bbox"][1])

    def in_any_table(bbox) -> bool:
        cy = (bbox[1] + bbox[3]) / 2
        cx = (bbox[0] + bbox[2]) / 2
        return any(t["bbox"][0] - 2 <= cx <= t["bbox"][2] + 2 and t["bbox"][1] - 2 <= cy <= t["bbox"][3] + 2 for t in tables)

    captions, headings, text_lines = [], [], []
    pending_caption = None
    for ln in body_lines:
        if in_any_table(ln["bbox"]):
            continue
        txt = ln["text"].replace(" ", " ")
        m = CAPTION_RE.match(txt)
        if m and "...." not in txt:  # list-of-tables lines carry dot leaders
            pending_caption = {"number": m.group(1), "title": m.group(2).strip(), "y": ln["bbox"][3]}
            captions.append(pending_caption)
            text_lines.append(txt)
            continue
        if pending_caption and ln["bbox"][1] - pending_caption["y"] < 4 and not ln["bold"] and ln["size"] <= body_size:
            pending_caption["title"] += " " + txt  # caption wrapped onto a second line
            pending_caption["y"] = ln["bbox"][3]
        else:
            pending_caption = None
        h = _heading(ln, body_size)
        if h:
            h["y"] = round(ln["bbox"][1], 1)
            headings.append(h)
        text_lines.append(txt)

    # Caption for each table: the nearest caption above it with no other table in between.
    used = set()
    for i, t in enumerate(tables):
        top = t["bbox"][1]
        prev_bottom = tables[i - 1]["bbox"][3] if i else 0
        cands = [c for c in captions if prev_bottom - 2 <= c["y"] <= top + 2 and id(c) not in used]
        if cands:
            cap = max(cands, key=lambda c: c["y"])
            used.add(id(cap))
            t["caption_number"], t["caption_title"] = cap["number"], cap["title"]

    text = "\n".join(text_lines)
    return {
        "page": page.number + 1,
        "printed_page": printed,
        "text": text,
        "char_count": len(text),
        "needs_ocr": len(text) < MIN_TEXT_CHARS and bool(page.get_images()),
        "headings": headings,
        "captions": [{k: v for k, v in c.items() if k != "y"} for c in captions],
        "tables": tables,
        "county_mentions": county_mentions(text),
    }


def _parse_batch(pdf_path: str, first: int, last: int) -> list[dict[str, Any]]:
    with pymupdf.open(pdf_path) as doc:
        return [parse_page(doc[i]) for i in range(first, last + 1)]


# ---------------------------------------------------------------- resumable driver


def doc_key(pdf_path: Path) -> str:
    return pdf_path.stem[:90]


def parse_document(
    pdf_path: Path,
    out_root: Path,
    batch_size: int = 25,
    workers: int | None = None,
    max_pages: int | None = None,
) -> Path:
    out_dir = out_root / doc_key(pdf_path)
    batch_dir = out_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "parse_meta.json"
    if meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8")).get("parse_version") != PARSE_VERSION:
        for f in batch_dir.glob("*.json"):
            f.unlink()  # parser changed: old batches are stale
    with pymupdf.open(pdf_path) as doc:
        n_pages = doc.page_count
    if max_pages:
        n_pages = min(n_pages, max_pages)
    meta_path.write_text(json.dumps({"parse_version": PARSE_VERSION, "source": pdf_path.name, "page_count": n_pages}), encoding="utf-8")

    todo = []
    for first in range(0, n_pages, batch_size):
        last = min(first + batch_size, n_pages) - 1
        if not (batch_dir / f"{first + 1:04d}-{last + 1:04d}.json").exists():
            todo.append((first, last))
    log.info("%s: %d pages, %d/%d batches to parse", pdf_path.name, n_pages, len(todo), -(-n_pages // batch_size))

    workers = workers or max(1, min(DEFAULT_MAX_WORKERS, (os.cpu_count() or 2) - 1))
    t0 = time.time()
    failed: list[str] = []
    if todo:
        log.info("  parsing with %d worker processes (--workers to change)", workers)
        pages_todo = sum(l - f + 1 for f, l in todo)
        pages_done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_parse_batch, str(pdf_path), f, l): (f, l) for f, l in todo}
            for done, fut in enumerate(as_completed(futures), 1):
                first, last = futures[fut]
                name = f"{first + 1:04d}-{last + 1:04d}"
                try:
                    pages = fut.result()
                    tmp = batch_dir / f"{name}.tmp"
                    tmp.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(tmp.with_suffix(".json"))  # atomic: a batch file is either whole or absent
                except Exception as exc:  # noqa: BLE001 - keep the other batches; rerun retries this one
                    failed.append(name)
                    log.error("  batch %s (pages %d-%d) failed: %s: %s", name, first + 1, last + 1, type(exc).__name__, exc)
                    continue
                pages_done += last - first + 1
                elapsed = time.time() - t0
                rate = pages_done / elapsed if elapsed else 0.0
                eta = (pages_todo - pages_done) / rate if rate else 0.0
                log.info("  batch %d/%d (pages %d-%d) %.0fs, %.2f pages/s, ~%.0f min left",
                         done, len(todo), first + 1, last + 1, elapsed, rate, eta / 60)
    if failed:
        raise RuntimeError(
            f"{pdf_path.name}: {len(failed)} batch(es) failed ({', '.join(failed)}); the others are saved. "
            "Re-run the same command to retry only the failed batches."
        )

    pages = []
    for f in sorted(batch_dir.glob("*.json")):
        pages.extend(json.loads(f.read_text(encoding="utf-8")))
    pages = [p for p in pages if p["page"] <= n_pages]
    document = assemble(pages)
    document.update(parse_version=PARSE_VERSION, source=pdf_path.name, page_count=n_pages)
    out = out_dir / "document.json"
    out.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return out


# ---------------------------------------------------------------- sequential context pass


def _header_signature(rows: list[list[str]]) -> str:
    return "|".join(re.sub(r"\s+", "", c).lower() for c in rows[0])


def assemble(pages: list[dict[str, Any]]) -> dict[str, Any]:
    pages = sorted(pages, key=lambda p: p["page"])
    section: list[dict[str, Any]] = []  # heading stack
    county = None
    tables_out = []
    prev_table = None
    for p in pages:
        start_section = " > ".join(h["title"] if not h["number"] else f'{h["number"]} {h["title"]}' for h in section) or None
        p["section_start"] = start_section
        p["county_section"] = county
        # Walk headings and tables top to bottom, so a table printed above a mid-page
        # "3.N County Government of X" heading keeps the previous county.
        events = [(h["y"], 0, h) for h in p["headings"]] + [(t["bbox"][1], 1, t) for t in p["tables"]]
        for _, is_table, item in sorted(events, key=lambda e: (e[0], e[1])):
            if is_table:
                item["county_section"] = county
                continue
            h = item
            while section and section[-1]["level"] >= h["level"]:
                section.pop()
            section.append(h)
            m = COUNTY_SECTION_RE.search(h["title"])
            if h["number"] and m and (c := match_county(m.group(1))):
                county = {"code": c.code, "name": c.name}
            elif h["level"] <= 2 and not (h["number"] or "").startswith("3"):
                county = None  # left the per-county chapter
            if not p["county_section"] and county:
                p["county_section"] = county
        p["section_end"] = " > ".join(h["title"] if not h["number"] else f'{h["number"]} {h["title"]}' for h in section) or None

        for idx, t in enumerate(p["tables"]):
            t["page"] = p["page"]
            t["index_on_page"] = idx
            if "caption_number" in t:
                t["continued_from_page"] = None
            elif prev_table is not None and prev_table["page"] >= p["page"] - 1 and (
                prev_table["n_cols"] == t["n_cols"] or _header_signature(prev_table["rows"]) == _header_signature(t["rows"])
            ):
                t["caption_number"] = prev_table.get("caption_number")
                t["caption_title"] = prev_table.get("caption_title")
                t["continued_from_page"] = prev_table["page"]
            else:
                t["caption_number"] = t["caption_title"] = None
                t["continued_from_page"] = None
            t["table_id"] = f'p{p["page"]:04d}_t{idx}'
            tables_out.append(t)
            prev_table = t
        p["table_ids"] = [t["table_id"] for t in p["tables"]]
        del p["tables"]
    return {"pages": pages, "tables": tables_out}
