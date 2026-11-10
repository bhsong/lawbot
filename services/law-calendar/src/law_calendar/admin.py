"""Service-token API for reviews and import diagnostics used by the chatbot admin proxy."""

import hmac
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from law_calendar.assembly_ingest import review_assembly
from law_calendar.db import read_database
from law_calendar.import_failures import list_import_failures
from law_calendar.review import held_review_template, review
from law_calendar.validation import StaleSnapshotError


class AssemblyReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    snapshot_id: str
    decision: str
    note: str


def _notice_template(database: Path, snapshot_id: str) -> dict | None:
    with read_database(database) as conn:
        row = conn.execute(
            'SELECT s.title,s.source_url,s.bill_no FROM assembly_entries e '
            'JOIN assembly_snapshots s ON s.id=e.snapshot_id '
            "WHERE s.id=? AND e.source='assembly_notice' AND e.review_state='pending'",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        dates = conn.execute(
            'SELECT role,event_on AS date FROM assembly_dates WHERE snapshot_id=? ORDER BY event_on,role DESC',
            (snapshot_id,),
        ).fetchall()
    return {
        'template': {'snapshot_id': snapshot_id, 'decision': 'approve', 'note': ''},
        **dict(row),
        'dates': [dict(date) for date in dates],
    }


def _validation_detail(exc: ValidationError) -> str:
    issues = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:5]:
        if error['type'] == 'extra_forbidden':
            issues.append('Unexpected review field')
            continue
        path = ''.join(f'[{part + 1}]' if isinstance(part, int) else f'.{part}' for part in error['loc']).lstrip('.')
        issues.append(f'{path}: {error["msg"]}')
    return ' / '.join(issues)


def _review_effective(database: Path, change_id: str, payload: dict, reviewer: str) -> None:
    with read_database(database) as conn:
        change = conn.execute(
            'SELECT c.event_id FROM article_changes c JOIN held_changes h ON h.change_id=c.id WHERE c.id=?',
            (change_id,),
        ).fetchone()
    if change is None:
        raise HTTPException(404, 'Held article not found')
    if payload.get('event_id') != change['event_id']:
        raise HTTPException(422, 'event_id: Review event does not match the held article')
    review(database, payload, reviewer=reviewer, idempotent=True)


def _review_notice(database: Path, snapshot_id: str, payload: dict, reviewer: str) -> None:
    decision = AssemblyReview.model_validate(payload)
    if decision.snapshot_id != snapshot_id:
        raise HTTPException(422, 'snapshot_id: Review snapshot does not match the held notice')
    with read_database(database) as conn:
        snapshot = conn.execute(
            "SELECT 1 FROM assembly_snapshots WHERE id=? AND source='assembly_notice'", (snapshot_id,)
        )
        if snapshot.fetchone() is None:
            raise HTTPException(404, 'Assembly notice not found')
    review_assembly(database, snapshot_id, decision.decision, reviewer=reviewer, note=decision.note, idempotent=True)


def router(database: Path) -> APIRouter:
    token = os.environ.get('LAWBOT_CALENDAR_ADMIN_TOKEN', '').strip()
    expected = f'Bearer {token}'.encode()

    async def require_service_token(request: Request) -> None:
        if not token:
            raise HTTPException(503, 'Calendar admin service token is not configured')
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), expected):
            raise HTTPException(403, 'Calendar admin service token is invalid')

    api = APIRouter(prefix='/api/admin', dependencies=[Depends(require_service_token)], include_in_schema=False)

    async def read_review_payload(request: Request) -> dict:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, 'Review body must be a JSON object') from exc
        if not isinstance(payload, dict):
            raise HTTPException(422, 'Review body must be a JSON object')
        return payload

    @api.get('/law-holds/{kind}/{hold_id}')
    def template(kind: Literal['effective', 'notice'], hold_id: str):
        result = held_review_template(database, hold_id) if kind == 'effective' else _notice_template(database, hold_id)
        if result is None:
            raise HTTPException(404, 'Current hold not found')
        return result

    @api.post('/law-holds/{kind}/{hold_id}')
    def decide(kind: Literal['effective', 'notice'], hold_id: str, body: Annotated[dict, Depends(read_review_payload)]):
        payload = body.copy()
        reviewer = payload.pop('reviewer', None)
        if not isinstance(reviewer, str):
            raise HTTPException(422, 'reviewer: Reviewer is required')
        try:
            if kind == 'effective':
                _review_effective(database, hold_id, payload, reviewer)
            else:
                _review_notice(database, hold_id, payload, reviewer)
        except StaleSnapshotError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'ok': True}

    @api.get('/import-failures')
    def import_failures(page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100)):
        return list_import_failures(database, page, page_size)

    return api
