"""Loopback-only administration. The public calendar never mounts these routes."""

from hmac import compare_digest
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from source_hub.config import Target
from source_hub.store import Conflict, Registry, UnknownTarget

WEB = Path(__file__).with_name('web')


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    changes: dict


def create_app(registry: Registry, username: str, password: str, *, host: str = '127.0.0.1') -> FastAPI:
    if not username or not password:
        raise ValueError('관리자 사용자와 암호를 설정하세요')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host])
    basic = HTTPBasic(realm='Lawbot Collector')

    def actor(credentials: Annotated[HTTPBasicCredentials, Depends(basic)]) -> str:
        valid_user = compare_digest(credentials.username.encode(), username.encode())
        valid_password = compare_digest(credentials.password.encode(), password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(
                401, '인증이 필요합니다', headers={'WWW-Authenticate': 'Basic realm="Lawbot Collector"'}
            )
        return username

    def writer(request: Request, user: Annotated[str, Depends(actor)]) -> str:
        if request.headers.get('origin') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '관리자 화면에서 설정을 변경하세요')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'JSON 요청이 필요합니다')
        return user

    @app.middleware('http')
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        return Response('요청 형식을 확인하세요', status_code=422, media_type='text/plain')

    @app.get('/', dependencies=[Depends(actor)], response_class=HTMLResponse)
    def index():
        return (WEB / 'index.html').read_text(encoding='utf-8')

    @app.get('/admin.js', dependencies=[Depends(actor)])
    def script():
        return Response((WEB / 'admin.js').read_bytes(), media_type='text/javascript')

    @app.get('/admin.css', dependencies=[Depends(actor)])
    def stylesheet():
        return Response((WEB / 'admin.css').read_bytes(), media_type='text/css')

    @app.get('/api/collector/sources', dependencies=[Depends(actor)])
    def sources():
        return registry.snapshot()

    @app.get('/api/collector/audit', dependencies=[Depends(actor)])
    def audit():
        return registry.history()

    @app.get('/api/collector/sources/{target}/runs', dependencies=[Depends(actor)])
    def runs(target: str):
        return registry.runs(target)

    def change(kind: Target, target: str, payload: Change, user: str, *, create: bool = False):
        try:
            version = registry.patch(
                kind, target, payload.changes, actor=user, reason=payload.reason, version=payload.version, create=create
            )
        except UnknownTarget as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(422, '수집원 설정 값과 공식 URL을 확인하세요') from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'config_version': version}

    @app.patch('/api/collector/sources/{target}')
    def edit_source(target: str, payload: Change, user: Annotated[str, Depends(writer)]):
        return change('source', target, payload, user)

    @app.post('/api/collector/sources/{target}', status_code=201)
    def add_source(target: str, payload: Change, user: Annotated[str, Depends(writer)]):
        return change('source', target, payload, user, create=True)

    @app.patch('/api/collector/settings/{kind}/{target:path}')
    def edit_setting(kind: Target, target: str, payload: Change, user: Annotated[str, Depends(writer)]):
        return change(kind, target, payload, user)

    return app
