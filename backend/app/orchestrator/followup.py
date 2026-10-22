"""Rewrite a conversational follow-up into a standalone question."""

from __future__ import annotations

from app.llm.ollama_client import OllamaClient

_SYSTEM = """You rewrite user follow-up questions into a single standalone question for a
Kenyan county budget assistant. Preserve fiscal year, period, county, and metric intent
from the prior turn when the follow-up is elliptical (e.g. "and Kilifi?").
Output ONLY the rewritten question, nothing else."""


def rewrite_standalone(
    client: OllamaClient,
    followup: str,
    *,
    prior_question: str,
    prior_answer: str | None = None,
) -> str:
    prior_a = (prior_answer or "").strip()
    if len(prior_a) > 800:
        prior_a = prior_a[:800] + "…"
    user = (
        f"Prior question: {prior_question}\n"
        f"Prior answer (optional context):\n{prior_a or '(none)'}\n\n"
        f"Follow-up: {followup}\n\n"
        f"Standalone question:"
    )
    raw = client.chat(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        model=client.settings.chat_model,
        temperature=0.0,
        read_timeout=client.settings.ollama_rewrite_timeout_s,
    ).strip()
    # One line; drop quotes the model sometimes wraps.
    line = raw.splitlines()[0].strip().strip('"').strip("'")
    return line or followup
