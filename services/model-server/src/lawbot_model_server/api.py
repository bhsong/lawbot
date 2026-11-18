# OpenAI-compatible embeddings and Cohere/Jina-compatible reranking.
# Every endpoint requires Bearer authentication; inference uses one GPU worker.
import asyncio
import base64
import hmac
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from lawbot_common import api as contract
from lawbot_common.logs import logger
from lawbot_model_server.registry import UnknownModel

log = logger('lawbot_model_server', 'model-server')

MAX_BODY = 8 * 1024 * 1024  # 요청 본문 최대 (조문 415개 인코딩, 질문 2,075개 rank가 들어감)
REQUEST_ID = re.compile(r'[A-Za-z0-9-]{1,32}')  # X-Request-Id 헤더 (웹앱 service.ask의 request_id)
# 요청·응답 형식과 오류 type은 웹앱과 같이 쓰는 계약 (lawbot_common.api)
EmbeddingRequest, RerankRequest = contract.EmbeddingRequest, contract.RerankRequest


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
    """Serialize embedding/rerank inference without blocking the event loop."""
    try:
        return await asyncio.get_running_loop().run_in_executor(request.app.state.worker, fn)
    except ApiError, UnknownModel:
        raise
    except Exception as e:
        raise ApiError(500, f'{type(e).__name__}: {e}', contract.SERVER_ERROR, type(e).__name__) from e


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
        request, lambda: reg.get(req.model, 'embedding').encode(texts, req.input_type, req.max_seq_length)
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
        request, lambda: reg.get(req.model, 'rerank').score(req.query, req.documents, req.instruction)
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


# ---- Authentication, receive-time body limits, and logging ----
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
                received = 0

                async def receive_limited():
                    nonlocal received
                    message = await receive()
                    if message['type'] == 'http.request':
                        received += len(message.get('body', b''))
                        if received > self.max_body:
                            raise ApiError(413, f'요청 본문이 {self.max_body}바이트를 넘습니다')
                    return message

                await self.app(scope, receive_limited, send_status)
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
    """Publish the registry only after all resident models have loaded."""
    app.state.registry = registry


def create_app(token, registry=None, max_body=MAX_BODY, commit=None):
    """Serve readiness while models load; publish the registry with set_registry."""

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            app.state.worker.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.registry = None
    app.state.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='model-inference')
    app.state.commit = commit
    if registry is not None:
        set_registry(app, registry)

    @app.exception_handler(ApiError)
    @app.exception_handler(UnknownModel)
    async def known_error(request, e):
        status, body = exception_body(e)
        return JSONResponse(body, status_code=status)

    @app.get(contract.HEALTH_PATH)
    async def health(request: Request):
        state = request.app.state
        return contract.HealthResponse(ready=state.registry is not None, commit=state.commit).model_dump()

    @app.get(contract.MODELS_PATH)
    async def models(request: Request):
        return contract.ModelsResponse(data=ready(request).listing()).model_dump()

    app.post(contract.EMBEDDINGS_PATH)(embeddings)
    app.post(contract.RERANK_PATH)(rerank)
    app.add_middleware(Guard, token=token, max_body=max_body)
    return app
