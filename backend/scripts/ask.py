"""Ask a question from the command line: hybrid retrieval + grounded, cited answer.

Usage (from backend/):
  python -m scripts.ask "What challenges did Kericho County face in the first nine months?"
  python -m scripts.ask "pending bills Nairobi City" --retrieve-only
  python -m scripts.ask "..." --county Turkana --period 9M --fy 2025/26 -n 8
This is the narrative (RAG) path only; numeric/SQL answering is Phase 4.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

import psycopg

from app.core.config import get_settings
from app.ingest.counties import BY_CODE, county_mentions, match_county
from app.llm.answer import build_messages, finalize
from app.llm.ollama_client import OllamaClient, OllamaError
from app.retrieval.search import search
from scripts.migrate import _env


def app_conninfo() -> str:
    s = get_settings()
    pw = _env("COB_APP_PASSWORD") or s.app_password
    return f"host={s.db_host} port={s.db_port} dbname={s.db_name} user=cob_app password={pw}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--county", help="restrict to one county (plus all-county material)")
    ap.add_argument("--fy", help="fiscal year, e.g. 2025/26")
    ap.add_argument("--period", choices=["Q1", "H1", "9M", "FY"])
    ap.add_argument("-n", type=int, default=None, help="chunks given to the model")
    ap.add_argument("--retrieve-only", action="store_true", help="show retrieved chunks, skip the LLM")
    args = ap.parse_args()
    s = get_settings()

    county = match_county(args.county) if args.county else None
    if args.county and not county:
        print(f"Unknown county {args.county!r}", file=sys.stderr)
        return 1
    if county is None:
        mentioned = county_mentions(args.question)
        if len(mentioned) == 1:  # the question names exactly one county: filter to it
            county = BY_CODE[next(iter(mentioned))]

    client = OllamaClient(s)
    try:
        qvec = client.embed([args.question])[0]
    except OllamaError as exc:
        print(f"Embedding failed ({exc}); falling back to keyword search only.", file=sys.stderr)
        qvec = None

    with psycopg.connect(app_conninfo()) as conn:
        allowed = [r[0] for r in conn.execute("SELECT collection_id FROM app.collections WHERE visibility = 'public'")]
        hits = search(conn, args.question, qvec, allowed, county.code if county else None, args.fy, args.period,
                      k=s.retrieval_top_k, n=args.n or s.rerank_top_n)

    scope = ", ".join(x for x in [county and county.name, args.fy, args.period] if x) or "all reports"
    print(f"Retrieved {len(hits)} chunks ({scope}; backend={s.llm_backend})\n")
    if args.retrieve_only or not hits:
        for i, h in enumerate(hits, 1):
            pages = h.page_start if h.page_start == h.page_end else f"{h.page_start}-{h.page_end}"
            print(f"[S{i}] {h.score:.4f} {h.kind:9} {h.fiscal_year} {h.period_type} p.{pages}  {h.context_header}")
            print(textwrap.indent(textwrap.shorten(h.text, 400), "      "))
        if not hits:
            print("Nothing relevant found in the reports you can access.")
        return 0

    try:
        raw = client.chat(build_messages(args.question, hits), model=s.chat_model)
    except OllamaError as exc:
        print(f"Answer generation failed: {exc}", file=sys.stderr)
        return 2
    ans = finalize(raw, hits)
    print(textwrap.fill(ans.text, 100, replace_whitespace=False) if "\n" not in ans.text else ans.text)
    print("\nSources:")
    for c in ans.citations:
        pages = c["pages"][0] if c["pages"][0] == c["pages"][1] else f"{c['pages'][0]}-{c['pages'][1]}"
        table = f", Table {c['table']}" if c["table"] else ""
        print(f"  [{c['tag']}] {c['document']} ({c['fiscal_year']} {c['period']}), PDF p.{pages}{table}")
    if not ans.citations:
        print("  (the answer cites no sources)")
    if ans.unsupported_numbers:
        print(f"\nWARNING: figures not found in the cited sources: {', '.join(ans.unsupported_numbers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
