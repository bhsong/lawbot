"""Explicit human review tied to the current immutable source snapshot."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from law_calendar.db import read_database, write_database
from law_calendar.ingest import utc_now
from law_calendar.validation import civil_date, official_url


class ChangeReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str
    effective_on: str
    summary: str = Field(min_length=1, max_length=300)
    evidence_url: str
    comparison_verified: bool


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event_id: str
    snapshot_id: str
    decision: Literal['approve', 'reject', 'withdraw']
    note: str = Field(min_length=1)
    source_url: str | None = None
    dates_verified: bool = False
    promulgated_on: str | None = None
    published_on: str | None = None
    comment_deadline: str | None = None
    changes: list[ChangeReview] = Field(default_factory=list)


def inspect_event(database: Path, event_id: str) -> dict:
    with read_database(database) as conn:
        event = conn.execute(
            'SELECT e.*,s.snapshot_id FROM change_events e JOIN event_sources s ON s.event_id=e.id WHERE e.id=?',
            (event_id,),
        ).fetchone()
        if event is None:
            raise ValueError('Imported event not found')
        snapshot = conn.execute('SELECT * FROM source_snapshots WHERE id=?', (event['snapshot_id'],)).fetchone()
        changes = conn.execute(
            'SELECT c.* FROM article_changes c JOIN change_versions v ON v.change_id=c.id '
            'WHERE c.event_id=? AND v.snapshot_id=? ORDER BY c.article',
            (event_id, event['snapshot_id']),
        ).fetchall()
        return {
            **dict(event),
            'changes': [dict(row) for row in changes],
            'source_event': json.loads(snapshot['raw_event']),
            'source_files': json.loads(snapshot['files_json']),
        }


def review_template(inspected: dict) -> dict:
    """Export an intentionally unapprovable manifest for a human to complete."""
    return {
        'event_id': inspected['id'],
        'snapshot_id': inspected['snapshot_id'],
        'decision': 'approve',
        'note': '',
        'source_url': inspected['source_url'],
        'dates_verified': False,
        'promulgated_on': None,
        'changes': [
            {'id': c['id'], 'effective_on': '', 'summary': '', 'evidence_url': '', 'comparison_verified': False}
            for c in inspected['changes']
        ],
    }


def review(database: Path, payload: dict, *, reviewer: str) -> None:
    decision = Decision.model_validate(payload)
    if not reviewer.strip() or not decision.note.strip():
        raise ValueError('Reviewer and review note are required')
    if decision.decision == 'approve':
        if not decision.dates_verified or not decision.changes:
            raise ValueError('Dates, addenda and at least one article must be reviewed')
        official_url(decision.source_url or '')
        civil_date(decision.promulgated_on or '')
        for value in (decision.published_on, decision.comment_deadline):
            if value is not None:
                civil_date(value)
        for change in decision.changes:
            civil_date(change.effective_on)
            official_url(change.evidence_url)
            if (
                not change.comparison_verified
                or not change.summary.strip()
                or '\n' in change.summary
                or '\r' in change.summary
            ):
                raise ValueError('An explicitly verified comparison and one-line summary are required')
        if len({c.id for c in decision.changes}) != len(decision.changes):
            raise ValueError('Duplicate change ID in review')
    reviewed_at = utc_now()
    with write_database(database) as conn:
        source = conn.execute('SELECT snapshot_id FROM event_sources WHERE event_id=?', (decision.event_id,)).fetchone()
        if source is None or source['snapshot_id'] != decision.snapshot_id:
            raise ValueError('Stale or missing source snapshot; inspect the event again')
        active = {
            r['change_id']
            for r in conn.execute('SELECT change_id FROM change_versions WHERE snapshot_id=?', (decision.snapshot_id,))
        }
        if any(c.id not in active for c in decision.changes):
            raise ValueError('Review contains a change outside this snapshot')
        state = {'approve': 'approved', 'reject': 'rejected', 'withdraw': 'withdrawn'}[decision.decision]
        if state == 'approved':
            conn.execute(
                "UPDATE change_events SET review_state=?,status='검토 승인',source_url=?,verified_at=?,"
                'promulgated_on=?,published_on=?,comment_deadline=? WHERE id=?',
                (
                    state,
                    decision.source_url,
                    reviewed_at,
                    decision.promulgated_on,
                    decision.published_on,
                    decision.comment_deadline,
                    decision.event_id,
                ),
            )
            for change in decision.changes:
                conn.execute(
                    'UPDATE article_changes SET effective_on=?,summary=?,comparable=1,review_state=?,verified_at=? WHERE id=?',
                    (change.effective_on, change.summary, state, reviewed_at, change.id),
                )
            conn.execute(
                'INSERT INTO collection_status(id,verified_at) VALUES (1,?) '
                'ON CONFLICT(id) DO UPDATE SET verified_at=excluded.verified_at',
                (reviewed_at,),
            )
        else:
            conn.execute(
                'UPDATE change_events SET review_state=?,verified_at=NULL WHERE id=?', (state, decision.event_id)
            )
            conn.execute(
                'UPDATE article_changes SET review_state=?,verified_at=NULL WHERE event_id=?',
                (state, decision.event_id),
            )
        audit = conn.execute(
            'INSERT INTO review_audit(event_id,decision,reviewer,reviewed_at,note) VALUES (?,?,?,?,?)',
            (decision.event_id, decision.decision, reviewer, reviewed_at, decision.note),
        )
        conn.execute(
            'INSERT INTO review_details VALUES (?,?,?)',
            (audit.lastrowid, decision.snapshot_id, decision.model_dump_json()),
        )
