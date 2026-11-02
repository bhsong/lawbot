# lawbot_model_server/api.py  →  OpenAI 호환 HTTP API (FastAPI)
#   POST /v1/chat/completions  (stream: SSE)   POST /v1/embeddings   POST /v1/rerank (Cohere/Jina 형식)
#   GET  /v1/models            GET /health (모델을 불러오는 중에도 응답)
# 모든 요청에 Bearer 토큰이 필요하다 (/health 포함). 질문·문서 내용은 로그에 남기지 않는다
# 모델 연산은 GPU 스레드 하나가 맡는다 (scheduler.py). chat은 LAWBOT_MAX_CONCURRENT개까지 동시에 생성하고,
# embeddings·rerank는 생성 스텝 사이에 실행한다. 스트림 중 클라이언트가 끊으면 다음 스텝에서 그 요청을 뺀다
# 확장 필드: chat_template_kwargs.enable_thinking, 응답의 timings / embeddings의 input_type·max_seq_length / rerank의 instruction
import base64
import hmac
import json
import re
import time
import uuid

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from lawbot_common import api as contract
from lawbot_common.logs import logger
from lawbot_model_server.registry import UnknownModel
from lawbot_model_server.scheduler import MAX_QUEUED_DEFAULT, ModelLoadError, QueueFull, Scheduler

log = logger('lawbot_model_server', 'model-server')

MAX_BODY = 8 * 1024 * 1024  # 요청 본문 최대 (조문 415개 인코딩, 질문 2,075개 rank가 들어감)
REQUEST_ID = re.compile(r'[A-Za-z0-9-]{1,32}')  # X-Request-Id 헤더 (웹앱 service.ask의 request_id)
DEFAULT_MAX_TOKENS = 1024  # max_tokens가 없을 때 (챗봇은 항상 1024를 보냄)
# 요청·응답 형식과 오류 type은 웹앱과 같이 쓰는 계약 (lawbot_common.api)
ChatRequest, EmbeddingRequest, RerankRequest = contract.ChatRequest, contract.EmbeddingRequest, contract.RerankRequest


# ---- 오류 ----
class ApiError(Exception):
    def __init__(self, status, message, kind=contract.INVALID_REQUEST, code=None):
        super().__init__(message)
        self.status, self.message, self.kind, self.code = status, message, kind, code


error_body = contract.error_body


def error_response(status, message, kind=contract.INVALID_REQUEST, code=None):
    return JSONResponse(error_body(message, kind, code), status_code=status)


def exception_body(e):
    """(HTTP 상태, 오류 본문). 모델 연산 오류는 예외 이름을 code로 (웹앱이 유형을 안내)"""
    if isinstance(e, ApiError):
        return e.status, error_body(e.message, e.kind, e.code)
    if isinstance(e, UnknownModel):
        return 404, error_body(str(e), contract.INVALID_REQUEST, contract.MODEL_NOT_FOUND)
    if isinstance(e, ModelLoadError):
        cause = e.__cause__
        return 500, error_body(f'모델을 불러오지 못했습니다: {cause}', contract.MODEL_LOAD, type(cause).__name__)
    return 500, error_body(f'{type(e).__name__}: {e}', contract.SERVER_ERROR, type(e).__name__)


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
        raise ApiError(503, '모델을 불러오는 중입니다', contract.SERVER_NOT_READY)
    return reg


async def run_call(request, fn):
    """embeddings·rerank처럼 한 번에 끝나는 연산 (GPU 스레드에서 생성 스텝 사이에 실행)"""
    t = request.app.state.scheduler.call(fn)
    try:
        kind, value = await t.next()
        if kind == 'error':  # 리랭커 점수 NaN 등 모델 연산 오류: 예외 이름을 code로 500
            if isinstance(value, (ApiError, UnknownModel)):
                raise value
            raise ApiError(
                500, f'{type(value).__name__}: {value}', contract.SERVER_ERROR, type(value).__name__
            ) from value
        await t.next()  # ('end', None)
        return value
    finally:
        t.cancel()


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

    try:
        job = request.app.state.scheduler.chat(
            req.model, messages, kw
        )  # 처음 쓰는 모델은 GPU 스레드가 불러옴 (LRU 교체)
    except QueueFull as e:
        raise ApiError(503, str(e), contract.SERVER_BUSY) from None
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
                log.warning(f'생성 중 오류 {req.model}: {type(v).__name__}')
                return
            yield sse(chunk({'role': 'assistant'} if first else {}, finish_reason(last), timings=timings_of(last)))
            if include_usage:
                yield sse(base | {'object': 'chat.completion.chunk', 'choices': [], 'usage': usage_of(last)})
            yield 'data: [DONE]\n\n'
        finally:
            job.cancel()  # 끊긴 경우: 다음 스텝에서 생성을 멈추고 자리를 비운다

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
    v = await run_call(
        request, lambda: reg.get(req.model, 'embedding')[0].encode(texts, req.input_type, req.max_seq_length)
    )
    return contract.EmbeddingResponse(
        model=req.model,
        data=[
            contract.EmbeddingItem(index=i, embedding=encode_vector(row, req.encoding_format))
            for i, row in enumerate(v)
        ],
        usage=contract.Usage(),  # 토큰 수는 세지 않음
    ).model_dump()


# ---- rerank ----
async def rerank(request: Request):
    req = await parse(request, RerankRequest)
    reg = ready(request)
    reg.entry(req.model, 'rerank')
    request.state.model = req.model
    scores = await run_call(
        request, lambda: reg.get(req.model, 'rerank')[0].score(req.query, req.documents, req.instruction)
    )
    order = sorted(range(len(scores)), key=lambda i: -scores[i])  # 점수 내림차순, 동점은 입력 순서
    results = [
        contract.RerankResult(
            index=i,
            relevance_score=float(scores[i]),
            document=contract.RerankDocument(text=req.documents[i]) if req.return_documents else None,
        )
        for i in order[: req.top_n]
    ]
    res = contract.RerankResponse(id=f'rerank-{uuid.uuid4().hex[:24]}', model=req.model, results=results)
    return res.model_dump(exclude_none=True)  # document는 return_documents일 때만


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
                    401, '모델 서버 토큰이 맞지 않습니다 (LAWBOT_MODEL_TOKEN)', contract.AUTHENTICATION
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
                rid = headers.get(b'x-request-id', b'').decode('ascii', 'replace')
                rid = (
                    rid if REQUEST_ID.fullmatch(rid) else '-'
                )  # 웹앱 요청 id (영숫자·-만 32자까지, 아니면 남기지 않음)
                log.info(
                    f'{scope["method"]} {scope["path"]} {model} {status[0] or 499} '
                    f'{time.perf_counter() - t0:.2f}s rid={rid}'
                )


def set_registry(app, registry):
    """모델을 다 불러온 뒤: 레지스트리와 GPU 스레드 스케줄러를 붙인다 (이때부터 ready)"""
    app.state.scheduler = Scheduler(registry, app.state.max_concurrent, max_queued=app.state.max_queued)
    app.state.registry = registry


def create_app(token, registry=None, max_body=MAX_BODY, max_concurrent=1, max_queued=MAX_QUEUED_DEFAULT):
    """registry가 None이면 /health가 ready:false이고 나머지는 503 (모델을 불러오는 중). 다 불러오면 set_registry"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry, app.state.scheduler = None, None
    app.state.max_body, app.state.max_concurrent, app.state.max_queued = max_body, max_concurrent, max_queued
    if registry is not None:
        set_registry(app, registry)

    @app.exception_handler(ApiError)
    @app.exception_handler(UnknownModel)
    async def known_error(request, e):
        status, body = exception_body(e)
        return JSONResponse(body, status_code=status)

    @app.get(contract.HEALTH_PATH)
    async def health(request: Request):
        return contract.HealthResponse(ready=request.app.state.registry is not None).model_dump()

    @app.get(contract.MODELS_PATH)
    async def models(request: Request):
        return contract.ModelsResponse(data=ready(request).listing()).model_dump()

    app.post(contract.CHAT_PATH)(chat_completions)
    app.post(contract.EMBEDDINGS_PATH)(embeddings)
    app.post(contract.RERANK_PATH)(rerank)
    app.add_middleware(Guard, token=token, max_body=max_body)
    return app
