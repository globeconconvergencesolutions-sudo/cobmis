from app.sql_agent.narrate import numbers_in_rows, unsupported_numbers_in_text


def test_unsupported_number_flagged():
    cols = ["county", "value", "source_page"]
    rows = [["Turkana", 22.0, 62]]
    text = "Turkana's development absorption was 22 percent, not 99."
    bad = unsupported_numbers_in_text(text, cols, rows)
    assert "99" in bad
    assert "22" not in bad


def test_numbers_in_rows_normalises():
    cols = ["value"]
    rows = [[86899380000]]
    nums = numbers_in_rows(cols, rows)
    assert "86899380000" in nums


def test_millions_and_fy_tokens_allowed():
    cols = ["value"]
    rows = [[637880000], [1037180000]]
    text = "OSR was KES 637.88 million; payables KES 1.037 billion in FY 2024/25."
    assert unsupported_numbers_in_text(text, cols, rows) == []
