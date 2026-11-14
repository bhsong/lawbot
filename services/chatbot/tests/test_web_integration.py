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

from admin_test_data import GOLD, add_feedback, seed_qa
from conftest import ADMIN_PW, ADMIN_USER, ENV_KEYS, free_port
from lawbot import view, web
from lawbot.db import migrate

pytestmark = pytest.mark.integration

USER, PW = 'tester', 'test-only-pw'  # 테스트 전용 가짜 값
SERVER = Path(__file__).with_name('web_server.py')
START_TIMEOUT = 60
DELAY = 0.2  # 가짜 모델 토큰 간격 (초): 조각 10개 → 생성 약 2초
WAIT_LIMIT = 15  # 기다리는 반복문의 상한 (초)


def server_log(path):  # 실패 진단용 서버 출력 (비밀번호는 가림)
    return path.read_text(encoding='utf-8', errors='replace').replace(PW, '***')


@contextmanager
def start_server(tmp, *, module_entry=False, **overrides):
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
    env.update(overrides)
    url = f'http://127.0.0.1:{port}'
    args = [sys.executable, str(SERVER), str(port), str(calls), str(DELAY)]
    if module_entry:
        args.append('--module')
    with out_path.open('w') as out:
        proc = subprocess.Popen(
            args,
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


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    with start_server(tmp_path_factory.mktemp('web_server')) as running:
        yield running


@contextmanager
def client(server):
    with httpx.Client(base_url=server.url, timeout=WAIT_LIMIT) as c:
        assert c.post('/api/login', json={'user': USER, 'password': PW}).status_code == 200
        yield c


def stream_events(c, question=view.EXAMPLES[0], fmt='full'):
    """채팅 스트림을 읽으며 (도착 시각, 이벤트, 데이터)를 차례로"""
    body = {'question': question, 'model': 'fast', 'format': fmt}
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
    assert not server.calls.exists() or 'start' not in server.calls.read_text()


def test_answer_arrives_in_pieces(server):
    with client(server) as c:
        got = list(stream_events(c))
    events = [e for _, e, _ in got]
    assert events[-1] == 'done' and '예고해야 합니다' in got[-1][2]['text']
    assert all(d['draft'] is True for _, e, d in got if e == 'answer')
    first_answer = next(t for t, e, _ in got if e == 'answer')
    assert got[-1][0] - first_answer > DELAY  # 끝나기 전에 첫 조각이 도착함 (모아서 한 번에 보내지 않음)
    row = log_rows(server)[-1]
    assert row['status'] == 'ok' and row['answer_verdict'] == 'shown'


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


@pytest.mark.parametrize('fmt', ['full', 'concise'])
def test_disconnect_marks_request_cancelled(server, fmt):
    """간결형은 결론 뒤 토큰이 화면 글을 바꾸지 않아 보낼 조각이 없다: 그 사이에도 끊김을 알아채야 한다"""
    before = len(log_rows(server))
    with client(server) as c:
        for _, event, data in stream_events(c, fmt=fmt):
            if event == 'answer' and '합니다' in data['text']:
                break  # 결론이 다 보인 뒤 끊는다 (화면의 중지 버튼). 간결형은 남은 토큰이 화면 글을 바꾸지 않음
    deadline = time.monotonic() + WAIT_LIMIT
    while len(log_rows(server)) == before:
        assert time.monotonic() < deadline, f'요청 로그가 남지 않음\n{server_log(server.out)}'
        time.sleep(0.1)
    assert log_rows(server)[-1]['status'] == 'cancelled'


@pytest.mark.parametrize('module_entry', [False, True], ids=['imported-main', 'module-main'])
def test_admin_review_and_promotion_in_server_process(tmp_path, admin_hash, module_entry):
    db = tmp_path / 'private' / 'lawbot.db'
    migrate.init(db)
    seed_qa(db)
    add_feedback(db, 'fixture')
    with (
        start_server(
            tmp_path,
            module_entry=module_entry,
            LAWBOT_DB=str(db),
            LAWBOT_ADMIN_USER=ADMIN_USER,
            LAWBOT_ADMIN_PW_HASH=admin_hash,
        ) as running,
        httpx.Client(base_url=running.url, timeout=WAIT_LIMIT) as session,
    ):
        assert session.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW}).status_code == 200
        calendar = session.get('/api/admin/calendar/law-calendar/status')
        assert (calendar.status_code, calendar.json().get('code')) == (503, 'calendar_not_configured')
        prefix = '/api/admin/feedbacks'
        assert session.get(prefix).json()['items'][0]['feedback_id'] == 'fixture'
        origin = {'Origin': running.url}
        review = session.post(
            f'{prefix}/fixture/review',
            headers=origin,
            json={
                'idempotency_key': 'review',
                'verdict': 'correct',
                'decision': 'approved',
                'gold_articles': GOLD,
            },
        )
        assert (review.status_code, review.json()['status']) == (201, 'reviewed')
        result = session.post(
            f'{prefix}/promote-up',
            headers=origin,
            json={
                'idempotency_key': 'promotion',
                'feedback_ids': ['fixture'],
            },
        )
        assert result.status_code == 200 and result.json()['results'][0]['qa_uid'] == 'feedback:FB-fixture-r1'
        assert session.get(prefix, params={'status': 'promoted'}).json()['items'][0]['feedback_id'] == 'fixture'
