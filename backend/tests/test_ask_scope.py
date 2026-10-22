"""CLI scope inference (county / FY / period) without talking to the LLM."""

import argparse

from app.ingest.cob_listing import parse_fiscal_year, parse_period
from app.ingest.counties import match_county
from scripts.ask import _resolve_scope


def _args(**kwargs):
    return argparse.Namespace(
        question=kwargs.get("question", ""),
        county=kwargs.get("county"),
        fy=kwargs.get("fy"),
        period=kwargs.get("period"),
    )


def test_resolve_scope_from_question_text():
    county, fy, period, err = _resolve_scope(
        "What challenges did Kericho County face in the first nine months of FY 2025/26?",
        _args(),
    )
    assert err is None
    assert county and county.name == "Kericho"
    assert fy == "2025/26"
    assert period == "9M"


def test_resolve_scope_flags_override_question():
    county, fy, period, err = _resolve_scope(
        "Kisumu challenges FY 2019/20",
        _args(county="Nairobi City", fy="2025/26", period="FY"),
    )
    assert err is None
    assert county and county.code == 47
    assert fy == "2025/26" and period == "FY"


def test_kisumu_year_parses_for_refusal_path():
    assert parse_fiscal_year("Kisumu County FY 2019/20") == "2019/20"
    assert parse_period("first nine months") == "9M"
    assert match_county("Kisumu").code == 42
