"""Administrator feedback and corpus API."""

import base64
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from httpx import URL, AsyncClient, RequestError, TimeoutException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from lawbot import audit_store, consult_admin_store, dataset_store, feedback_store
from lawbot.consult_trace import stamp
from lawbot.db.connection import connect, transaction
from lawbot.qa_rules import ProtectedQA, assigned_overlap
from lawbot.web import (
    AdminError,
    AdminWho,
    read_admin_body,
    require_admin,
    require_admin_post_rate,
    require_json,
    require_same_origin,
)
from lawbot_common.logs import logger

STATUSES = ('pending', 'reviewed', 'hold', 'rejected', 'quarantined', 'promoted', 'all')
REASONS = ('retrieval', 'answer', 'false_refusal', 'other')
CALENDAR_PATH = re.compile(r'(?:law-calendar(?:/status)?|law-events|law-holds|law-changes/[A-Za-z0-9_:-]+)')
CALENDAR_REVIEW_PATH = re.compile(r'law-holds/effective/[A-Za-z0-9_:-]+')
CALENDAR_TIMEOUT = 10
log = logger('lawbot', 'lawbot')


Key = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Question = Annotated[Nonempty, Field(max_length=2000)]
Answer = Annotated[Nonempty, Field(max_length=8000)]
Note = Annotated[str, Field(max_length=2000)]
Scope = Literal['in', 'out']


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    idempotency_key: Key


class CalendarReview(Body):
    """Validate the proxy envelope and leave review fields to the calendar service."""

    model_config = ConfigDict(extra='allow', strict=True)


class Gold(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    law: Nonempty
    article_no: Nonempty


class Review(Body):
    verdict: Literal['correct', 'incorrect', 'uncertain']
    decision: Literal['approved', 'hold', 'rejected']
    scope: Scope | None = None
    gold_articles: list[Gold] = Field(max_length=10)
    corrected_question: Question | None = None
    corrected_answer: Answer | None = None
    note: Note | None = None


class PromoteUp(Body):
    feedback_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=255)]] = Field(
        min_length=1, max_length=50
    )


class PromoteDown(Body):
    corrected_question: Question
    corrected_answer: Answer
    gold_articles: list[Gold] = Field(min_length=1, max_length=10)
    review_verdict: Literal['correct', 'incorrect']
    review_note: Note | None = None
    scope: Scope | None = None


class Revoke(Body):
    note: Annotated[Nonempty, Field(max_length=2000)]


UserHash = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{16}$')]
TraceDate = Annotated[str, StringConstraints(max_length=40)]


class ConsultDeletePreview(Body):
    user_hash: UserHash | None = None
    start: TraceDate | None = None
    end: TraceDate | None = None


class ConsultDelete(ConsultDeletePreview):
    confirmation: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{96}$')]


def _consult_filters(user_hash, start, end, status=None):
    filters = {}
    if user_hash is not None:
        if re.fullmatch(r'[a-f0-9]{16}', user_hash) is None:
            raise AdminError(422, '사용자 해시를 확인하세요.', 'invalid_filter')
        filters['user_hash'] = user_hash
    for key, value in (('start', start), ('end', end)):
        if value is None:
            continue
        try:
            if len(value) > 40 or (date := datetime.fromisoformat(value)).utcoffset() is None:
                raise ValueError
            filters[key] = stamp(date)
        except ValueError, OverflowError:
            raise AdminError(422, '기간에는 시간대를 포함해야 합니다.', 'invalid_filter') from None
    if 'start' in filters and 'end' in filters and filters['start'] >= filters['end']:
        raise AdminError(422, '종료 시각은 시작 시각보다 뒤여야 합니다.', 'invalid_filter')
    if status is not None:
        if status not in ('ok', 'error', 'cancelled'):
            raise AdminError(422, '상담 상태를 확인하세요.', 'invalid_filter')
        filters['status'] = status
    return filters


def _mutation(model, body, who, endpoint):
    try:
        parsed = model.model_validate_json(body)
    except ValidationError as exc:
        raise AdminError(422, '요청 본문이 올바르지 않습니다.', 'invalid_body') from exc
    mutation = feedback_store.Mutation(who[0], endpoint, parsed.idempotency_key, hashlib.sha256(body).hexdigest())
    return mutation, parsed


async def _write(fn, *args):
    try:
        status, payload = await anyio.to_thread.run_sync(fn, *args)
    except feedback_store.StoreError as exc:
        raise AdminError(exc.status, exc.detail, exc.code) from exc
    except (OSError, feedback_store.DatabaseError) as exc:
        log.warning('관리자 DB 쓰기 실패 (%s).', type(exc).__name__)
        raise AdminError(500, '관리자 저장을 완료하지 못했습니다.', 'storage_error') from None
    if status >= 400:
        raise AdminError(status, payload['detail'], payload['code'])
    return JSONResponse(payload, status_code=status)


def _require_admin_json(request: Request):
    try:
        require_json(request)
    except HTTPException as exc:
        raise AdminError(415, str(exc.detail), 'unsupported_media_type') from exc


def _calendar_receipt(db, mutation):
    with connect(db) as conn:
        return mutation.receipt(conn)


def _save_calendar_review(db, mutation, kind, hold_id, decision):
    with connect(db, write=True) as conn, transaction(conn):
        if receipt := mutation.receipt(conn):
            return receipt
        at = datetime.now(UTC).isoformat()
        summary = {'decision': decision} if decision == 'approve' else None
        audit_store.record_action(conn, at, mutation.actor, 'calendar_review', kind, hold_id, summary=summary)
        result = {'ok': True}
        mutation.save(conn, 200, result, at)
        return 200, result


def _cursor(value):
    if value is None:
        return None
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        data = json.loads(raw)
    except ValueError as exc:
        raise AdminError(422, '커서가 올바르지 않습니다.', 'invalid_cursor') from exc
    if (
        not isinstance(data, dict)
        or set(data) != {'ts', 'id'}
        or not all(isinstance(v, str) and v for v in data.values())
    ):
        raise AdminError(422, '커서가 올바르지 않습니다.', 'invalid_cursor')
    return data['ts'], data['id']


def _encode_cursor(value):
    return base64.urlsafe_b64encode(json.dumps(dict(ts=value[0], id=value[1])).encode()).decode().rstrip('=')


class Corpus:
    def __init__(self, app):
        r = app.retriever
        self.articles = {
            (str(law), str(no)): (str(title), str(kind), text)
            for law, no, title, kind, text in zip(r.laws, r.ids, r.titles, r.kinds, r.docs, strict=True)
        }
        self.version = getattr(app, 'dataset_version', None)
        self.sha = getattr(r, 'articles_sha256', None)

    def checks(self, feedback):
        changed = []
        for hit in feedback.get('hits') or ():
            if not isinstance(hit, dict) or 'definition_item' in hit:
                continue
            law, article_id = hit.get('law'), hit.get('article_id')
            if not isinstance(law, str) or not isinstance(article_id, str):
                continue
            item = self.articles.get((law, article_id))
            status = 'missing' if item is None else 'same' if item[2] == hit.get('doc') else 'changed'
            changed.append(dict(law=law, article_id=article_id, status=status))
        return dict(
            feedback_dataset_version=feedback.get('dataset_version'),
            feedback_articles_sha256=feedback.get('articles_sha256'),
            current_dataset_version=self.version,
            current_articles_sha256=self.sha,
            changed_hits=changed,
        )

    def search(self, q):
        parts = q.casefold().split()
        return [
            dict(law=law, article_no=no, title=title, kind=kind)
            for (law, no), (title, kind, _) in self.articles.items()
            if all(part in f'{law} {no}'.casefold() for part in parts)
        ][:20]


async def _post_body(request: Request) -> bytes:
    _require_admin_json(request)
    require_same_origin(request)
    return await read_admin_body(request)


PostBody = Annotated[bytes, Depends(_post_body)]


def load_protected(db):
    return ProtectedQA(dataset_store.protection_rows(db))


def router(db, app, *, protected=None, calendar_url=None, calendar_token=None):
    api = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])
    index = load_protected(db) if protected is None else protected
    corpus = Corpus(app)
    post_guards = [Depends(_post_body), Depends(require_admin_post_rate)]
    calendar_review_lock = anyio.Lock()
    consult_confirmation_key = secrets.token_bytes(32)

    @api.get('/consults')
    def consults(
        user_hash: str | None = None,
        start: str | None = None,
        end: str | None = None,
        status: str | None = None,
        limit: str = '50',
        cursor: str | None = None,
    ):
        if not re.fullmatch(r'[0-9]{1,3}', limit) or not 1 <= int(limit) <= 200 or len(cursor or '') > 256:
            raise AdminError(422, '목록 조건이 올바르지 않습니다.', 'invalid_query')
        filters = _consult_filters(user_hash, start, end, status)
        items, after = consult_admin_store.list_consults(db, filters, int(limit), _cursor(cursor))
        return {'items': items, 'next_cursor': _encode_cursor(after) if after else None}

    @api.get('/consults/{consult_id}')
    def consult_detail(consult_id: str):
        result = consult_admin_store.detail(db, consult_id)
        if result is None:
            raise AdminError(404, '상담 기록을 찾을 수 없습니다.', 'not_found')
        return result

    @api.get('/consults/{consult_id}/text')
    def consult_text(consult_id: str):
        result = consult_admin_store.consult_text(db, consult_id)
        if result is None:
            raise AdminError(404, '상담 기록을 찾을 수 없습니다.', 'not_found')
        return result

    @api.get('/consults/{consult_id}/steps/{seq}')
    def consult_step(consult_id: str, seq: int):
        result = consult_admin_store.step_result(db, consult_id, seq)
        if result is None:
            raise AdminError(404, '상담 단계를 찾을 수 없습니다.', 'not_found')
        return result

    @api.get('/consults/{consult_id}/calls/{seq}/body')
    def consult_call_body(consult_id: str, seq: int, part: consult_admin_store.BodyPart, cursor: str = '1:0'):
        match = re.fullmatch(r'([1-9][0-9]{0,8}):([0-9]{1,9})', cursor)
        if match is None or (part != 'chunks' and match[1] != '1'):
            raise AdminError(422, '커서가 올바르지 않습니다.', 'invalid_cursor')
        result = consult_admin_store.call_body(db, consult_id, seq, part, (int(match[1]), int(match[2])))
        if result is None:
            raise AdminError(404, '모델 호출을 찾을 수 없습니다.', 'not_found')
        return result

    @api.post('/consults/delete-preview', dependencies=post_guards)
    async def consult_delete_preview(body: PostBody, who: AdminWho):
        mutation, parsed = _mutation(ConsultDeletePreview, body, who, 'consult_trace_delete_preview')
        filters = _consult_filters(parsed.user_hash, parsed.start, parsed.end)
        return await _write(
            consult_admin_store.delete_consults, db, mutation, filters, consult_confirmation_key, who[1], None
        )

    @api.post('/consults/delete', dependencies=post_guards)
    async def consult_delete(body: PostBody, who: AdminWho):
        mutation, parsed = _mutation(ConsultDelete, body, who, 'consult_trace_delete')
        filters = _consult_filters(parsed.user_hash, parsed.start, parsed.end)
        return await _write(
            consult_admin_store.delete_consults,
            db,
            mutation,
            filters,
            consult_confirmation_key,
            who[1],
            parsed.confirmation,
        )

    @api.get('/feedbacks')
    async def feedbacks(
        status: str = 'pending',
        label: str | None = None,
        reason: str | None = None,
        q: str = '',
        limit: str = '50',
        cursor: str | None = None,
    ):
        if (
            status not in STATUSES
            or label not in (None, 'like', 'flag')
            or reason not in (None, *REASONS)
            or len(q) > 100
            or not limit.isascii()
            or not limit.isdecimal()
            or len(limit) > 3
            or not 1 <= int(limit) <= 200
        ):
            raise AdminError(422, '목록 조건이 올바르지 않습니다.', 'invalid_query')
        items, next_cursor = await anyio.to_thread.run_sync(
            partial(
                feedback_store.list_feedback,
                db,
                index,
                current_sha=corpus.sha,
                current_version=corpus.version,
                status=status,
                label=label,
                reason=reason,
                q=q.casefold(),
                limit=int(limit),
                cursor=_cursor(cursor),
            )
        )
        return dict(items=items, next_cursor=_encode_cursor(next_cursor) if next_cursor else None)

    def detail_payload(feedback_id):
        result = feedback_store.feedback_detail(db, feedback_id)
        if result is None:
            raise AdminError(404, '피드백을 찾을 수 없습니다.', 'not_found')
        feedback, reviews, promotions, assigned = result
        review = next((row for row in reviews if row['feedback_id'] == feedback_id), None)
        overlap = feedback_store.overlap_of(index, feedback, review)
        if overlap is None or overlap['split'] == 'train':
            overlap = (
                assigned_overlap(assigned, feedback['question'], review.get('corrected_question') if review else None)
                or overlap
            )
        return dict(
            feedback=feedback,
            reviews=reviews,
            promotions=promotions,
            checks=dict(overlap=overlap, corpus=corpus.checks(feedback)),
        )

    @api.get('/feedbacks/{feedback_id}')
    async def detail(feedback_id: str):
        return await anyio.to_thread.run_sync(detail_payload, feedback_id)

    @api.get('/articles')
    async def articles(q: str = ''):
        return {'items': corpus.search(q)}

    def check_calendar_configuration(private):
        if calendar_url is None:
            raise AdminError(503, '캘린더 주소가 설정되지 않았습니다.', 'calendar_not_configured')
        if private and not calendar_token:
            raise AdminError(503, '캘린더 관리자 서비스 토큰이 설정되지 않았습니다.', 'calendar_review_disabled')

    async def calendar_request(method, path, *, private=False, query=b'', payload=None):
        headers = {'Accept-Encoding': 'identity'}
        prefix = '/api'
        if private:
            headers['Authorization'] = f'Bearer {calendar_token}'
            prefix += '/admin'
        target = URL(f'{calendar_url}{prefix}/{path}').copy_with(query=query)
        try:
            with anyio.fail_after(CALENDAR_TIMEOUT):
                async with AsyncClient(timeout=CALENDAR_TIMEOUT, follow_redirects=False, trust_env=False) as client:
                    response = await client.request(method, target, headers=headers, json=payload)
        except TimeoutException, TimeoutError:
            raise AdminError(504, '캘린더 응답 시간이 초과됐습니다.', 'calendar_timeout') from None
        except RequestError:
            raise AdminError(502, '캘린더에 연결할 수 없습니다.', 'calendar_unavailable') from None
        if private and response.status_code == 403:
            raise AdminError(502, '캘린더 관리자 서비스 인증에 실패했습니다.', 'calendar_auth_failed')
        if private and response.status_code == 503:
            raise AdminError(503, '캘린더 관리자 서비스를 사용할 수 없습니다.', 'calendar_unavailable')
        content_type = response.headers.get('content-type')
        response_headers = {'content-type': content_type} if content_type else {}
        if private:
            response_headers['cache-control'] = 'no-store'
        return Response(
            response.content,
            status_code=response.status_code,
            headers=response_headers,
        )

    @api.get('/calendar/{path:path}')
    async def calendar(path: str, request: Request):
        private = path == 'import-failures' or CALENDAR_REVIEW_PATH.fullmatch(path) is not None
        if not private and not CALENDAR_PATH.fullmatch(path):
            raise AdminError(404, '허용된 캘린더 경로가 아닙니다.', 'not_found')
        check_calendar_configuration(private)
        return await calendar_request('GET', path, private=private, query=request.scope['query_string'])

    @api.post('/calendar/law-holds/{kind}/{hold_id}', dependencies=post_guards)
    async def calendar_review(kind: str, hold_id: str, body: PostBody, who: AdminWho):
        path = f'law-holds/{kind}/{hold_id}'
        if not CALENDAR_REVIEW_PATH.fullmatch(path):
            raise AdminError(404, '허용된 캘린더 경로가 아닙니다.', 'not_found')
        check_calendar_configuration(private=True)
        mutation, parsed = _mutation(CalendarReview, body, who, f'/calendar/{path}')
        payload = parsed.model_dump(exclude={'idempotency_key'}) | {'reviewer': who[0]}
        async with calendar_review_lock:
            try:
                receipt = await anyio.to_thread.run_sync(_calendar_receipt, db, mutation)
            except feedback_store.StoreError as exc:
                raise AdminError(exc.status, exc.detail, exc.code) from exc
            except (OSError, feedback_store.DatabaseError) as exc:
                log.warning('캘린더 검토 기록을 읽지 못했습니다 (%s).', type(exc).__name__)
                raise AdminError(500, '관리자 저장을 확인하지 못했습니다.', 'storage_error') from None
            if receipt:
                status, result = receipt
                return JSONResponse(result, status_code=status)
            response = await calendar_request('POST', path, private=True, payload=payload)
            if response.status_code != 200:
                return response
            return await _write(_save_calendar_review, db, mutation, kind, hold_id, payload.get('decision'))

    @api.post('/feedbacks/{feedback_id}/review', dependencies=post_guards)
    async def review(feedback_id: str, body: PostBody, who: AdminWho):
        mutation, parsed = _mutation(Review, body, who, f'/feedbacks/{feedback_id}/review')
        return await _write(
            feedback_store.save_review,
            db,
            index,
            corpus.articles,
            mutation,
            feedback_id,
            parsed.model_dump(exclude={'idempotency_key'}),
        )

    @api.post('/feedbacks/promote-up', dependencies=post_guards)
    async def promote_up(body: PostBody, who: AdminWho):
        mutation, parsed = _mutation(PromoteUp, body, who, '/feedbacks/promote-up')
        if len(set(parsed.feedback_ids)) != len(parsed.feedback_ids):
            raise AdminError(422, '피드백 ID는 중복될 수 없습니다.', 'invalid_body')
        return await _write(feedback_store.promote_up, db, index, corpus.articles, mutation, parsed.feedback_ids)

    @api.post('/feedbacks/{feedback_id}/promote-down', dependencies=post_guards)
    async def promote_down(feedback_id: str, body: PostBody, who: AdminWho):
        mutation, parsed = _mutation(PromoteDown, body, who, f'/feedbacks/{feedback_id}/promote-down')
        review = parsed.model_dump(exclude={'idempotency_key', 'review_verdict', 'review_note'}) | dict(
            verdict=parsed.review_verdict,
            decision='approved',
            note=parsed.review_note,
        )
        return await _write(feedback_store.promote_down, db, index, corpus.articles, mutation, feedback_id, review)

    @api.post('/promotions/{promotion_id}/revoke', dependencies=post_guards)
    async def revoke(promotion_id: str, body: PostBody, who: AdminWho):
        if len(promotion_id) > 18 or not promotion_id.isascii() or not promotion_id.isdecimal():
            raise AdminError(422, '승격 ID가 올바르지 않습니다.', 'invalid_body')
        mutation, parsed = _mutation(Revoke, body, who, f'/promotions/{int(promotion_id)}/revoke')
        return await _write(feedback_store.revoke, db, mutation, int(promotion_id), parsed.note)

    return api
