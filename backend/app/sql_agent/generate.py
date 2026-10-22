"""LLM SQL generation (Ollama sql_model)."""

from __future__ import annotations

import re

from app.llm.ollama_client import OllamaClient
from app.sql_agent.schema_prompt import build_system_prompt, build_user_prompt


def extract_sql(raw: str) -> str:
    text = (raw or "").strip()
    fence = re.search(r"```(?:sql)?\s*([\s\S]*?)```", text, re.I)
    if fence:
        return fence.group(1).strip()
    # If the model added a preamble, take from the first SELECT/WITH.
    m = re.search(r"(?is)\b(with|select)\b", text)
    if m:
        return text[m.start():].strip().rstrip(";")
    return text.rstrip(";")


def generate_sql(
    client: OllamaClient,
    question: str,
    *,
    fiscal_year: str | None = None,
    period: str | None = None,
    county: str | None = None,
    repair_error: str | None = None,
    previous_sql: str | None = None,
) -> str:
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {
            "role": "user",
            "content": build_user_prompt(
                question,
                fiscal_year=fiscal_year,
                period=period,
                county=county,
                repair_error=repair_error,
                previous_sql=previous_sql,
            ),
        },
    ]
    raw = client.chat(messages, model=client.settings.sql_model, temperature=0.0)
    return extract_sql(raw)
