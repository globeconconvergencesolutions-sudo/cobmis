"""Execute validated SQL as cob_readonly against cob_test fixture facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.roles import DatabaseSetupError, connect, connect_owner
from app.ingest.tables_to_sql import load_document
from app.sql_agent.execute import execute_sql
from app.sql_agent.validate import validate_sql
from scripts.migrate import migrate

FIXTURE = Path(__file__).parent / "fixtures" / "h1_chapter2_tables.json"
TEST_DB = "cob_test_sql"  # separate from cob_test used by other modules (DROP FORCE races)


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
def facts(db, tmp_path):
    with connect("cob_ingest", TEST_DB) as ingest:
        ingest.execute("DELETE FROM public_data.source_document")
        path = tmp_path / "doc.json"
        path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        rec = {
            "title": "CGBIRR H1 FY 2025/26",
            "fiscal_year": "2025/26",
            "period": "H1",
            "url": "https://example.test/h1",
            "file": "h1.pdf",
            "sha256": "f" * 64,
        }
        stats = load_document(ingest, path, rec)
        ingest.commit()
        assert stats["facts"] > 0
        yield stats


def test_validated_baringo_budget_matches_fixture(facts):
    sql = validate_sql(
        "SELECT county, metric, value FROM public_data.v_county_metrics "
        "WHERE county = 'Baringo' AND metric = 'budget_total' "
        "AND fiscal_year = '2025/26' AND period_type = 'H1' LIMIT 5"
    )
    res = execute_sql(sql, dbname=TEST_DB)
    assert res.error is None, res.error
    assert res.rows, "expected Baringo budget_total from H1 fixture"
    assert res.rows[0][0] == "Baringo"
    assert res.rows[0][1] == "budget_total"
    # budget_total = recurrent + development from fixture (see test_db_pipeline)
    assert res.rows[0][2] == 9_542_030_000


def test_readonly_cannot_be_bypassed_by_content_select(facts):
    with pytest.raises(Exception):
        validate_sql("SELECT text FROM content.chunks LIMIT 1")
