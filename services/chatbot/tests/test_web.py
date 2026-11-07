# tests/test_web.py  →  새 화면 API (lawbot.web): 로그인·설정·채팅 SSE·피드백·정적 파일 (가짜 모델, 같은 프로세스 TestClient)
# 실제 프로세스로 띄우는 검사(대기열·연결 끊김)는 test_web_integration.py
import asyncio
import json
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import lawbot.service as service
from conftest import ADMIN_PW, ADMIN_USER, set_auth
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot import feedback, feedback_store, settings, view, web
from lawbot.api_client import ModelServerTimeout
from lawbot.db import connection, migrate
from lawbot.qa_rules import latest_rows

USER, PW = 'tester', 'test-only-pw'  # 테스트 전용 가짜 값
QUESTION = view.EXAMPLES[0]
CHUNKS = ['【결론】\n예고해야', ' 합니다.\n\n【근거 조문】\n근로기준법 제26조\n\n【설명】\n- 설명']


@pytest.fixture
def lawbot(env):
    env.setattr(FakeLLM, 'chunks', CHUNKS)
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(web, 'LOGIN_FAIL_DELAY', 0)
    return service.load_app()


def make_client(lawbot, **kw):
    return TestClient(web.create_app(lawbot, (USER, PW), **kw))


@pytest.fixture
def client(lawbot):
    with make_client(lawbot) as c:
        yield c


def login(c, user=USER, pw=PW):
    return c.post('/api/login', json={'user': user, 'password': pw})


@pytest.fixture
def authed(client):
    assert login(client).status_code == 200
    return client


def events(r):
    """SSE 응답 → [(이벤트, 데이터)]"""
    assert r.status_code == 200, r.text
    assert r.headers['content-type'].startswith('text/event-stream')
    out = []
    for block in r.text.split('\n\n'):
        lines = dict(line.split(': ', 1) for line in block.splitlines() if line)
        if lines:
            out.append((lines['event'], json.loads(lines['data'])))
    return out


def chat(c, question=QUESTION, model='fast', fmt='full'):
    return c.post('/api/chat', json={'question': question, 'model': model, 'format': fmt})


def names(evs):
    return [e for e, _ in evs]


# ---- 로그인 ----
PROTECTED = [
    ('GET', '/api/me'),
    ('GET', '/api/config'),
    ('POST', '/api/chat'),
    ('POST', '/api/feedback'),
    ('POST', '/api/logout'),
]


def test_api_requires_login(client, env):
    calls = []
    env.setattr(web, 'ask', lambda *a, **kw: calls.append(a) or iter(()))
    for method, path in PROTECTED:
        r = client.request(method, path, json={'question': QUESTION, 'model': 'fast', 'format': 'full'})
        assert r.status_code == 401, (method, path)
        assert '노동법' not in r.text and 'fast' not in r.text  # 설정·모델 정보가 나가지 않음
    assert calls == []


@pytest.mark.parametrize(
    'body',
    [
        {'user': USER, 'password': 'wrong'},
        {'user': 'other', 'password': PW},
        {'user': USER},
        {'user': USER, 'password': 1},
        [],
    ],
)
def test_bad_login_rejected(client, body):
    r = client.post('/api/login', json=body)
    assert r.status_code == 401
    assert 'set-cookie' not in r.headers
    assert client.get('/api/me').status_code == 401


def test_login_sets_private_cookie_and_logout(client):
    r = login(client)
    assert r.status_code == 200 and r.json() == {'user': USER}
    cookie = r.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/' in cookie
    assert PW not in r.headers['set-cookie']
    assert client.get('/api/me').json() == {'user': USER}
    assert client.post('/api/logout', json={}).status_code == 200
    assert client.get('/api/me').status_code == 401


def test_cookie_secure_only_over_https(lawbot):
    """Tailscale 등 https 프록시(X-Forwarded-Proto)를 거치면 Secure. 127.0.0.1의 http에서는 빼야 쿠키가 저장된다"""
    app = web.create_app(lawbot, (USER, PW))
    with TestClient(app, base_url='https://testserver') as c:
        assert 'secure' in login(c).headers['set-cookie'].lower()
    with TestClient(app) as c:
        assert 'secure' not in login(c).headers['set-cookie'].lower()


def test_post_must_be_json(client):
    r = client.post('/api/login', data={'user': USER, 'password': PW})
    assert r.status_code == 415
    login(client)
    r = client.post('/api/chat', content='question=x', headers={'content-type': 'text/plain'})
    assert r.status_code == 415


def test_session_cookie_from_other_app_is_rejected(lawbot):
    """서버를 다시 띄우면 세션이 비어 다시 로그인해야 한다 (메모리 세션)"""
    with make_client(lawbot) as a, make_client(lawbot) as b:
        login(a)
        b.cookies.update(a.cookies)
        assert b.get('/api/me').status_code == 401


@pytest.fixture
def admin_app(lawbot, env, tmp_path, admin_hash):
    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    set_auth(env, USER, PW)
    env.setenv('LAWBOT_DB', str(path))
    env.setenv('LAWBOT_ADMIN_USER', ADMIN_USER)
    env.setenv('LAWBOT_ADMIN_PW_HASH', admin_hash)
    env.setattr(web, 'load_app', lambda _: lawbot)
    env.setattr(web, 'require_build', lambda _: None)
    apps = []
    env.setattr(web.uvicorn, 'run', lambda app, **_: apps.append(app))
    web.main([])
    return apps[0], path


def test_roles_are_assigned_from_credentials(admin_app, env):
    from lawbot import auth

    app, _ = admin_app
    verify = auth.verify_password
    calls = []

    def record_verify(password, encoded):
        calls.append(encoded)
        return verify(password, encoded)

    env.setattr(auth, 'verify_password', record_verify)
    with TestClient(app) as c:
        regular = c.post('/api/login', json={'user': USER, 'password': PW, 'role': 'admin'})
        assert regular.json() == {'user': USER}
        assert c.get('/api/me').json() == {'user': USER, 'role': 'user'}
        admin = c.post('/api/login', json={'user': ADMIN_USER, 'password': ADMIN_PW, 'role': 'user'})
        assert admin.json() == {'user': ADMIN_USER}
        assert c.get('/api/me').json() == {'user': ADMIN_USER, 'role': 'admin'}
        unknown = login(c, 'fixture-unknown', ADMIN_PW)
        assert unknown.status_code == 401
        assert len(calls) == 3 and calls[0] == calls[2] != calls[1]
        cookie = admin.headers['set-cookie'].lower()
        assert all(value in cookie for value in ('httponly', 'samesite=strict', 'path=/')) and 'secure' not in cookie


def test_login_limit_expires_and_counts_by_user(admin_app, env):
    app, _ = admin_app
    original_time = web.time
    now = [0.0]
    env.setattr(web, 'time', SimpleNamespace(monotonic=lambda: now[0], strftime=original_time.strftime))
    with TestClient(app) as c:
        for _ in range(5):
            assert login(c, ADMIN_USER, 'bad-test-password').status_code == 401
        assert login(c, ADMIN_USER, ADMIN_PW).status_code == 429
        assert login(c).status_code == 200
        now[0] = 899
        assert login(c, ADMIN_USER, ADMIN_PW).status_code == 429
        now[0] = 900
        assert login(c, ADMIN_USER, ADMIN_PW).status_code == 200


def test_admin_login_audit_records_only_safe_fields(admin_app, admin_hash, env):
    app, path = admin_app

    def unexpected_check(*args, **kwargs):
        pytest.fail('Audit write revalidated the DB path')

    env.setattr(connection, 'check_private', unexpected_check)
    with TestClient(app) as c:
        bad = c.post('/api/login', json={'user': ADMIN_USER, 'password': 'bad-test-password', 'session_id': 'fake-sid'})
        assert bad.status_code == 401
        assert login(c).status_code == 200
        good = login(c, ADMIN_USER, ADMIN_PW)
        assert good.status_code == 200
        token = c.cookies[web.COOKIE]
        sid = app.state.sessions.get(token)[1]
    with connection.connect(path) as conn:
        rows = conn.execute(
            'SELECT actor, role, action, target_type, target_id, summary FROM audit_events ORDER BY event_id'
        ).fetchall()
    assert [row[:3] for row in rows] == [(ADMIN_USER, 'admin', 'login_fail'), (ADMIN_USER, 'admin', 'login_ok')]
    serialized = str(rows)
    assert all(
        secret not in serialized for secret in (ADMIN_PW, admin_hash, 'bad-test-password', 'fake-sid', token, sid)
    )


def test_admin_audit_failure_does_not_block_login(admin_app, admin_hash, caplog):
    app, path = admin_app
    with connection.connect(path, write=True) as conn:
        conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT, 'test'); END")
    with TestClient(app) as c:
        assert login(c, ADMIN_USER, 'bad-test-password').status_code == 401
        assert login(c, ADMIN_USER, ADMIN_PW).status_code == 200
        assert c.get('/api/me').json() == {'user': ADMIN_USER, 'role': 'admin'}
    assert '감사 기록' in caplog.text and all(secret not in caplog.text for secret in (ADMIN_PW, admin_hash))


def test_admin_scrypt_does_not_block_other_users_chat(admin_app, env):
    from lawbot import auth

    app, _ = admin_app
    started, release = Event(), Event()
    verify = auth.verify_password

    def pause_verify(password, encoded):
        started.set()
        assert release.wait(5), 'Chat could not finish during password verification'
        return verify(password, encoded)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url='http://testserver') as regular,
            httpx.AsyncClient(transport=transport, base_url='http://testserver') as administrator,
        ):
            assert (await login(regular)).status_code == 200
            env.setattr(auth, 'verify_password', pause_verify)
            pending = asyncio.create_task(login(administrator, ADMIN_USER, ADMIN_PW))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                result = await asyncio.wait_for(chat(regular), timeout=2)
                assert events(result)[-1][0] == 'done' and not pending.done()
            finally:
                release.set()
                response = await asyncio.wait_for(pending, timeout=5)
            assert response.status_code == 200

    asyncio.run(exercise())


# ---- 설정 ----
def test_config(authed, lawbot):
    cfg = authed.get('/api/config').json()
    assert cfg['title'] == 'Lawbot'
    assert cfg['intro'].endswith(view.DISCLAIMER) and cfg['notice'] == view.data_notice(lawbot)
    assert [m['key'] for m in cfg['models']] == list(lawbot.models.specs)
    first = lawbot.profile.models[0]
    assert cfg['default_model'] == first.key
    assert {m['key']: m['format'] for m in cfg['models']}[first.key] == first.fmt
    assert cfg['formats'] == [{'value': 'concise', 'label': '간결'}, {'value': 'full', 'label': '전체 (4항목)'}]
    assert cfg['examples'] == view.EXAMPLES
    assert cfg['sources_title'] == view.sources_title(lawbot)
    assert cfg['feedback'] == {'enabled': False, 'reasons': []}
    assert 'token' not in json.dumps(cfg).lower()


def test_config_lists_disabled_models(env, lawbot):
    env.setenv('LAWBOT_MODELS', 'fast')
    app = service.load_app()
    with make_client(app) as c:
        login(c)
        cfg = c.get('/api/config').json()
    assert [m['key'] for m in cfg['models']] == ['fast']
    assert [m['key'] for m in cfg['disabled']] == ['pro']


# ---- 채팅 ----
def test_chat_streams_events_in_order(authed):
    evs = events(chat(authed))
    n = names(evs)
    assert n[0] == 'status' and '조문 검색' in evs[0][1]['text']
    assert n[1] == 'sources' and n[-1] == 'done'
    assert n.count('sources') == 2 and 'answer' in n
    first_sources = evs[1][1]
    assert first_sources['groups'] == [
        {'label': None, 'open': True, 'items': [web.source_item(h) for h in HITS]}
    ]  # 생성 중에는 모두 펼침
    final_sources = [d for e, d in evs if e == 'sources'][-1]
    assert [(g['label'], g['open'], [i['title'] for i in g['items']]) for g in final_sources['groups']] == [
        (view.CITED_LABEL, True, ['근로기준법 제26조 해고의 예고']),
        ('검색만 된 조문', False, ['근로기준법 제36조 금품 청산']),
    ]
    answers = [d['text'] for e, d in evs if e == 'answer']
    assert answers[0] == '【결론】\n예고해야' and answers == sorted(answers, key=len)  # 화면 글 전체가 늘어남
    stats = [d['text'] for e, d in evs if e == 'stats']
    assert '완료' in stats[-1] and all('완료' not in s for s in stats[:-1])
    done = evs[-1][1]
    assert '예고해야 합니다' in done['text'] and done['turn_id'] is None  # 피드백이 꺼져 있으면 turn_id 없음


def test_chat_item_fields(authed):
    item = web.source_item(HITS[0])
    assert item == {
        'title': '근로기준법 제26조 해고의 예고',
        'scores': view.source_scores(HITS[0]),
        'body': '사용자는 근로자를 해고하려면 적어도 30일 전에 예고를 하여야 한다.',
        'decree': False,
    }


def test_chat_empty_answer(authed, env):
    env.setattr(FakeLLM, 'chunks', [''])
    evs = events(chat(authed))
    assert evs[-1][0] == 'done' and evs[-1][1]['text'] == view.EMPTY


@pytest.mark.parametrize(
    'body',
    [
        {'question': '  ', 'model': 'fast', 'format': 'full'},
        {'question': QUESTION, 'model': 'nope', 'format': 'full'},
        {'question': QUESTION, 'model': 'fast', 'format': 'long'},
        {'question': QUESTION, 'model': 'fast'},
        {'question': 3, 'model': 'fast', 'format': 'full'},
        {'question': 'x' * (web.MAX_QUESTION + 1), 'model': 'fast', 'format': 'full'},
    ],
)
def test_chat_invalid_inputs(authed, env, body):
    calls = []
    env.setattr(web, 'ask', lambda *a, **kw: calls.append(a) or iter(()))
    r = authed.post('/api/chat', json=body)
    assert r.status_code == 400 and r.json()['detail']
    assert calls == []


def test_chat_disabled_model(env, lawbot):
    env.setenv('LAWBOT_MODELS', 'fast')
    with make_client(service.load_app()) as c:
        login(c)
        r = chat(c, model='pro')
    assert r.status_code == 400 and r.json()['detail'] == view.DISABLED_MODEL


@pytest.mark.parametrize(
    ('exc', 'kind', 'text'),
    [
        (RuntimeError('boom'), 'generation', '답변을 생성하지 못했습니다 (RuntimeError). 다시 질문해 주세요.'),
        (ModelServerTimeout('5초'), 'model_server_timeout', '모델 서버 응답이 없습니다'),
    ],
)
def test_chat_error_event(authed, env, exc, kind, text):
    env.setattr(FakeLLM, 'chunks', ['【결론】', exc])
    evs = events(chat(authed))
    assert evs[-1][0] == 'error' and 'done' not in names(evs)
    assert evs[-1][1]['kind'] == kind and evs[-1][1]['message'].startswith(text)


def test_chat_config_error_is_reported_without_details(authed, env):
    def broken(*a, **kw):
        raise service.AskError('config', 'load', KeyError('secret-model-id'))
        yield

    env.setattr(web, 'ask', broken)
    evs = events(chat(authed))
    assert evs[-1] == ('error', {'kind': 'config', 'message': web.CONFIG_ERROR})
    assert 'secret-model-id' not in json.dumps(evs, ensure_ascii=False)


def test_hidden_tokens_still_yield_ticks(lawbot, env):
    """간결형은 결론 뒤 토큰(근거·설명)이 화면 글을 바꾸지 않는다: 그래도 토큰마다 TICK을 내야 연결 끊김을 확인할 수 있다"""
    env.setattr(
        FakeLLM,
        'chunks',
        ['【결론】\n예고해야 합니다.', '\n【근거 조문】\n', '근로기준법 제26조', '\n【설명】\n', '- 설명'],
    )
    evs = list(web.chat_events(lawbot, QUESTION, 'fast', 'concise'))
    generate = [e for e, _ in evs if e in ('answer', 'status', 'stats', web.TICK)]
    assert generate.count(web.TICK) >= 3
    assert evs[-1][0] == 'done'


def test_event_format_keeps_korean_and_newlines():
    line = web.sse('answer', {'text': '한글\n\n줄'})
    assert line == 'event: answer\ndata: {"text": "한글\\n\\n줄"}\n\n'


# ---- 피드백 ----
@pytest.fixture
def fb_client(lawbot, tmp_path):
    fb = feedback.Feedback(tmp_path)
    with make_client(lawbot, fb=fb) as c:
        login(c)
        yield c, tmp_path


def saved(folder):
    return [json.loads(x) for f in sorted(folder.glob('feedback_*.jsonl')) for x in f.read_text().splitlines()]


def test_feedback_like_and_flag(fb_client):
    c, folder = fb_client
    cfg = c.get('/api/config').json()['feedback']
    assert cfg['enabled'] and [r['value'] for r in cfg['reasons']] == list(feedback.REASONS.values())
    done = events(chat(c))[-1][1]
    tid = done['turn_id']
    assert tid
    assert c.post('/api/feedback', json={'turn_id': tid, 'label': 'like'}).status_code == 200
    r = c.post('/api/feedback', json={'turn_id': tid, 'label': 'flag', 'reason': 'retrieval'})
    assert r.status_code == 200
    rows = saved(folder)
    assert [(x['label'], x['reason']) for x in rows] == [('like', None), ('flag', 'retrieval')]
    assert rows[0]['turn_id'] == tid and rows[0]['question'] == QUESTION
    assert rows[0]['answer_shown'] == done['text'] and rows[0]['model'] == 'fast' and rows[0]['fmt'] == 'full'
    assert set(rows[0]) >= {'hits', 'system', 'answer_raw', 'citations', 'timing', 'request_id', 'commit'}


@pytest.mark.parametrize(
    ('body', 'code'),
    [
        ({'turn_id': 'unknown', 'label': 'like'}, 404),
        ({'turn_id': None, 'label': 'like'}, 400),
        ({'label': 'like'}, 400),
        ({'turn_id': 'x', 'label': 'dislike'}, 400),
        ({'turn_id': 'x', 'label': 'flag', 'reason': '기타'}, 400),
    ],
)
def test_feedback_bad_requests(fb_client, body, code):
    c, folder = fb_client
    events(chat(c))
    assert c.post('/api/feedback', json=body).status_code == code
    assert saved(folder) == []


def test_feedback_other_session_cannot_label(lawbot, tmp_path):
    fb = feedback.Feedback(tmp_path)
    app = web.create_app(lawbot, (USER, PW), fb=fb)
    with TestClient(app) as a, TestClient(app) as b:
        login(a)
        login(b)
        tid = events(chat(a))[-1][1]['turn_id']
        assert b.post('/api/feedback', json={'turn_id': tid, 'label': 'like'}).status_code == 404
    assert saved(tmp_path) == []


def test_feedback_disabled(authed):
    assert authed.post('/api/feedback', json={'turn_id': 'x', 'label': 'like'}).status_code == 404


@pytest.mark.parametrize('db_failure', [False, True])
def test_feedback_http_sqlite_contract(lawbot, env, tmp_path, caplog, db_failure):
    from conftest import set_auth

    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    set_auth(env)
    env.setenv('LAWBOT_FEEDBACK', '1')
    env.setenv('LAWBOT_DB', str(path))
    env.setattr(feedback, 'DEFAULT_DIR', tmp_path / 'fallback')
    cfg = settings.load(web=True)

    def unexpected_check(*args, **kwargs):
        pytest.fail('The request revalidated the DB path')

    env.setattr(connection, 'check_private', unexpected_check)
    if db_failure:
        with connection.connect(path, write=True) as conn:
            conn.execute(
                "CREATE TRIGGER fail_feedback BEFORE INSERT ON feedbacks BEGIN SELECT RAISE(ABORT, 'test'); END"
            )
    with make_client(lawbot, fb=cfg.feedback) as c:
        assert login(c).status_code == 200
        done = events(chat(c))[-1][1]
        payload = {'turn_id': done['turn_id'], 'label': 'like'}
        assert done['text'] and done['turn_id']
        assert c.post('/api/feedback', json=payload | {'label': 'unknown'}).status_code == 400
        assert c.post('/api/feedback', json=payload | {'turn_id': 'unknown'}).status_code == 404
        response = c.post('/api/feedback', json=payload)
        assert response.status_code == 200 and response.json() == {'saved': True}
        assert c.post('/api/feedback', json=payload).json() == {'saved': False}

    with connection.connect(path) as conn:
        rows = conn.execute('SELECT turn_id, label FROM feedbacks').fetchall()
    assert rows == ([] if db_failure else [(done['turn_id'], 'like')])
    assert not (tmp_path / 'fallback').exists()
    assert ('피드백을 쓰지 못했습니다' in caplog.text) is db_failure
    assert QUESTION not in caplog.text


def test_feedback_requests_keep_click_order(lawbot, env, tmp_path):
    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    fb = feedback.Feedback(db=migrate.startup_path(str(path)))
    app = web.create_app(lawbot, (USER, PW), fb=fb, dist=None)
    started, release = Event(), Event()
    write = fb.write

    def delay_like(row):
        if row['label'] == 'like':
            started.set()
            assert release.wait(5), 'Timed out waiting to release the first write'
        write(row)

    env.setattr(fb, 'write', delay_like)
    env.setattr(
        feedback,
        'time',
        SimpleNamespace(monotonic=feedback.time.monotonic, strftime=lambda _: '2026-11-08T00:00:00+0000'),
    )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            assert (await login(client)).status_code == 200
            turn_id = events(await chat(client))[-1][1]['turn_id']
            first = asyncio.create_task(client.post('/api/feedback', json={'turn_id': turn_id, 'label': 'like'}))
            try:
                assert await asyncio.to_thread(started.wait, 5), 'The first write never started'
                second = asyncio.create_task(
                    client.post('/api/feedback', json={'turn_id': turn_id, 'label': 'cleared'})
                )
                assert (await asyncio.wait_for(client.get('/api/me'), timeout=2)).status_code == 200
                await asyncio.wait([second], timeout=1)
            finally:
                release.set()
            replies = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
            assert [reply.json() for reply in replies] == [{'saved': True}, {'saved': True}]

    asyncio.run(exercise())
    rows = feedback_store.read_records(path)
    assert [row['label'] for row in rows] == ['like', 'cleared']
    assert latest_rows(rows) == ([], 1)


# ---- 정적 파일 (SPA) ----
def test_spa_files_and_fallback(lawbot, tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text('<!doctype html><title>spa</title>', encoding='utf-8')
    (tmp_path / 'assets' / 'app.js').write_text('console.log(1)', encoding='utf-8')
    with make_client(lawbot, dist=tmp_path) as c:
        assert 'spa' in c.get('/').text  # 로그인 화면은 SPA가 그린다
        assert 'spa' in c.get('/some/page').text
        assert c.get('/assets/app.js').text == 'console.log(1)'
        assert c.get('/assets/none.js').status_code == 404
        assert c.get('/api/unknown').status_code == 404
        assert '[project]' not in c.get('/%2e%2e/pyproject.toml').text


def test_without_build_only_api(lawbot, tmp_path):
    with make_client(lawbot, dist=tmp_path / 'missing') as c:
        r = c.get('/')
        assert c.get('/api/me').status_code == 401
    assert r.status_code == 404 and 'npm run build' in r.text


# ---- 모듈 경계 ----
def test_view_and_settings_do_not_import_web_server(env):
    """화면 문구·설정 검사는 웹 서버(fastapi) 없이 (평가·배포 검사 명령도 씀)"""
    from conftest import set_auth

    set_auth(env)
    code = 'import sys, lawbot.view, lawbot.settings as s; s.load(web=True); print("fastapi" in sys.modules)'
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == 'False'


# ---- 화면 빌드 확인 (lawbot-app 시작 때) ----
def frontend(tmp_path):
    """작은 화면 소스 폴더와 그 지문으로 만든 dist (npm run build가 쓰는 build.json과 같은 형식)"""
    root = tmp_path / 'frontend'
    (root / 'src' / 'components').mkdir(parents=True)
    for rel, text in {
        'src/main.tsx': 'main',
        'src/components/A.tsx': 'a',
        'index.html': '<html>',
        'package.json': '{}',
        'package-lock.json': '{}',
        'tsconfig.json': '{}',
        'vite.config.ts': 'cfg',
        'fingerprint.ts': 'fp',
    }.items():
        (root / rel).write_text(text, encoding='utf-8')
    dist = root / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>', encoding='utf-8')
    (dist / 'build.json').write_text(json.dumps({'source': web.source_fingerprint(root)}), encoding='utf-8')
    return root, dist


def test_build_matches_sources(tmp_path):
    root, dist = frontend(tmp_path)
    web.require_build(dist)  # 통과
    (root / 'src' / 'components' / 'A.tsx').write_text('changed', encoding='utf-8')
    with pytest.raises(RuntimeError, match='npm ci && npm run build'):
        web.require_build(dist)


def test_build_missing_or_unreadable(tmp_path):
    root, dist = frontend(tmp_path)
    (dist / 'build.json').write_text('not json', encoding='utf-8')
    with pytest.raises(RuntimeError, match='npm ci && npm run build'):
        web.require_build(dist)
    (dist / 'build.json').unlink()
    with pytest.raises(RuntimeError, match='npm ci && npm run build'):
        web.require_build(dist)
    with pytest.raises(RuntimeError, match='npm ci && npm run build'):
        web.require_build(tmp_path / 'none')


def test_fingerprint_ignores_files_outside_sources(tmp_path):
    root, dist = frontend(tmp_path)
    (root / 'node_modules').mkdir()
    (root / 'node_modules' / 'x.js').write_text('x', encoding='utf-8')
    (root / 'README.md').write_text('notes', encoding='utf-8')
    web.require_build(dist)
