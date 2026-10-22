"""Ask: catalog | sql | rag | both (with optional follow-up rewrite).

Usage (from backend/):
  python -m scripts.ask "What data do you have?"
  python -m scripts.ask "Top 5 counties by pending bills in FY 2025/26"
  python -m scripts.ask "What challenges did Kericho face and what was their OSR performance in 9M FY 2025/26?"
  python -m scripts.ask "and what about Kilifi?" --prior "Top 5 counties by pending bills in FY 2025/26"
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from app.catalog import answer_catalog, allowed_collections
from app.core.config import get_settings
from app.db.roles import DatabaseSetupError, connect
from app.ingest.cob_listing import parse_fiscal_year, parse_period
from app.ingest.counties import BY_CODE, county_mentions, match_county
from app.llm.ollama_client import OllamaClient, OllamaError
from app.orchestrator import answer_both, answer_rag, retrieve_hits, rewrite_standalone
from app.retrieval.search import search
from app.router import classify
from app.sql_agent import answer_sql


def _resolve_scope(question: str, args: argparse.Namespace):
    county = match_county(args.county) if args.county else None
    if args.county and not county:
        return None, None, None, f"Unknown county {args.county!r}"
    if county is None:
        mentioned = county_mentions(question)
        if len(mentioned) == 1:
            county = BY_CODE[next(iter(mentioned))]
    fy = args.fy or parse_fiscal_year(question)
    period = args.period or parse_period(question)
    return county, fy, period, None


def _print_sql_block(ans) -> None:
    print("\nHow I got this:")
    if ans.sql:
        print(ans.sql)
    else:
        print("(no SQL produced)")
    if ans.columns and ans.rows:
        print()
        print(" | ".join(ans.columns))
        print(" | ".join("---" for _ in ans.columns))
        for row in ans.rows[:30]:
            print(" | ".join("" if v is None else str(v) for v in row))
        if len(ans.rows) > 30:
            print(f"... ({len(ans.rows)} rows total)")
    if ans.citations:
        print("\nSQL sources:")
        for c in ans.citations:
            pages = c.get("pages")
            p = f", PDF p.{pages[0]}" if pages and pages[0] is not None else ""
            table = f", {c['table']}" if c.get("table") else ""
            print(f"  - {c.get('document')}{p}{table}")
    if ans.repaired:
        print("\n(note: SQL was regenerated once after a validation/execution/empty result)")
    if ans.error:
        print(f"\n(note: {ans.error})")


def _print_rag_sources(citations: list[dict]) -> None:
    print("\nSources:")
    for c in citations:
        pages = c["pages"]
        p = pages[0] if pages[0] == pages[1] else f"{pages[0]}-{pages[1]}"
        table = f", Table {c['table']}" if c.get("table") else ""
        print(f"  [{c['tag']}] {c['document']} ({c['fiscal_year']} {c['period']}), PDF p.{p}{table}")
    if not citations:
        print("  (the answer cites no sources)")


def _print_hits(hits) -> None:
    primary = [h for h in hits if not h.is_national_fallback]
    fallback = [h for h in hits if h.is_national_fallback]

    def one(i, h, prefix="S"):
        pages = h.page_start if h.page_start == h.page_end else f"{h.page_start}-{h.page_end}"
        label = "  [national fallback]" if h.is_national_fallback else ""
        print(f"[{prefix}{i}] {h.score:.4f} {h.kind:9} {h.fiscal_year} {h.period_type} p.{pages}{label}  {h.context_header}")
        print(textwrap.indent(textwrap.shorten(h.text, 400), "      "))

    for i, h in enumerate(primary, 1):
        one(i, h)
    if fallback:
        print("\nNational fallback (capped; used for the answer only if the county pool is empty):")
        for i, h in enumerate(fallback, 1):
            one(i, h, prefix="N")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--county")
    ap.add_argument("--fy")
    ap.add_argument("--period", choices=["Q1", "H1", "9M", "FY"])
    ap.add_argument("-n", type=int, default=None)
    ap.add_argument("--retrieve-only", action="store_true")
    ap.add_argument("--route", choices=["catalog", "sql", "rag", "both", "other"])
    ap.add_argument("--user-id", type=int, default=None)
    ap.add_argument("--prior", help="previous question; rewrites this turn into a standalone question")
    ap.add_argument("--prior-answer", default=None, help="optional prior answer text for follow-up rewrite")
    ap.add_argument("--rank-of", metavar="NEEDLE")
    args = ap.parse_args()
    s = get_settings()

    question = args.question
    client = OllamaClient(s)

    if args.prior:
        try:
            rewritten = rewrite_standalone(
                client, question, prior_question=args.prior, prior_answer=args.prior_answer,
            )
        except OllamaError as exc:
            print(f"Follow-up rewrite failed: {exc}", file=sys.stderr)
            return 2
        print(f"rewritten={rewritten!r}")
        question = rewritten

    route = classify(question, force=args.route)
    print(f"route={route}")

    if route == "catalog":
        with connect("cob_app") as conn:
            allowed = allowed_collections(conn, args.user_id)
            ans = answer_catalog(conn, question, allowed)
        print(ans.text)
        return 0

    county, fy, period, err = _resolve_scope(question, args)
    if err:
        print(err, file=sys.stderr)
        return 1

    if route == "sql":
        try:
            ans = answer_sql(
                client, question,
                fiscal_year=fy, period=period,
                county=county.name if county else None,
            )
        except OllamaError as exc:
            print(f"SQL answering failed: {exc}", file=sys.stderr)
            return 2
        print(ans.text)
        if ans.unsupported_numbers:
            print(f"\nWARNING: figures not found in the SQL result: {', '.join(ans.unsupported_numbers)}")
        _print_sql_block(ans)
        return 0

    try:
        qvec = client.embed([question])[0]
    except OllamaError as exc:
        print(f"Embedding failed ({exc}); falling back to keyword search only.", file=sys.stderr)
        qvec = None

    n = args.n or s.rerank_top_n
    with connect("cob_app") as conn:
        allowed = allowed_collections(conn, args.user_id)
        if args.rank_of:
            deep = search(
                conn, question, qvec, allowed, county.code if county else None, fy, period,
                k=s.retrieval_top_k, n=max(80, n), national_fallback=0,
            )
            rank = next((i for i, h in enumerate(deep, 1) if args.rank_of in (h.context_header or "")), None)
            print(f"rank_of {args.rank_of!r}: {rank} (of {len(deep)} county-scoped hits)")
            for i, h in enumerate(deep[:12], 1):
                pages = h.page_start if h.page_start == h.page_end else f"{h.page_start}-{h.page_end}"
                print(f"  {i:2} {h.score:.4f} county={h.county_id} p.{pages}  {h.context_header}")
            return 0

        if route == "both":
            try:
                both = answer_both(
                    client, conn, question, allowed, qvec,
                    county_id=county.code if county else None,
                    county_name=county.name if county else None,
                    fiscal_year=fy, period=period,
                    k=s.retrieval_top_k, n=n,
                )
            except OllamaError as exc:
                print(f"Both-path failed: {exc}", file=sys.stderr)
                return 2
            print(both.text)
            if both.unsupported_numbers:
                print(f"\nWARNING: figures not found in the SQL result: {', '.join(both.unsupported_numbers)}")
            _print_rag_sources(both.rag_answer.citations)
            _print_sql_block(both.sql_answer)
            return 0

        # rag
        hits = retrieve_hits(
            conn, question, qvec, allowed,
            county_id=county.code if county else None,
            fiscal_year=fy, period=period, k=s.retrieval_top_k, n=n,
        )

    scope = ", ".join(x for x in [county and county.name, fy, period] if x) or "all reports"
    print(f"Retrieved {len(hits)} chunks ({scope}; backend={s.llm_backend})\n")

    if args.retrieve_only or not hits:
        _print_hits(hits)
        if not hits:
            print("Nothing relevant found in the reports you can access.")
            print("The provided reports do not contain this information.")
        return 0

    rag = answer_rag(client, question, hits)
    if rag.error:
        print(f"Answer generation failed: {rag.error}", file=sys.stderr)
        return 2
    print(textwrap.fill(rag.text, 100, replace_whitespace=False) if "\n" not in rag.text else rag.text)
    _print_rag_sources(rag.citations)
    if rag.unsupported_numbers:
        print(f"\nWARNING: figures not found in the cited sources: {', '.join(rag.unsupported_numbers)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DatabaseSetupError as exc:
        print(f"ERROR: {exc.code}", file=sys.stderr)
        sys.exit(1)
