# tests/conftest.py  →  모든 테스트 공통 설정
# 기본 테스트는 실제 모델·네트워크·비밀번호 없이 돈다. 실제 모델 테스트는 test_models.py (-m models)
import socket
import pytest

ENV_KEYS = ('LAWBOT_USER', 'LAWBOT_PW', 'LLM_PATH',
            'LAWBOT_PROFILE', 'LAWBOT_EMB', 'LAWBOT_FAST_PATH', 'LAWBOT_PRO_PATH', 'LAWBOT_PRO_SYSTEM', 'LAWBOT_MAX_RESIDENT',
            'LAWBOT_EARLY_STOP', 'LAWBOT_V4_ARTICLES', 'LAWBOT_V4_EMB', 'LAWBOT_V4_SYSTEM',
            'GRADIO_SERVER_NAME', 'GRADIO_SERVER_PORT', 'GRADIO_SHARE')

@pytest.fixture(autouse=True)
def env(request, monkeypatch):
    """개발자 셸의 인증·Gradio 환경 변수를 지우고, 외부 네트워크 접속을 막는다
    (integration 테스트는 127.0.0.1의 테스트 서버 접속만 허용)"""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('LAWBOT_PROFILE', 'v1')   # 가짜 모델·조문(fakes.py)은 v1 형식. v4·기본값 테스트는 직접 바꿈
    if request.node.get_closest_marker('models') is None:
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

def set_auth(env, user='tester', pw='test-only-pw'):   # 테스트 전용 가짜 값
    env.setenv('LAWBOT_USER', user)
    env.setenv('LAWBOT_PW', pw)
