from app.ingest.chunk import chunk_document, estimate_tokens, narrative_chunks, table_chunks


def _page(n, text, headings=(), county=None, section=None):
    return {"page": n, "text": text, "needs_ocr": False, "headings": list(headings),
            "county_section": county, "section_start": section, "tables": []}


def _h(number, title, level):
    return {"number": number, "title": title, "level": level, "y": 0}


def test_county_switch_mid_page_and_noise_removed():
    p1 = _page(
        297,
        "3.12.19 General Observations and Recommendations\n"
        "Kericho reported late submission of financial reports.\n"
        "Source: Kericho County Treasury\n"
        "3.13. County Government of Kiambu\n"
        "Kiambu's approved budget was Kshs.26.83 billion.",
        [_h("3.12.19", "General Observations and Recommendations", 3), _h("3.13", "County Government of Kiambu", 2)],
        county={"code": 35, "name": "Kericho"},
        section="3.12 County Government of Kericho",
    )
    front = _page(5, "LIST OF TABLES\nTable 2.1: Own Source Revenue......................2", [], None, None)
    chunks = narrative_chunks({"pages": [front, p1]}, "2025/26", "9M")
    by_text = {c.text: c for c in chunks}
    kericho = next(c for t, c in by_text.items() if "late submission" in t)
    kiambu = next(c for t, c in by_text.items() if "26.83" in t)
    assert kericho.county_id == 35 and kiambu.county_id == 22
    assert "Kiambu County" in kiambu.context_header and "First Nine Months" in kiambu.context_header
    assert all("Source:" not in c.text and "......" not in c.text for c in chunks)


def test_narrative_chunks_respect_size_and_overlap():
    sentences = " ".join(f"Sentence number {i} talks about county absorption of development funds." for i in range(200))
    chunks = narrative_chunks({"pages": [_page(10, sentences, section="2.4 Expenditure Analysis")]}, "2025/26", "H1", target=200)
    assert len(chunks) > 5
    assert all(estimate_tokens(c.text) <= 220 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks), "never cut mid-sentence"
    # consecutive chunks share their boundary sentence(s)
    assert chunks[0].text.split(". ")[-1] in chunks[1].text


def test_big_table_splits_between_rows_with_header_repeated():
    header = [["County", "Budget (Kshs. Million)", "Expenditure (Kshs. Million)"], ["", "A", "B"]]
    rows = [[f"County{i}", f"{i},000.00", f"{i},500.00"] for i in range(120)]
    table = {"table_id": "p0055_t0", "page": 55, "rows": header + rows, "caption_number": "2.4",
             "caption_title": "County Budget Allocation", "continued_from_page": None, "county_section": None}
    chunks = table_chunks({"tables": [table]}, "2025/26", "H1", max_tokens=300)
    assert len(chunks) > 1
    seen = []
    for c in chunks:
        lines = c.text.split("\n")
        assert lines[1].startswith("| County |") and "(part" in lines[0]
        seen += [ln for ln in lines if ln.startswith("| County") and not ln.startswith("| County |")]
    assert len(seen) == 120, "every row appears exactly once, none split"


def test_chunk_document_combines_kinds():
    doc = {"pages": [_page(1, "Counties spent Kshs.192.59 billion.", section="2.4 Expenditure")], "tables": []}
    kinds = {c.kind for c in chunk_document(doc, "2025/26", "H1")}
    assert kinds == {"narrative"}
