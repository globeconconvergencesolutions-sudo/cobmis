"""Retrieval regressions: strict county filter, observation intent, FY refusal.

Uses the throwaway cob_test database (same fixture pattern as test_db_pipeline).
"""

from __future__ import annotations

import pytest

from app.db.roles import DatabaseSetupError, connect, connect_owner
from app.ingest.chunk import Chunk
from app.ingest.store import sync_chunks, upsert_document
from app.retrieval.search import hits_for_answer, search
from scripts.migrate import migrate

TEST_DB = "cob_test"


@pytest.fixture(scope="module")
def db():
    try:
        admin = connect_owner("postgres", autocommit=True)
    except DatabaseSetupError as exc:
        pytest.skip(f"Postgres not reachable: {exc.code}")
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    migrate(dbname=TEST_DB)
    yield
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.close()


@pytest.fixture
def ingest(db):
    with connect("cob_ingest", TEST_DB) as conn:
        conn.execute("DELETE FROM content.documents")
        conn.commit()
        yield conn


def _rec(period: str, sha: str, fy: str = "2025/26") -> dict:
    return {
        "title": f"CGBIRR {period} FY {fy}",
        "fiscal_year": fy,
        "period": period,
        "url": f"https://example.test/{fy}/{period}",
        "file": f"{fy}_{period}.pdf",
        "sha256": sha,
    }


def _chunk(
    kind: str,
    page: int,
    section: str | None,
    county: int | None,
    header: str,
    text: str,
    page_end: int | None = None,
    table_ref: str | None = None,
) -> Chunk:
    return Chunk(kind, page, page_end or page, section, county, table_ref, header, text)


@pytest.fixture
def seeded(ingest):
    """Kericho observations, noisy national 'challenges' chapters, Nairobi bills, Kisumu 2025/26."""
    doc = upsert_document(ingest, _rec("9M", "a" * 64), 400, 2)
    chunks = [
        _chunk(
            "narrative", 910, "4.1 Introduction", None,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, 4.1 Introduction",
            "Counties face many challenges. Cross-cutting challenges and challenges again. "
            "National challenges chapter discusses challenges repeatedly.",
        ),
        _chunk(
            "narrative", 922, "5.12 Conclusion", None,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, 5.12 Conclusion",
            "The main challenges across counties remain absorption and pending bills challenges.",
        ),
        _chunk(
            "narrative", 55, "2.2.2 Own Source Revenue", None,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, 2.2.2 Own Source Revenue",
            "Own source revenue collection challenges affected several counties nationally.",
        ),
        _chunk(
            "narrative", 297, "3.12.19 General Observations and Recommendations", 35,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, "
            "Kericho County, 3.12.19 General Observations and Recommendations",
            "Kericho County reported late submission of financial reports and weak internal audit. "
            "The county should strengthen revenue administration.",
            page_end=298,
        ),
        _chunk(
            "narrative", 280, "3.12.1 Overview of FY 2025/26 Budget", 35,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, "
            "Kericho County, 3.12.1 Overview of FY 2025/26 Budget",
            "Kericho County had an own source revenue target of Kshs.870.00 million.",
        ),
        _chunk(
            "table", 100, None, 47,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, "
            "Nairobi City County, Table 3.200: Pending Bills",
            "Table 3.200: Nairobi City pending bills\n| Entity | Amount |\n| Executive | 12,500.00 |",
            table_ref="3.200",
        ),
        _chunk(
            "table", 101, None, 35,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, "
            "Kericho County, Table 3.183: Kericho County Trade Payables",
            "Table 3.183: Kericho pending bills\n| Entity | Amount |\n| Executive | 400.00 |",
            table_ref="3.183",
        ),
        _chunk(
            "narrative", 50, "2.5 Pending Bills", None,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, 2.5 Pending Bills",
            "Pending bills for all counties are analysed in this national chapter.",
        ),
        _chunk(
            "narrative", 400, "3.40 Overview", 42,
            "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, "
            "Kisumu County, 3.40 Overview",
            "Kisumu County overview for FY 2025/26 nine months.",
        ),
    ]
    sync_chunks(ingest, doc, chunks)
    ingest.commit()
    with connect("cob_app", TEST_DB) as app:
        allowed = [r[0] for r in app.execute("SELECT collection_id FROM app.collections WHERE visibility = 'public'")]
        yield app, allowed


def test_kericho_challenges_cites_observations_pages(seeded):
    conn, allowed = seeded
    hits = search(conn, "challenges", None, allowed, county_id=35, fiscal_year="2025/26", period="9M", n=6)
    answer = hits_for_answer(hits)
    assert answer, "county pool must not be empty"
    assert all(h.county_id == 35 for h in answer)
    assert any(h.page_start <= 298 and h.page_end >= 297 and "3.12.19" in (h.context_header or "") for h in answer)
    assert any(h.is_national_fallback for h in hits), "national chapters remain as labelled fallback"
    assert all(h.county_id is None for h in hits if h.is_national_fallback)
    assert sum(1 for h in hits if h.is_national_fallback) <= 2


def test_nairobi_bills_only_nairobi_tagged_for_answer(seeded):
    conn, allowed = seeded
    hits = search(conn, "pending bills", None, allowed, county_id=47, fiscal_year="2025/26", period="9M", n=6)
    answer = hits_for_answer(hits)
    assert answer
    assert all(h.county_id == 47 for h in answer)
    assert any("Nairobi" in h.context_header for h in answer)
    assert not any(h.county_id == 35 for h in answer)


def test_kisumu_2019_20_refuses(seeded):
    conn, allowed = seeded
    hits = search(
        conn, "What challenges did Kisumu face in FY 2019/20?", None, allowed,
        county_id=42, fiscal_year="2019/20", period=None, n=6,
    )
    assert hits_for_answer(hits) == []
    assert hits == []


def test_own_source_still_finds_kericho_section(seeded):
    conn, allowed = seeded
    hits = search(
        conn, "Kericho own source revenue target 870 million", None, allowed,
        county_id=35, fiscal_year="2025/26", period="9M", n=6,
    )
    answer = hits_for_answer(hits)
    assert any("870" in h.text for h in answer)
    assert all(h.county_id == 35 for h in answer)
