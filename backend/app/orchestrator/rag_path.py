"""Shared RAG answer path (hybrid retrieval + grounded chat)."""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from app.llm.answer import Answer, build_messages, finalize
from app.llm.ollama_client import OllamaClient, OllamaError
from app.retrieval.search import Hit, hits_for_answer, search


@dataclass
class RagAnswer:
    text: str
    citations: list[dict] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    all_hits: list[Hit] = field(default_factory=list)
    error: str | None = None

    @property
    def empty(self) -> bool:
        return not self.hits and not self.text


def retrieve_hits(
    conn: psycopg.Connection,
    question: str,
    query_vector: list[float] | None,
    allowed: list[int],
    *,
    county_id: int | None = None,
    fiscal_year: str | None = None,
    period: str | None = None,
    k: int = 30,
    n: int = 8,
) -> list[Hit]:
    return search(
        conn, question, query_vector, allowed,
        county_id=county_id, fiscal_year=fiscal_year, period=period, k=k, n=n,
    )


def answer_rag(
    client: OllamaClient,
    question: str,
    hits: list[Hit],
    *,
    retrieve_only: bool = False,
) -> RagAnswer:
    """Grounded answer over ``hits``. If retrieve_only, return empty text with hits kept."""
    answer_hits = hits_for_answer(hits)
    if retrieve_only:
        return RagAnswer(text="", hits=answer_hits, all_hits=hits)
    if not answer_hits:
        return RagAnswer(
            text="The provided reports do not contain this information.",
            hits=[],
            all_hits=hits,
        )
    try:
        raw = client.chat(build_messages(question, answer_hits), model=client.settings.chat_model)
    except OllamaError as exc:
        return RagAnswer(text="", hits=answer_hits, all_hits=hits, error=str(exc))
    ans: Answer = finalize(raw, answer_hits)
    return RagAnswer(
        text=ans.text,
        citations=ans.citations,
        unsupported_numbers=ans.unsupported_numbers,
        hits=answer_hits,
        all_hits=hits,
    )
