import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from admin_test_data import seed_qa
from conftest import ADMIN_PW, ADMIN_USER, TEST_LLM_TOKEN, TEST_TOKEN
from fakes import FakeLLM
from lawbot import auth, consult_admin_store, consult_store, consult_trace, service, settings, web
from lawbot.api_client import ModelServerError
from lawbot.db.connection import connect
from lawbot.inference import REQUEST_ID
from lawbot.llm import LLM
from test_consult_trace import ADMIN_PATH, ORIGIN, QUESTION, USER, Chunks, ask, login
from test_consult_trace import cfg as cfg
from test_consult_trace import db as db


@pytest.mark.parametrize(('terminal', 'accepted'), [('done', True), ('done', False), ('error', True)])
def test_terminal_event_persists_even_when_connection_closes(cfg, db, env, terminal, accepted):
    if terminal == 'error':
        env.setattr(FakeLLM, 'chunks', [RuntimeError('synthetic private diagnostic')])
    fb = SimpleNamespace(remember_turn=lambda *_: 'fixture-turn')
    app = web.create_app(service.load_app(cfg), USER, fb=fb, dist=None, db=db)
    cookie = app.state.sessions.create(USER[0])
    event_prefix = f'event: {terminal}\n'.encode()

    async def exercise():
        disconnected = asyncio.Event()
        initial = True
        final = None

        async def receive():
            nonlocal initial
            if initial:
                initial = False
                return {'type': 'http.request', 'body': json.dumps({'question': QUESTION}).encode()}
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            nonlocal final
            body = message.get('body', b'')
            if body.startswith(event_prefix):
                final = json.loads(body.split(b'\ndata: ', 1)[1])
                disconnected.set()
                if not accepted:
                    await anyio.sleep_forever()

        with anyio.fail_after(5):
            await app(
                {
                    'type': 'http',
                    'method': 'POST',
                    'scheme': 'http',
                    'path': '/api/chat',
                    'query_string': b'',
                    'headers': [(b'content-type', b'application/json'), (b'cookie', f'{web.COOKIE}={cookie}'.encode())],
                    'asgi': {'spec_version': '2.4'},
                },
                receive,
                send,
            )
        assert final is not None
        return final

    final = asyncio.run(exercise())
    with connect(db) as conn:
        saved = conn.execute(
            'SELECT status, delivery_status, answer_shown, answer_verdict, feedback_turn_id FROM consults'
        ).fetchone()
    if terminal == 'done':
        assert saved == (
            'ok' if accepted else 'cancelled',
            'sent' if accepted else 'interrupted',
            final['text'],
            'shown',
            'fixture-turn',
        )
    else:
        assert saved == ('error', 'sent', final['message'], None, None)


def test_storage_failure_is_visible_without_request_log(cfg, db, env, capsys):
    env.setenv('LAWBOT_REQUEST_LOG', '')
    app = web.create_app(service.load_app(settings.load(web=True)), USER, dist=None, db=db)
    with connect(db, write=True) as conn:
        conn.execute("""CREATE TRIGGER reject_trace BEFORE INSERT ON consults
                        BEGIN SELECT RAISE(ABORT, 'private trigger diagnostic'); END""")
    with TestClient(app) as client:
        login(client)
        assert 'event: done' in ask(client)
    logged = capsys.readouterr().out
    assert '상담 기록 저장 실패: request_id=' in logged and 'exception=IntegrityError' in logged
    assert QUESTION not in logged and 'private trigger diagnostic' not in logged
    assert not cfg.request_log.exists()


def _trace(label):
    return consult_trace.Consultation(label, QUESTION, 'a' * 16, 'server-session', 'user', {}, None)


def _save(cfg, db, trace):
    consult_store.save(
        db, trace, consult_store.Redactor(TEST_TOKEN, TEST_LLM_TOKEN, cfg.model_server, cfg.llm_url, cfg.port)
    )


@pytest.mark.parametrize(
    ('error', 'expected'), [(httpx.ConnectError, 'failed_before_response'), (httpx.WriteTimeout, 'unknown')]
)
def test_transport_without_response_stores_unconfirmed_attempt(cfg, db, error, expected):
    trace = _trace('synthetic-send-unknown')
    attempt = b'{"model":"fixture","query":"synthetic"}'

    def handler(_):
        raise error('synthetic transport failure')

    with (
        consult_trace.ACTIVE.set(trace),
        REQUEST_ID.set(trace.request_id),
        consult_trace.TraceClient(transport=httpx.MockTransport(handler), base_url=cfg.model_server) as client,
        pytest.raises(error),
    ):
        client.post('/v1/rerank', content=attempt)
    _save(cfg, db, trace)
    with connect(db) as conn:
        row = conn.execute('SELECT send_status, status_code, request_body, error_type FROM model_calls').fetchone()
    assert row == (expected, None, attempt, error.__name__)


def test_transport_boundaries_multiline_usage_and_completed_call_on_cancel(cfg, db, monkeypatch):
    trace = _trace('synthetic-stream-boundaries')
    parts = (
        b'data: {"id":"fixture","model":"fixture","created":1,"object":"chat.completion.chunk",\r\n',
        b'data: "usage":{"prompt_tokens":4,"completion_tokens":3,"total_tokens":7},\r\n'
        b'data: "choices":[{"index":0,"finish_reason":"stop","delta":{}}]}\r\n\r\n',
        b'data: [DONE]\r\n\r\n',
    )
    offsets = (40, 170, 430)
    now = trace.start

    class TimedChunks(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal now
            for offset, piece in zip(offsets, parts, strict=True):
                now = trace.calls[-1].start + offset
                yield piece

    def handler(_):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=TimedChunks())

    app = service.load_app(cfg)
    monkeypatch.setattr(consult_trace.time, 'monotonic_ns', lambda: now)
    with (
        consult_trace.ACTIVE.set(trace),
        REQUEST_ID.set(trace.request_id),
        consult_trace.TraceClient(transport=httpx.MockTransport(handler)) as http,
        OpenAI(base_url=cfg.llm_url, api_key=cfg.llm_key, http_client=http, max_retries=0) as sdk,  # pyright: ignore[reportArgumentType]
    ):
        app.llm = LLM('fixture', sdk, 'Fixture system')
        pipeline = service.write_from_context(app, QUESTION, [], request_id=trace.request_id, report=lambda *_: None)
        assert next(pipeline)[3]['ttft'] is not None
        pipeline.close()
    _save(cfg, db, trace)
    with connect(db) as conn:
        stored = conn.execute('SELECT offset_ns,body FROM model_call_chunks ORDER BY seq').fetchall()
        status, usage, error = conn.execute('SELECT send_status,usage,error_type FROM model_calls').fetchone()
    assert stored == list(zip(offsets, parts, strict=True))
    assert status == 'sent' and json.loads(usage)['total_tokens'] == 7 and error is None


def test_router_missing_finish_is_attributed_to_the_actual_http_call(cfg, db):
    trace = _trace('synthetic-router-validation')
    payload = {
        'id': 'fixture',
        'object': 'chat.completion',
        'model': 'fixture',
        'created': 1,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'lookup'}}],
    }

    def answer(_):
        return httpx.Response(
            200, stream=Chunks(json.dumps(payload).encode()), headers={'content-type': 'application/json'}
        )

    with (
        consult_trace.ACTIVE.set(trace),
        REQUEST_ID.set(trace.request_id),
        consult_trace.TraceClient(transport=httpx.MockTransport(answer)) as http,
        OpenAI(base_url=cfg.llm_url, api_key=cfg.llm_key, http_client=http, max_retries=0) as sdk,  # pyright: ignore[reportArgumentType]
        pytest.raises(ModelServerError, match='finish_reason'),
    ):
        LLM('fixture', sdk, 'Fixture system').route_calculation(QUESTION)
    _save(cfg, db, trace)
    with connect(db) as conn:
        assert conn.execute('SELECT purpose,send_status,status_code,error_type FROM model_calls').fetchone() == (
            'router_select',
            'sent',
            200,
            'missing_finish_reason',
        )


def test_admin_payload_pages_are_bounded_and_reassemble_large_wire(cfg, db, admin_hash):
    seed_qa(db)
    trace = _trace('synthetic-body-paging')
    with trace.step('writer', raw='stage fixture'):
        pass
    wire = (b'\xff' * (consult_admin_store.BODY_PAGE_BYTES + 9), *(b'part\n' for _ in range(70)))
    request = json.dumps({'model': 'fixture', 'stream': True, 'messages': '한' * 17000}, ensure_ascii=False).encode()

    def answer(_):
        return httpx.Response(200, stream=Chunks(*wire))

    with (
        consult_trace.ACTIVE.set(trace),
        REQUEST_ID.set(trace.request_id),
        consult_trace.TraceClient(transport=httpx.MockTransport(answer), base_url=cfg.model_server) as http,
        http.stream('POST', '/v1/chat/completions', content=request) as response,
    ):
        assert response.read() == b''.join(wire)
    _save(cfg, db, trace)
    app = web.create_app(
        service.load_app(cfg),
        USER,
        dist=None,
        db=db,
        admin=auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash)),
    )

    def as_bytes(item):
        return item['data'].encode() if item['encoding'] == 'utf-8' else base64.b64decode(item['data'])

    with TestClient(app) as admin:
        login(admin, (ADMIN_USER, ADMIN_PW))
        path = f'{ADMIN_PATH}/{trace.id}'
        metadata = admin.get(path).json()
        assert 'question' not in metadata['consult'] and 'answer_shown' not in metadata['consult']
        assert 'result' not in metadata['steps'][0] and 'request_body' not in metadata['calls'][0]
        assert admin.get(f'{path}/text').json()['question'] == QUESTION
        assert admin.get(f'{path}/steps/1').json()['result'] == {'raw': 'stage fixture'}
        for part, expected in (('request', [request]), ('chunks', wire)):
            seen = {}
            cursor = '1:0'
            pages = 0
            while cursor is not None:
                response = admin.get(f'{path}/calls/1/body', params={'part': part, 'cursor': cursor})
                assert response.status_code == 200
                body = response.json()
                items = (
                    body['items']
                    if part == 'chunks'
                    else [
                        {'seq': 1, 'byte_offset': int(cursor.split(':')[1]), 'body': body['body']},
                    ]
                )
                size = 0
                for item in items:
                    current = seen.setdefault(item['seq'], bytearray())
                    piece = as_bytes(item['body'])
                    assert item['byte_offset'] == len(current)
                    current.extend(piece)
                    size += len(piece)
                assert (
                    size <= consult_admin_store.BODY_PAGE_BYTES and len(items) <= consult_admin_store.CHUNK_PAGE_COUNT
                )
                cursor = body['next_cursor']
                pages += 1
                assert pages <= 5
            assert pages > 1 and list(seen.values()) == list(expected)
        assert admin.get(f'{path}/calls/1/body', params={'part': 'chunks', 'cursor': 'bad'}).status_code == 422


def test_preview_does_not_lock_out_consultation_writes(cfg, db, admin_hash, monkeypatch):
    seed_qa(db)
    first = _trace('first-preview-fixture')
    _save(cfg, db, first)
    app = web.create_app(
        service.load_app(cfg),
        USER,
        dist=None,
        db=db,
        admin=auth.AdminCredentials(ADMIN_USER, auth.parse_password_hash(admin_hash)),
    )
    selection_entered, release = Event(), Event()
    selection = consult_admin_store._selection

    def held_selection(*args):
        selection_entered.set()
        assert release.wait(5)
        return selection(*args)

    monkeypatch.setattr(consult_admin_store, '_selection', held_selection)
    body = {'idempotency_key': 'preview-fixture', 'user_hash': first.user_hash}
    with TestClient(app) as admin, ThreadPoolExecutor(max_workers=1) as worker:
        login(admin, (ADMIN_USER, ADMIN_PW))
        pending = worker.submit(admin.post, f'{ADMIN_PATH}/delete-preview', json=body, headers=ORIGIN)
        try:
            assert selection_entered.wait(2)
            next_trace = _trace('second-preview-fixture')
            _save(cfg, db, next_trace)
        finally:
            release.set()
        preview = pending.result(timeout=5)
    assert preview.status_code == 200 and preview.json()['counts']['consults'] == 1
