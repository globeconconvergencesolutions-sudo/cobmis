"""Scope / clarify helpers for Ask — no Ollama required."""

from app.api.ask_service import (
    clarify_if_underspecified,
    enrich_question_with_scope,
    format_scope_label,
    period_notice_for_route,
)


def test_format_scope_label():
    assert format_scope_label("Machakos", "2025/26", "FY") == "Machakos · FY 2025/26 · FY"
    assert format_scope_label(None, None, None) == ""


def test_enrich_question_includes_scope():
    out = enrich_question_with_scope(
        "What challenges were faced",
        county_name="Machakos",
        fiscal_year="2025/26",
        period="FY",
    )
    assert "Machakos" in out
    assert "What challenges were faced" in out


def test_clarify_challenges_without_county():
    msg = clarify_if_underspecified(
        "What challenges were faced",
        route="rag",
        county_name=None,
    )
    assert msg is not None
    assert "county" in msg.lower()


def test_no_clarify_when_county_filter_set():
    assert (
        clarify_if_underspecified(
            "What challenges were faced",
            route="rag",
            county_name="Machakos",
        )
        is None
    )


def test_period_notice_for_challenges_fy():
    tip = period_notice_for_route(
        "What challenges were faced",
        route="rag",
        period="FY",
    )
    assert tip is not None
    assert "9M" in tip or "H1" in tip


def test_no_period_notice_for_9m():
    assert (
        period_notice_for_route(
            "What challenges were faced",
            route="rag",
            period="9M",
        )
        is None
    )
