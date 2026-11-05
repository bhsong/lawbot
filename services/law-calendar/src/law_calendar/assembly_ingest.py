"""Persist and review dated Assembly observations independently of article changes."""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from law_calendar.db import read_database, write_database
from law_calendar.ingest import utc_now
from law_calendar.validation import civil_date, timestamp

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
            raise ValueError('Unexpected Assembly event type')
        if notice:
            if ctx.endpoint not in NOTICE_ENDPOINTS or ctx.status != NOTICE_ENDPOINTS[ctx.endpoint][0]:
                raise ValueError('Invalid notice endpoint or status')
            host, path, key = 'pal.assembly.go.kr', NOTICE_ENDPOINTS[ctx.endpoint][1], 'lgsltPaId'
            roles = {'notice_start', 'notice_end'}
        else:
            if ctx.endpoint != 'TVBPMBILL11' or target.item_id != target.bill_id:
                raise ValueError('Invalid bill endpoint or identity')
            host, path, key = 'likms.assembly.go.kr', '/bill/billDetail.do', 'billId'
            roles = BILL_ROLES
        url = urlsplit(ctx.source_url)
        if (
            url.scheme != 'https'
            or url.netloc != host
            or url.path != path
            or url.fragment
            or parse_qs(url.query, keep_blank_values=True) != {key: [target.item_id]}
            or any(c.isspace() for c in ctx.source_url)
        ):
            raise ValueError('Invalid official Assembly evidence URL')
        if not set(self.dates) <= roles or set(ctx.date_sources) != set(self.dates):
            raise ValueError('Unexpected Assembly dates')
        for role, value in self.dates.items():
            civil_date(value)
            expected = host if role == 'notice_start' else ctx.endpoint
            if ctx.date_sources[role] != expected:
                raise ValueError('Invalid Assembly date evidence')
        if 'notice_start' in self.dates and self.dates['notice_start'] > self.dates.get('notice_end', ''):
            raise ValueError('Invalid notice period')
        timestamp(self.detected_at)


def import_assembly(database: Path, source: str, collected_at: str, events: list[dict]) -> tuple[int, int]:
    records = [AssemblyEvent.model_validate(event) for event in events]
    for event in records:
        event.validate_evidence()
        if event.source != source:
            raise ValueError('Assembly snapshot source mismatch')
    imported = 0
    with write_database(database) as conn:
        for event in records:
            target, ctx = event.target, event.context
            entry_id = 'assembly-' + sha256(f'{source}:{target.item_id}'.encode()).hexdigest()
            revision = int(event.new.version_key)
            encoded = event.model_dump_json()
            prior = conn.execute('SELECT event_json FROM assembly_snapshots WHERE id=?', (event.event_id,)).fetchone()
            if prior:
                if prior['event_json'] != encoded:
                    raise ValueError('Registered Assembly snapshot was altered')
                continue
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
                    encoded,
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
                if latest and latest['review_state'] == 'approved':
                    conn.execute(
                        "INSERT INTO assembly_review_audit VALUES (NULL,?,?,'invalidate','sync',?,?)",
                        (entry_id, latest['snapshot_id'], utc_now(), '공식 원천 관측값 변경'),
                    )
                conn.execute(
                    "INSERT INTO assembly_entries VALUES (?,?,?,?,'pending',NULL) "
                    "ON CONFLICT(id) DO UPDATE SET snapshot_id=excluded.snapshot_id,review_state='pending',verified_at=NULL",
                    (entry_id, source, target.item_id, event.event_id),
                )
            imported += 1
        previous = conn.execute(
            'SELECT collected_at FROM assembly_collection_status WHERE source=?', (source,)
        ).fetchone()
        if not previous or datetime.fromisoformat(previous[0]) < datetime.fromisoformat(collected_at):
            conn.execute(
                'INSERT INTO assembly_collection_status VALUES (?,?) '
                'ON CONFLICT(source) DO UPDATE SET collected_at=excluded.collected_at',
                (source, timestamp(collected_at)),
            )
    return imported, len(events) - imported


def list_assembly(database: Path) -> list[dict]:
    with read_database(database) as conn:
        rows = conn.execute(
            'SELECT e.id,e.snapshot_id,e.review_state,s.title,s.bill_no,s.status,s.collected_at '
            'FROM assembly_entries e JOIN assembly_snapshots s ON s.id=e.snapshot_id ORDER BY s.collected_at DESC,e.id'
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


def review_assembly(database: Path, snapshot_id: str, decision: str, *, reviewer: str, note: str) -> None:
    states = {'approve': 'approved', 'reject': 'rejected', 'withdraw': 'withdrawn'}
    if decision not in states or not reviewer.strip() or not note.strip():
        raise ValueError('Decision, reviewer and review note are required')
    now = utc_now()
    with write_database(database) as conn:
        row = conn.execute('SELECT id FROM assembly_entries WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        if not row:
            raise ValueError('Stale or missing Assembly snapshot; inspect the current observation again')
        if (
            decision == 'approve'
            and not conn.execute('SELECT 1 FROM assembly_dates WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        ):
            raise ValueError('An official date is required to publish an Assembly item')
        conn.execute(
            'UPDATE assembly_entries SET review_state=?,verified_at=? WHERE id=?',
            (states[decision], now if decision == 'approve' else None, row['id']),
        )
        conn.execute(
            'INSERT INTO assembly_review_audit VALUES (NULL,?,?,?,?,?,?)',
            (row['id'], snapshot_id, decision, reviewer, now, note),
        )
