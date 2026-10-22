"""Collection ACL for catalog (and later chat). Enforce in SQL, never by post-filtering titles."""

from __future__ import annotations

import psycopg


def allowed_collections(conn: psycopg.Connection, user_id: int | None = None) -> list[int]:
    """Collection ids the caller may read.

    ``user_id is None``: every public collection (anonymous / default CLI).
    Otherwise: all public collections plus restricted ones with ``can_read`` for that user.
    """
    if user_id is None:
        rows = conn.execute(
            "SELECT collection_id FROM app.collections WHERE visibility = 'public' ORDER BY collection_id"
        ).fetchall()
        return [r[0] for r in rows]
    rows = conn.execute(
        """
        SELECT c.collection_id
        FROM app.collections c
        WHERE c.visibility = 'public'
           OR EXISTS (
                SELECT 1 FROM app.collection_access a
                WHERE a.collection_id = c.collection_id
                  AND a.user_id = %(uid)s
                  AND a.can_read
           )
        ORDER BY c.collection_id
        """,
        {"uid": user_id},
    ).fetchall()
    return [r[0] for r in rows]
