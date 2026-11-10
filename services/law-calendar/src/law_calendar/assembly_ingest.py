"""Persist and review dated Assembly observations independently of article changes."""

import json
import sqlite3
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from law_calendar.db import read_database, write_database
from law_calendar.import_failures import ImportInputError, record_import_failure
from law_calendar.ingest import utc_now
from law_calendar.validation import StaleSnapshotError, civil_date, timestamp

ASSEMBLY_TYPES = {
    'assembly_bill': ('assembly_bill_new', 'assembly_bill_changed'),
    'assembly_notice': ('assembly_notice_observed', 'assembly_notice_changed', 'assembly_notice_closed'),
}
NOTICE_ENDPOINTS = {
    'nknalejkafmvgzmpt': ('진행 중', '/napal/lgsltpa/lgsltpaOngoing/view.do'),
    'nohgwtzsamojdozky': ('종료', '/napal/lgsltpa/lgsltpaDone/view.do'),
}
BILL_ROLES = {
    'bill_proposed',
    'bill_decided',
    'committee_referred',
    'committee_reviewed',
    'committee_presented',
    'committee_decided',
    'judiciary_referred',
    'judiciary_presented',
    'judiciary_decided',
}
REVIEW_STATES = {'approve': 'approved', 'reject': 'rejected', 'withdraw': 'withdrawn'}
AUTO_NOTICE_REVIEWER = 'notice-auto'


class AssemblyTarget(BaseModel):
    model_config = ConfigDict(strict=True)
    item_id: str = Field(pattern=r'^[A-Za-z0-9_]+$')
    bill_id: str = Field(pattern=r'^[A-Za-z0-9_]+$')
    bill_no: str = Field(pattern=r'^\d+$')
    law_name: str = Field(min_length=1)
    agency: str | None
    age: int = Field(gt=0)


class AssemblyContext(BaseModel):
    model_config = ConfigDict(strict=True)
    schema_version: Literal[1]
    source_url: str
    status: str = Field(min_length=1)
    endpoint: str
    date_sources: dict[str, str]
    status_fields: dict[str, str] = Field(default_factory=dict)


class Revision(BaseModel):
    version_key: str = Field(pattern=r'^[1-9]\d{0,8}$')


class AssemblyEvent(BaseModel):
    model_config = ConfigDict(strict=True)
    event_id: str = Field(min_length=1)
    source: Literal['assembly_bill', 'assembly_notice']
    change_type: str
    detected_at: str
    new: Revision
    dates: dict[str, str]
    target: AssemblyTarget
    context: AssemblyContext

    def validate_evidence(self) -> None:
        target, ctx = self.target, self.context
        notice = self.source == 'assembly_notice'
        if self.change_type not in ASSEMBLY_TYPES[self.source]:
            raise ImportInputError('Unexpected Assembly event type')
        if notice:
            if ctx.endpoint not in NOTICE_ENDPOINTS or ctx.status != NOTICE_ENDPOINTS[ctx.endpoint][0]:
                raise ImportInputError('Invalid notice endpoint or status')
            host, path, key = 'pal.assembly.go.kr', NOTICE_ENDPOINTS[ctx.endpoint][1], 'lgsltPaId'
            roles = {'notice_start', 'notice_end'}
        else:
            if ctx.endpoint != 'TVBPMBILL11' or target.item_id != target.bill_id:
                raise ImportInputError('Invalid bill endpoint or identity')
            host, path, key = 'likms.assembly.go.kr', '/bill/billDetail.do', 'billId'
            roles = BILL_ROLES
        try:
            url = urlsplit(ctx.source_url)
        except ValueError as exc:
            raise ImportInputError('Invalid official Assembly evidence URL') from exc
        if (
            url.scheme != 'https'
            or url.netloc != host
            or url.path != path
            or url.fragment
            or parse_qs(url.query, keep_blank_values=True) != {key: [target.item_id]}
            or any(c.isspace() for c in ctx.source_url)
        ):
            raise ImportInputError('Invalid official Assembly evidence URL')
        if not set(self.dates) <= roles or set(ctx.date_sources) != set(self.dates):
            raise ImportInputError('Unexpected Assembly dates')
        for role, value in self.dates.items():
            try:
                civil_date(value)
            except ValueError as exc:
                raise ImportInputError(f'Invalid Assembly date for {role}; expected YYYY-MM-DD') from exc
            expected = host if role == 'notice_start' else ctx.endpoint
            if ctx.date_sources[role] != expected:
                raise ImportInputError('Invalid Assembly date evidence')
        if 'notice_start' in self.dates and self.dates['notice_start'] > self.dates.get('notice_end', ''):
            raise ImportInputError('Invalid notice period')
        try:
            timestamp(self.detected_at)
        except ValueError as exc:
            raise ImportInputError('Assembly observation requires an ISO timestamp with a timezone') from exc


def _store_assembly_snapshot(conn: sqlite3.Connection, source: str, event: AssemblyEvent) -> bool:
    target, ctx = event.target, event.context
    entry_id = 'assembly-' + sha256(f'{source}:{target.item_id}'.encode()).hexdigest()
    revision = int(event.new.version_key)
    excluded = {'detected_at'}
    prior = conn.execute('SELECT event_json FROM assembly_snapshots WHERE id=?', (event.event_id,)).fetchone()
    if prior is None:
        prior = conn.execute(
            'SELECT event_json FROM assembly_snapshots WHERE source=? AND upstream_id=? AND revision=?',
            (source, target.item_id, revision),
        ).fetchone()
        excluded.add('event_id')
    if prior:
        stored = json.loads(prior['event_json'])
        if not isinstance(stored, dict):
            raise ImportInputError('Registered Assembly snapshot was altered')
        for field in excluded:
            stored.pop(field, None)
        observed = event.model_dump(mode='json', exclude=excluded)
        if json.dumps(stored, sort_keys=True) != json.dumps(observed, sort_keys=True):
            raise ImportInputError('Registered Assembly snapshot was altered')
        return False
    conn.execute(
        'INSERT INTO assembly_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (
            event.event_id,
            source,
            target.item_id,
            revision,
            target.law_name,
            target.agency,
            target.bill_no,
            ctx.status,
            ctx.source_url,
            event.detected_at,
            event.model_dump_json(),
        ),
    )
    dates = [
        (event.event_id, role, day)
        for role, day in event.dates.items()
        if role in ('notice_start', 'notice_end', 'bill_proposed', 'bill_decided')
    ]
    conn.executemany('INSERT INTO assembly_dates VALUES (?,?,?)', dates)
    latest = conn.execute(
        'SELECT e.snapshot_id,e.review_state,s.revision FROM assembly_entries e '
        'JOIN assembly_snapshots s ON s.id=e.snapshot_id WHERE e.id=?',
        (entry_id,),
    ).fetchone()
    if not latest or revision > latest['revision']:
        review_state = 'pending'
        if latest and source == 'assembly_notice' and latest['review_state'] in ('rejected', 'withdrawn'):
            review_state = latest['review_state']
        if latest and latest['review_state'] == 'approved':
            conn.execute(
                "INSERT INTO assembly_review_audit VALUES (NULL,?,?,'invalidate','sync',?,?)",
                (entry_id, latest['snapshot_id'], utc_now(), '공식 원천 관측값 변경'),
            )
        conn.execute(
            'INSERT INTO assembly_entries VALUES (?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET '
            'snapshot_id=excluded.snapshot_id,review_state=excluded.review_state,verified_at=NULL',
            (entry_id, source, target.item_id, event.event_id, review_state),
        )
    return True


def import_assembly(
    database: Path, source: str, collected_at: str, events: list[dict], *, record_failures: bool = False
) -> tuple[int, int]:
    event_id: str | None = None
    try:
        records = []
        for payload in events:
            event_id = payload.get('event_id') if isinstance(payload, dict) else None
            event = AssemblyEvent.model_validate(payload)
            event.validate_evidence()
            if event.source != source:
                raise ImportInputError('Assembly snapshot source mismatch')
            records.append(event)

        imported = 0
        event_id = None
        with write_database(database) as conn:
            for event in records:
                event_id = event.event_id
                imported += _store_assembly_snapshot(conn, source, event)
            event_id = None
            previous = conn.execute(
                'SELECT collected_at FROM assembly_collection_status WHERE source=?', (source,)
            ).fetchone()
            if not previous or datetime.fromisoformat(previous[0]) < datetime.fromisoformat(collected_at):
                conn.execute(
                    'INSERT INTO assembly_collection_status VALUES (?,?) '
                    'ON CONFLICT(source) DO UPDATE SET collected_at=excluded.collected_at',
                    (source, timestamp(collected_at)),
                )
    except (OSError, ValueError, sqlite3.Error) as exc:
        if record_failures:
            record_import_failure(database, source, event_id, exc)
        raise
    return imported, len(events) - imported


def list_assembly(database: Path, *, notice_sample: int | None = None) -> list[dict]:
    if notice_sample is not None and notice_sample < 1:
        raise ValueError('Notice sample size must be positive')
    with read_database(database) as conn:
        rows = conn.execute(
            'SELECT e.id,e.snapshot_id,e.review_state,s.title,s.bill_no,s.status,s.collected_at, '
            'a.reviewer,a.reviewed_at,a.note FROM assembly_entries e JOIN assembly_snapshots s ON s.id=e.snapshot_id '
            'LEFT JOIN assembly_review_audit a ON a.id=(SELECT MAX(id) FROM assembly_review_audit WHERE entry_id=e.id) '
            "WHERE (? IS NULL OR (e.source='assembly_notice' AND e.review_state='approved' AND a.reviewer=?)) "
            'ORDER BY CASE WHEN ? IS NULL THEN s.collected_at ELSE a.reviewed_at END DESC,e.id LIMIT COALESCE(?,-1)',
            (notice_sample, AUTO_NOTICE_REVIEWER, notice_sample, notice_sample),
        ).fetchall()
    return [dict(row) for row in rows]


def inspect_assembly(database: Path, snapshot_id: str) -> dict:
    with read_database(database) as conn:
        row = conn.execute(
            'SELECT e.id,e.review_state,s.event_json FROM assembly_entries e '
            'JOIN assembly_snapshots s ON s.id=e.snapshot_id WHERE s.id=?',
            (snapshot_id,),
        ).fetchone()
        if not row:
            raise ValueError('Current Assembly snapshot not found')
    return {'id': row['id'], 'review_state': row['review_state'], 'event': json.loads(row['event_json'])}


def review_assembly(
    database: Path, snapshot_id: str, decision: str, *, reviewer: str, note: str, idempotent: bool = False
) -> None:
    if decision not in REVIEW_STATES:
        raise ValueError('decision: Select approve, reject or withdraw')
    if not reviewer.strip():
        raise ValueError('reviewer: Reviewer is required')
    if not note.strip():
        raise ValueError('note: Review note is required')
    with write_database(database) as conn:
        row = conn.execute('SELECT id FROM assembly_entries WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        if not row:
            raise StaleSnapshotError('Stale or missing Assembly snapshot; inspect the current observation again')
        if (
            decision == 'approve'
            and not conn.execute('SELECT 1 FROM assembly_dates WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        ):
            raise ValueError('An official date is required to publish an Assembly item')
        if (
            idempotent
            and conn.execute(
                'SELECT 1 FROM assembly_review_audit '
                'WHERE entry_id=? AND snapshot_id=? AND decision=? AND reviewer=? AND note=? LIMIT 1',
                (row['id'], snapshot_id, decision, reviewer, note),
            ).fetchone()
        ):
            return
        record_assembly_review(conn, row['id'], snapshot_id, decision, reviewer=reviewer, note=note)


def record_assembly_review(
    conn: sqlite3.Connection, entry_id: str, snapshot_id: str, decision: str, *, reviewer: str, note: str
) -> None:
    """Record a verified decision for the current entry within the caller's write transaction."""
    now = utc_now()
    conn.execute(
        'UPDATE assembly_entries SET review_state=?,verified_at=? WHERE id=?',
        (REVIEW_STATES[decision], now if decision == 'approve' else None, entry_id),
    )
    conn.execute(
        'INSERT INTO assembly_review_audit VALUES (NULL,?,?,?,?,?,?)',
        (entry_id, snapshot_id, decision, reviewer, now, note),
    )
