"""Score eval/golden_questions.yaml: route accuracy + catalog/sql checks.

Usage (from backend/):
  python -m scripts.eval_run
  python -m scripts.eval_run --routes-only
Exit 0 when route accuracy >= --min-route (default 0.85) and all refuse cases pass.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from app.core.config import REPO_ROOT, get_settings
from app.db.roles import DatabaseSetupError, connect
from app.router import classify
from app.sql_agent.execute import execute_sql
from app.sql_agent.validate import validate_sql

GOLDEN = REPO_ROOT / "eval" / "golden_questions.yaml"


def _norm_route(r: str | None) -> str:
    if r == "other":
        return "rag"
    return r or "rag"


def _load() -> list[dict]:
    data = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    return list(data.get("questions") or [])


def _check_contains(text: str, needles: list[str]) -> list[str]:
    missing = []
    low = text.lower()
    for n in needles:
        if n.lower() not in low:
            missing.append(n)
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--routes-only", action="store_true")
    ap.add_argument("--min-route", type=float, default=0.85)
    args = ap.parse_args()
    items = _load()
    if not items:
        print(f"No questions in {GOLDEN}", file=sys.stderr)
        return 2

    route_ok = 0
    route_fail: list[str] = []
    for item in items:
        q = item["question"]
        expected = _norm_route(item.get("expect", {}).get("route") or item.get("route"))
        got = classify(q)
        if got == expected:
            route_ok += 1
        else:
            route_fail.append(f"{item.get('id')}: expected {expected}, got {got} :: {q[:70]}")

    route_acc = route_ok / len(items)
    print(f"Route accuracy: {route_ok}/{len(items)} = {route_acc:.1%}")
    for line in route_fail:
        print(f"  FAIL {line}")

    if args.routes_only:
        return 0 if route_acc >= args.min_route and not route_fail else 1

    # Heavier checks needing DB (catalog contains / sql_check / refuse).
    refuse_ok = 0
    refuse_n = 0
    sql_ok = 0
    sql_n = 0
    try:
        conn = connect("cob_readonly")
    except DatabaseSetupError as exc:
        print(f"Skipping DB checks: {exc.code}", file=sys.stderr)
        conn = None

    if conn is not None:
        with conn:
            for item in items:
                exp = item.get("expect") or {}
                if exp.get("refuse"):
                    refuse_n += 1
                    sc = exp.get("sql_check")
                    route = _norm_route(item.get("route") or exp.get("route"))
                    if sc and route == "sql":
                        try:
                            sql = validate_sql(sc)
                            res = execute_sql(sql, conn=conn)
                            if res.error is None and res.empty:
                                refuse_ok += 1
                            else:
                                print(
                                    f"  REFUSE FAIL {item.get('id')}: "
                                    f"error={res.error!r} rows={len(res.rows)}"
                                )
                        except Exception as exc:
                            print(f"  REFUSE FAIL {item.get('id')}: {exc}")
                    else:
                        # No sql_check: treat as pass if route classified correctly (above).
                        refuse_ok += 1

                sc = exp.get("sql_check")
                if sc and not exp.get("refuse"):
                    sql_n += 1
                    try:
                        sql = validate_sql(sc)
                        res = execute_sql(sql, conn=conn)
                        if res.error:
                            print(f"  SQL FAIL {item.get('id')}: {res.error}")
                        elif res.empty:
                            print(f"  SQL FAIL {item.get('id')}: empty result")
                        else:
                            sql_ok += 1
                    except Exception as exc:
                        print(f"  SQL FAIL {item.get('id')}: {exc}")

        print(f"SQL checks: {sql_ok}/{sql_n}")
        print(f"Refuse checks: {refuse_ok}/{refuse_n}")

    refuse_pass = refuse_n == 0 or refuse_ok == refuse_n
    ok = route_acc >= args.min_route and not route_fail and refuse_pass
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
