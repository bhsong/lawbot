# services/model-server/tests/conftest.py  →  모델 서버 테스트 공통 설정 (모델 파일·네트워크 불필요)
import socket

import pytest

from .ms_fakes import reset_fakes

ENV_KEYS = (
    'LAWBOT_MODEL_SERVER_PORT',
    'LAWBOT_MODEL_TOKEN',
    'LAWBOT_SERVE_EMBEDDING',
    'LAWBOT_SERVE_RERANK',
    'LAWBOT_LLM_BASE_URL',
    'LAWBOT_LLM_API_KEY',
    'LAWBOT_LLM_ALLOW_EXTERNAL',
    'LAWBOT_PRO_MODEL',
    'HTTP_PROXY',
    'HTTPS_PROXY',
    'ALL_PROXY',
    'http_proxy',
    'https_proxy',
    'all_proxy',
)


@pytest.fixture(autouse=True)
def ms_env(request, monkeypatch):
    """Isolate settings and fake state; only explicit models tests may use external networking."""
    reset_fakes()
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('LAWBOT_ENV_FILE', '')
    if request.node.get_closest_marker('models') is not None:
        return monkeypatch
    local_ok = request.node.get_closest_marker('integration') is not None
    connect, create_connection = socket.socket.connect, socket.create_connection

    def check(address):
        if not (local_ok and isinstance(address, tuple) and address[0] == '127.0.0.1'):
            raise AssertionError(f'테스트 중 외부 네트워크 접속 시도: {address!r}')

    def guarded_connect(sock, address):
        check(address)
        return connect(sock, address)

    def guarded_create_connection(address, *a, **kw):
        check(address)
        return create_connection(address, *a, **kw)

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket, 'create_connection', guarded_create_connection)
    return monkeypatch
