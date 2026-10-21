"""Visual QA: each extracted table next to an image of its source page(s).

Writes one self-contained HTML file per document to data/qa/<doc>.html. Detected table areas
are outlined on the page image; cells failing an arithmetic check are highlighted.

Usage (from backend/):
  python -m scripts.table_qa                       # every parsed document, default sample
  python -m scripts.table_qa --only 9M --random 10 --max-failing 40
  python -m scripts.table_qa --pages 55,56         # specific PDF pages
Default sample: every chapter-2 (all-county) table, every table failing a check (up to
--max-failing), and --random county tables (fixed seed, so reruns show the same tables).
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import random
import sys
from pathlib import Path

import pymupdf

from app.core.config import get_settings
from app.ingest.table_checks import TableCheck, check_table, find_formula_row, logical_tables

DPI = 72


def _page_image(doc: pymupdf.Document, pno: int) -> str:
    pix = doc[pno - 1].get_pixmap(dpi=DPI)
    return base64.b64encode(pix.tobytes("jpeg", jpg_quality=55)).decode()


def _cell_flags(rows: list[list[str]], check: TableCheck) -> dict[tuple[int, int], str]:
    flags: dict[tuple[int, int], str] = {}
    fi = find_formula_row(rows)
    if fi is None:
        return flags
    letter_col = {}
    for col, cell in enumerate(rows[fi]):
        c = cell.strip()
        if c:
            letter_col.setdefault(c[0].upper(), col)
    for issue in check.formula_failures + check.total_failures:
        col = letter_col.get(issue.column)
        if col is not None:
            flags[(issue.row_index, col)] = f"expected {issue.expected:,.2f}, found {issue.actual:,.2f}"
    return flags


def render(doc_json: Path, pdf: Path, out: Path, pages: set[int] | None, n_random: int, max_failing: int) -> dict:
    data = json.loads(doc_json.read_text(encoding="utf-8"))
    frags = {t["table_id"]: t for t in data["tables"]}
    lts = logical_tables(data["tables"])
    checks = {lt.key: check_table(lt.rows) for lt in lts}

    if pages:
        chosen = [lt for lt in lts if set(lt.pages) & pages]
    else:
        chapter2 = [lt for lt in lts if (lt.caption_number or "").startswith("2.")]
        failing = [lt for lt in lts if not checks[lt.key].ok and lt not in chapter2][:max_failing]
        rest = [lt for lt in lts if lt not in chapter2 and lt not in failing and lt.county_code]
        chosen = chapter2 + failing + random.Random(42).sample(rest, min(n_random, len(rest)))
        chosen.sort(key=lambda lt: lt.pages[0] if lt.pages else 0)

    page_w = None
    parts = []
    with pymupdf.open(pdf) as doc:
        page_w, page_h = doc[0].rect.width, doc[0].rect.height
        img_cache: dict[int, str] = {}
        for lt in chosen:
            c = checks[lt.key]
            status = "pass" if c.ok and c.formula_row is not None else ("fail" if not c.ok else "unchecked")
            imgs = []
            for pno in lt.pages or [frags[lt.fragment_ids[0]]["page"]]:
                img = img_cache.setdefault(pno, _page_image(doc, pno))
                boxes = "".join(
                    f'<div class="box" style="left:{f["bbox"][0] / page_w:.2%};top:{f["bbox"][1] / page_h:.2%};'
                    f'width:{(f["bbox"][2] - f["bbox"][0]) / page_w:.2%};height:{(f["bbox"][3] - f["bbox"][1]) / page_h:.2%}"></div>'
                    for f in (frags[i] for i in lt.fragment_ids) if f["page"] == pno
                )
                imgs.append(f'<figure><div class="page"><img loading="lazy" src="data:image/jpeg;base64,{img}">{boxes}</div><figcaption>PDF page {pno}</figcaption></figure>')
            flags = _cell_flags(lt.rows, c)
            n_header = len(lt.header_rows)
            trs = []
            for ri, row in enumerate(lt.rows):
                tds = []
                for ci, cell in enumerate(row):
                    tag = "th" if ri < n_header else "td"
                    flag = flags.get((ri, ci))
                    attr = f' class="bad" title="{html.escape(flag)}"' if flag else ""
                    tds.append(f"<{tag}{attr}>{html.escape(cell)}</{tag}>")
                page_note = f'<td class="pg">p{lt.row_pages[ri - n_header]}</td>' if ri >= n_header else "<th></th>"
                trs.append(f"<tr>{''.join(tds)}{page_note}</tr>")
            issues = "".join(
                f"<li>row “{html.escape(i.label)}” column {i.column}: expected {i.expected:,.2f}, found {i.actual:,.2f}</li>"
                for i in (c.formula_failures + c.total_failures)[:12]
            )
            more = len(c.formula_failures + c.total_failures) - 12
            notes = "".join(f"<li class='note'>{html.escape(n)}</li>" for n in c.notes)
            county = f" · county code {lt.county_code}" if lt.county_code else ""
            parts.append(
                f'<section class="{status}"><h2><span class="tag">{status}</span> Table {html.escape(lt.caption_number or "?")}: '
                f'{html.escape(lt.caption_title or "(no caption)")}</h2>'
                f'<p class="meta">pages {", ".join(map(str, lt.pages))} · {len(lt.body_rows)} rows · {len(lt.fragment_ids)} fragment(s)'
                f'{county} · {c.rows_checked} rows formula-checked</p>'
                f'<ul>{issues}{f"<li>… {more} more</li>" if more > 0 else ""}{notes}</ul>'
                f'<div class="pair"><div class="imgs">{"".join(imgs)}</div><div class="tbl"><table>{"".join(trs)}</table></div></div></section>'
            )

    n_fail = sum(1 for lt in lts if not checks[lt.key].ok)
    n_checked = sum(1 for lt in lts if checks[lt.key].formula_row is not None)
    summary = (
        f"{data['page_count']} pages · {len(data['tables'])} table fragments → {len(lts)} tables · "
        f"{n_checked} with a formula row · {n_fail} failing a check · showing {len(chosen)}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.format(title=html.escape(data["source"]), summary=summary, body="".join(parts)), encoding="utf-8")
    return {"tables": len(lts), "checked": n_checked, "failing": n_fail, "shown": len(chosen), "out": str(out)}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Table QA</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--bad:#fde2e1;--badb:#d93025;--ok:#1e8e3e;--warn:#b06000;--th:#f3f3f3}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#161616;--fg:#eee;--muted:#aaa;--line:#333;--bad:#5c1d1b;--badb:#ff6b5f;--ok:#5bd17a;--warn:#f0a050;--th:#222}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}}
h1{{font-size:18px;margin:0 0 4px}} .sum{{color:var(--muted);margin:0 0 16px}}
section{{border-top:1px solid var(--line);padding:12px 0}} h2{{font-size:15px;margin:0 0 4px}}
.meta{{color:var(--muted);margin:0 0 6px;font-size:12px}} ul{{margin:4px 0 8px;padding-left:18px;font-size:12px}} .note{{color:var(--warn)}}
.tag{{font-size:11px;text-transform:uppercase;padding:1px 6px;border-radius:3px;border:1px solid}}
.pass .tag{{color:var(--ok)}} .fail .tag{{color:var(--badb)}} .unchecked .tag{{color:var(--muted)}}
.pair{{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}} .imgs{{flex:0 1 460px;min-width:0}} .tbl{{flex:1 1 420px;overflow-x:auto;min-width:0}}
figure{{margin:0 0 8px}} figcaption{{font-size:11px;color:var(--muted)}} .page{{position:relative}} .page img{{width:100%;display:block;border:1px solid var(--line)}}
.box{{position:absolute;outline:2px solid #e8710a;background:rgba(232,113,10,.08)}}
table{{border-collapse:collapse;font-size:11px}} td,th{{border:1px solid var(--line);padding:2px 4px;vertical-align:top}} th{{background:var(--th)}}
td:not(:first-child){{text-align:right;font-variant-numeric:tabular-nums}} .bad{{background:var(--bad);outline:1px solid var(--badb)}} .pg{{color:var(--muted)}}
</style></head><body><h1>{title}</h1><p class="sum">{summary}</p>{body}</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only")
    ap.add_argument("--pages", help="comma-separated PDF page numbers")
    ap.add_argument("--random", type=int, default=6)
    ap.add_argument("--max-failing", type=int, default=25)
    args = ap.parse_args()
    data_dir = get_settings().data_dir
    pages = {int(p) for p in args.pages.split(",")} if args.pages else None
    for doc_json in sorted((data_dir / "parsed").glob("*/document.json")):
        if args.only and args.only not in doc_json.parent.name:
            continue
        source = json.loads(doc_json.read_text(encoding="utf-8"))["source"]
        pdf = data_dir / "raw" / source
        res = render(doc_json, pdf, data_dir / "qa" / f"{doc_json.parent.name}.html", pages, args.random, args.max_failing)
        print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
