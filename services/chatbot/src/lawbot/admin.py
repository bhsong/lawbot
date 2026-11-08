"""Administrator feedback and corpus API."""

import base64
import hashlib
import json
import re
from functools import partial
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from httpx import URL, AsyncClient, RequestError, TimeoutException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from lawbot import dataset_store, feedback_store
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
CALENDAR_PATH = re.compile(
    r'(?:law-calendar(?:/status)?|law-events|law-holds|law-changes/[A-Za-z0-9_:-]+(?:/notice-comparison/[0-9]+)?)'
)
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
            if not isinstance(hit, dict):
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


def router(db, app, *, protected=None, calendar_url=None):
    api = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])
    index = load_protected(db) if protected is None else protected
    corpus = Corpus(app)
    post_guards = [Depends(_post_body), Depends(require_admin_post_rate)]

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

    @api.get('/calendar/{path:path}')
    async def calendar(path: str, request: Request):
        if not CALENDAR_PATH.fullmatch(path):
            raise AdminError(404, '허용된 캘린더 경로가 아닙니다.', 'not_found')
        if calendar_url is None:
            raise AdminError(503, '캘린더 주소가 설정되지 않았습니다.', 'calendar_not_configured')
        target = URL(f'{calendar_url}/api/{path}').copy_with(query=request.scope['query_string'])
        try:
            with anyio.fail_after(CALENDAR_TIMEOUT):
                async with AsyncClient(timeout=CALENDAR_TIMEOUT, follow_redirects=False, trust_env=False) as client:
                    response = await client.get(target, headers={'Accept-Encoding': 'identity'})
        except TimeoutException, TimeoutError:
            raise AdminError(504, '캘린더 응답 시간이 초과됐습니다.', 'calendar_timeout') from None
        except RequestError:
            raise AdminError(502, '캘린더에 연결할 수 없습니다.', 'calendar_unavailable') from None
        content_type = response.headers.get('content-type')
        return Response(
            response.content,
            status_code=response.status_code,
            headers={'content-type': content_type} if content_type else None,
        )

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
