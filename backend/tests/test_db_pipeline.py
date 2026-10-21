"""Integration tests against a real Postgres (the docker compose one). They build a throwaway
database `cob_test` with the same migrations, so the development data is never touched.
Skipped when Postgres is not reachable.

Fixture data: real extracted rows of tables 2.3 and 2.4 of the FY 2025/26 first-half report.
"""

import copy
import json
from pathlib import Path

import psycopg
import pytest

from app.ingest.chunk import Chunk
from app.ingest.store import embed_pending, sync_chunks, upsert_document
from app.ingest.tables_to_sql import consistency_check, load_document
from app.retrieval.search import search
from app.db.roles import DatabaseSetupError, connect, connect_owner
from scripts.migrate import migrate

FIXTURE = Path(__file__).parent / "fixtures" / "h1_chapter2_tables.json"
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
        conn.execute("DELETE FROM public_data.source_document")  # cascades to facts, quarantine, results
        conn.execute("DELETE FROM content.documents")
        conn.commit()
        yield conn


def _rec(period: str, sha: str) -> dict:
    return {"title": f"CGBIRR {period} FY 2025/26", "fiscal_year": "2025/26", "period": period,
            "file": f"{period}.pdf", "sha256": sha, "url": "https://cob.go.ke/download/x/"}


def _write_doc(tmp_path: Path, mutate=None, name: str = "doc.json") -> Path:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if mutate:
        mutate(doc)
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_load_real_tables_with_provenance(ingest, tmp_path):
    stats = load_document(ingest, _write_doc(tmp_path), _rec("H1", "a" * 64))
    ingest.commit()
    assert stats["quarantined"] == 0 and stats["cross_table_mismatches"] == 0
    t24 = next(v for k, v in stats["tables"].items() if k.startswith("2.4"))
    assert t24["county_rows"] == 47 and t24["total_failures"] == []

    row = ingest.execute(
        "SELECT value, printed_value, printed_unit, source_page, source_table FROM public_data.fact_county_budget f "
        "JOIN public_data.dim_county c USING (county_id) WHERE c.name = 'Baringo' AND metric = 'budget_development'"
    ).fetchone()
    assert row[0] == 3_748_430_000 and row[1] == "3,748.43" and row[2] == "Kshs. Million" and row[3] == 55
    assert row[4].startswith("2.4 County Budget Allocation")

    # Tharaka-Nithi is printed "Tah raka-Nithi" on page 56 (a glyph-order quirk); it must still resolve.
    pct = ingest.execute(
        "SELECT value, source_page FROM public_data.fact_county_budget f JOIN public_data.dim_county c USING (county_id) "
        "WHERE c.name = 'Tharaka-Nithi' AND metric = 'absorption_total_pct'"
    ).fetchone()
    assert pct == (40, 56)

    # Total expenditure appears in both 2.3 and 2.4: agreement is recorded for every county.
    agree = ingest.execute(
        "SELECT status, count(*) FROM public_data.validation_results WHERE check_name = 'cross_table_agreement' GROUP BY status"
    ).fetchall()
    assert agree == [("pass", 47 * 3)]


def test_bad_rows_are_quarantined_not_loaded(ingest, tmp_path):
    def corrupt(doc):
        t = next(t for t in doc["tables"] if t["table_id"] == "p0055_t0")
        baringo = next(r for r in t["rows"] if r[0] == "Baringo")
        baringo[3] = "954.03"   # C=A+B now false: a lost digit
        bomet = next(r for r in t["rows"] if r[0] == "Bomet")
        bomet[0] = "Bomett Kounty"  # not a county

    stats = load_document(ingest, _write_doc(tmp_path, corrupt), _rec("H1", "b" * 64))
    ingest.commit()
    q = dict(ingest.execute("SELECT row_label, string_agg(check_name, ',' ORDER BY check_name) FROM public_data.quarantine_rows GROUP BY row_label").fetchall())
    assert q == {"Baringo": "row_formula,row_formula", "Bomett Kounty": "county_name"}
    # Baringo's absorption-table values are not loaded (its 2.3 values still are).
    n = ingest.execute(
        "SELECT count(*) FROM public_data.fact_county_budget f JOIN public_data.dim_county c USING (county_id) "
        "WHERE c.name = 'Baringo' AND source_table LIKE '2.4%%'"
    ).fetchone()[0]
    assert n == 0
    structure = ingest.execute(
        "SELECT status, details->'county_rows' FROM public_data.validation_results WHERE check_name = 'table_structure' AND scope LIKE '2.4%%'"
    ).fetchone()
    assert structure == ("fail", 46)
    assert stats["quarantined"] == 2


def test_cumulative_decrease_is_flagged(ingest, tmp_path):
    load_document(ingest, _write_doc(tmp_path), _rec("H1", "c" * 64))

    def lower_turkana(doc):  # a "9M" report where Turkana's spending fell, consistently in both tables
        for t in doc["tables"]:
            for r in t["rows"]:
                if r[0] != "Turkana":
                    continue
                if t["caption_number"] == "2.4":  # budget/expenditure/absorption
                    r[4], r[5], r[6] = "3,500.00", "500.00", "4,000.00"
                    r[7], r[8], r[9] = "28", "7", "20"
                else:  # 2.3 economic classification
                    r[1], r[2], r[3], r[4], r[5] = "3,000.00", "500.00", "3,500.00", "500.00", "4,000.00"

    load_document(ingest, _write_doc(tmp_path, lower_turkana, "9m.json"), _rec("9M", "d" * 64))
    res = consistency_check(ingest)
    ingest.commit()
    flagged = {(f[0], f[1]) for f in res["failures"]}
    assert ("Turkana", "expenditure_total") in flagged
    assert {c for c, _ in flagged} == {"Turkana"}, "equal values in other counties pass"


def test_readonly_role_sees_facts_but_not_quarantine_or_chunks(ingest, tmp_path):
    load_document(ingest, _write_doc(tmp_path), _rec("H1", "e" * 64))
    ingest.commit()
    with connect("cob_readonly", TEST_DB, autocommit=True) as ro:
        assert ro.execute("SELECT count(*) FROM public_data.v_county_metrics").fetchone()[0] > 0
        for table in ("public_data.quarantine_rows", "public_data.validation_results", "content.chunks"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                ro.execute(f"SELECT 1 FROM {table} LIMIT 1")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            ro.execute("CREATE TABLE public_data.x (i int)")


def _chunks(n: int, county=None) -> list[Chunk]:
    return [Chunk("narrative", i + 1, i + 1, "2.4 Expenditure Analysis", county, None,
                  "County Governments Budget Implementation Review Report, FY 2025/26",
                  f"Counties reported pending bills and low absorption, item {i}.") for i in range(n)]


def test_embedding_resumes_after_interruption(ingest):
    doc_id = upsert_document(ingest, _rec("H1", "f" * 64), 792, 2)
    sync_chunks(ingest, doc_id, _chunks(10))
    ingest.commit()
    calls = []

    def flaky(texts):
        calls.append(len(texts))
        if len(calls) == 3:
            raise RuntimeError("Ollama went away")
        return [[0.1] * 1024 for _ in texts]

    with pytest.raises(RuntimeError):
        embed_pending(ingest, flaky, "bge-m3", batch_size=3, doc_ids=[doc_id])
    ingest.rollback()
    done = ingest.execute("SELECT count(*) FROM content.chunks WHERE embedding IS NOT NULL").fetchone()[0]
    assert done == 6, "the two committed batches survive the crash"

    calls.clear()
    n = embed_pending(ingest, lambda t: [[0.2] * 1024 for _ in t], "bge-m3", batch_size=3, doc_ids=[doc_id])
    assert n == 4 and ingest.execute("SELECT count(*) FROM content.chunks WHERE embedding IS NULL").fetchone()[0] == 0

    # Re-syncing identical chunks keeps rows and vectors; a changed chunk is replaced.
    changed = _chunks(10)
    changed[0] = copy.replace(changed[0], text="Something new.")
    res = sync_chunks(ingest, doc_id, changed)
    assert res == {"kept": 9, "added": 1, "removed": 1}


def test_search_never_crosses_collections(ingest):
    with connect_owner(TEST_DB, autocommit=True) as admin:
        admin.execute("INSERT INTO app.collections (name, visibility) VALUES ('secret-test', 'restricted') ON CONFLICT DO NOTHING")
        ids = dict(admin.execute("SELECT name, collection_id FROM app.collections").fetchall())
    public_doc = upsert_document(ingest, _rec("H1", "1" * 64), 10, 2)
    secret_doc = upsert_document(ingest, _rec("9M", "2" * 64), 10, 2, collection="secret-test")
    sync_chunks(ingest, public_doc, _chunks(2))
    secret = [Chunk("narrative", 1, 1, "Board minutes", None, None, "Restricted upload",
                    "Confidential: pending bills settlement plan for Turkana, item secret.")]
    sync_chunks(ingest, secret_doc, secret)
    ingest.commit()

    hits = search(ingest, "pending bills settlement plan Turkana", None, [ids["cob-public"]], n=10)
    assert hits and all("Confidential" not in h.text for h in hits)
    both = search(ingest, "pending bills settlement plan Turkana", None, [ids["cob-public"], ids["secret-test"]], n=10)
    assert any("Confidential" in h.text for h in both)
    assert search(ingest, "pending bills", None, [], n=10) == []
