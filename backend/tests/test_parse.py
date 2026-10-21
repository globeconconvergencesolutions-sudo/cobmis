import json

import pymupdf
import pytest

from app.ingest import parse as parse_mod
from app.ingest.counties import county_mentions, match_county
from app.ingest.parse import assemble, chars_to_text, parse_document, parse_page


def ch(text, x, y, size=8.0, w=4.0):
    """Glyphs laid out left to right from x on baseline y (no space glyphs, like InDesign)."""
    out = []
    for c in text:
        if c == " ":
            x += w * 1.5
            continue
        out.append((c, x, y - size, x + w, y, size))
        x += w
    return out


def test_chars_to_text_orders_by_x_and_adds_word_gaps():
    glyphs = ch("5,793.60", 100, 200)
    assert chars_to_text(list(reversed(glyphs))) == "5,793.60"
    assert chars_to_text(ch("Trans Nzoia", 10, 50)) == "Trans Nzoia"


def test_chars_to_text_joins_lines_and_hyphenation():
    glyphs = ch("Revenue Per-", 10, 50) + ch("formance(%)", 10, 60)
    assert chars_to_text(glyphs) == "Revenue Performance(%)"
    assert chars_to_text(ch("Budget", 10, 50) + ch("Estimates", 10, 60)) == "Budget Estimates"


@pytest.mark.parametrize(
    "raw,code",
    [("Tah raka-Nithi", 13), ("TransNzoia", 26), ("Nairobi City", 47), ("Nairobi", 47), ("Murang’a", 21),
     ("Elgeyo Marakwet", 28), ("Taita Taveta", 6), ("Homa Bay", 43), ("County Government of Nyamira", 46)],
)
def test_match_county(raw, code):
    assert match_county(raw).code == code


@pytest.mark.parametrize("raw", ["Total", "Grand Total", "Sub-Total", "numerous", "", "Kenya"])
def test_match_county_rejects_non_counties(raw):
    assert match_county(raw) is None


def test_county_mentions_word_boundaries():
    assert county_mentions("Meru and Murang'a; numerous Kisii-based; West Pokot") == {12: 1, 21: 1, 45: 1, 24: 1}


def _make_pdf(path):
    """Two-page report-like PDF: a county section heading, a ruled table, and a continuation."""
    doc = pymupdf.open()
    for pno in range(2):
        page = doc.new_page(width=595, height=842)
        y = 60
        if pno == 0:
            page.insert_text((57, y), "3.1. County Government of Baringo", fontname="hebo", fontsize=12.3)
            y += 30
            page.insert_text((57, y), "Baringo spent Kshs.2.46 billion in the period. " * 2, fontname="helv", fontsize=10.4)
            y += 30
            page.insert_text((57, y), "Table 3.6: Baringo County Summary of Budget", fontname="helv", fontsize=10.4)
            y += 10
        rows = [["Item", "Budget", "Expenditure"], ["Compensation", "4,018.08", "1,673.19"], ["O&M", "1,775.52", "385.38"]]
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                x0, y0 = 57 + c * 150, y + r * 20
                page.draw_rect(pymupdf.Rect(x0, y0, x0 + 150, y0 + 20), color=(0, 0, 0), width=0.5)
                page.insert_text((x0 + 3, y0 + 14), cell, fontname="helv", fontsize=8)
        page.insert_text((515, 815), str(12 + pno), fontname="hebo", fontsize=12)
        page.insert_text((61, 808), "COUNTY GOVERNMENTS BUDGET IMPLEMENTATION REVIEW REPORT", fontname="helv", fontsize=8)
    doc.save(path)


def test_parse_page_tables_headings_footer(tmp_path):
    pdf = tmp_path / "r.pdf"
    _make_pdf(pdf)
    with pymupdf.open(pdf) as doc:
        p = parse_page(doc[0])
    assert p["printed_page"] == "12"
    assert "BUDGET IMPLEMENTATION REVIEW REPORT" not in p["text"], "running footer is stripped"
    assert [h["number"] for h in p["headings"]] == ["3.1"]
    [t] = p["tables"]
    assert t["rows"][1] == ["Compensation", "4,018.08", "1,673.19"]
    assert t["caption_number"] == "3.6"
    assert "4,018.08" not in p["text"], "table cells are not repeated in narrative text"
    assert p["county_mentions"] == {30: 4}  # heading, two sentences, caption


def test_assemble_carries_county_and_links_continuations(tmp_path):
    pdf = tmp_path / "r.pdf"
    _make_pdf(pdf)
    with pymupdf.open(pdf) as doc:
        pages = [parse_page(doc[i]) for i in range(2)]
    out = assemble(pages)
    t0, t1 = out["tables"]
    assert t1["caption_number"] == "3.6" and t1["continued_from_page"] == 1
    assert out["pages"][1]["county_section"] == {"code": 30, "name": "Baringo"}
    assert t1["county_section"]["code"] == 30


def test_parse_document_resumes_from_finished_batches(tmp_path, monkeypatch):
    pdf = tmp_path / "r.pdf"
    _make_pdf(pdf)
    calls = []
    real = parse_mod._parse_batch

    def counting(path, first, last):
        calls.append((first, last))
        return real(path, first, last)

    monkeypatch.setattr(parse_mod, "_parse_batch", counting)
    monkeypatch.setattr(parse_mod, "ProcessPoolExecutor", _InlineExecutor)
    out = parse_document(pdf, tmp_path / "parsed", batch_size=1, workers=1)
    assert calls == [(0, 0), (1, 1)]

    # Simulate a crash after the first batch: remove the second batch and rerun.
    batches = sorted((out.parent / "batches").glob("*.json"))
    batches[1].unlink()
    calls.clear()
    out = parse_document(pdf, tmp_path / "parsed", batch_size=1, workers=1)
    assert calls == [(1, 1)], "only the missing batch is parsed again"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["page_count"] == 2 and len(doc["tables"]) == 2


class _InlineExecutor:
    """Runs submitted work in-process so monkeypatched functions are seen."""

    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args):
        from concurrent.futures import Future

        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f


def test_ligatures_are_expanded():
    assert "Of\ufb01ce of the Controller, e\ufb00ective, \ufb02ow".translate(parse_mod.LIGATURES) == "Office of the Controller, effective, flow"


def test_failed_batch_keeps_the_others_and_retries_alone(tmp_path, monkeypatch):
    pdf = tmp_path / "r.pdf"
    _make_pdf(pdf)
    real = parse_mod._parse_batch
    calls = []
    fail_once = {1}  # batch starting at page index 1 fails on its first attempt only

    def flaky(path, first, last):
        calls.append(first)
        if first in fail_once:
            fail_once.discard(first)
            raise MemoryError("simulated worker failure")
        return real(path, first, last)

    monkeypatch.setattr(parse_mod, "_parse_batch", flaky)
    monkeypatch.setattr(parse_mod, "ProcessPoolExecutor", _InlineExecutor)
    with pytest.raises(RuntimeError, match="1 batch\\(es\\) failed \\(0002-0002\\).*Re-run"):
        parse_document(pdf, tmp_path / "parsed", batch_size=1, workers=1)
    batch_dir = tmp_path / "parsed" / "r" / "batches"
    assert [f.name for f in sorted(batch_dir.glob("*.json"))] == ["0001-0001.json"]

    calls.clear()
    out = parse_document(pdf, tmp_path / "parsed", batch_size=1, workers=1)
    assert calls == [1], "only the failed batch is parsed again"
    assert json.loads(out.read_text(encoding="utf-8"))["page_count"] == 2
