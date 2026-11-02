# tests/test_web_integration.py  →  새 화면 API를 실제 프로세스(web_server.py, 가짜 모델)로 띄워 HTTP로 검사
# 로그인 보호 · 스트림이 끝나기 전에 조각이 도착 · 동시 처리 1이면 둘째 질문은 기다림 · 연결을 끊으면 요청 로그에 cancelled
# 접속은 127.0.0.1만 (conftest.py의 integration 마커 처리)
import json
import os
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from conftest import ENV_KEYS, free_port
from lawbot import view, web

pytestmark = pytest.mark.integration

USER, PW = 'tester', 'test-only-pw'  # 테스트 전용 가짜 값
SERVER = Path(__file__).with_name('web_server.py')
START_TIMEOUT = 60
DELAY = 0.3  # 가짜 모델 토큰 간격 (초): 조각 4개 → 생성 약 1.2초
WAIT_LIMIT = 15  # 기다리는 반복문의 상한 (초)


def server_log(path):  # 실패 진단용 서버 출력 (비밀번호는 가림)
    return path.read_text(encoding='utf-8', errors='replace').replace(PW, '***')


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('web_server')
    port, calls, out_path = free_port(), tmp / 'llm_calls.txt', tmp / 'server.log'
    request_log = tmp / 'requests.jsonl'
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(
        LAWBOT_USER=USER,
        LAWBOT_PW=PW,
        LAWBOT_ENV_FILE='',
        LAWBOT_REQUEST_LOG=str(request_log),
        LAWBOT_CHAT_CONCURRENCY='1',
        LAWBOT_EMBED_CACHE='0',
    )
    url = f'http://127.0.0.1:{port}'
    with out_path.open('w') as out:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), str(port), str(calls), str(DELAY)],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            if proc.poll() is not None:
                pytest.fail(f'테스트 서버가 종료됨 (code {proc.returncode})\n{server_log(out_path)}')
            try:
                if httpx.get(url + '/api/me', timeout=1).status_code == 401:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f'테스트 서버가 {START_TIMEOUT}초 안에 뜨지 않음\n{server_log(out_path)}')
            time.sleep(0.2)
        yield types.SimpleNamespace(url=url, calls=calls, request_log=request_log, out=out_path)
    finally:  # 테스트 실패·중단 시에도 서버를 남기지 않음
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextmanager
def client(server):
    with httpx.Client(base_url=server.url, timeout=WAIT_LIMIT) as c:
        assert c.post('/api/login', json={'user': USER, 'password': PW}).status_code == 200
        yield c


def stream_events(c, question=view.EXAMPLES[0]):
    """채팅 스트림을 읽으며 (도착 시각, 이벤트, 데이터)를 차례로"""
    body = {'question': question, 'model': 'fast', 'format': 'full'}
    with c.stream('POST', '/api/chat', json=body) as r:
        assert r.status_code == 200
        event = None
        for line in r.iter_lines():
            if line.startswith('event: '):
                event = line.removeprefix('event: ')
            elif line.startswith('data: '):
                yield time.monotonic(), event, json.loads(line.removeprefix('data: '))


def log_rows(server):
    p = server.request_log
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def test_login_required_over_http(server):
    with httpx.Client(base_url=server.url, timeout=10) as c:
        assert c.get('/api/config').status_code == 401
        r = c.post('/api/chat', json={'question': 'x', 'model': 'fast', 'format': 'full'})
        assert r.status_code == 401
        assert c.post('/api/login', json={'user': USER, 'password': 'wrong'}).status_code == 401
        page = c.get('/')
        assert page.status_code == 404 and 'npm run build' in page.text  # PR 1: 화면 빌드 없음
    assert not server.calls.exists() or 'start' not in server.calls.read_text()


def test_answer_arrives_in_pieces(server):
    with client(server) as c:
        got = list(stream_events(c))
    events = [e for _, e, _ in got]
    assert events[-1] == 'done' and '예고해야 합니다' in got[-1][2]['text']
    first_answer = next(t for t, e, _ in got if e == 'answer')
    assert got[-1][0] - first_answer > DELAY  # 끝나기 전에 첫 조각이 도착함 (모아서 한 번에 보내지 않음)
    assert log_rows(server)[-1]['status'] == 'ok'


def test_second_question_waits_for_first(server):
    results = {}

    def run(name):
        with client(server) as c:
            results[name] = [(e, d) for _, e, d in stream_events(c)]

    first = threading.Thread(target=run, args=('first',))
    first.start()
    deadline = time.monotonic() + WAIT_LIMIT
    while not (server.calls.exists() and server.calls.read_text().splitlines()[-1].startswith('start')):
        assert time.monotonic() < deadline, '첫 질문이 생성을 시작하지 않음'
        time.sleep(0.05)
    run('second')
    first.join(WAIT_LIMIT)
    assert results['second'][0] == ('status', {'text': web.WAITING})
    assert results['first'][-1][0] == 'done' and results['second'][-1][0] == 'done'
    marks = [line.split() for line in server.calls.read_text().splitlines()]
    kinds, times = [k for k, _ in marks], [float(t) for _, t in marks]
    assert kinds == ['start', 'end'] * (len(kinds) // 2) and times == sorted(times)  # 생성이 겹치지 않음


def test_disconnect_marks_request_cancelled(server):
    before = len(log_rows(server))
    with client(server) as c:
        for _, event, _ in stream_events(c):
            if event == 'answer':
                break  # 첫 조각을 받자마자 연결을 끊는다 (화면의 중지 버튼)
    deadline = time.monotonic() + WAIT_LIMIT
    while len(log_rows(server)) == before:
        assert time.monotonic() < deadline, f'요청 로그가 남지 않음\n{server_log(server.out)}'
        time.sleep(0.1)
    assert log_rows(server)[-1]['status'] == 'cancelled'
