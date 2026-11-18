# services/model-server/tests/test_ms_api.py  →  OpenAI 호환 API (가짜 모델 + FastAPI TestClient)
# 형식은 OpenAI 공식 SDK로도 확인한다 (클라우드와 같은 코드로 쓸 수 있어야 함)
import base64
import subprocess
import sys
import threading
import time

import numpy as np
import openai
import pytest
from fastapi.testclient import TestClient

from lawbot_model_server import api

from .ms_fakes import AUTH, TOKEN, Compute, FakeEmbedder, FakeReranker, registry


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


# ---- 인증·준비·크기 ----
@pytest.mark.parametrize('path', ['/health', '/v1/models'])
def test_token_required_everywhere(client, path):
    for headers in ({}, {'Authorization': 'Bearer wrong-token-000000'}, {'Authorization': TOKEN}):
        r = client.get(path, headers=headers)
        assert r.status_code == 401 and r.json()['error']['type'] == 'authentication_error'
        assert TOKEN not in r.text


@pytest.mark.parametrize(
    'path,body',
    [
        ('/v1/embeddings', {'model': 'emb', 'input': 'x'}),
        ('/v1/rerank', {'model': 'rr', 'query': 'q', 'documents': ['x']}),
    ],
)
def test_post_requires_token(client, path, body):
    for headers in ({}, {'Authorization': TOKEN}, {'Authorization': 'Bearer wrong-token-000000'}):
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 401
        assert response.json()['error']['type'] == 'authentication_error'


def test_not_ready_until_registry_set():
    app = api.create_app(TOKEN)
    with TestClient(app) as c:
        assert c.get('/health', headers=AUTH).json() == {'ready': False, 'commit': None}
        r = c.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x'})
        assert r.status_code == 503 and r.json()['error']['type'] == 'server_not_ready'
        app.state.registry = registry()
        assert c.get('/health', headers=AUTH).json() == {'ready': True, 'commit': None}


def test_body_too_large():
    with TestClient(api.create_app(TOKEN, registry(), max_body=100)) as c:
        r = c.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x' * 200})
        assert r.status_code == 413


def test_no_docs_endpoints(client):
    for path in ('/docs', '/openapi.json', '/redoc'):
        assert client.get(path, headers=AUTH).status_code == 404


def test_chat_route_is_not_served(client):
    response = client.post(
        '/v1/chat/completions',
        headers=AUTH,
        json={'model': 'chat-a', 'messages': [{'role': 'user', 'content': 'hello'}]},
    )
    assert response.status_code == 404


def test_models_are_embedding_and_rerank_only(client):
    data = client.get('/v1/models', headers=AUTH).json()['data']
    assert [(model['id'], model['type']) for model in data] == [('emb', 'embedding'), ('rr', 'rerank')]


def test_models_listing(client, reg):
    data = client.get('/v1/models', headers=AUTH).json()['data']
    assert [(m['id'], m['type'], m['loaded']) for m in data] == [
        ('emb', 'embedding', True),
        ('rr', 'rerank', True),
    ]
    assert all(m['object'] == 'model' for m in data)
    assert [m.id for m in sdk(client).models.list()] == ['emb', 'rr']


def test_bad_json(client):
    r = client.post('/v1/embeddings', headers=AUTH, content=b'{not json')
    assert r.status_code == 400 and 'JSON' in r.json()['error']['message']


def test_unknown_or_wrong_kind_model(client):
    for model in ('없음', 'rr'):
        r = client.post('/v1/embeddings', headers=AUTH, json={'model': model, 'input': 'x'})
        assert r.status_code == 404 and r.json()['error']['code'] == 'model_not_found'
    r = client.post('/v1/rerank', headers=AUTH, json={'model': 'emb', 'query': 'q', 'documents': ['x']})
    assert r.status_code == 404 and r.json()['error']['code'] == 'model_not_found'


def test_sdk_authentication_error(client):
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
    FakeEmbedder.delay = 0.01
    with TestClient(api.create_app(TOKEN, reg)) as c:

        def rank():
            response = c.post('/v1/rerank', headers=AUTH, json={'model': 'rr', 'query': 'q', 'documents': ['x']})
            assert response.status_code == 200

        def embed():
            for _ in range(5):
                response = c.post('/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x'})
                assert response.status_code == 200
                time.sleep(0.005)

        ts = [threading.Thread(target=f) for f in (rank, embed, rank, embed)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
    assert Compute.overlaps == 0 and len(Compute.threads) == 1
    assert len(reg.fixed['emb'].calls) == 10 and len(reg.fixed['rr'].calls) == 2


def test_package_does_not_import_chatbot_or_ui():
    code = (
        'import sys, lawbot_model_server.api, lawbot_model_server.server, lawbot_model_server.check; '
        'print([m for m in ("lawbot", "gradio", "pandas") if m in sys.modules])'
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == '[]'


def test_health_has_no_generation_runtime(client):
    assert client.get('/health?runtime=1').status_code == 401
    assert client.get('/health?runtime=1', headers=AUTH).json() == {'ready': True, 'commit': None}
