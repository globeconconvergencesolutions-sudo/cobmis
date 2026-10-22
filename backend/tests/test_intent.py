from app.retrieval.intent import (
    expand_query_for_fts,
    has_observation_intent,
    intent_score_bonus,
    section_matches_intent,
)


def test_observation_intent_detection():
    assert has_observation_intent("What challenges did Kericho face?")
    assert has_observation_intent("key recommendations for Nairobi")
    assert not has_observation_intent("own source revenue target 870 million")


def test_expand_query_adds_synonyms_once():
    q = expand_query_for_fts("challenges")
    assert "challenges" in q
    assert "observations" in q
    assert "recommendations" in q
    assert expand_query_for_fts("own source revenue") == "own source revenue"


def test_section_title_boost_for_intent():
    header = (
        "County Governments Budget Implementation Review Report, FY 2025/26, "
        "First Nine Months, Kericho County, 3.12.19 General Observations and Recommendations"
    )
    assert section_matches_intent("3.12.19 General Observations and Recommendations", header)
    assert intent_score_bonus("challenges", "3.12.19 General Observations and Recommendations", header) > 0
    assert intent_score_bonus("own source revenue", "3.12.19 General Observations and Recommendations", header) == 0
