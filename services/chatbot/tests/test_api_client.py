# tests/test_api_client.py  →  모델 서버 클라이언트(api_client)·생성 API 주소 설정(security.llm_endpoint)과
# 두 서비스 계약: 챗봇의 실제 클라이언트·openai SDK가 실제 모델 서버 앱(lawbot_model_server.api, 가짜 모델)과 통한다
import base64
import types

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

import lawbot.api_client as ac
import lawbot.app as app_mod
import lawbot.service as service
from conftest import TEST_TOKEN
from lawbot import security
from lawbot_model_server import api as server_api
from lawbot_model_server.config import ModelEntry
from lawbot_model_server.registry import Registry


# ---- api_client (httpx MockTransport) ----
def mock_client(handler):
    return ac.ModelServerClient(
        'http://127.0.0.1:9',
        TEST_TOKEN,
        http=httpx.Client(base_url='http://127.0.0.1:9', transport=httpx.MockTransport(handler)),
    )


def test_embed_base64_exact_and_request_fields():
    want = np.array([[0.1, 1 / 3, -2.5], [1e-8, 0.0, 7.25]], dtype=np.float32)
    seen = {}

    def handler(req):
        seen['auth'], seen['body'] = req.headers['authorization'], req.read()
        data = [
            {'index': i, 'embedding': base64.b64encode(row.astype('<f4').tobytes()).decode()}
            for i, row in enumerate(want)
        ]
        return httpx.Response(200, json={'data': data[::-1]})  # 순서가 바뀌어 와도 index로 맞춤

    got = mock_client(handler).embed('emb', ['가', '나'], 'query', 2304)
    assert got.dtype == np.float32 and np.array_equal(got, want)
    assert seen['auth'] == f'Bearer {TEST_TOKEN}'
    import json

    assert json.loads(seen['body']) == {
        'model': 'emb',
        'input': ['가', '나'],
        'encoding_format': 'base64',
        'input_type': 'query',
        'max_seq_length': 2304,
    }


def test_rerank_maps_scores_to_input_order():
    def handler(req):
        return httpx.Response(
            200,
            json={
                'results': [
                    {'index': 2, 'relevance_score': 0.9},
                    {'index': 0, 'relevance_score': 0.5},
                    {'index': 1, 'relevance_score': 0.1},
                ]
            },
        )

    assert mock_client(handler).rerank('rr', 'q', ['a', 'b', 'c'], 'TASK') == [0.5, 0.1, 0.9]

    def partial(req):
        return httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': 0.5}]})

    with pytest.raises(ac.ModelServerError, match='빠진 문서'):
        mock_client(partial).rerank('rr', 'q', ['a', 'b'])


@pytest.mark.parametrize(
    'status,body,exc,match',
    [
        (401, {'error': {'message': 'x', 'type': 'authentication_error'}}, ac.ModelServerError, 'LAWBOT_MODEL_TOKEN'),
        (503, {'error': {'message': '불러오는 중', 'type': 'server_not_ready'}}, ac.ModelServerError, '불러오는 중'),
        (
            500,
            {'error': {'message': '폴더 없음', 'type': 'model_load_error', 'code': 'FileNotFoundError'}},
            ac.ModelLoadError,
            '폴더 없음',
        ),
        (502, None, ac.ModelServerError, '502'),
    ],
)
def test_errors(status, body, exc, match):
    def handler(req):
        return httpx.Response(status, json=body) if body else httpx.Response(status, text='bad gateway')

    with pytest.raises(exc, match=match) as e:
        mock_client(handler).models()
    if exc is ac.ModelServerError:
        assert e.value.status == status
    assert TEST_TOKEN not in str(e.value)


def test_connection_error_is_connection_error():
    def handler(req):
        raise httpx.ConnectError('refused')

    with pytest.raises(ConnectionError, match='모델 서버에 연결할 수 없습니다'):
        mock_client(handler).ready()


def test_proxy_env_is_ignored(env):
    # 프록시 환경 변수가 있어도 127.0.0.1 모델 서버에 직접 접속 (질문이 프록시로 나가지 않음)
    env.setenv('HTTP_PROXY', 'http://10.255.255.1:3128')
    env.setenv('ALL_PROXY', 'http://10.255.255.1:3128')
    c = ac.ModelServerClient('http://127.0.0.1:9', TEST_TOKEN)
    assert c.http._trust_env is False
    assert c.http.timeout.connect == 5.0 and c.http.timeout.read is None


def test_wait_ready(env):
    env.setattr(ac, 'WAIT_INTERVAL', 0.01)
    state = {'n': 0}

    class C:
        def ready(self):
            state['n'] += 1
            if state['n'] == 1:
                raise ConnectionError('아직 안 뜸')
            return state['n'] >= 3

    ac.wait_ready(C(), 5)
    assert state['n'] == 3

    class Never:
        def ready(self):
            return False

    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_SERVER_WAIT'):
        ac.wait_ready(Never(), 0.05)


# ---- 생성 API 주소 ----
SERVER = 'http://127.0.0.1:65535'


def test_llm_endpoint_default_is_model_server(env):
    assert security.llm_endpoint(SERVER) == (f'{SERVER}/v1', TEST_TOKEN, False)


def test_llm_endpoint_external_needs_opt_in(env):
    env.setenv('LAWBOT_LLM_BASE_URL', 'https://api.example.com/v1')
    with pytest.raises(ValueError, match='LAWBOT_LLM_ALLOW_EXTERNAL=1'):
        security.llm_endpoint(SERVER)
    env.setenv('LAWBOT_LLM_ALLOW_EXTERNAL', '1')
    with pytest.raises(RuntimeError, match='LAWBOT_LLM_API_KEY'):
        security.llm_endpoint(SERVER)  # 외부에 모델 서버 토큰을 보내지 않음
    env.setenv('LAWBOT_LLM_API_KEY', 'sk-test-only')
    assert security.llm_endpoint(SERVER) == ('https://api.example.com/v1', 'sk-test-only', True)
    env.setenv('LAWBOT_LLM_BASE_URL', 'http://api.example.com/v1')
    with pytest.raises(ValueError, match='https만'):
        security.llm_endpoint(SERVER)


def test_llm_endpoint_other_local_port(env):
    env.setenv('LAWBOT_LLM_BASE_URL', 'http://127.0.0.1:65534/v1/')
    assert security.llm_endpoint(SERVER) == ('http://127.0.0.1:65534/v1', TEST_TOKEN, False)
    env.setenv('LAWBOT_LLM_BASE_URL', 'https://127.0.0.1/v1')
    with pytest.raises(ValueError, match=r'http://127\.0\.0\.1:<포트>/v1'):
        security.llm_endpoint(SERVER)
    env.setenv('LAWBOT_LLM_BASE_URL', 'https://user:pw@api.example.com/v1')
    with pytest.raises(ValueError, match='형식 오류'):
        security.llm_endpoint(SERVER)


def test_load_app_requires_model_server(env):
    env.delenv('LAWBOT_MODEL_SERVER')
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_SERVER'):
        service.load_app()


def test_external_llm_skips_server_chat_models(env, model_server):
    from fakes import FakeLLM, FakeRetriever

    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setenv('LAWBOT_LLM_BASE_URL', 'https://api.example.com/v1')
    env.setenv('LAWBOT_LLM_ALLOW_EXTERNAL', '1')
    env.setenv('LAWBOT_LLM_API_KEY', 'sk-test-only')
    env.setenv('LAWBOT_PRO_MODEL', 'gpt-cloud')
    model_server.available = [{'id': 'lawbot-emb-v1', 'type': 'embedding'}]  # 서버에는 임베딩만
    a = service.load_app()
    assert a.llm_mode == 'external' and a.models.is_loaded('pro')  # 외부 API는 '불러오는 중' 단계 없음
    llm = a.models.get('pro')[0]
    assert llm.path == 'gpt-cloud' and llm.client.kw['base_url'] == 'https://api.example.com/v1'
    assert llm.client.kw['api_key'] == 'sk-test-only' and llm.client.kw['http_client']._trust_env is True


# ---- 두 서비스 계약: 챗봇 클라이언트 ↔ 실제 모델 서버 앱 (가짜 모델) ----
class Chat:
    def __init__(self, path):
        self.calls = []

    def stream(self, messages, **kw):
        self.calls.append((messages, kw))
        text = '【결론】\n예고해야 합니다.\n\n【근거 조문】\n제26조(해고의 예고)'
        for i, c in enumerate((text[:8], text[8:])):
            yield types.SimpleNamespace(
                text=c,
                prompt_tokens=50,
                generation_tokens=i + 1,
                prompt_tps=500.0,
                generation_tps=25.0,
                peak_memory=3.25,
                finish_reason='stop' if i else None,
            )


class Emb:
    def __init__(self, path):
        self.calls = []

    def encode(self, texts, input_type='document', max_seq_length=None):
        self.calls.append((len(texts), input_type, max_seq_length))
        v = np.zeros((len(texts), 64), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 64] += 1 / 3
        return v


class Rr:
    def __init__(self, path):
        self.calls = []

    def score(self, query, docs, instruction=None):
        self.calls.append(instruction)
        return [1 / (1 + len(d)) for d in docs]


@pytest.fixture
def real_server(env):
    """lawbot_model_server.api 앱 (v1 프로필이 쓰는 모델 id, 가짜 모델) + 챗봇이 이 앱으로 접속하게"""
    entries = [
        ModelEntry('lawbot-emb-v1', 'embedding', None),
        ModelEntry('qwen3-reranker-4b', 'rerank', None),
        ModelEntry('lawbot-fast-v1', 'chat', None),
        ModelEntry('qwen3-14b-4bit', 'chat', None),
    ]
    reg = Registry(entries, {'chat': Chat, 'embedding': Emb, 'rerank': Rr}, max_resident=1)
    reg.load_fixed()
    tc = TestClient(server_api.create_app(TEST_TOKEN, reg))
    real_client = ac.ModelServerClient
    env.setattr(service, 'ModelServerClient', lambda url, token: real_client(url, token, http=tc))
    env.setattr(service, 'OpenAI', lambda **kw: OpenAI(**(kw | {'http_client': tc})))
    with tc:
        yield reg


def test_end_to_end_through_real_server_app(env, real_server, tmp_path):
    env.setenv('LAWBOT_REQUEST_LOG', str(tmp_path / 'requests.jsonl'))
    a = service.load_app()  # v1: 130조문 인덱스를 서버 임베딩으로
    emb = real_server.fixed['lawbot-emb-v1']
    assert emb.calls == [(130, 'document', 512)]
    assert len(a.retriever.ids) == 130 and a.retriever.D.shape == (130, 64)
    assert not a.models.is_loaded('fast')  # 서버에 아직 올라 있지 않음
    steps = list(service.ask(a, '수습 기간에도 해고 예고를 해야 하나요?', 'fast'))
    assert steps[0][0] == 'load' and steps[-1][0] == 'done'
    _, hits, raw, st = steps[-1]
    assert raw == '【결론】\n예고해야 합니다.\n\n【근거 조문】\n제26조(해고의 예고)'
    assert (st['prompt_tokens'], st['gen_tokens'], st['tps'], st['peak_gb'], st['finish']) == (
        50,
        2,
        25.0,
        3.25,
        'stop',
    )
    assert emb.calls[-1] == (1, 'query', 512)
    messages, kw = real_server.chat['lawbot-fast-v1'].calls[0]
    assert messages[1]['content'].startswith('[참고 조문]\n[1] ') and messages[1]['content'].endswith(
        '[질문]\n수습 기간에도 해고 예고를 해야 하나요?'
    )
    assert kw == {'max_tokens': 1024, 'temperature': 0.0, 'top_p': None, 'enable_thinking': False}
    assert a.models.is_loaded('fast')
    text, src, stats = list(app_mod.make_chat(a)('해고 예고?', [], 'fast', 'concise'))[-1]
    assert text.startswith('예고해야 합니다.') and '25.0 tok/s' in stats


def test_reranker_through_real_server_app(env, real_server):
    from lawbot.reranker import TASK, Reranker

    client = service.ModelServerClient('http://127.0.0.1:65535', TEST_TOKEN)
    cands = [{'doc': d, 'article_id': d} for d in ('긴 문서입니다 정말 긴', '짧음', '중간 길이')]
    out = Reranker(client, 'qwen3-reranker-4b').rerank('질문', cands, top_k=3)
    assert [h['doc'] for h in out] == ['짧음', '중간 길이', '긴 문서입니다 정말 긴']
    assert real_server.fixed['qwen3-reranker-4b'].calls == [TASK]


def test_server_errors_reach_web_app_as_known_types(env, real_server):
    a = service.load_app()

    def broken(path):
        raise FileNotFoundError('모델 폴더 없음')

    real_server.loaders['chat'] = broken
    with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
        list(app_mod.make_chat(a)('질문', [], 'pro', 'full'))
    env.setenv('LAWBOT_PRO_MODEL', '없는-모델')
    with pytest.raises(RuntimeError, match='chat 없는-모델'):  # 시작할 때 /v1/models로 확인
        service.load_app()
