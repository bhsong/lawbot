# services/model-server/tests/test_ms_api.py  →  OpenAI 호환 API (가짜 모델 + FastAPI TestClient)
# 형식은 OpenAI 공식 SDK로도 확인한다 (클라우드와 같은 코드로 쓸 수 있어야 함)
import base64
import json
import subprocess
import sys
import threading
import time

import numpy as np
import openai
import pytest
from fastapi.testclient import TestClient

from lawbot_model_server import api

from .ms_fakes import AUTH, TOKEN, Compute, FakeChat, FakeReranker, registry


@pytest.fixture
def reg():
    return registry()


@pytest.fixture
def client(reg):
    with TestClient(api.create_app(TOKEN, reg)) as c:
        yield c


def sdk(client):
    """OpenAI 공식 SDK가 TestClient로 이 서버를 부른다"""
    return openai.OpenAI(base_url='http://testserver/v1', api_key=TOKEN, http_client=client, max_retries=0)


MESSAGES = [{'role': 'system', 'content': 'SYS'}, {'role': 'user', 'content': '해고 예고?'}]


def sse_events(text):
    out = []
    for block in text.strip().split('\n\n'):
        assert block.startswith('data: ')
        data = block.removeprefix('data: ')
        out.append(data if data == '[DONE]' else json.loads(data))
    return out


# ---- 인증·준비·크기 ----
@pytest.mark.parametrize('path', ['/health', '/v1/models'])
def test_token_required_everywhere(client, path):
    for headers in ({}, {'Authorization': 'Bearer wrong-token-000000'}, {'Authorization': TOKEN}):
        r = client.get(path, headers=headers)
        assert r.status_code == 401 and r.json()['error']['type'] == 'authentication_error'
        assert TOKEN not in r.text


def test_post_requires_token(client):
    r = client.post('/v1/chat/completions', json={'model': 'chat-a', 'messages': MESSAGES})
    assert r.status_code == 401


def test_not_ready_until_registry_set():
    app = api.create_app(TOKEN)
    with TestClient(app) as c:
        assert c.get('/health', headers=AUTH).json() == {'ready': False}
        r = c.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x'})
        assert r.status_code == 503 and r.json()['error']['type'] == 'server_not_ready'
        app.state.registry = registry()
        assert c.get('/health', headers=AUTH).json() == {'ready': True}


def test_body_too_large():
    with TestClient(api.create_app(TOKEN, registry(), max_body=100)) as c:
        r = c.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x' * 200})
        assert r.status_code == 413


def test_no_docs_endpoints(client):
    for path in ('/docs', '/openapi.json', '/redoc'):
        assert client.get(path, headers=AUTH).status_code == 404


def test_models_listing(client, reg):
    data = client.get('/v1/models', headers=AUTH).json()['data']
    assert [(m['id'], m['type'], m['loaded']) for m in data] == [
        ('chat-a', 'chat', False),
        ('chat-b', 'chat', False),
        ('emb', 'embedding', True),
        ('rr', 'rerank', True),
    ]
    assert all(m['object'] == 'model' for m in data)
    assert [m.id for m in sdk(client).models.list()] == ['chat-a', 'chat-b', 'emb', 'rr']


# ---- chat completions ----
def test_chat_non_stream(client, reg):
    r = client.post('/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES})
    body = r.json()
    assert r.status_code == 200 and body['object'] == 'chat.completion' and body['model'] == 'chat-a'
    assert body['choices'][0]['message'] == {'role': 'assistant', 'content': '【결론】예고해야 합니다.'}
    assert body['choices'][0]['finish_reason'] == 'stop'
    assert body['usage'] == {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}
    assert body['timings'] == {'prompt_tps': 100.0, 'generation_tps': 20.0, 'peak_memory_gb': 1.5}
    call = reg.chat['chat-a'].calls[0]
    assert call == {
        'messages': MESSAGES,
        'max_tokens': api.DEFAULT_MAX_TOKENS,
        'temperature': 0.0,  # 없으면 탐욕적 생성 (지금 웹앱과 같음)
        'top_p': None,
        'enable_thinking': False,
    }


def test_chat_stream_format(client):
    r = client.post(
        '/v1/chat/completions',
        headers=AUTH,
        json={'model': 'chat-a', 'messages': MESSAGES, 'stream': True, 'stream_options': {'include_usage': True}},
    )
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/event-stream')
    ev = sse_events(r.text)
    assert ev[-1] == '[DONE]'
    chunks = ev[:-1]
    assert chunks[0]['choices'][0]['delta'] == {'role': 'assistant', 'content': '【결론】'}
    assert [c['choices'][0]['delta'].get('content') for c in chunks[:3]] == ['【결론】', '예고해야', ' 합니다.']
    fin = chunks[3]
    assert fin['choices'][0]['finish_reason'] == 'stop' and fin['choices'][0]['delta'] == {}
    assert fin['timings']['generation_tps'] == 20.0
    assert chunks[4]['choices'] == [] and chunks[4]['usage']['completion_tokens'] == 3
    assert len({c['id'] for c in chunks}) == 1 and all(c['object'] == 'chat.completion.chunk' for c in chunks)


def test_chat_stream_without_usage(client):
    r = client.post(
        '/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES, 'stream': True}
    )
    chunks = sse_events(r.text)[:-1]
    assert all(c['choices'] for c in chunks) and 'usage' not in chunks[-1]


def test_chat_options_passed(client, reg):
    client.post(
        '/v1/chat/completions',
        headers=AUTH,
        json={
            'model': 'chat-b',
            'messages': [
                {'role': 'user', 'content': [{'type': 'text', 'text': '요약'}, {'type': 'text', 'text': '해'}]}
            ],
            'max_completion_tokens': 77,
            'temperature': 0.7,
            'top_p': 0.9,
            'chat_template_kwargs': {'enable_thinking': True},
        },
    )
    assert reg.chat['chat-b'].calls[0] == {
        'messages': [{'role': 'user', 'content': '요약해'}],
        'max_tokens': 77,
        'temperature': 0.7,
        'top_p': 0.9,
        'enable_thinking': True,
    }


def test_finish_length(client):
    FakeChat.finish = 'length'
    r = client.post('/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES})
    assert r.json()['choices'][0]['finish_reason'] == 'length'


@pytest.mark.parametrize(
    'extra,needle',
    [
        ({'tools': []}, '지원하지 않는 필드: tools'),
        ({'logprobs': True}, '지원하지 않는 필드: logprobs'),
        ({'n': 2}, 'n'),
        ({'temperature': 3}, 'temperature'),
        ({'max_tokens': 0}, 'max_tokens'),
        ({'messages': []}, 'messages'),
        ({'messages': [{'role': 'tool', 'content': 'x'}]}, 'role'),
        ({'chat_template_kwargs': {'other': 1}}, '지원하지 않는 필드'),
    ],
)
def test_chat_bad_requests(client, extra, needle):
    r = client.post('/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES} | extra)
    assert r.status_code == 400 and needle in r.json()['error']['message']
    assert r.json()['error']['type'] == 'invalid_request_error'


def test_bad_json(client):
    r = client.post('/v1/chat/completions', headers=AUTH, content=b'{not json')
    assert r.status_code == 400 and 'JSON' in r.json()['error']['message']


def test_unknown_or_wrong_kind_model(client):
    for model in ('없음', 'emb'):
        r = client.post('/v1/chat/completions', headers=AUTH, json={'model': model, 'messages': MESSAGES})
        assert r.status_code == 404 and r.json()['error']['code'] == 'model_not_found'
    r = client.post('/v1/embeddings', headers=AUTH, json={'model': 'chat-a', 'input': 'x'})
    assert r.status_code == 404


def test_model_load_failure_is_500_before_stream():
    def broken(path):
        raise FileNotFoundError(f'모델 폴더 없음: {path}')

    reg = registry()
    reg.loaders['chat'] = broken
    with TestClient(api.create_app(TOKEN, reg)) as c:
        r = c.post('/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES, 'stream': True})
        err = r.json()['error']
        assert r.status_code == 500 and err['type'] == 'model_load_error' and err['code'] == 'FileNotFoundError'
        assert '모델 폴더 없음' in err['message']


def test_error_mid_stream(client):
    FakeChat.chunks = ['【결론】', RuntimeError('생성 실패')]
    r = client.post(
        '/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES, 'stream': True}
    )
    ev = sse_events(r.text)
    assert ev[0]['choices'][0]['delta']['content'] == '【결론】'
    assert ev[-1]['error']['code'] == 'RuntimeError' and '생성 실패' in ev[-1]['error']['message']
    with pytest.raises(openai.APIError, match='생성 실패'):  # SDK는 error 이벤트를 예외로
        for _ in sdk(client).chat.completions.create(model='chat-a', messages=MESSAGES, stream=True):
            pass


def test_queue_full_is_503_server_busy(client):
    from lawbot_model_server.scheduler import QueueFull

    def full(*a):
        raise QueueFull('대기 중인 생성 요청이 32개라 받지 않습니다')

    client.app.state.scheduler.chat = full
    for stream in (False, True):
        r = client.post(
            '/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES, 'stream': stream}
        )
        err = r.json()['error']
        assert r.status_code == 503 and err['type'] == 'server_busy' and '32개' in err['message']


def test_create_app_passes_queue_limit(reg):
    app = api.create_app(TOKEN, reg, max_queued=7)
    assert app.state.scheduler.max_queued == 7


def test_error_before_first_token(client):
    FakeChat.chunks = [RuntimeError('프리필 실패')]
    r = client.post('/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES})
    assert r.status_code == 500 and r.json()['error']['code'] == 'RuntimeError'


def test_lru_switch(client, reg):
    for m in ('chat-a', 'chat-b'):
        client.post('/v1/chat/completions', headers=AUTH, json={'model': m, 'messages': MESSAGES})
    assert list(reg.chat) == ['chat-b']  # 상주 1개: a를 내리고 b
    loaded = {m['id']: m['loaded'] for m in client.get('/v1/models', headers=AUTH).json()['data']}
    assert loaded == {'chat-a': False, 'chat-b': True, 'emb': True, 'rr': True}


# ---- OpenAI SDK 계약 ----
def test_sdk_chat(client):
    c = sdk(client)
    r = c.chat.completions.create(model='chat-a', messages=MESSAGES, max_tokens=50, temperature=0)
    assert r.choices[0].message.content == '【결론】예고해야 합니다.' and r.usage.completion_tokens == 3
    text, usage, timings = '', None, None
    for ch in c.chat.completions.create(
        model='chat-a', messages=MESSAGES, stream=True, stream_options={'include_usage': True}
    ):
        if ch.choices:
            text += ch.choices[0].delta.content or ''
            timings = (ch.model_extra or {}).get('timings') or timings
        usage = ch.usage or usage
    assert text == '【결론】예고해야 합니다.' and usage.prompt_tokens == 11 and timings['peak_memory_gb'] == 1.5


def test_sdk_extra_body_and_errors(client, reg):
    c = sdk(client)
    c.chat.completions.create(
        model='chat-a', messages=MESSAGES, extra_body={'chat_template_kwargs': {'enable_thinking': True}}
    )
    assert reg.chat['chat-a'].calls[-1]['enable_thinking'] is True
    with pytest.raises(openai.NotFoundError):
        c.chat.completions.create(model='없음', messages=MESSAGES)
    with pytest.raises(openai.AuthenticationError):
        openai.OpenAI(
            base_url='http://testserver/v1', api_key='wrong-key-0000000000', http_client=client, max_retries=0
        ).models.list()


def test_sdk_embeddings(client):
    r = sdk(client).embeddings.create(model='emb', input=['가나', '다'], encoding_format='float')
    assert [d.index for d in r.data] == [0, 1] and len(r.data[0].embedding) == 8


# ---- embeddings ----
def test_embeddings_float_and_base64_exact(client, reg):
    texts = ['수습 기간 해고', '연차']
    want = reg.fixed['emb'].encode(texts)
    f = client.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': texts}).json()
    assert np.array_equal(np.array([d['embedding'] for d in f['data']], dtype=np.float32), want)
    b = client.post(
        '/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': texts, 'encoding_format': 'base64'}
    ).json()
    got = np.stack([np.frombuffer(base64.b64decode(d['embedding']), dtype='<f4') for d in b['data']])
    assert np.array_equal(got, want)  # float32 비트 그대로


def test_embeddings_options_reach_encoder(client, reg):
    client.post(
        '/v1/embeddings',
        headers=AUTH,
        json={'model': 'emb', 'input': '질문', 'input_type': 'query', 'max_seq_length': 2304},
    )
    client.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': ['문서1', '문서2']})
    assert reg.fixed['emb'].calls[-2:] == [(['질문'], 'query', 2304), (['문서1', '문서2'], 'document', None)]


def test_embeddings_bad_requests(client):
    for body in (
        {'model': 'emb', 'input': []},
        {'model': 'emb', 'input': [[1, 2]]},  # 토큰 배열은 받지 않음
        {'model': 'emb', 'input': 'x', 'input_type': 'passage'},
        {'model': 'emb', 'input': 'x', 'dimensions': 4},
    ):
        assert client.post('/v1/embeddings', headers=AUTH, json=body).status_code == 400, body


# ---- rerank ----
def test_rerank_sorted_ties_by_index(client, reg):
    FakeReranker.scores = {'a': 0.5, 'b': 0.9, 'c': 0.9, 'd': 0.1}
    r = client.post(
        '/v1/rerank',
        headers=AUTH,
        json={'model': 'rr', 'query': '질문', 'documents': ['a', 'b', 'c', 'd'], 'instruction': '법령 검색'},
    ).json()
    assert [(x['index'], x['relevance_score']) for x in r['results']] == [(1, 0.9), (2, 0.9), (0, 0.5), (3, 0.1)]
    assert reg.fixed['rr'].calls[-1] == ('질문', ['a', 'b', 'c', 'd'], '법령 검색')
    top = client.post(
        '/v1/rerank',
        headers=AUTH,
        json={'model': 'rr', 'query': 'q', 'documents': ['a', 'b', 'c'], 'top_n': 1, 'return_documents': True},
    ).json()['results']
    assert top == [{'index': 1, 'relevance_score': 0.9, 'document': {'text': 'b'}}]


def test_rerank_errors(client, reg):
    assert (
        client.post('/v1/rerank', headers=AUTH, json={'model': 'rr', 'query': '', 'documents': ['a']}).status_code
        == 400
    )
    assert (
        client.post('/v1/rerank', headers=AUTH, json={'model': 'rr', 'query': 'q', 'documents': []}).status_code == 400
    )

    def bad(*a):
        raise RuntimeError('리랭커 점수가 올바르지 않습니다')

    reg.fixed['rr'].score = bad
    r = client.post('/v1/rerank', headers=AUTH, json={'model': 'rr', 'query': 'q', 'documents': ['a']})
    assert r.status_code == 500 and r.json()['error']['code'] == 'RuntimeError'


# ---- 모델 연산은 GPU 스레드 하나 ----
def test_model_calls_never_overlap(reg):
    """여러 요청이 동시에 와도 모델 연산은 겹치지 않고 한 스레드에서만 돈다 (MLX·MPS)"""
    FakeChat.delay = 0.01
    with TestClient(api.create_app(TOKEN, reg)) as c:

        def chat():
            c.post('/v1/chat/completions', headers=AUTH, json={'model': 'chat-a', 'messages': MESSAGES})

        def embed():
            for _ in range(5):
                c.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x'})
                time.sleep(0.005)

        ts = [threading.Thread(target=f) for f in (chat, embed, chat, embed)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
    assert Compute.overlaps == 0 and len(Compute.threads) == 1
    assert len(reg.fixed['emb'].calls) == 10 and len(reg.chat['chat-a'].calls) == 2


def test_package_does_not_import_chatbot_or_ui():
    code = (
        'import sys, lawbot_model_server.api, lawbot_model_server.server, lawbot_model_server.check; '
        'print([m for m in ("lawbot", "gradio", "pandas") if m in sys.modules])'
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == '[]'
