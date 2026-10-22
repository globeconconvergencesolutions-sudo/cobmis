"""Question router: catalog | sql | rag | both.

Heuristics only (no LLM). Order: catalog → both → rag-intent → sql → rag.
Legacy force/value ``other`` is accepted as an alias for ``rag``.
"""

from __future__ import annotations

import re
from typing import Literal

Route = Literal["catalog", "sql", "rag", "both"]
_Force = Route | Literal["other"]

_CATALOG_RE = re.compile(
    r"(?:"
    r"what data do you have|what documents|what sources|what can i ask|"
    r"which (?:years|counties|periods)|do you have|"
    r"what is this report about|about this (?:report|dataset)|"
    r"how up to date|how current is|"
    r"metric dictionary|what metrics|"
    r"coverage|what reports (?:do you|are)|"
    r"example questions|starter questions"
    r")",
    re.I,
)

_RAG_INTENT_RE = re.compile(
    r"\b(challenges?|issues?|problems?|recommendations?|observations?|why did|what did .+ report)\b",
    re.I,
)

_METRIC_CUE_RE = re.compile(
    r"\b("
    r"absorption|pending bills?|own[\s-]*source|osr|exchequer|"
    r"budget|expenditure|revenue|trade payables?|"
    r"how much|figures?|numbers?|percent|%|performance"
    r")\b",
    re.I,
)

_BOTH_PHRASE_RE = re.compile(
    r"(?:"
    r"performance and challenges|challenges and (?:osr|own[\s-]*source|absorption|pending|revenue|budget)|"
    r"explain .{0,40}\b(with )?(figures?|numbers?|how much)|"
    r"what happened and by how much|"
    r"\bwhy\b.{0,60}\b(absorption|revenue|osr|pending|budget|expenditure)\b|"
    r"\b(absorption|revenue|osr|pending|budget).{0,60}\bwhy\b"
    r")",
    re.I,
)

_SQL_RE = re.compile(
    r"(?:"
    r"\b(highest|lowest|top\s*\d+|bottom\s*\d+|rank(?:ing|ed)?|compare|comparison|"
    r"how much|how many|average|mean|total|sum|percent|%|"
    r"absorption|pending bills?|own[\s-]*source|osr|exchequer|"
    r"budget|expenditure|revenue target|trade payables?)\b"
    r")",
    re.I,
)


def _normalize_force(force: _Force | None) -> Route | None:
    if force is None:
        return None
    if force == "other":
        return "rag"
    return force


def has_narrative_intent(question: str) -> bool:
    return bool(_RAG_INTENT_RE.search(question or ""))


def classify(question: str, *, force: _Force | None = None) -> Route:
    forced = _normalize_force(force)
    if forced is not None:
        return forced
    q = (question or "").strip()
    if not q:
        return "rag"
    if _CATALOG_RE.search(q):
        return "catalog"
    # Hybrid: narrative intent plus metric/figure cues, or explicit both-phrases.
    if _BOTH_PHRASE_RE.search(q) or (_RAG_INTENT_RE.search(q) and _METRIC_CUE_RE.search(q)):
        return "both"
    if _RAG_INTENT_RE.search(q):
        return "rag"
    if _SQL_RE.search(q):
        return "sql"
    return "rag"
