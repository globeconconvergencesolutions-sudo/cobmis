from app.orchestrator.merge import merge_both
from app.orchestrator.rag_path import RagAnswer
from app.sql_agent.pipeline import SqlAnswer


def test_merge_sql_empty_uses_rag_only():
    # merge_both without a client when one side empty — call the early branches via empty sql.
    # We only test the early-return paths that do not need Ollama.
    sql = SqlAnswer(text="", sql=None, columns=[], rows=[])
    rag = RagAnswer(text="Kericho faced late reporting [S1].", citations=[{"tag": "S1"}])
    # Import the early-return logic by calling with a dummy — empty sql short-circuits before client use.
    class _Boom:
        settings = type("S", (), {"chat_model": "x"})()

        def chat(self, *a, **k):
            raise AssertionError("should not call chat when SQL empty")

    text, bad = merge_both(_Boom(), "challenges?", sql, rag)
    assert "late reporting" in text
    assert bad == []


def test_merge_both_empty():
    class _Boom:
        settings = type("S", (), {"chat_model": "x"})()

        def chat(self, *a, **k):
            raise AssertionError("no chat")

    text, bad = merge_both(
        _Boom(), "q",
        SqlAnswer(text="", sql=None),
        RagAnswer(text=""),
    )
    assert "do not contain" in text.lower()
    assert bad == []
