"""One command for the whole local pipeline. Every step is resumable; rerun after any stop.

  1. migrate      create/update database roles, apply db/migrations (owner role)
  2. parse        PDFs -> data/parsed/<doc>/document.json (batched, parallel, resumable)
  3. tables       all-county tables -> public_data facts, with validation + quarantine
  4. chunk        narrative + table chunks -> content.chunks (unchanged chunks are kept)
  5. embed        chunks without a vector -> Ollama embeddings, committed per batch

Usage (from backend/):
  python -m scripts.ingest                      # everything in data/manifest.json
  python -m scripts.ingest --only 9M            # one report
  python -m scripts.ingest --skip-embed         # stop before embedding
  python -m scripts.ingest --embed-limit 200    # trial: embed at most 200 chunks
Uses COB_LLM_BACKEND (ollama | mock) from .env for embeddings.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from app.core.config import get_settings
from app.db.roles import DatabaseSetupError, connect
from app.ingest.chunk import chunk_document
from app.ingest.parse import doc_key, parse_document
from app.ingest.store import embed_pending, sync_chunks, upsert_document
from app.ingest.tables_to_sql import consistency_check, load_document
from app.llm.ollama_client import OllamaClient, OllamaError
from scripts.migrate import migrate


def _finish(parse_failures: list[str], log: logging.Logger) -> int:
    if parse_failures:
        log.error("not ingested (re-run to retry): %s", ", ".join(parse_failures))
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--skip-parse", action="store_true")
    ap.add_argument("--skip-tables", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--embed-limit", type=int)
    ap.add_argument("--chunk-tokens", type=int, default=600)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per embed batch is noise
    log = logging.getLogger("ingest")
    s = get_settings()
    data_dir = s.data_dir

    res = migrate()
    log.info("roles created: %s; migrations: %s", ", ".join(res["roles_created"]) or "none",
             ", ".join(res["applied"]) or "up to date")

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    recs = [r for r in manifest["documents"] if r["status"] in ("ok", "scanned") and r.get("file")]
    if args.only:
        recs = [r for r in recs if args.only in r["file"]]

    doc_ids = []
    parse_failures: list[str] = []
    with connect("cob_ingest") as conn:
        for rec in recs:
            pdf = data_dir / "raw" / rec["file"]
            if not pdf.exists():
                log.warning("missing %s; skipped", pdf)
                continue
            doc_json = data_dir / "parsed" / doc_key(Path(rec["file"])) / "document.json"
            if not args.skip_parse:
                try:
                    doc_json = parse_document(pdf, data_dir / "parsed", workers=args.workers)
                except RuntimeError as exc:  # some batches failed; finished ones are saved
                    log.error("%s", exc)
                    parse_failures.append(rec["file"])
                    continue
            if not doc_json.exists():
                log.error("%s has not been parsed yet (run without --skip-parse)", rec["file"])
                parse_failures.append(rec["file"])
                continue
            doc = json.loads(doc_json.read_text(encoding="utf-8"))

            if not args.skip_tables:
                with conn.transaction():
                    stats = load_document(conn, doc_json, rec)
                log.info("%s %s tables: %d facts, %d quarantined rows", rec["fiscal_year"], rec["period"], stats["facts"], stats["quarantined"])

            chunks = chunk_document(doc, rec["fiscal_year"], rec["period"], target=args.chunk_tokens)
            with conn.transaction():
                doc_id = upsert_document(conn, rec, doc["page_count"], doc["parse_version"])
                res = sync_chunks(conn, doc_id, chunks)
            doc_ids.append(doc_id)
            log.info("%s %s chunks: %d kept, %d added, %d removed", rec["fiscal_year"], rec["period"], res["kept"], res["added"], res["removed"])

        if not args.skip_tables:
            with conn.transaction():
                cc = consistency_check(conn)
            log.info("cross-period check: %d pairs, %d failures", cc["pairs_checked"], len(cc["failures"]))

        if args.skip_embed:
            return _finish(parse_failures, log)
        client = OllamaClient(s)
        try:
            client.embed(["warm-up"])  # fail fast if Ollama or the model is missing
        except OllamaError as exc:
            log.error("embedding model unavailable: %s", exc)
            return 2

        def progress(done: int, total: int, elapsed: float) -> None:
            rate = done / elapsed if elapsed else 0
            eta = (total - done) / rate if rate else 0
            log.info("embedded %d/%d chunks (%.1f/s, ~%.0f min left)", done, total, rate, eta / 60)

        t = time.time()
        n = embed_pending(conn, client.embed, s.embed_model, s.embed_batch_size, doc_ids, args.embed_limit, progress)
        log.info("embedding done: %d chunks in %.0fs (backend=%s, model=%s)", n, time.time() - t, s.llm_backend, s.embed_model)
    return _finish(parse_failures, log)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DatabaseSetupError as exc:
        print(f"ERROR: {exc.code}", file=sys.stderr)
        sys.exit(1)
