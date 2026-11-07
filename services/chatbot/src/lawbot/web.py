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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from lawbot import envfile, feedback, security, settings, view
from lawbot.answer_format import answer_citations, render
from lawbot.decree import is_decree
from lawbot.paths import ROOT
from lawbot.service import AskError, ask, load_app
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')

DIST = ROOT / 'services' / 'chatbot' / 'frontend' / 'dist'  # npm run build 결과 (git 제외)
# 화면 소스 지문을 만드는 파일·폴더 (frontend/fingerprint.ts의 SOURCES와 같아야 함)
SOURCES = (
    'src',
    'index.html',
    'package.json',
    'package-lock.json',
    'tsconfig.json',
    'vite.config.ts',
    'fingerprint.ts',
)
REBUILD = '화면 파일을 다시 만드세요: services/chatbot/frontend에서 npm ci && npm run build'
COOKIE = 'lawbot_session'
SESSION_TTL = 7 * 24 * 3600  # 초. 서버를 다시 띄워도 세션이 사라진다 (메모리)
MAX_SESSIONS = 200
LOGIN_FAIL_DELAY = 1.0  # 초. 비밀번호 대입을 느리게
MAX_QUESTION = 2000  # 글자. 모델 서버에 보내는 입력 길이 상한
WAITING = '⏳ 앞 질문을 기다리는 중…'
CONFIG_ERROR = '웹앱 설정 오류로 답하지 못했습니다. 서버 로그를 확인하세요.'
TICK = 'tick'  # 화면에 보낼 것이 없는 생성 단계 (보내지 않고 연결 끊김만 확인)
NO_BUILD = '화면 파일이 없습니다. services/chatbot/frontend에서 npm ci && npm run build를 실행하세요. (API는 /api/*)'


class Sessions:
    """로그인 세션: 쿠키 값 → (사용자, 세션 id, 만든 시각). 세션 id는 피드백 턴을 묶는 데만 쓴다 (쿠키 값과 별개)"""

    def __init__(self):
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def create(self, user):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.items[token] = (user, secrets.token_hex(8), time.monotonic())
            while len(self.items) > MAX_SESSIONS:
                self.items.popitem(last=False)
        return token

    def get(self, token):
        """(사용자, 세션 id) 또는 None (없음·만료)"""
        with self.lock:
            found = self.items.get(token)
            if found is None:
                return None
            if time.monotonic() - found[2] > SESSION_TTL:
                del self.items[token]
                return None
            return found[:2]

    def drop(self, token):
        with self.lock:
            self.items.pop(token, None)


def source_fingerprint(root):
    """화면 소스의 sha256: 경로(posix)를 정렬해 '경로 NUL 내용 NUL'을 이어 붙인 해시 (fingerprint.ts와 같은 규칙)"""
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
    if built != source_fingerprint(dist.parent):
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


def chat_events(app, question, model_key, fmt, remember=None):
    """질문 하나 → (이벤트, 데이터)를 차례로. remember(턴 기록) → turn_id (피드백이 꺼져 있으면 None)
    이벤트: status → sources(모두 펼침) → answer·stats… → sources(인용/검색만 구분) → stats → done, 실패하면 error
    생성 단계에서 화면 글이 그대로면(간결형의 근거·설명 토큰) TICK을 낸다: 받는 쪽이 토큰마다 연결 끊김을 확인하게"""
    spec = app.models.specs[model_key]
    yield 'status', {'text': view.searching_stats(app, model_key)}
    sent_sources, last = False, {}

    def changed(event, data):
        if last.get(event) == data:
            return []
        last[event] = data
        return [(event, data)]

    try:
        for stage, hits, raw, st in ask(app, question, model_key, fmt=fmt):
            if not sent_sources:
                sent_sources = True
                yield 'sources', sources_data(hits)
            if stage == 'load':
                yield 'status', {'text': view.loading_text(spec)}
                yield from changed('stats', {'text': view.fmt_stats(spec.label, stage, st)})
                continue
            if stage != 'done':
                text = render(raw, hits, fmt, done=False)
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
                    tid = remember(feedback.turn_record(app, question, hits, raw, text, model_key, fmt, st))
                except Exception as e:  # 피드백 기록 실패는 답변을 막지 않는다
                    log.warning(f'경고: 피드백용 턴 기록 실패 ({type(e).__name__})')
            yield 'done', {'text': text, 'finish': st.get('finish'), 'turn_id': tid}
    except AskError as err:  # 유형(kind)은 요청 로그의 error_type과 같음. 원래 예외는 __cause__
        msg = view.error_message(err.kind, err.__cause__, spec)
        # 원래 예외와 traceback은 서버 로그에 남긴다 (화면에는 안내 문구만, 설정 오류는 자세한 값도 숨김)
        log.error(f'질문 처리 실패 ({err.kind}, {err.stage})', exc_info=err.__cause__ or err)
        yield 'error', {'kind': err.kind, 'message': CONFIG_ERROR if msg is None else msg}


def current(request: Request):
    """로그인한 요청의 (사용자, 세션 id). 아니면 401"""
    token = request.cookies.get(COOKIE)
    found = request.app.state.sessions.get(token) if token else None
    if found is None:
        raise HTTPException(401, '로그인이 필요합니다.')
    return found


Who = Annotated[tuple, Depends(current)]


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
        'examples': view.EXAMPLES,
        'sources_title': view.sources_title(app),
        'feedback': {
            'enabled': fb is not None,
            'reasons': [{'value': v, 'label': k} for k, v in feedback.REASONS.items()] if fb is not None else [],
        },
    }


def create_app(app, auth: tuple[str, str], concurrency: int = 1, fb=None, dist=DIST):
    """app: service.load_app() 결과, auth: (사용자, 비밀번호), fb: feedback.Feedback 또는 None, dist: SPA 빌드 폴더"""
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions, slots = Sessions(), asyncio.Semaphore(concurrency)
    api.state.sessions = sessions
    user_b, pw_b = auth[0].encode(), auth[1].encode()

    def matches(user: str, pw: str):
        """둘 다 비교한다 (어느 쪽이 틀렸는지 시간으로 드러나지 않게)"""
        return hmac.compare_digest(user.encode(), user_b) & hmac.compare_digest(pw.encode(), pw_b)

    @api.post('/api/login', dependencies=[Depends(require_json)])
    async def login(request: Request, response: Response):
        body = await read_json(request)
        user, pw = (body.get('user'), body.get('password')) if isinstance(body, dict) else (None, None)
        if not (isinstance(user, str) and isinstance(pw, str) and matches(user, pw)):
            await asyncio.sleep(LOGIN_FAIL_DELAY)
            raise HTTPException(401, '사용자명 또는 비밀번호가 틀렸습니다.')
        # https(Tailscale 등 프록시의 X-Forwarded-Proto, uvicorn이 127.0.0.1에서 온 것만 믿음)일 때만 Secure
        secure = request.url.scheme == 'https'
        response.set_cookie(COOKIE, sessions.create(user), httponly=True, samesite='strict', path='/', secure=secure)
        return {'user': user}

    @api.post('/api/logout', dependencies=[Depends(require_json)])
    def logout(request: Request, response: Response, who: Who):
        sessions.drop(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path='/')
        return {}

    @api.get('/api/me')
    def me(who: Who):
        return {'user': who[0]}

    @api.get('/api/config')
    def config(who: Who):
        return config_data(app, fb)

    @api.post('/api/chat', dependencies=[Depends(require_json)])
    async def chat(request: Request, who: Who):
        question, model_key, fmt = valid_chat(app, await read_json(request))
        remember = partial(fb.remember_turn, who[1]) if fb is not None else None
        return StreamingResponse(
            stream(request, chat_events(app, question, model_key, fmt, remember)),
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
        try:
            row = await anyio.to_thread.run_sync(
                fb.save, who[1], body['turn_id'], body.get('label'), body.get('reason')
            )
        except ValueError as e:
            raise HTTPException(400, '알 수 없는 피드백입니다.') from e
        except LookupError as e:
            raise HTTPException(404, '기록이 없는 답변입니다.') from e
        return {'saved': row is not None}

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
    api = create_app(load_app(cfg), cfg.auth, cfg.chat_concurrency, cfg.feedback)
    log.info(f'웹앱: http://{security.SERVER_NAME}:{port}')
    uvicorn.run(api, host=security.SERVER_NAME, port=port, log_level='warning')


if __name__ == '__main__':
    main()
