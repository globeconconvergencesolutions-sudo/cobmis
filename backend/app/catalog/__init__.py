"""Deterministic catalog / meta answers from SQL (no RAG, no LLM numbers)."""

from app.catalog.service import (
    CatalogAnswer,
    about_report,
    answer_catalog,
    build_catalog,
    have_coverage,
    render_catalog_answer,
)
from app.catalog.acl import allowed_collections

__all__ = [
    "CatalogAnswer",
    "about_report",
    "allowed_collections",
    "answer_catalog",
    "build_catalog",
    "have_coverage",
    "render_catalog_answer",
]
