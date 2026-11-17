"""Find and withdraw law changes omitted from the current source snapshot."""

import json
import sqlite3
from datetime import UTC, datetime
from itertools import groupby

SUPERSEDED_NOTE_PREFIX = 'Superseded by snapshot '

OUTSIDE_CURRENT_SNAPSHOT = """
FROM article_changes c JOIN event_sources s ON s.event_id=c.event_id
WHERE NOT EXISTS (
    SELECT 1 FROM change_versions v WHERE v.change_id=c.id AND v.snapshot_id=s.snapshot_id
)
"""


def find_superseded_changes(conn: sqlite3.Connection, *, event_id: str | None = None) -> list[dict]:
    """List stale rows that still need a state, verification timestamp, or hold cleanup."""
    query = f"""
        SELECT c.id AS change_id,c.article,c.event_id,s.snapshot_id
        {OUTSIDE_CURRENT_SNAPSHOT}
        AND (c.review_state!='withdrawn' OR c.verified_at IS NOT NULL
             OR EXISTS (SELECT 1 FROM held_changes h WHERE h.change_id=c.id))
    """
    params = ()
    if event_id is not None:
        query += ' AND c.event_id=?'
        params = (event_id,)
    return [dict(row) for row in conn.execute(query + ' ORDER BY c.event_id,c.id', params)]


def withdraw_superseded_changes(conn: sqlite3.Connection, candidates: list[dict]) -> None:
    """Apply ordered candidates selected within the same write transaction."""
    now = datetime.now(UTC).isoformat(timespec='seconds')
    for (event_id, snapshot_id), changes in groupby(candidates, lambda row: (row['event_id'], row['snapshot_id'])):
        change_ids = [change['change_id'] for change in changes]
        params = [(change_id,) for change_id in change_ids]
        conn.executemany("UPDATE article_changes SET review_state='withdrawn',verified_at=NULL WHERE id=?", params)
        conn.executemany('DELETE FROM held_changes WHERE change_id=?', params)
        audit = conn.execute(
            "INSERT INTO review_audit(event_id,decision,reviewer,reviewed_at,note) VALUES (?,'withdraw','import-adapter',?,?)",
            (event_id, now, SUPERSEDED_NOTE_PREFIX + snapshot_id),
        )
        conn.execute(
            'INSERT INTO review_details VALUES (?,?,?)',
            (audit.lastrowid, snapshot_id, json.dumps({'change_ids': change_ids})),
        )


def count_hidden_holds(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(
        conn.execute(
            f"""
            SELECT (SELECT COUNT(*) {OUTSIDE_CURRENT_SNAPSHOT} AND c.review_state='pending') AS pending_outside_snapshot,
                   (SELECT COUNT(*) FROM held_changes h JOIN article_changes c ON c.id=h.change_id
                    WHERE c.review_state='pending'
                    AND NOT EXISTS (SELECT 1 FROM public_holds p WHERE p.id=h.change_id)) AS hidden_pending_holds
            """
        ).fetchone()
    )
