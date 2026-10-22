"""Question intent helpers for retrieval: synonym expansion and section-title boosts.

No re-embedding. The query vector stays the user's words; only the keyword (FTS) side and
post-fusion ranking use these signals.
"""

from __future__ import annotations

import re

# Challenge / observation questions. Section titles in the COB reports usually say
# "General Observations and Recommendations", not "Challenges".
INTENT_KEYWORDS = (
    "challenge",
    "challenges",
    "issue",
    "issues",
    "problem",
    "problems",
    "recommendation",
    "recommendations",
    "observation",
    "observations",
)

# Extra FTS terms when the question matches the intent (OR'd with the original terms).
INTENT_SYNONYMS = (
    "challenges",
    "issues",
    "problems",
    "recommendations",
    "observations",
    "general observations",
)

_INTENT_RE = re.compile(
    r"\b(challenges?|issues?|problems?|recommendations?|observations?)\b",
    re.I,
)
_SECTION_INTENT_RE = re.compile(
    r"\b(challenge|issue|problem|recommendation|observation)s?\b",
    re.I,
)

# Roughly one RRF step at rank 1 (1/(60+1) ≈ 0.016); enough to lift a mid-list
# observations chunk without drowning a strong keyword match.
SECTION_TITLE_BONUS = 0.018


def has_observation_intent(question: str) -> bool:
    return bool(_INTENT_RE.search(question or ""))


def expand_query_for_fts(question: str) -> str:
    """Return the text used for full-text search (original + intent synonyms when needed)."""
    q = (question or "").strip()
    if not q or not has_observation_intent(q):
        return q
    extra = " ".join(t for t in INTENT_SYNONYMS if t.lower() not in q.lower())
    return f"{q} {extra}".strip() if extra else q


def section_matches_intent(section_title: str | None, context_header: str | None = None) -> bool:
    hay = " ".join(x for x in (section_title, context_header) if x)
    return bool(_SECTION_INTENT_RE.search(hay))


def intent_score_bonus(question: str, section_title: str | None, context_header: str | None = None) -> float:
    if not has_observation_intent(question):
        return 0.0
    return SECTION_TITLE_BONUS if section_matches_intent(section_title, context_header) else 0.0
