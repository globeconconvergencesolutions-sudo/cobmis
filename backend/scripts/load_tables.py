"""Load parsed all-county tables into public_data (as the ingest role), then run the
cross-period consistency check. Re-running replaces what was loaded from each file.

Usage (from backend/):  python -m scripts.load_tables [--only 9M]
"""

from __future__ import annotations

import argparse
import json
import sys

import psycopg

from app.core.config import get_settings
from app.ingest.parse import doc_key
from app.ingest.tables_to_sql import consistency_check, load_document
from scripts.migrate import _env


def ingest_conninfo() -> str:
    s = get_settings()
    pw = _env("COB_INGEST_PASSWORD")
    if not pw:
        raise SystemExit("COB_INGEST_PASSWORD is not set (.env)")
    return f"host={s.db_host} port={s.db_port} dbname={s.db_name} user=cob_ingest password={pw}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only")
    args = ap.parse_args()
    data_dir = get_settings().data_dir
    from pathlib import Path

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    with psycopg.connect(ingest_conninfo()) as conn:
        for rec in manifest["documents"]:
            if not rec.get("file") or (args.only and args.only not in rec["file"]):
                continue
            doc_json = data_dir / "parsed" / doc_key(Path(rec["file"])) / "document.json"
            if not doc_json.exists():
                print(f"skip (not parsed yet): {rec['file']}")
                continue
            with conn.transaction():
                stats = load_document(conn, doc_json, rec)
            print(f"\n{rec['fiscal_year']} {rec['period']}  doc_id={stats['doc_id']}  facts={stats['facts']}  "
                  f"quarantined_rows={stats['quarantined']}  cross_table_mismatches={stats['cross_table_mismatches']}")
            for name, t in stats["tables"].items():
                flag = "" if t["county_rows"] == 47 and not t["total_failures"] and not t["unmapped_metrics"] else "  <-- check"
                print(f"   p{t['pages']} {name[:80]}: facts={t['facts']} quarantined={t['quarantined_rows']} counties={t['county_rows']}"
                      f"{' unmapped=' + ','.join(t['unmapped_metrics']) if t['unmapped_metrics'] else ''}"
                      f"{' total_failures=' + str(t['total_failures']) if t['total_failures'] else ''}{flag}")
            if stats["missing_specs"]:
                print("   expected tables not found:", ", ".join(stats["missing_specs"]))
        with conn.transaction():
            cc = consistency_check(conn)
    print(f"\nCross-period check (cumulative metrics must not decrease H1 -> 9M -> FY): "
          f"{cc['pairs_checked']} pairs, {len(cc['failures'])} failures")
    for f in cc["failures"][:30]:
        county, metric, fy, p0, p1, v0, v1, pg0, pg1 = f
        print(f"   {fy} {county:16} {metric:36} {p0} {v0:>18,.2f} (p{pg0}) -> {p1} {v1:>18,.2f} (p{pg1})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
