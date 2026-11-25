"""Service-token API for reviews and import diagnostics used by the chatbot admin proxy."""

import hmac
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from law_calendar.db import read_database
from law_calendar.import_failures import list_import_failures
from law_calendar.review import held_review_template, review
from law_calendar.validation import StaleSnapshotError
from lawbot_common.edge import via_cloudflare


def _validation_detail(exc: ValidationError) -> str:
    issues = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:5]:
        if error['type'] == 'extra_forbidden':
            issues.append('Unexpected review field')
            continue
        path = ''.join(f'[{part + 1}]' if isinstance(part, int) else f'.{part}' for part in error['loc']).lstrip('.')
        issues.append(f'{path}: {error["msg"]}')
    return ' / '.join(issues)


def _review_effective(database: Path, change_id: str, payload: dict, reviewer: str) -> bool:
    if payload.get('decision') != 'approve':
        raise HTTPException(422, 'decision: Only approval is allowed for held articles')
    with read_database(database) as conn:
        change = conn.execute(
            'SELECT c.event_id FROM article_changes c JOIN held_changes h ON h.change_id=c.id WHERE c.id=?',
            (change_id,),
        ).fetchone()
    if change is None:
        raise HTTPException(404, 'Held article not found')
    if payload.get('event_id') != change['event_id']:
        raise HTTPException(422, 'event_id: Review event does not match the held article')
    return review(database, payload, reviewer=reviewer, idempotent=True)


def router(database: Path, *, on_review: Callable[[], None] | None = None) -> APIRouter:
    token = os.environ.get('LAWBOT_CALENDAR_ADMIN_TOKEN', '').strip()
    expected = f'Bearer {token}'.encode()

    async def require_service_token(request: Request) -> None:
        if via_cloudflare(request.headers):
            raise HTTPException(404, 'Not Found')
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

    @api.get('/law-holds/effective/{hold_id}')
    def template(hold_id: str):
        result = held_review_template(database, hold_id)
        if result is None:
            raise HTTPException(404, 'Current hold not found')
        return result

    @api.post('/law-holds/effective/{hold_id}')
    def decide(hold_id: str, body: Annotated[dict, Depends(read_review_payload)]):
        payload = body.copy()
        reviewer = payload.pop('reviewer', None)
        if not isinstance(reviewer, str):
            raise HTTPException(422, 'reviewer: Reviewer is required')
        try:
            changed = _review_effective(database, hold_id, payload, reviewer)
        except StaleSnapshotError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if changed and on_review is not None:
            on_review()
        return {'ok': True}

    @api.get('/import-failures')
    def import_failures(page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100)):
        return list_import_failures(database, page, page_size)

    return api
