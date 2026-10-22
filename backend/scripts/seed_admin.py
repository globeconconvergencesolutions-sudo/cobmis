"""Create or reset the local admin user for the Evidence Terminal UI.

Usage (from backend/):
  python -m scripts.seed_admin
  python -m scripts.seed_admin --email admin@local --password change-me-admin
"""

from __future__ import annotations

import argparse
import sys

from app.auth.passwords import hash_password
from app.db.roles import DatabaseSetupError, connect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", default="admin@local")
    ap.add_argument("--password", default="change-me-admin")
    ap.add_argument("--name", default="Admin")
    args = ap.parse_args()

    pw_hash = hash_password(args.password)
    with connect("cob_app") as conn:
        row = conn.execute(
            "SELECT user_id FROM app.users WHERE lower(email) = lower(%s)",
            (args.email,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE app.users
                SET password_hash = %s, display_name = %s, role = 'admin', is_active = true
                WHERE user_id = %s
                """,
                (pw_hash, args.name, row[0]),
            )
            uid = row[0]
            action = "updated"
        else:
            uid = conn.execute(
                """
                INSERT INTO app.users (email, display_name, role, password_hash)
                VALUES (%s, %s, 'admin', %s) RETURNING user_id
                """,
                (args.email.lower(), args.name, pw_hash),
            ).fetchone()[0]
            action = "created"
        conn.commit()
    print(f"Admin user {action}: id={uid} email={args.email}")
    print("Log in at http://127.0.0.1:3000/login")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DatabaseSetupError as exc:
        print(f"ERROR: {exc.code}", file=sys.stderr)
        sys.exit(1)
