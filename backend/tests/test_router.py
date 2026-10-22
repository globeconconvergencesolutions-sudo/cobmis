from app.router.classify import classify


def test_catalog_triggers():
    for q in (
        "What data do you have?",
        "what documents are loaded?",
        "What sources do you use?",
        "What can I ask?",
        "Which years and counties are covered?",
        "Do you have FY 2022/23?",
        "What is this report about?",
        "How up to date is this?",
    ):
        assert classify(q) == "catalog", q


def test_sql_triggers():
    for q in (
        "Top 5 counties by pending bills in FY 2025/26",
        "Turkana development absorption first nine months",
        "Compare Nairobi and Mombasa OSR performance H1",
        "How much was Baringo's total budget in H1?",
        "Which county had the highest absorption rate?",
    ):
        assert classify(q) == "sql", q


def test_rag_pure_challenges():
    assert classify("What challenges did Kericho face?") == "rag"
    assert classify("Recommendations for Kisumu County") == "rag"
    assert classify("What challenges did Kericho County face in the first nine months of FY 2025/26?") == "rag"


def test_both_when_narrative_and_metric():
    assert classify(
        "What challenges did Kericho face and what was their OSR performance in 9M FY 2025/26?"
    ) == "both"
    assert classify("Kericho challenges and own-source revenue") == "both"
    assert classify("performance and challenges for Turkana") == "both"
    assert classify("explain Nairobi absorption with figures") == "both"


def test_force_override_and_other_alias():
    assert classify("challenges", force="catalog") == "catalog"
    assert classify("What data do you have?", force="other") == "rag"
    assert classify("challenges", force="sql") == "sql"
    assert classify("pending bills", force="both") == "both"
