"""Phase 2: parse downloaded PDFs into data/parsed/<doc>/document.json (resumable, batched).

Usage (from backend/):
  python -m scripts.parse_docs                    # every ok/scanned PDF in data/manifest.json
  python -m scripts.parse_docs --only 9M          # files whose name contains "9M"
  python -m scripts.parse_docs --workers 6 --batch-size 25
Interrupt at any time; rerunning continues from the last finished batch.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from app.core.config import get_settings
from app.ingest.parse import parse_document


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="substring filter on file name")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--max-pages", type=int, help="parse only the first N pages (trial runs)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    data_dir = get_settings().data_dir
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    docs = [d for d in manifest["documents"] if d["status"] in ("ok", "scanned") and d.get("file")]
    if args.only:
        docs = [d for d in docs if args.only in d["file"]]
    if not docs:
        print("No downloaded documents match.", file=sys.stderr)
        return 1
    for d in docs:
        pdf = data_dir / "raw" / d["file"]
        if not pdf.exists():
            print(f"missing file {pdf}", file=sys.stderr)
            continue
        t = time.time()
        out = parse_document(pdf, data_dir / "parsed", batch_size=args.batch_size, workers=args.workers, max_pages=args.max_pages)
        doc = json.loads(out.read_text(encoding="utf-8"))
        print(
            f"{d['fiscal_year']} {d['period']}: {doc['page_count']} pages, {len(doc['tables'])} tables, "
            f"{sum(p['needs_ocr'] for p in doc['pages'])} OCR-candidate pages -> {out} ({time.time() - t:.0f}s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
