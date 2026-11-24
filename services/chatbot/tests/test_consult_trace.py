import base64
import gzip
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from secrets import token_urlsafe
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from admin_test_data import seed_qa
from conftest import ADMIN_PW, ADMIN_USER, TEST_LLM_TOKEN, TEST_TOKEN, free_port, set_auth
from fakes import FakeLLM, FakeRetriever
from lawbot import auth, consult_admin_store, consult_store, consult_trace, service, settings, web
from lawbot.db import migrate
from lawbot.db.connection import connect
from lawbot.inference import REQUEST_ID
from lawbot.llm import LLM

QUESTION = '합성 사례: 해고 예고가 필요한가요?'
ANSWER = '【결론】\n합성 답변입니다.\n【근거 조문】\n- 근로기준법 제26조\n'
USER = ('tester', 'test-only-pw')
ADMIN_PATH = '/api/admin/consults'
ORIGIN = {'Origin': 'http://testserver'}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'private' / 'trace.db'
    migrate.init(path)
    return path


@pytest.fixture
def cfg(env, db, tmp_path):
    set_auth(env, *USER)
    env.setenv('LAWBOT_DB', str(db))
    env.setenv('LAWBOT_PORT', str(free_port()))
    env.setenv('LAWBOT_REQUEST_LOG', str(tmp_path / 'requests.jsonl'))
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(FakeLLM, 'chunks', [ANSWER])
    return settings.load(web=True)


def login(client, credentials=USER):
    user, password = credentials
    assert client.post('/api/login', json={'user': user, 'password': password}).status_code == 200


def ask(client, question=QUESTION):
    response = client.post('/api/chat', json={'question': question})
    assert response.status_code == 200
    return response.text


def test_consult_settings_and_disabled_recording(env, cfg, db):
    assert cfg.consult_trace
    env.delenv('LAWBOT_DB')
    assert not settings.load(web=True).consult_trace
    env.setenv('LAWBOT_DB', str(db))
    env.setenv('LAWBOT_CONSULT_TRACE', '0')
    off = settings.load(web=True)
    assert not off.consult_trace
    with TestClient(web.create_app(service.load_app(off), USER, dist=None, db=db)) as client:
        login(client)
        assert client.get('/api/config').json()['consult_trace'] is False
        assert 'event: done' in ask(client)
    with connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM consults').fetchone() == (0,)
    env.setenv('LAWBOT_CONSULT_TRACE', 'secret-invalid-value')
    with pytest.raises(settings.SettingsError, match='LAWBOT_CONSULT_TRACE') as exc:
        settings.load(web=True)
    assert 'secret-invalid-value' not in str(exc.value)


def test_saved_consult_redacts_and_request_log_does_not_contain_raw(cfg, db):
    app = web.create_app(service.load_app(cfg), USER, dist=None, db=db)
    with TestClient(app) as client:
        login(client)
        assert client.get('/api/config').json()['consult_trace'] is True
        token = client.cookies.get(web.COOKIE)
        assert token
        marker = ' '.join((TEST_TOKEN, TEST_LLM_TOKEN, cfg.model_server, cfg.llm_url, str(cfg.port), f'X{token}Y'))
        events = ask(client, QUESTION + ' ' + marker)
        assert '합성 답변입니다.' in events
    with connect(db) as conn:
        row = conn.execute(
            'SELECT request_id, user_hash, role, session_id, question, answer_shown, answer_verdict, '
            'retention_policy, redaction_count, created_at, ended_at FROM consults'
        ).fetchone()
        stored_steps = '\n'.join(r[0] for r in conn.execute('SELECT result FROM consult_steps ORDER BY seq'))
    rid, user_hash, role, sid, question, answer, verdict, policy, count, created, ended = row
    assert user_hash == hashlib.sha256(USER[0].encode()).hexdigest()[:16]
    assert role == 'user' and sid and sid != token
    assert QUESTION in question and answer.startswith('합성 답변입니다.') and verdict == 'shown'
    assert policy == 'consult-trace-v1' and count >= 6
    assert datetime.fromisoformat(created) <= datetime.fromisoformat(ended)
    for secret in (TEST_TOKEN, TEST_LLM_TOKEN, cfg.model_server, cfg.llm_url, str(cfg.port), token):
        assert secret not in question + stored_steps
    assert '[REDACTED:session_cookie]' in question
    assert 'X[REDACTED:session_cookie]Y' in question
    log = cfg.request_log.read_text()
    assert QUESTION not in log and '합성 답변입니다.' not in log and token not in log
    assert json.loads(log)['request_id'] == rid
    assert json.loads(log)['trace_status'] == 'saved'


def test_write_failure_rolls_back_and_does_not_block_answer(cfg, db):
    app = web.create_app(service.load_app(cfg), USER, dist=None, db=db)
    with connect(db, write=True) as conn:
        conn.execute("""CREATE TRIGGER reject_trace BEFORE INSERT ON consult_steps
                        BEGIN SELECT RAISE(ABORT, 'private payload'); END""")
    with TestClient(app) as client:
        login(client)
        assert 'event: done' in ask(client)
    with connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM consults').fetchone() == (0,)
    log = cfg.request_log.read_text()
    row = json.loads(log)
    assert (row['status'], row['trace_status'], row['trace_error']) == ('ok', 'failed', sqlite3.IntegrityError.__name__)
    assert 'private payload' not in log and QUESTION not in log


def test_delete_preview_confirmation_filters_snapshot_and_receipt(cfg, db, admin_hash):
    seed_qa(db)
    app = web.create_app(
        service.load_app(cfg),
        USER,
        dist=None,
        db=db,
        admin=auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash)),
    )
    with TestClient(app) as user, TestClient(app) as admin:
        login(user)
        login(admin, (ADMIN_USER, ADMIN_PW))
        assert admin.get(ADMIN_PATH, headers={'CF-Connecting-IP': '203.0.113.7'}).status_code == 404
        preview_path = f'{ADMIN_PATH}/delete-preview'
        assert admin.post(preview_path, json={}).status_code == 403
        assert admin.post(preview_path, content='form', headers=ORIGIN).status_code == 415
        assert (
            admin.post(
                preview_path,
                content=b' ' * (web.ADMIN_BODY_LIMIT + 1),
                headers=ORIGIN | {'Content-Type': 'application/json'},
            ).status_code
            == 413
        )
        ask(user, QUESTION + ' 첫째')
        ask(admin, QUESTION + ' 관리자')
        ask(user, QUESTION + ' 둘째')
        user_hash = hashlib.sha256(USER[0].encode()).hexdigest()[:16]
        listing = admin.get(ADMIN_PATH, params={'limit': 1, 'user_hash': user_hash, 'status': 'ok'}).json()
        first = listing['items'][0]
        assert first['role'] == 'user' and first['status'] == 'ok'
        older = admin.get(ADMIN_PATH, params={'cursor': listing['next_cursor'], 'user_hash': user_hash}).json()
        assert len(older['items']) == 1
        assert older['items'][0]['id'] != first['id'] and older['next_cursor'] is None
        body = {'user_hash': user_hash}

        def post(path, key, **part):
            return admin.post(ADMIN_PATH + path, json={'idempotency_key': key, **part}, headers=ORIGIN)

        assert post('/delete-preview', 'empty').status_code == 422
        assert post('/delete-preview', 'bad-period', start='2026-01-01').status_code == 422
        preview = post('/delete-preview', 'preview', **body).json()
        assert preview['counts']['consults'] == 2
        ask(user, QUESTION + ' 셋째')
        assert post('/delete', 'stale', **body, confirmation=preview['confirmation']).status_code == 409
        time_filter = {'start': first['created_at']}
        preview = post('/delete-preview', 'filtered', **body, **time_filter).json()
        assert preview['counts']['consults'] == 2
        result = post('/delete', 'confirm', **body, **time_filter, confirmation=preview['confirmation'])
        assert result.status_code == 200 and result.json() == preview['counts']
        assert (
            post('/delete', 'confirm', **body, **time_filter, confirmation=preview['confirmation']).json()
            == result.json()
        )
        assert admin.get(f'{ADMIN_PATH}/{first["id"]}').status_code == 404
        assert {item['role'] for item in admin.get(ADMIN_PATH).json()['items']} == {'user', 'admin'}
    with connect(db) as conn:
        (deletion,) = conn.execute(
            'SELECT admin_hash, conditions, consult_count, step_count, call_count FROM consult_trace_deletions'
        ).fetchall()
        assert deletion[0] == hashlib.sha256(ADMIN_USER.encode()).hexdigest()[:16]
        assert json.loads(deletion[1]) == body | time_filter
        assert deletion[2:] == (2, preview['counts']['steps'], preview['counts']['calls'])
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='consult_trace_delete'").fetchone() == (1,)


class Chunks(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes):
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks


@pytest.mark.parametrize('compressed', [False, True])
def test_redact_response_at_chunk_boundaries_and_preserve_binary(cfg, db, compressed):
    rid = 'synthetic-wire-redaction'
    trace = consult_trace.Consultation(rid, QUESTION, 'user-hash', 'server-session', 'user', {}, None)
    cookie = token_urlsafe(32)
    model_addr = urlsplit(cfg.model_server).netloc
    llm_addr = urlsplit(cfg.llm_url).netloc
    raw = (
        f'prefix={TEST_TOKEN}; {TEST_LLM_TOKEN} {model_addr} {llm_addr} {cfg.port} {web.COOKIE}={cookie}; end'.encode()
    )
    body = gzip.compress(raw) if compressed else raw
    split = len(body) // 2 if compressed else raw.index(TEST_TOKEN.encode()) + 3
    wire = [body[:split], body[split:]]
    seen = []
    binary = b'\x00\xfftail'

    def handler(request):
        seen.append(request.content)
        if request.url.path.endswith('/rerank'):
            return httpx.Response(502, stream=Chunks(binary))
        headers = {'Content-Encoding': 'gzip'} if compressed else {}
        return httpx.Response(200, headers=headers, stream=Chunks(*wire))

    with (
        consult_trace.ACTIVE.set(trace),
        REQUEST_ID.set(rid),
        consult_trace.TraceClient(transport=httpx.MockTransport(handler), base_url=cfg.model_server) as http,
    ):
        http.post('/v1/rerank', json={'model': 'synthetic-reranker'})
        se = {'model': 'synthetic-writer', 'stream': True, 'messages': [{'role': 'user', 'content': raw.decode()}]}
        with http.stream('POST', '/v1/chat/completions', json=se) as response:
            assert list(response.iter_raw()) == wire
    redactor = consult_store.Redactor(TEST_TOKEN, TEST_LLM_TOKEN, cfg.model_server, cfg.llm_url, cfg.port)
    consult_store.save(db, trace, redactor)
    with connect(db) as conn:
        saved_binary, saved_stream = conn.execute(
            'SELECT request_body, response_body, response_chunks, response_decoded, redaction_reasons '
            'FROM model_calls ORDER BY seq'
        ).fetchall()
    assert saved_binary[:2] == (seen[0], binary)
    sent, _, chunks, decoded, reasons = saved_stream
    expected = (
        'prefix=[REDACTED:model_token]; [REDACTED:generation_api_key] [REDACTED:model_server_address] '
        '[REDACTED:generation_server_address] [REDACTED:web_port] lawbot_session=[REDACTED:session_cookie]; end'
    )
    assert json.loads(sent)['messages'][0]['content'] == expected
    parts = [base64.b64decode(value) for _, value in json.loads(chunks)]
    assert len(parts) == 2 and b''.join(parts).decode() == expected and decoded == int(compressed)
    if not compressed:
        assert parts[0] == b'prefix=[REDACTED:model_token]' and parts[1].startswith(b'; ')
    assert json.loads(reasons) == dict.fromkeys(
        (
            'model_token',
            'generation_api_key',
            'model_server_address',
            'generation_server_address',
            'web_port',
            'session_cookie',
        ),
        2,
    )
    detail = consult_admin_store.detail(db, trace.id)
    assert detail['calls'][0]['response_body'] == {'encoding': 'base64', 'data': base64.b64encode(binary).decode()}
    assert detail['calls'][1]['request_body'] == {'encoding': 'utf-8', 'data': sent.decode()}


def test_opt_in_clarify_and_resume_keep_their_original_llm_bodies(cfg):
    app = service.load_app(cfg)
    seen = []

    def completion(request):
        payload = json.loads(request.content)
        seen.append(payload)
        base = {'id': 'synthetic-completion', 'model': 'synthetic-writer', 'created': 1}
        if not payload['stream']:
            answer = base | {
                'object': 'chat.completion',
                'choices': [
                    {
                        'index': 0,
                        'finish_reason': 'stop',
                        'message': {
                            'role': 'assistant',
                            'content': '{"ask":true,"missing":["기간"],"question":"기간은?"}',
                        },
                    }
                ],
            }
            return httpx.Response(
                200, stream=Chunks(json.dumps(answer).encode()), headers={'Content-Type': 'application/json'}
            )
        chunk = base | {
            'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'delta': {'content': ANSWER}}],
        }
        return httpx.Response(200, stream=Chunks(f'data: {json.dumps(chunk)}\n\n'.encode(), b'data: [DONE]\n\n'))

    rid = 'synthetic-resumed-request'
    trace = consult_trace.Consultation(rid, QUESTION, 'user-hash', 'server-session', 'user', {}, None)
    with (
        consult_trace.ACTIVE.set(trace),
        consult_trace.TraceClient(transport=httpx.MockTransport(completion)) as transport,
        OpenAI(base_url=cfg.llm_url, api_key=cfg.llm_key, http_client=transport, max_retries=0) as sdk,
    ):
        app.llm = LLM('synthetic-writer', sdk, 'Synthetic writer system.')
        last = list(service.ask(app, QUESTION, request_id=rid, clarify=True, report=lambda *_: None))[-1]
        event, hits, question, _ = last
        assert (event, question) == ('clarify', '기간은?')
        resumed = list(
            service.answer_after_clarification(
                app,
                QUESTION,
                hits,
                question,
                '두 달',
                request_id=rid,
                report=lambda *_: None,
            )
        )[-1]
    assert resumed[:3] == ('done', hits, ANSWER)
    assert [item.stage for item in trace.stages] == ['search', 'definitions', 'clarify', 'writer', 'citation']
    assert [call.purpose for call in trace.calls] == ['clarify', 'writer']
    assert [json.loads(call.request_body) for call in trace.calls] == seen
    assert seen[1]['messages'][1]['content'].endswith(f'{QUESTION}\n\n[확인 질문]\n기간은?\n\n[사용자 답]\n두 달')
