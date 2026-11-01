# tests/test_auth_integration.py  →  실제 Gradio 서버를 띄워 로그인 필수가 지켜지는지 검사
# 서버는 별도 프로세스(integration_server.py, 가짜 모델)로 127.0.0.1의 빈 포트에 띄우고 끝나면 정리한다
# 접속은 127.0.0.1만 허용 (conftest.py의 integration 마커 처리)
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

from conftest import ENV_KEYS, free_port
from lawbot.app import EXAMPLES

pytestmark = pytest.mark.integration

USER, PW = 'tester', 'test-only-pw'  # 테스트 전용 가짜 값
QUESTION = EXAMPLES[0]  # 로그인 후 챗봇 화면에만 나오는 예시 질문
SERVER = Path(__file__).with_name('integration_server.py')
START_TIMEOUT = 60


def server_log(path):  # 실패 진단용 서버 출력 (비밀번호는 가림)
    return path.read_text(encoding='utf-8', errors='replace').replace(PW, '***')


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('auth_server')
    port, calls, log = free_port(), tmp / 'chat_calls.txt', tmp / 'server.log'
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(
        LAWBOT_USER=USER,
        LAWBOT_PW=PW,
        LAWBOT_ENV_FILE='',
        GRADIO_ANALYTICS_ENABLED='False',
        LAWBOT_REQUEST_LOG='',
    )  # module 범위라 conftest의 env보다 먼저 실행됨: 레포 logs/에 쓰지 않게 직접 끔
    url = f'http://127.0.0.1:{port}'
    with log.open('w') as out:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), str(port), str(calls)],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            if proc.poll() is not None:
                pytest.fail(f'테스트 서버가 종료됨 (code {proc.returncode})\n{server_log(log)}')
            try:
                if httpx.get(url + '/', timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f'테스트 서버가 {START_TIMEOUT}초 안에 뜨지 않음\n{server_log(log)}')
            time.sleep(0.2)
        yield types.SimpleNamespace(url=url, calls=calls)
    finally:  # 테스트 실패·중단 시에도 서버를 남기지 않음
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture
def client(server):  # 테스트마다 쿠키가 비어 있는 새 클라이언트
    with httpx.Client(base_url=server.url, timeout=10) as c:
        yield c


def chat_calls(server):
    return server.calls.read_text(encoding='utf-8').splitlines() if server.calls.exists() else []


def login(c, user, pw):
    return c.post('/login', data={'username': user, 'password': pw})


def ask(c, question, model='fast', fmt='full'):
    """chat API 호출. 성공하면 마지막 출력 (답변, 대화창, 근거 조문, 측정)을, 실패하면 응답 객체를 돌려준다
    입력은 [질문, 모델 key, 답변 형식] (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)"""
    r = c.post('/gradio_api/call/chat', json={'data': [question, model, fmt]})
    if r.status_code != 200:
        return r
    events = c.get(f'/gradio_api/call/chat/{r.json()["event_id"]}').text
    data = [line.removeprefix('data: ') for line in events.splitlines() if line.startswith('data: ')]
    return json.loads(data[-1])


# 로그인 없이 쓰면 챗봇 설정·기능을 받을 수 있는 경로
PROTECTED_GET = ['/config', '/gradio_api/info', '/gradio_api/queue/status', '/gradio_api/file=pyproject.toml']
PROTECTED_POST = ['/gradio_api/call/chat', '/gradio_api/run/chat', '/gradio_api/queue/join']


def assert_locked_out(c, server):
    before = len(chat_calls(server))
    for path in PROTECTED_GET:
        assert c.get(path).status_code == 401, path
    for path in PROTECTED_POST:
        assert c.post(path, json={'data': [QUESTION, 'fast', 'full'], 'fn_index': 0}).status_code == 401, path
    assert len(chat_calls(server)) == before  # chat 함수가 실행되지 않았음


# A. 로그인 없이 접근하면 차단
def test_root_shows_login_page_only(client):
    r = client.get('/')
    assert r.status_code == 200
    assert '"auth_required":true' in r.text
    assert QUESTION not in r.text  # 챗봇 화면(예시 질문)은 내려주지 않음


def test_unauthenticated_requests_are_rejected(client, server):
    assert_locked_out(client, server)


# B. 잘못된 계정·비밀번호는 거부
@pytest.mark.parametrize(
    'user,pw',
    [
        (USER, 'wrong-pw'),
        ('nobody', PW),
        ('nobody', 'wrong-pw'),
        (USER, ''),
        (USER.upper(), PW),
    ],
    ids=['wrong-pw', 'unknown-user', 'both-wrong', 'empty-pw', 'user-case'],
)
def test_bad_credentials_rejected(client, server, user, pw):
    r = login(client, user, pw)
    assert r.status_code in (400, 422)  # 400 틀린 계정, 422 빈 값 (폼 검증에서 거부)
    assert not any(k.startswith('access-token') for k in client.cookies)
    assert_locked_out(client, server)


# C. 올바른 계정은 챗봇 사용 가능
def test_valid_login_can_use_chatbot(client, server):
    r = login(client, USER, PW)
    assert r.status_code == 200 and r.json() == {'success': True}
    assert any(k.startswith('access-token') for k in client.cookies)

    page = client.get('/')
    assert page.status_code == 200 and QUESTION in page.text  # 챗봇 화면
    cfg = client.get('/config')
    assert cfg.status_code == 200 and cfg.json()['title'] == '노동법 챗봇'

    before = len(chat_calls(server))
    answer, _, sources, stats = ask(client, QUESTION)
    assert answer == '【결론】예고해야 합니다.'
    assert '제26조 해고의 예고' in sources
    assert stats.startswith('Lawbot Fast')
    answer, _, _, stats = ask(client, QUESTION, 'pro', 'concise')  # 같은 로그인으로 모델·형식 선택
    assert answer.startswith('예고해야 합니다.') and stats.startswith('Lawbot Pro')
    assert chat_calls(server)[before:] == [QUESTION, QUESTION]
