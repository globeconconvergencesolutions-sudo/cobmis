import pytest

from app.sql_agent.validate import ValidationError, validate_sql


def test_accepts_clean_select_and_injects_limit():
    sql = validate_sql(
        "SELECT county, value FROM public_data.v_county_metrics "
        "WHERE metric = 'pending_bills_total' ORDER BY value DESC"
    )
    assert "LIMIT" in sql.upper()
    assert "v_county_metrics" in sql


def test_accepts_markdown_fence():
    raw = "```sql\nSELECT county FROM public_data.v_county_metrics LIMIT 3\n```"
    sql = validate_sql(raw)
    assert sql.upper().startswith("SELECT")


def test_rejects_delete():
    with pytest.raises(ValidationError):
        validate_sql("DELETE FROM public_data.v_county_metrics")


def test_rejects_multi_statement():
    with pytest.raises(ValidationError):
        validate_sql(
            "SELECT 1 FROM public_data.v_county_metrics; "
            "SELECT 2 FROM public_data.v_county_metrics"
        )


def test_rejects_content_schema():
    with pytest.raises(ValidationError, match="not allowed"):
        validate_sql("SELECT text FROM content.chunks LIMIT 5")


def test_rejects_pg_sleep():
    with pytest.raises(ValidationError):
        validate_sql("SELECT pg_sleep(10) FROM public_data.v_county_metrics")


def test_caps_huge_limit():
    sql = validate_sql("SELECT county FROM public_data.v_county_metrics LIMIT 99999")
    assert re_search_limit(sql) <= 500


def re_search_limit(sql: str) -> int:
    import re
    m = re.search(r"LIMIT\s+(\d+)", sql, re.I)
    assert m
    return int(m.group(1))


def test_qualifies_bare_table():
    sql = validate_sql("SELECT county FROM v_county_metrics LIMIT 1")
    assert "public_data" in sql.lower()
