"""Merge SQL figures with RAG narrative into one answer."""

from __future__ import annotations

import json
from typing import Any

from app.llm.ollama_client import OllamaClient
from app.orchestrator.rag_path import RagAnswer
from app.sql_agent.narrate import unsupported_numbers_in_text
from app.sql_agent.pipeline import SqlAnswer

MERGE_SYSTEM = """You merge two evidence packs into one answer about Kenyan county budgets.

Pack A — TABLE FIGURES (authoritative for all money amounts and percentages):
Use ONLY these numbers for figures. Never invent totals or rates.
Copy KES amounts from Pack A as full shillings (e.g. 637880000) or as millions only when
million × 1,000,000 equals the Pack A value (e.g. KES 637.88 million for 637880000).
Do not copy money or % figures from Pack B when Pack A has that metric.

Pack B — NARRATIVE SOURCES (authoritative for challenges, recommendations, qualitative facts):
Cite them with the same [S1], [S2] tags as given.

Rules:
- Prefer Pack A numbers whenever a figure appears in both packs.
- If Pack A is empty, answer from narrative only and say figures were not available in the structured tables.
- If Pack B is empty, answer from the table figures only.
- Be concise. Keep [S#] citations on narrative claims.
- Do not mention "Pack A", "Pack B", SQL, databases, or queries in the answer — write for a budget analyst."""


def merge_both(
    client: OllamaClient,
    question: str,
    sql_ans: SqlAnswer,
    rag_ans: RagAnswer,
) -> tuple[str, list[str]]:
    """Return (merged_text, unsupported_numbers vs SQL rows when SQL has data)."""
    sql_empty = not sql_ans.rows
    rag_empty = rag_ans.empty

    if sql_empty and rag_empty:
        return "The provided reports do not contain this information.", []
    if sql_empty:
        return rag_ans.text or "The provided reports do not contain this information.", []
    if rag_empty:
        return sql_ans.text or "The loaded reports do not contain this information.", []

    sql_block = {
        "sql": sql_ans.sql,
        "columns": sql_ans.columns,
        "rows": sql_ans.rows[:40],
        "narration": sql_ans.text,
    }
    rag_block = {
        "answer": rag_ans.text,
        "sources": [
            {
                "tag": c.get("tag"),
                "document": c.get("document"),
                "pages": c.get("pages"),
                "snippet": c.get("snippet"),
            }
            for c in rag_ans.citations
        ],
    }
    messages = [
        {"role": "system", "content": MERGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Pack A — TABLE FIGURES (JSON):\n{json.dumps(sql_block, default=str)}\n\n"
                f"Pack B — NARRATIVE (JSON):\n{json.dumps(rag_block, default=str)}\n\n"
                "Merged answer:"
            ),
        },
    ]
    text = client.chat(messages, model=client.settings.chat_model, temperature=0.0).strip()
    bad = unsupported_numbers_in_text(text, sql_ans.columns, sql_ans.rows)
    return text, bad
