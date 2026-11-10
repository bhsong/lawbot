# lawbot/web.py  →  웹앱: uv run lawbot-app [--port N]
# 로그인(메모리 세션 쿠키) · 화면 설정 · 질문 하나를 SSE로 · 답변 피드백 · 빌드한 SPA 파일 (docs/plans/web-spa.md 2절)
# 주소는 127.0.0.1 고정
import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from functools import partial
from pathlib import Path
from typing import Annotated

import anyio
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from lawbot import audit_store, envfile, feedback, security, settings, view
from lawbot import auth as auth_logic
from lawbot.answer_format import answer_citations, render
from lawbot.calc_gate import is_calculation_question
from lawbot.decree import is_decree
from lawbot.hits import Timing
from lawbot.paths import ROOT
from lawbot.service import AskError, ask, load_app, log_request
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')

FRONTEND = Path('services/chatbot/frontend')
DIST = ROOT / FRONTEND / 'dist'  # npm run build 결과 (git 제외)
# 화면 소스 지문을 만드는 파일·폴더 (frontend/fingerprint.ts의 SOURCES와 같아야 함)
SOURCES = (
    *(
        FRONTEND / name
        for name in (
            'src',
            'index.html',
            'package.json',
            'package-lock.json',
            'tsconfig.json',
            'vite.config.ts',
            'fingerprint.ts',
        )
    ),
    Path('packages/lawbot-ui/src'),
)
REBUILD = '화면 파일을 다시 만드세요: services/chatbot/frontend에서 npm ci && npm run build'
COOKIE = 'lawbot_session'
SESSION_TTL = 7 * 24 * 3600  # 초. 서버를 다시 띄워도 세션이 사라진다 (메모리)
MAX_SESSIONS = 200
LOGIN_FAIL_DELAY = 1.0  # 초. 비밀번호 대입을 느리게
LOGIN_ATTEMPT_CAPACITY = 1024
ADMIN_BODY_LIMIT = 64 * 1024
MAX_QUESTION = 2000  # 글자. 모델 서버에 보내는 입력 길이 상한
WAITING = '⏳ 앞 질문을 기다리는 중…'
CONFIG_ERROR = '웹앱 설정 오류로 답하지 못했습니다. 서버 로그를 확인하세요.'
TICK = 'tick'  # 화면에 보낼 것이 없는 생성 단계 (보내지 않고 연결 끊김만 확인)
NO_BUILD = '화면 파일이 없습니다. services/chatbot/frontend에서 npm ci && npm run build를 실행하세요. (API는 /api/*)'


class Sessions:
    """Store (user, session ID, creation time, role) under the cookie token."""

    def __init__(self):
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def create(self, user, role: auth_logic.Role = 'user'):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.items[token] = (user, secrets.token_hex(8), time.monotonic(), role)
            while len(self.items) > MAX_SESSIONS:
                self.items.popitem(last=False)
        return token

    def get(self, token):
        """Return (user, session ID, role) for a live session."""
        with self.lock:
            found = self.items.get(token)
            if found is None:
                return None
            if time.monotonic() - found[2] > SESSION_TTL:
                del self.items[token]
                return None
            return found[0], found[1], found[3]

    def drop(self, token):
        with self.lock:
            self.items.pop(token, None)


def source_fingerprint(root):
    """Hash sorted repository-relative POSIX paths and file bytes, as in fingerprint.ts."""
    root = Path(root)
    files = sorted(
        p.relative_to(root).as_posix()
        for name in SOURCES
        for p in ([root / name] if (root / name).is_file() else (root / name).rglob('*'))
        if p.is_file()
    )
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode() + b'\0' + (root / rel).read_bytes() + b'\0')
    return h.hexdigest()


def require_build(dist):
    """빌드한 화면이 지금 소스와 같은지 (운영 반영 때 빌드를 빠뜨리면 옛 화면이 새 API를 부른다). 다르면 RuntimeError"""
    dist = Path(dist)
    if not (dist / 'index.html').is_file():
        raise RuntimeError(f'화면 파일이 없습니다 ({dist}). {REBUILD}')
    try:
        built = json.loads((dist / 'build.json').read_text(encoding='utf-8'))['source']
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f'화면 빌드 정보(build.json)를 읽지 못했습니다. {REBUILD}') from e
    root = dist.parent.resolve().parents[len(FRONTEND.parts) - 1]
    if built != source_fingerprint(root):
        raise RuntimeError(f'화면 파일이 지금 소스와 다릅니다. {REBUILD}')


def sse(event, data):
    """SSE 한 덩어리. JSON은 한 줄 (줄바꿈은 \\n으로 이스케이프됨)"""
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def source_item(h):
    return {
        'title': view.source_title(h),
        'scores': view.source_scores(h),
        'body': view.source_body(h),
        'decree': is_decree(h),
    }


def sources_data(hits, cited=None, refused=False):
    return {
        'groups': [
            {'label': label, 'open': opened, 'items': [source_item(h) for h in group]}
            for label, opened, group in view.source_groups(hits, cited, refused)
        ]
    }


def cached_events(app, question, model_key, fmt, remember, decision, request_id, started):
    match = decision.match
    namespace = app.exact_cache.snapshot.namespace
    metadata = {
        'request_id': request_id,
        'response_source': 'exact_cache',
        'cache_answer_id': match.answer_id,
        'corpus_version': namespace.corpus_version,
        'answer_policy_version': namespace.answer_policy_version,
    }
    hits = [article.hit() for article in match.articles]
    items = [source_item(hit) | {'scores': ''} for hit in hits]
    sources = {'groups': [{'label': view.CITED_LABEL, 'open': True, 'items': items}]}
    st = {
        'model': model_key,
        'request_id': request_id,
        'finish': 'stop',
        'n_cited': len(hits),
        'refused': False,
        'cache_metrics': decision.metrics() | metadata,
        'call_counts': dict.fromkeys(('embedding', 'search', 'rerank', 'llm'), 0),
    }
    status = 'cancelled'
    try:
        yield 'sources', sources
        st['first_text'] = time.perf_counter() - started
        yield 'answer', {'text': match.text}
        tid = None
        if remember is not None:
            record = {
                'question': question,
                'answer_shown': match.text,
                'answer_raw': None,
                'hits': [{k: v for k, v in h.items() if k != 'score'} for h in hits],
                'citations': [dict(law=h['law'], article_id=h['article_id'], verdict='ok') for h in hits],
                'model': model_key,
                'fmt': fmt,
                'refused': False,
                'n_cited': len(hits),
                'profile': app.profile.name,
                'dataset_version': app.dataset_version,
                'corpus_release_id': app.corpus_release_id,
                'articles_sha256': app.articles_sha256,
                'commit': feedback.commit(),
                'instance': security.instance_name(),
            } | metadata
            try:
                tid = remember(record)
            except (OSError, ValueError, RuntimeError) as exc:
                log.warning('경고: 캐시 피드백용 턴 기록 실패 (%s)', type(exc).__name__)
        yield 'stats', {'text': f'정확 일치 캐시 조회 {decision.elapsed * 1000:.1f}ms'}
        yield 'done', {'text': match.text, 'finish': 'stop', 'turn_id': tid} | metadata
        status = 'ok'
    finally:
        st['total'] = time.perf_counter() - started
        st['cache_metrics']['request_to_complete_ms'] = round(st['total'] * 1000, 3)
        if 'first_text' in st:
            st['cache_metrics']['request_to_first_answer_ms'] = round(st['first_text'] * 1000, 3)
        log_request(app, st, fmt, status)


def chat_events(app, question, model_key, fmt, remember=None, *, started=None):
    """질문 하나 → (이벤트, 데이터)를 차례로. remember(턴 기록) → turn_id (피드백이 꺼져 있으면 None)
    이벤트: status → sources(모두 펼침) → answer·stats… → sources(인용/검색만 구분) → stats → done, 실패하면 error
    생성 단계에서 화면 글이 그대로면(간결형의 근거·설명 토큰) TICK을 낸다: 받는 쪽이 토큰마다 연결 끊김을 확인하게"""
    started = time.perf_counter() if started is None else started
    cache = getattr(app, 'exact_cache', None)
    metadata, ask_options = {}, {}
    outcomes: list[tuple[Timing, str, str | None]] = []
    sent_done = False

    def report(st, status, error):
        outcomes.append((st, status, error))

    if is_calculation_question(question):
        request_id = secrets.token_hex(6)
        metadata = {'request_id': request_id, 'response_source': 'rag'}
        ask_options = {
            'request_id': request_id,
            'cache_metrics': {
                'cache_mode': 'bypass',
                'cache_reason': 'calculation_question',
                'cache_eligible': False,
                'would_hit': False,
                'exact_lookup_ms': None,
            }
            | metadata,
            'report': report,
            'calculation_candidate': True,
        }
    elif cache is not None:
        decision = cache.lookup(question, model_key, fmt)
        request_id = secrets.token_hex(6)
        if decision.mode == 'on' and decision.match is not None:
            yield from cached_events(app, question, model_key, fmt, remember, decision, request_id, started)
            return
        metadata = {'request_id': request_id, 'response_source': 'rag'}
        ask_options = {'request_id': request_id, 'cache_metrics': decision.metrics() | metadata, 'report': report}
    spec = app.models.specs[model_key]
    yield 'status', {'text': view.searching_stats(app, model_key)}
    sent_sources, last = False, {}
    first_ms = None

    def changed(event, data):
        if last.get(event) == data:
            return []
        last[event] = data
        return [(event, data)]

    try:
        for stage, hits, raw, st in ask(app, question, model_key, fmt=fmt, **ask_options):
            if stage == 'calculation':
                yield 'calculation', st['_calculation']
                continue
            if not sent_sources:
                sent_sources = True
                yield 'sources', sources_data(hits)
            if stage == 'load':
                yield 'status', {'text': view.loading_text(spec)}
                yield from changed('stats', {'text': view.fmt_stats(spec.label, stage, st)})
                continue
            if stage != 'done':
                text = render(raw, hits, fmt, done=False)
                if first_ms is None and text.strip():
                    first_ms = round((time.perf_counter() - started) * 1000, 3)
                out = changed('answer', {'text': text}) if text.strip() else changed('status', {'text': view.WRITING})
                out += changed('stats', {'text': view.fmt_stats(spec.label, stage, st)})
                yield from out or [(TICK, None)]
                continue
            text = render(raw, hits, fmt, done=True) if raw.strip() else view.EMPTY
            used, _, no = answer_citations(raw, hits)
            yield 'sources', sources_data(hits, used, no)
            yield from changed('stats', {'text': view.fmt_stats(spec.label, stage, st)})
            tid = None
            if remember is not None:
                try:
                    tid = remember(feedback.turn_record(app, question, hits, raw, text, model_key, fmt, st) | metadata)
                except Exception as e:  # 피드백 기록 실패는 답변을 막지 않는다
                    log.warning(f'경고: 피드백용 턴 기록 실패 ({type(e).__name__})')
            if first_ms is None:
                first_ms = round((time.perf_counter() - started) * 1000, 3)
            check = st.get('calculation_metrics', {}).get('calculation_check')
            if check and check != 'not_run':
                yield 'calculation_check', {'status': check}
            yield 'done', {'text': text, 'finish': st.get('finish'), 'turn_id': tid} | metadata
            sent_done = True
    except AskError as err:  # 유형(kind)은 요청 로그의 error_type과 같음. 원래 예외는 __cause__
        msg = view.error_message(err.kind, err.__cause__, spec)
        # 원래 예외와 traceback은 서버 로그에 남긴다 (화면에는 안내 문구만, 설정 오류는 자세한 값도 숨김)
        log.error(f'질문 처리 실패 ({err.kind}, {err.stage})', exc_info=err.__cause__ or err)
        yield 'error', {'kind': err.kind, 'message': CONFIG_ERROR if msg is None else msg}
    finally:
        if outcomes:
            timing, status, error = outcomes[-1]
            if status == 'ok' and not sent_done:
                status = 'cancelled'
            metrics = timing.setdefault('cache_metrics', {})
            metrics['request_to_first_answer_ms'] = first_ms
            metrics['request_to_complete_ms'] = round((time.perf_counter() - started) * 1000, 3)
            log_request(app, timing, fmt, status, error)


def _session(request: Request):
    token = request.cookies.get(COOKIE)
    return request.app.state.sessions.get(token) if token else None


def current(request: Request):
    """Return the current session or raise 401."""
    found = _session(request)
    if found is None:
        raise HTTPException(401, '로그인이 필요합니다.')
    return found


Who = Annotated[tuple, Depends(current)]


class AdminError(HTTPException):
    def __init__(self, status: int, detail: str, code: str):
        super().__init__(status, detail)
        self.code = code


def require_admin(request: Request):
    found = _session(request)
    if found is None:
        raise AdminError(401, '로그인이 필요합니다.', 'login_required')
    if found[2] != 'admin':
        raise AdminError(403, '관리자 권한이 필요합니다.', 'admin_required')
    return found


AdminWho = Annotated[tuple, Depends(require_admin)]


def require_same_origin(request: Request):
    host = request.headers.get('host')
    if not host or request.headers.getlist('origin') != [f'{request.url.scheme}://{host}']:
        raise AdminError(403, '관리자 요청의 출처가 올바르지 않습니다.', 'bad_origin')


async def read_admin_body(request: Request) -> bytes:
    """Return the raw body for an admin POST, enforcing the limit while streaming."""
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > ADMIN_BODY_LIMIT:
            raise AdminError(413, '관리자 요청 본문이 너무 큽니다.', 'body_too_large')
        body.extend(chunk)
    return bytes(body)


async def require_admin_post_rate(request: Request, who: AdminWho):
    limit = request.app.state.admin_post_limit
    sid = who[1]
    if not limit.allowed(sid):
        raise AdminError(429, '관리자 요청이 너무 많습니다.', 'rate_limited')
    limit.record(sid)


def require_json(request: Request):
    """POST는 JSON만 (SameSite=Strict 쿠키와 함께 다른 사이트의 폼 전송을 막는다)"""
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON으로 보내세요.')


async def read_json(request):
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(400, 'JSON 형식이 아닙니다.') from e


def valid_chat(app, body):
    """요청 본문 → (질문, 모델 key, 형식). 틀리면 400"""
    if not isinstance(body, dict):
        raise HTTPException(400, '요청 형식이 틀렸습니다.')
    question, model_key, fmt = body.get('question'), body.get('model'), body.get('format')
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(400, '질문을 입력해 주세요.')
    if len(question) > MAX_QUESTION:
        raise HTTPException(400, f'질문이 너무 깁니다 (최대 {MAX_QUESTION}자).')
    if any(s.key == model_key for s in app.profile.disabled):
        raise HTTPException(400, view.DISABLED_MODEL)
    if model_key not in app.models.specs:
        raise HTTPException(400, '알 수 없는 답변 모델입니다.')
    if fmt not in ('concise', 'full'):
        raise HTTPException(400, '알 수 없는 답변 형식입니다.')
    return question, model_key, fmt


def config_data(app, fb):
    p = app.profile
    return {
        'title': view.page_title(app),
        'intro': view.intro_text(app) + view.DISCLAIMER,
        'notice': view.data_notice(app),
        'models': [
            {'key': s.key, 'label': s.label, 'info': s.info, 'format': s.fmt} for s in app.models.specs.values()
        ],
        'disabled': [{'key': s.key, 'label': s.label} for s in p.disabled],
        'default_model': p.models[0].key,
        'formats': [{'value': v, 'label': label} for label, v in view.FORMATS],
        'examples': view.V5_EXAMPLES if p.name == 'v5' else view.EXAMPLES,
        'sources_title': view.sources_title(app),
        'feedback': {
            'enabled': fb is not None,
            'reasons': [{'value': v, 'label': k} for k, v in feedback.REASONS.items()] if fb is not None else [],
        },
    }


def create_app(
    app,
    auth: tuple[str, str],
    concurrency: int = 1,
    fb=None,
    dist=DIST,
    *,
    admin: auth_logic.AdminCredentials | None = None,
    db: Path | None = None,
    protected=None,
    calendar_url: str | None = None,
    calendar_token: str | None = None,
):
    """app: service.load_app() 결과, auth: (사용자, 비밀번호), fb: feedback.Feedback 또는 None, dist: SPA 빌드 폴더"""
    if admin is not None and db is None:
        raise RuntimeError('관리자 계정을 쓰려면 LAWBOT_DB를 설정하세요.')
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions, slots = Sessions(), asyncio.Semaphore(concurrency)
    feedback_writes = asyncio.Lock()
    api.state.sessions = sessions
    api.state.admin_post_limit = auth_logic.AttemptWindow(30, 60, MAX_SESSIONS)
    user_b, pw_b = auth[0].encode(), auth[1].encode()
    admin_b = admin.user.encode() if admin is not None else None
    reserved = (hashlib.sha256(name).hexdigest() for name in (user_b, admin_b) if name is not None)
    failures = auth_logic.AttemptWindow(
        5, 15 * 60, LOGIN_ATTEMPT_CAPACITY, cooldown=True, reserved=reserved, clock=lambda: time.monotonic()
    )
    login_lock, audit_lock = asyncio.Lock(), asyncio.Lock()

    @api.exception_handler(AdminError)
    async def admin_error(_: Request, exc: AdminError):
        return JSONResponse({'detail': exc.detail, 'code': exc.code}, status_code=exc.status_code)

    def matches(user: str, pw: str):
        """둘 다 비교한다 (어느 쪽이 틀렸는지 시간으로 드러나지 않게)"""
        return hmac.compare_digest(user.encode(), user_b) & hmac.compare_digest(pw.encode(), pw_b)

    async def verify_login(user: str, pw: object, role: auth_logic.Role | None):
        assert admin is not None and admin_b is not None
        encoded_user = user.encode()
        is_admin = hmac.compare_digest(encoded_user, admin_b)
        key = hashlib.sha256(encoded_user).hexdigest()
        async with login_lock:
            blocked = not failures.allowed(key)
            if blocked:
                role = None
            else:
                if isinstance(pw, str):
                    stored = admin.password_hash if is_admin else auth_logic.DUMMY_HASH
                    verified = await anyio.to_thread.run_sync(auth_logic.verify_password, pw, stored)
                    if is_admin and verified:
                        role = 'admin'
                if role is None:
                    failures.record(key)
                else:
                    failures.clear(key)
        if is_admin and db is not None:
            async with audit_lock:
                await anyio.to_thread.run_sync(audit_store.record_login, db, admin.user, role == 'admin')
        return role, blocked

    @api.post('/api/login', dependencies=[Depends(require_json)])
    async def login(request: Request, response: Response):
        body = await read_json(request)
        user, pw = (body.get('user'), body.get('password')) if isinstance(body, dict) else (None, None)
        role: auth_logic.Role | None = (
            'user' if isinstance(user, str) and isinstance(pw, str) and matches(user, pw) else None
        )
        blocked = False
        if admin is not None and isinstance(user, str):
            role, blocked = await verify_login(user, pw, role)
        if role is None:
            await asyncio.sleep(LOGIN_FAIL_DELAY)
            if blocked:
                raise HTTPException(429, '로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.')
            raise HTTPException(401, '사용자명 또는 비밀번호가 틀렸습니다.')
        # https(Tailscale 등 프록시의 X-Forwarded-Proto, uvicorn이 127.0.0.1에서 온 것만 믿음)일 때만 Secure
        secure = request.url.scheme == 'https'
        response.set_cookie(
            COOKIE, sessions.create(user, role), httponly=True, samesite='strict', path='/', secure=secure
        )
        return {'user': user}

    @api.post('/api/logout', dependencies=[Depends(require_json)])
    def logout(request: Request, response: Response, who: Who):
        sessions.drop(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path='/')
        return {}

    @api.get('/api/me')
    def me(who: Who):
        return {'user': who[0]} if admin is None else {'user': who[0], 'role': who[2]}

    @api.get('/api/config')
    def config(who: Who):
        return config_data(app, fb)

    @api.post('/api/chat', dependencies=[Depends(require_json)])
    async def chat(request: Request, who: Who):
        started = time.perf_counter()
        question, model_key, fmt = valid_chat(app, await read_json(request))
        remember = partial(fb.remember_turn, who[1]) if fb is not None else None
        return StreamingResponse(
            stream(request, chat_events(app, question, model_key, fmt, remember, started=started)),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache'},
        )

    async def stream(request, events):
        """동시 처리 수(LAWBOT_CHAT_CONCURRENCY)만큼만 생성. 자리가 없으면 기다린다
        ask는 동기 생성기라 한 단계씩 스레드에서 돌린다. 연결이 끊기면 닫아서 요청 로그에 cancelled를 남긴다
        끊김은 조각마다 직접 확인한다 (보내기 실패만으로는 브라우저 중지를 놓친다: 2026-11-02 e2e_stub에서 확인)"""
        if slots.locked():
            yield sse('status', {'text': WAITING})
        async with slots:
            try:
                while (item := await anyio.to_thread.run_sync(next, events, None)) is not None:
                    if await request.is_disconnected():
                        return
                    if item[0] != TICK:
                        yield sse(*item)
            finally:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(events.close)

    @api.post('/api/feedback', dependencies=[Depends(require_json)])
    async def feedback_(request: Request, who: Who):
        if fb is None:
            raise HTTPException(404, '피드백이 꺼져 있습니다.')
        body = await read_json(request)
        if not isinstance(body, dict) or not isinstance(body.get('turn_id'), str):
            raise HTTPException(400, '요청 형식이 틀렸습니다.')
        received_at = time.monotonic()
        try:
            # Equal-timestamp records are reviewed in append order.
            async with feedback_writes:
                row = await anyio.to_thread.run_sync(
                    fb.save, who[1], body['turn_id'], body.get('label'), body.get('reason'), received_at
                )
        except ValueError as e:
            raise HTTPException(400, '알 수 없는 피드백입니다.') from e
        except LookupError as e:
            raise HTTPException(404, '기록이 없는 답변입니다.') from e
        return {'saved': row is not None}

    if admin is not None:
        from lawbot.admin import router

        api.include_router(
            router(db, app, protected=protected, calendar_url=calendar_url, calendar_token=calendar_token)
        )

        @api.exception_handler(StarletteHTTPException)
        async def admin_route_error(request: Request, exc: StarletteHTTPException):
            if request.url.path.startswith('/api/admin/') and exc.status_code == 405:
                return JSONResponse(
                    {'detail': '허용되지 않는 메서드입니다.', 'code': 'method_not_allowed'},
                    status_code=405,
                    headers=exc.headers,
                )
            return await http_exception_handler(request, exc)

    mount_spa(api, Path(dist) if dist is not None else None)
    return api


def mount_spa(api, dist):
    """빌드한 SPA: /assets/*는 파일, 그 밖의 경로는 dist의 파일 또는 index.html (화면 안 경로). /api/*는 404"""
    root = dist.resolve() if dist is not None and (dist / 'index.html').is_file() else None
    if root is not None:
        api.mount('/assets', StaticFiles(directory=root / 'assets', check_dir=False), name='assets')

    @api.get('/{path:path}', include_in_schema=False)
    def page(path: str):
        if path == 'api' or path.startswith('api/'):
            return JSONResponse({'detail': 'Not Found'}, status_code=404)
        if root is None:
            return HTMLResponse(NO_BUILD, status_code=404)
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            return FileResponse(file)
        return FileResponse(root / 'index.html', headers={'Cache-Control': 'no-cache'})


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lawbot 웹앱 (127.0.0.1, 로그인 필수)')
    parser.add_argument('--port', help='포트 (기본: .env의 LAWBOT_PORT). 테스트에서 빈 포트로 띄울 때')
    args = parser.parse_args(argv)
    envfile.load()  # .env (셸에 있는 변수 우선). 설정을 읽기 전에
    cfg = settings.load(web=True)  # 인증·포트·모델·피드백 폴더를 모두 검사 (모델 서버에 접속하기 전에)
    require_build(DIST)  # 화면 파일이 없거나 소스와 다르면 모델 서버에 접속하기 전에 거부
    port = security.server_port(dict(os.environ, LAWBOT_PORT=args.port)) if args.port else cfg.port
    if cfg.auth is None or port is None or cfg.chat_concurrency is None:  # load(web=True)가 채운다
        raise RuntimeError('웹 설정(로그인·포트·동시 처리 수)을 읽지 못했습니다.')
    protected = None
    if cfg.admin is not None:
        from lawbot.admin import load_protected

        protected = load_protected(cfg.db)
    api = create_app(
        load_app(cfg),
        cfg.auth,
        cfg.chat_concurrency,
        cfg.feedback,
        admin=cfg.admin,
        db=cfg.db,
        protected=protected,
        calendar_url=cfg.calendar_api_url,
        calendar_token=cfg.calendar_admin_token,
    )
    log.info(f'웹앱: http://{security.SERVER_NAME}:{port}')
    uvicorn.run(api, host=security.SERVER_NAME, port=port, log_level='warning')


if __name__ == '__main__':
    # Register handlers from the same module that the admin router imports.
    from lawbot.web import main as entry

    entry()
