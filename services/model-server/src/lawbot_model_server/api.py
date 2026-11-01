# lawbot_model_server/api.py  →  OpenAI 호환 HTTP API (FastAPI)
#   POST /v1/chat/completions  (stream: SSE)   POST /v1/embeddings   POST /v1/rerank (Cohere/Jina 형식)
#   GET  /v1/models            GET /health (모델을 불러오는 중에도 응답)
# 모든 요청에 Bearer 토큰이 필요하다 (/health 포함). 질문·문서 내용은 로그에 남기지 않는다
# 모델 연산은 락 하나로 한 번에 하나 (MLX·MPS를 여러 스레드에서 동시에 돌리지 않음). 작업 스레드가 락을 잡고 계산해
# 결과를 asyncio 큐로 넘긴다. 스트림 중 클라이언트가 끊으면 다음 토큰에서 멈추고 락을 푼다
# 확장 필드: chat_template_kwargs.enable_thinking, 응답의 timings / embeddings의 input_type·max_seq_length / rerank의 instruction
import asyncio
import base64
import hmac
import json
import threading
import time
import uuid
from typing import Literal

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lawbot_model_server.registry import UnknownModel

MAX_BODY = 8 * 1024 * 1024  # 요청 본문 최대 (조문 415개 인코딩, 질문 2,075개 rank가 들어감)
DEFAULT_MAX_TOKENS = 1024  # max_tokens가 없을 때 (챗봇은 항상 1024를 보냄)
MAX_TOKENS_LIMIT = 32768
MAX_INPUTS = 4096  # embeddings input 개수
MAX_DOCUMENTS = 1000  # rerank documents 개수


# ---- 요청 형식 (모르는 필드는 400: 조용히 무시하지 않음) ----
class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class TextPart(Strict):
    type: Literal['text']
    text: str


class Message(Strict):
    role: Literal['system', 'user', 'assistant']
    content: str | list[TextPart]
    name: str | None = None


class StreamOptions(Strict):
    include_usage: bool = False


class TemplateKwargs(Strict):
    enable_thinking: bool = False


class ChatRequest(Strict):
    model: str
    messages: list[Message] = Field(min_length=1)
    max_tokens: int | None = Field(None, ge=1, le=MAX_TOKENS_LIMIT)
    max_completion_tokens: int | None = Field(None, ge=1, le=MAX_TOKENS_LIMIT)
    temperature: float | None = Field(None, ge=0, le=2)  # 없으면 0 (탐욕적 생성). OpenAI 기본값(1)과 다름
    top_p: float | None = Field(None, gt=0, le=1)
    stream: bool = False
    stream_options: StreamOptions | None = None
    n: int | None = Field(None, ge=1, le=1)
    chat_template_kwargs: TemplateKwargs | None = None  # 기본 생각 끔
    user: str | None = None  # 받기만 하고 쓰지 않음


class EmbeddingRequest(Strict):
    model: str
    input: str | list[str] = Field(min_length=1, max_length=MAX_INPUTS)
    encoding_format: Literal['float', 'base64'] = 'float'  # base64: float32 little-endian 그대로
    input_type: Literal['query', 'document'] = 'document'
    max_seq_length: int | None = Field(None, ge=1, le=32768)
    user: str | None = None


class RerankRequest(Strict):
    model: str
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    top_n: int | None = Field(None, ge=1)
    instruction: str | None = None
    return_documents: bool = False


# ---- 오류 ----
class ApiError(Exception):
    def __init__(self, status, message, kind='invalid_request_error', code=None):
        super().__init__(message)
        self.status, self.message, self.kind, self.code = status, message, kind, code


class ModelLoadError(RuntimeError):
    """chat 모델을 불러오지 못함 (폴더 없음·형식 오류). 원래 예외 이름을 code로 보낸다"""


def error_body(message, kind, code=None):
    return {'error': {'message': message, 'type': kind, 'param': None, 'code': code}}


def error_response(status, message, kind='invalid_request_error', code=None):
    return JSONResponse(error_body(message, kind, code), status_code=status)


def exception_body(e):
    """(HTTP 상태, 오류 본문). 모델 연산 오류는 예외 이름을 code로 (웹앱이 유형을 안내)"""
    if isinstance(e, ApiError):
        return e.status, error_body(e.message, e.kind, e.code)
    if isinstance(e, UnknownModel):
        return 404, error_body(str(e), 'invalid_request_error', 'model_not_found')
    if isinstance(e, ModelLoadError):
        cause = e.__cause__
        return 500, error_body(f'모델을 불러오지 못했습니다: {cause}', 'model_load_error', type(cause).__name__)
    return 500, error_body(f'{type(e).__name__}: {e}', 'server_error', type(e).__name__)


def validation_message(e):
    err = e.errors()[0]
    loc = '.'.join(str(x) for x in err.get('loc', ()))
    if err.get('type') == 'extra_forbidden':
        return f'지원하지 않는 필드: {loc}'
    if err.get('type') == 'json_invalid':
        return 'JSON 형식 오류'
    return f'{loc}: {err.get("msg")}' if loc else str(err.get('msg'))


async def parse(request, model):
    body = await request.body()
    if len(body) > request.app.state.max_body:
        raise ApiError(413, f'요청 본문이 {request.app.state.max_body}바이트를 넘습니다')
    try:
        return model.model_validate_json(body or b'null')
    except ValidationError as e:
        raise ApiError(400, validation_message(e)) from None


def ready(request):
    reg = request.app.state.registry
    if reg is None:
        raise ApiError(503, '모델을 불러오는 중입니다', 'server_not_ready')
    return reg


# ---- 작업 스레드 ----
class Job:
    """work()가 돌려주는 반복자를 작업 스레드에서 lock을 잡고 돌려, 항목을 ('item', x)로 큐에 넣는다
    끝은 ('end', None), 오류는 ('error', 예외). cancel() 뒤에는 다음 항목에서 멈추고 반복자를 닫는다 (락이 풀림)"""

    def __init__(self, lock, work):
        self.loop, self.q = asyncio.get_running_loop(), asyncio.Queue()
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(lock, work), daemon=True)
        self.thread.start()

    def _put(self, item):
        try:
            self.loop.call_soon_threadsafe(self.q.put_nowait, item)
        except RuntimeError:  # 이벤트 루프가 이미 닫힘 (서버 종료)
            pass

    def _run(self, lock, work):
        try:
            with lock:
                if self.cancelled.is_set():
                    return
                it = work()
                try:
                    for x in it:
                        self._put(('item', x))
                        if self.cancelled.is_set():
                            break
                finally:
                    close = getattr(it, 'close', None)
                    if close is not None:
                        close()
        except Exception as e:
            self._put(('error', e))
            return
        self._put(('end', None))

    async def next(self):
        return await self.q.get()

    def cancel(self):
        self.cancelled.set()


async def run_locked(request, fn):
    """embeddings·rerank처럼 한 번에 끝나는 연산 (스트림 없음)"""
    job = Job(request.app.state.lock, lambda: iter([fn()]))
    try:
        kind, value = await job.next()
        if kind == 'error':  # 리랭커 점수 NaN 등 모델 연산 오류: 예외 이름을 code로 500
            if isinstance(value, (ApiError, UnknownModel)):
                raise value
            raise ApiError(500, f'{type(value).__name__}: {value}', 'server_error', type(value).__name__) from value
        await job.next()  # ('end', None): 락이 풀린 뒤 응답
        return value
    finally:
        job.cancel()


# ---- chat completions ----
def text_of(content):
    return content if isinstance(content, str) else ''.join(p.text for p in content)


def finish_reason(r):
    return 'length' if getattr(r, 'finish_reason', None) == 'length' else 'stop'


def usage_of(r):
    p, c = (getattr(r, 'prompt_tokens', 0) or 0), (getattr(r, 'generation_tokens', 0) or 0)
    return {'prompt_tokens': p, 'completion_tokens': c, 'total_tokens': p + c}


def timings_of(r):
    """확장 필드: 프리필·생성 속도와 MLX 최대 메모리 (요청 로그·측정용)"""
    return {
        'prompt_tps': getattr(r, 'prompt_tps', None),
        'generation_tps': getattr(r, 'generation_tps', None),
        'peak_memory_gb': getattr(r, 'peak_memory', None),
    }


def sse(obj):
    return f'data: {json.dumps(obj, ensure_ascii=False)}\n\n'


async def chat_completions(request: Request):
    req = await parse(request, ChatRequest)
    reg = ready(request)
    reg.entry(req.model, 'chat')  # 없는 모델은 생성 전에 404
    request.state.model = req.model
    messages = [{'role': m.role, 'content': text_of(m.content)} for m in req.messages]
    kw = {
        'max_tokens': req.max_completion_tokens or req.max_tokens or DEFAULT_MAX_TOKENS,
        'temperature': 0.0 if req.temperature is None else req.temperature,
        'top_p': req.top_p,
        'enable_thinking': bool(req.chat_template_kwargs and req.chat_template_kwargs.enable_thinking),
    }

    def work():
        try:
            model, _ = reg.get(req.model, 'chat')  # 처음 쓰는 모델은 여기서 불러옴 (LRU 교체)
        except Exception as e:
            raise ModelLoadError(str(e)) from e
        return model.stream(messages, **kw)

    job = Job(request.app.state.lock, work)
    try:
        kind, value = await job.next()  # 첫 토큰 전 오류(모델 불러오기 실패 등)는 HTTP 상태로 알린다
    except BaseException:
        job.cancel()
        raise
    if kind == 'error':
        job.cancel()
        status, body = exception_body(value)
        return JSONResponse(body, status_code=status)
    cid, created = f'chatcmpl-{uuid.uuid4().hex[:24]}', int(time.time())
    base = {'id': cid, 'created': created, 'model': req.model, 'system_fingerprint': None}

    if not req.stream:
        parts, last = [], None
        try:
            while kind == 'item':
                parts.append(value.text)
                last = value
                kind, value = await job.next()
        finally:
            job.cancel()
        if kind == 'error':
            status, body = exception_body(value)
            return JSONResponse(body, status_code=status)
        return JSONResponse(
            base
            | {
                'object': 'chat.completion',
                'choices': [
                    {
                        'index': 0,
                        'message': {'role': 'assistant', 'content': ''.join(parts)},
                        'finish_reason': finish_reason(last),
                        'logprobs': None,
                    }
                ],
                'usage': usage_of(last),
                'timings': timings_of(last),
            }
        )

    include_usage = bool(req.stream_options and req.stream_options.include_usage)

    def chunk(delta, finish=None, **extra):
        choice = {'index': 0, 'delta': delta, 'logprobs': None, 'finish_reason': finish}
        return base | {'object': 'chat.completion.chunk', 'choices': [choice]} | extra

    async def events():
        k, v, last, first = kind, value, None, True
        try:
            while k == 'item':
                delta = {'role': 'assistant', 'content': v.text} if first else {'content': v.text}
                first = False
                yield sse(chunk(delta))
                last = v
                k, v = await job.next()
            if k == 'error':  # 생성 중 오류: OpenAI SDK는 error 이벤트를 예외로 올림
                yield sse(exception_body(v)[1])
                print(f'[model-server] 생성 중 오류 {req.model}: {type(v).__name__}', flush=True)
                return
            yield sse(chunk({'role': 'assistant'} if first else {}, finish_reason(last), timings=timings_of(last)))
            if include_usage:
                yield sse(base | {'object': 'chat.completion.chunk', 'choices': [], 'usage': usage_of(last)})
            yield 'data: [DONE]\n\n'
        finally:
            job.cancel()  # 끊긴 경우: 다음 토큰에서 생성을 멈추고 락을 푼다

    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


# ---- embeddings ----
def encode_vector(row, fmt):
    if fmt == 'base64':
        return base64.b64encode(np.ascontiguousarray(row, dtype='<f4').tobytes()).decode('ascii')
    return row.astype(np.float32).tolist()


async def embeddings(request: Request):
    req = await parse(request, EmbeddingRequest)
    reg = ready(request)
    reg.entry(req.model, 'embedding')
    request.state.model = req.model
    texts = [req.input] if isinstance(req.input, str) else req.input
    v = await run_locked(
        request, lambda: reg.get(req.model, 'embedding')[0].encode(texts, req.input_type, req.max_seq_length)
    )
    return {
        'object': 'list',
        'model': req.model,
        'data': [
            {'object': 'embedding', 'index': i, 'embedding': encode_vector(row, req.encoding_format)}
            for i, row in enumerate(v)
        ],
        'usage': {'prompt_tokens': 0, 'total_tokens': 0},  # 토큰 수는 세지 않음
    }


# ---- rerank ----
async def rerank(request: Request):
    req = await parse(request, RerankRequest)
    reg = ready(request)
    reg.entry(req.model, 'rerank')
    request.state.model = req.model
    scores = await run_locked(
        request, lambda: reg.get(req.model, 'rerank')[0].score(req.query, req.documents, req.instruction)
    )
    order = sorted(range(len(scores)), key=lambda i: -scores[i])  # 점수 내림차순, 동점은 입력 순서
    results = [
        {'index': i, 'relevance_score': float(scores[i])}
        | ({'document': {'text': req.documents[i]}} if req.return_documents else {})
        for i in order[: req.top_n]
    ]
    return {'id': f'rerank-{uuid.uuid4().hex[:24]}', 'model': req.model, 'results': results}


# ---- 인증·크기 제한·로그 (ASGI 미들웨어: 스트림과 끊김을 그대로 전달) ----
class Guard:
    def __init__(self, app, token, max_body):
        self.app, self.token, self.max_body = app, f'Bearer {token}'.encode(), max_body

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        t0, status = time.perf_counter(), [None]
        scope.setdefault('state', {})

        async def send_status(message):
            if message['type'] == 'http.response.start':
                status[0] = message['status']
            await send(message)

        headers = dict(scope.get('headers') or [])
        length = headers.get(b'content-length', b'0')
        try:
            if not hmac.compare_digest(headers.get(b'authorization', b''), self.token):  # 토큰 값은 남기지 않음
                resp = error_response(
                    401, '모델 서버 토큰이 맞지 않습니다 (LAWBOT_MODEL_TOKEN)', 'authentication_error'
                )
            elif not length.isdigit() or int(length) > self.max_body:
                resp = error_response(413, f'요청 본문이 {self.max_body}바이트를 넘습니다')
            else:
                resp = None
            if resp is not None:
                await resp(scope, receive, send_status)
            else:
                await self.app(scope, receive, send_status)
        finally:
            if scope['path'] != '/health':
                model = scope['state'].get('model') or '-'
                print(
                    f'[model-server] {scope["method"]} {scope["path"]} {model} {status[0] or 499} '
                    f'{time.perf_counter() - t0:.2f}s',
                    flush=True,
                )


def create_app(token, registry=None, max_body=MAX_BODY):
    """registry가 None이면 /health가 ready:false이고 나머지는 503 (모델을 불러오는 중). 다 불러오면 app.state.registry에 넣는다"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry, app.state.lock, app.state.max_body = registry, threading.Lock(), max_body

    @app.exception_handler(ApiError)
    @app.exception_handler(UnknownModel)
    async def known_error(request, e):
        status, body = exception_body(e)
        return JSONResponse(body, status_code=status)

    @app.get('/health')
    async def health(request: Request):
        return {'ready': request.app.state.registry is not None}

    @app.get('/v1/models')
    async def models(request: Request):
        return {'object': 'list', 'data': ready(request).listing()}

    app.post('/v1/chat/completions')(chat_completions)
    app.post('/v1/embeddings')(embeddings)
    app.post('/v1/rerank')(rerank)
    app.add_middleware(Guard, token=token, max_body=max_body)
    return app
