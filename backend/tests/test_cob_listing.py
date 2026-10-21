import json
from collections import Counter
from pathlib import Path

import pytest

from app.ingest.cob_listing import parse_fiscal_year, parse_listing, parse_period, parse_size

FIXTURE = Path(__file__).parent / "fixtures" / "cob_listing_live.html"
EXPECTED = Path(__file__).parent / "fixtures" / "cob_listing_live_expected.json"
BASE = "https://cob.go.ke/reports/consolidated-county-budget-implementation-review-reports/"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FY 2023/24", "2023/24"),
        ("Financial Year 2023/2024", "2023/24"),
        ("FY2019-20", "2019/20"),
        ("2012 – 2013", "2012/13"),
        ("for the period 2019/20", "2019/20"),
        ("1999/00", None),  # out of range: we only accept 20xx
        ("2020/22", None),  # not consecutive
        ("County Budget Implementation Review Report", None),
        ("fy 2099/00", None),  # century rollover not consecutive under our rule; corpus ends 2025/26
    ],
)
def test_parse_fiscal_year(text, expected):
    assert parse_fiscal_year(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Report for the First Quarter of FY 2023/24", "Q1"),
        ("FIRST QUARTER", "Q1"),
        ("Half Year", "H1"),
        ("First Half of FY", "H1"),
        ("Second Quarter", "H1"),
        ("Nine Months", "9M"),
        ("Third Quarter", "9M"),
        ("Annual County Governments BIRR", "FY"),
        ("Quarter Three FY 2013-2014", "9M"),
        ("Fourth Quarter", "FY"),
        ("for the period ended 30th June", None),
        ("County Budget Implementation Review Report", None),
    ],
)
def test_parse_period(text, expected):
    assert parse_period(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("12.40 MB", int(12.4 * 1024**2)), ("0.00 KB", 0), ("512 bytes", 512), ("3 MB", 3 * 1024**2), (None, None), ("n/a", None)],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_live_snapshot_matches_golden():
    """Every one of the 53 entries on the real page (snapshot 2026-10) parses to the reviewed values."""
    entries = parse_listing(FIXTURE.read_text(), BASE)
    got = [
        {"slug": e.slug, "fiscal_year": e.fiscal_year, "period": e.period, "size_text": e.size_text, "listed_as_empty": e.listed_as_empty}
        for e in entries
    ]
    assert got == json.loads(EXPECTED.read_text())


def test_live_snapshot_invariants():
    entries = parse_listing(FIXTURE.read_text(), BASE)
    assert len(entries) == 53
    assert all(e.fiscal_year and e.period for e in entries), "every entry has a FY and a period"
    assert all(e.url.startswith("https://cob.go.ke/download/") for e in entries)
    assert Counter(e.period for e in entries) == {"FY": 14, "9M": 13, "H1": 13, "Q1": 13}

    fys = {e.fiscal_year for e in entries}
    assert fys == {f"{y}/{str(y + 1)[-2:]}" for y in range(2012, 2026)}, "FY 2012/13 to 2025/26"
    assert Counter(e.fiscal_year for e in entries)["2012/13"] == 1

    empty = sorted((e.fiscal_year, e.period) for e in entries if e.listed_as_empty)
    assert empty == [("2013/14", "Q1"), ("2014/15", "9M"), ("2014/15", "FY"), ("2014/15", "H1"), ("2014/15", "Q1"), ("2021/22", "FY")]

    # Each (FY, period) pair appears once.
    assert len({(e.fiscal_year, e.period) for e in entries}) == 53

    by_slug = {e.slug: e for e in entries}
    fy2526 = by_slug["county-governments-budget-implementation-review-report-for-the-financial-year-2025-26"]
    assert fy2526.period == "FY" and any("inferred" in n for n in fy2526.parse_notes)
    assert fy2526.title == "COUNTY GOVERNMENTS BUDGET IMPLEMENTATION REVIEW REPORT FOR THE FINANCIAL YEAR 2025/26"
    assert by_slug["budget-implementation-review-report-third-quarter-fy-201617"].period == "9M"
    # Entry titles are <h3> elements; they must not be mistaken for section headings.
    assert all(e.heading != e.title for e in entries)


def test_fiscal_year_falls_back_to_heading():
    html = '<main><h2>FY 2018/19</h2><div><a href="/download/q1-report/">First Quarter Report</a> 2 MB</div></main>'
    [e] = parse_listing(html, BASE)
    assert e.fiscal_year == "2018/19" and "fiscal_year from heading" in e.parse_notes
