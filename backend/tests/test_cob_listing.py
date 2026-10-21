from pathlib import Path

import pytest

from app.ingest.cob_listing import parse_fiscal_year, parse_listing, parse_period, parse_size

FIXTURE = Path(__file__).parent / "fixtures" / "cob_listing_synthetic.html"
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


def test_parse_listing_synthetic():
    entries = parse_listing(FIXTURE.read_text(), BASE)
    by_slug = {e.slug: e for e in entries}

    assert "site-logo" not in by_slug, "links outside the main content are ignored"
    assert len(entries) == 7, "duplicate links to the same package are collapsed"

    q1 = by_slug["county-governments-budget-implementation-review-report-for-the-first-quarter-of-fy-2023-24"]
    assert (q1.fiscal_year, q1.period, q1.size_bytes) == ("2023/24", "Q1", int(12.4 * 1024**2))
    assert q1.title.startswith("County Governments Budget Implementation Review Report")

    h1 = by_slug["cgbirr-half-year-2023-24"]
    assert (h1.fiscal_year, h1.period) == ("2023/24", "H1")
    assert "fiscal_year from heading" not in h1.parse_notes, "FY comes from the slug when the title lacks it"
    assert h1.url == "https://cob.go.ke/download/cgbirr-half-year-2023-24/"

    assert by_slug["cgbirr-nine-months-fy-2023-2024"].period == "9M"

    annual = by_slug["annual-cgbirr-fy-2021-22"]
    assert annual.listed_as_empty and annual.period == "FY" and annual.heading == "FY 2021/22"

    third = by_slug["third-quarter-cbirr-2016-17"]
    assert (third.fiscal_year, third.period) == ("2016/17", "9M")

    misc = by_slug["cbirr-2016-17-misc"]
    assert misc.fiscal_year == "2016/17"
    assert misc.period is None and "period not recognised" in misc.parse_notes

    q4 = by_slug["q4-2012-13"]
    assert (q4.fiscal_year, q4.period) == ("2012/13", "FY")


def test_fiscal_year_falls_back_to_heading():
    html = '<main><h2>FY 2018/19</h2><div><a href="/download/q1-report/">First Quarter Report</a> 2 MB</div></main>'
    [e] = parse_listing(html, BASE)
    assert e.fiscal_year == "2018/19" and "fiscal_year from heading" in e.parse_notes
