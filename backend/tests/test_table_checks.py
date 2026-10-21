"""Checks use rows copied from the FY 2025/26 reports (tables 2.1, 2.4/2.5, 2.9, county 3.x)."""

import pytest

from app.ingest.table_checks import check_table, find_formula_row, logical_tables, parse_number

ABSORPTION_HEADER = [
    ["County", "Budget Estimates (Kshs.Million", "", "", "Actual Expenditure (Kshs.Million)", "", "", "Absorption Rate(%)", "", ""],
    ["", "Rec", "Dev", "Total", "Rec", "Dev", "Total", "Rec", "Dev", "Total"],
    ["", "A", "B", "C=A+B", "D", "E", "F=D+E", "G=D/A 100", "H=E/B 100", "I=F/C 100"],
]
BARINGO = ["Baringo", "5,793.60", "3,748.43", "9,542.03", "2,058.57", "405.01", "2,463.58", "36", "11", "26"]
BOMET = ["Bomet", "6,818.55", "3,997.23", "10,815.78", "2,265.82", "921.59", "3,187.41", "33", "23", "29"]


@pytest.mark.parametrize(
    "text,value",
    [("5,793.60", 5793.6), ("(4,740,887.00)", -4740887.0), ("-92,523,912.65", -92523912.65), ("100.00", 100.0),
     ("27%", 27.0), ("-", None), ("", None), ("N/A", None), ("Baringo", None), ("6104082008", 6104082008.0)],
)
def test_parse_number(text, value):
    assert parse_number(text) == value


def test_formula_row_found():
    assert find_formula_row(ABSORPTION_HEADER) == 2
    assert find_formula_row([["County", "Budget"], ["Baringo", "1"]]) is None


def test_clean_table_passes_with_total():
    total = ["Total", "12,612.15", "7,745.66", "20,357.81", "4,324.39", "1,326.60", "5,650.99", "34", "17", "28"]
    c = check_table(ABSORPTION_HEADER + [BARINGO, BOMET, total])
    assert c.ok, (c.formula_failures, c.total_failures)
    assert c.rows_checked == 3


def test_lost_digit_is_caught():
    bad = BARINGO.copy()
    bad[3] = "954.03"  # a digit dropped during extraction
    c = check_table(ABSORPTION_HEADER + [bad])
    assert [(f.label, f.column) for f in c.formula_failures] == [("Baringo", "C"), ("Baringo", "I")]


def test_total_row_mismatch_is_caught():
    total = ["Total", "12,612.15", "7,745.66", "20,357.81", "4,324.39", "1,326.60", "5,650.99", "34", "17", "28"]
    bad_total = total.copy()
    bad_total[1] = "1,261.15"
    c = check_table(ABSORPTION_HEADER + [BARINGO, BOMET, bad_total])
    assert [f.column for f in c.total_failures] == ["A"]


def test_reused_formula_letters_are_skipped_not_failed():
    # Table 2.1, FY 2025/26 report: "F" and "G" are each defined twice in the source.
    header = [
        ["County", "Target", "", "", "Revenue", "", "", "Performance (%)", "", ""],
        ["", "A", "B", "C=A+B", "D", "E", "F=D+E", "G=D/A*100", "F=E/B*100", "G=F/C*100"],
    ]
    row = ["Baringo", "331.94", "595.06", "927.00", "237.50", "320.28", "557.78", "71.55", "53.82", "60.17"]
    c = check_table(header + [row])
    assert c.ok and any("reuses letter" in n for n in c.notes)
    assert c.rows_checked == 1, "C=A+B is still checked"


def test_bracketed_negative_sign_convention():
    # Kitui revenue arrears: "paid" printed in brackets under f=a-b-c+d.
    header = [["No", "Stream", "Arrears", "Waivers", "Paid", "Earned", "Closing"], ["", "", "a", "b", "C", "D", "f=a-b-c+d"]]
    row = ["1", "Land/Poll Rate", "1,282,533,263.00", "0.00", "(4,740,887.00)", "396,979,693.00", "1,674,772,069.00"]
    c = check_table(header + [row])
    assert c.ok and "sign convention: bracketed amounts" in c.notes


def test_source_unit_mismatch_is_reported():
    # Table 2.9 (MCA allowances), FY 2025/26 report: B in Kshs millions, E in Kshs.
    header = [["County", "Budget", "Exp", "Abs", "MCAs", "Avg"], ["", "A", "B", "C=B/A100", "D", "E=B/D/12"]]
    row = ["Baringo", "50.78", "23.34", "45.96", "46", "42,283"]
    c = check_table(header + [row])
    assert [(f.column, f.actual) for f in c.formula_failures] == [("E", 42283.0)]


def test_logical_tables_merge_continuations_and_keep_row_pages():
    frag1 = {"table_id": "p0055_t0", "page": 55, "rows": ABSORPTION_HEADER + [BARINGO], "caption_number": "2.4",
             "caption_title": "Absorption", "continued_from_page": None, "county_section": None}
    frag2 = {"table_id": "p0056_t0", "page": 56, "rows": ABSORPTION_HEADER + [BOMET], "caption_number": "2.4",
             "caption_title": "Absorption", "continued_from_page": 55, "county_section": None}
    [lt] = logical_tables([frag1, frag2])
    assert lt.body_rows == [BARINGO, BOMET]
    assert lt.row_pages == [55, 56] and lt.fragment_ids == ["p0055_t0", "p0056_t0"]


def test_formula_contradicting_header_is_skipped():
    # Tharaka-Nithi revenue table, 9M FY 2025/26 report: "%" column labelled with a variance formula.
    header = [["No", "Stream", "Target", "Receipts", "Receivables", "Total", "Revenue Performance (%)"],
              ["", "", "A", "B", "C", "D=B+C", "E=A-D"]]
    row = ["1", "Equitable Share", "5,058,286,293", "3,363,803,385", "-", "3,363,803,385", "67"]
    c = check_table(header + [row])
    assert c.ok and any("contradicts column header" in n for n in c.notes)
    assert c.rows_checked == 1  # D=B+C still checked, with the dash in C read as nil
