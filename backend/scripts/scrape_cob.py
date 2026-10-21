"""Download the COB consolidated county BIRR PDFs and maintain data/manifest.json.

Usage (from backend/):
  python -m scripts.scrape_cob                 # full run; reruns skip unchanged files
  python -m scripts.scrape_cob --check-new     # only fetch entries not yet in the manifest
  python -m scripts.scrape_cob --retry-unavailable
  python -m scripts.scrape_cob --report        # print the manifest report, no network
  python -m scripts.scrape_cob --listing-file page.html   # parse a saved page (offline)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.ingest.scrape import LISTING_URL, run


def report(manifest_path: Path) -> str:
    if not manifest_path.exists():
        return f"No manifest at {manifest_path}"
    docs = json.loads(manifest_path.read_text(encoding="utf-8"))["documents"]
    docs.sort(key=lambda d: (d.get("fiscal_year") or "", d.get("period") or "", d["title"]))
    counts = Counter(d["status"] for d in docs)
    total_mb = sum(d.get("size") or 0 for d in docs) / 1024**2
    lines = [
        f"Manifest: {manifest_path}",
        f"Entries: {len(docs)}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())) + f"  ({total_mb:,.1f} MB on disk)",
        "",
    ]
    for status in ("ok", "scanned", "unavailable", "failed"):
        group = [d for d in docs if d["status"] == status]
        if not group:
            continue
        lines.append(f"== {status.upper()} ({len(group)})")
        for d in group:
            size = f"{(d.get('size') or 0) / 1024**2:6.1f} MB" if d.get("size") else f"listed {d.get('listed_size') or '?'}"
            extra = f"  [{d['reason']}]" if d.get("reason") else ""
            notes = f"  notes: {'; '.join(d['parse_notes'])}" if d.get("parse_notes") else ""
            lines.append(f"  {d.get('fiscal_year') or '????/??'} {d.get('period') or '??':3}  {size:>14}  {d['title'][:90]}{extra}{notes}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=LISTING_URL)
    ap.add_argument("--check-new", action="store_true")
    ap.add_argument("--retry-unavailable", action="store_true")
    ap.add_argument("--limit", type=int, help="process at most N entries (for a trial run)")
    ap.add_argument("--listing-file", type=Path, help="parse this saved HTML instead of fetching the listing")
    ap.add_argument("--report", action="store_true", help="only print the report from the existing manifest")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_dir = get_settings().data_dir
    manifest_path = data_dir / "manifest.json"
    if not args.report:
        html = args.listing_file.read_text(encoding="utf-8") if args.listing_file else None
        try:
            summary = run(
                data_dir, args.url, check_new=args.check_new, retry_unavailable=args.retry_unavailable,
                limit=args.limit, listing_html=html,
            )
        except httpx.HTTPError as exc:
            print(f"Could not fetch the listing page {args.url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print("\nRun summary:", json.dumps(summary))
    print(report(manifest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
