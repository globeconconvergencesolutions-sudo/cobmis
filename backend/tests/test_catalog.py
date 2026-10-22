"""Catalog route: SQL-only answers, ACL-scoped collections, deterministic have/coverage."""

from __future__ import annotations

import pytest

from app.catalog import answer_catalog, allowed_collections, build_catalog, have_coverage
from app.db.roles import DatabaseSetupError, connect, connect_owner
from app.ingest.chunk import Chunk
from app.ingest.store import sync_chunks, upsert_document
from scripts.migrate import migrate

TEST_DB = "cob_test"
SECRET_TITLE = "RESTRICTED-SECRET-BOARD-MINUTES-XYZ"


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
def seeded(db):
    with connect_owner(TEST_DB, autocommit=True) as admin:
        admin.execute(
            "INSERT INTO app.collections (name, visibility) VALUES ('secret-catalog', 'restricted') "
            "ON CONFLICT (name) DO NOTHING"
        )
    with connect("cob_ingest", TEST_DB) as ingest:
        ingest.execute("DELETE FROM content.documents")
        ingest.execute("DELETE FROM app.collection_access")
        ingest.execute("DELETE FROM app.users")
        ingest.commit()

        public = upsert_document(
            ingest,
            {
                "title": "CGBIRR 9M FY 2025/26",
                "fiscal_year": "2025/26",
                "period": "9M",
                "url": "https://cob.go.ke/download/public-9m/",
                "file": "public-9m.pdf",
                "sha256": "p" * 64,
            },
            page_count=100,
            parse_version=2,
            needs_ocr_pages=2,
        )
        sync_chunks(
            ingest,
            public,
            [
                Chunk(
                    "narrative", 5, 6, "Executive Summary", None, None,
                    "County Governments Budget Implementation Review Report, FY 2025/26, First Nine Months, EXECUTIVE SUMMARY",
                    "This report reviews county budget implementation for the first nine months of FY 2025/26.",
                ),
                Chunk(
                    "narrative", 297, 298, "3.12.19 General Observations and Recommendations", 35, None,
                    "Kericho County, 3.12.19 General Observations and Recommendations",
                    "Kericho reported late submission of financial reports.",
                ),
            ],
        )

        secret = upsert_document(
            ingest,
            {
                "title": SECRET_TITLE,
                "fiscal_year": "2025/26",
                "period": "FY",
                "url": "https://example.test/secret",
                "file": "secret.pdf",
                "sha256": "s" * 64,
            },
            page_count=10,
            parse_version=2,
            collection="secret-catalog",
        )
        sync_chunks(
            ingest,
            secret,
            [
                Chunk(
                    "narrative", 1, 1, "Minutes", None, None,
                    SECRET_TITLE,
                    "Confidential settlement plan.",
                ),
            ],
        )

        uid = ingest.execute(
            "INSERT INTO app.users (email, display_name, role) VALUES ('analyst@test', 'Analyst', 'analyst') "
            "RETURNING user_id"
        ).fetchone()[0]
        coll = ingest.execute(
            "SELECT collection_id FROM app.collections WHERE name = 'secret-catalog'"
        ).fetchone()[0]
        ingest.execute(
            "INSERT INTO app.collection_access (user_id, collection_id, can_read, can_write) "
            "VALUES (%s, %s, true, false)",
            (uid, coll),
        )
        ingest.commit()

    with connect("cob_app", TEST_DB) as app:
        yield app, uid


def test_public_catalog_hides_restricted_title(seeded):
    conn, _uid = seeded
    public_allowed = allowed_collections(conn, None)
    cat = build_catalog(conn, public_allowed)
    titles = [d.title for d in cat.documents]
    assert SECRET_TITLE not in titles
    assert any("CGBIRR" in t for t in titles)
    assert cat.counts["documents"] == 1
    assert SECRET_TITLE not in render_blob(cat)


def test_restricted_user_sees_secret_title(seeded):
    conn, uid = seeded
    allowed = allowed_collections(conn, uid)
    cat = build_catalog(conn, allowed)
    titles = [d.title for d in cat.documents]
    assert SECRET_TITLE in titles
    assert cat.counts["documents"] == 2
    public = build_catalog(conn, allowed_collections(conn, None))
    assert cat.counts["documents"] > public.counts["documents"]
    assert cat.counts["pages"] > public.counts["pages"]


def test_do_you_have_fy_2022_23_says_no(seeded):
    conn, _uid = seeded
    allowed = allowed_collections(conn, None)
    have = have_coverage(conn, allowed, fiscal_year="2022/23")
    assert have["present"] is False
    assert have["document_count"] == 0
    ans = answer_catalog(conn, "Do you have FY 2022/23?", allowed)
    assert ans.kind == "have"
    assert ans.text.startswith("No.")
    assert "2022/23" in ans.text


def test_do_you_have_loaded_year_says_yes(seeded):
    conn, _uid = seeded
    allowed = allowed_collections(conn, None)
    have = have_coverage(conn, allowed, fiscal_year="2025/26", period="9M")
    assert have["present"] is True
    assert have["document_count"] == 1


def test_catalog_numbers_match_sql(seeded):
    conn, _uid = seeded
    allowed = allowed_collections(conn, None)
    cat = build_catalog(conn, allowed)
    docs_sql = conn.execute(
        "SELECT count(*) FROM content.documents WHERE collection_id = ANY(%s)", (allowed,)
    ).fetchone()[0]
    pages_sql = conn.execute(
        "SELECT coalesce(sum(page_count), 0) FROM content.documents WHERE collection_id = ANY(%s)",
        (allowed,),
    ).fetchone()[0]
    chunks_sql = conn.execute(
        "SELECT count(*) FROM content.chunks WHERE collection_id = ANY(%s)", (allowed,)
    ).fetchone()[0]
    ocr_sql = conn.execute(
        "SELECT coalesce(sum(needs_ocr_pages), 0) FROM content.documents WHERE collection_id = ANY(%s)",
        (allowed,),
    ).fetchone()[0]
    assert cat.counts["documents"] == docs_sql
    assert cat.counts["pages"] == pages_sql
    assert cat.counts["chunks"] == chunks_sql
    assert cat.counts["needs_ocr_pages"] == ocr_sql
    ans = answer_catalog(conn, "What data do you have?", allowed)
    assert str(docs_sql) in ans.text
    assert str(pages_sql) in ans.text


def test_overview_includes_examples_and_limitations(seeded):
    conn, _uid = seeded
    ans = answer_catalog(conn, "What data do you have?", allowed_collections(conn, None))
    assert ans.kind == "overview"
    assert "Example questions" in ans.text
    assert "Known limitations" in ans.text


def render_blob(cat) -> str:
    from app.catalog.service import render_catalog_answer

    return render_catalog_answer(cat)
