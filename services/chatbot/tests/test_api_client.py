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


# ---- 질문 처리 중 호출의 시간 상한 (LAWBOT_MODEL_READ_TIMEOUT) ----
def test_read_timeout_is_model_server_timeout():
    def slow(req):
        raise httpx.ReadTimeout('slow', request=req)

    c = mock_client(slow)
    with pytest.raises(ac.ModelServerTimeout, match='300초'):
        c.embed('emb', ['q'], 'query', timeout=ac.request_timeout(300))
    with pytest.raises(ac.ModelServerTimeout):
        c.rerank('rr', 'q', ['d'], timeout=ac.request_timeout(300))


def test_connect_timeout_stays_connection_error():
    def down(req):
        raise httpx.ConnectTimeout('down', request=req)

    with pytest.raises(ConnectionError, match='연결할 수 없습니다'):
        mock_client(down).embed('emb', ['q'], 'query', timeout=ac.request_timeout(300))


def test_timeout_only_on_question_calls():
    # 시작할 때의 조문 인덱스 임베딩은 오래 걸려 제한 없음, 질문 임베딩·리랭크에만 상한
    seen = []

    def handler(req):
        seen.append((req.url.path, req.extensions['timeout']))
        if req.url.path == '/v1/rerank':
            return httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': 0.5}]})
        vec = base64.b64encode(np.zeros(4, '<f4').tobytes()).decode()
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': vec}]})

    c = mock_client(handler)
    emb = ac.Embedder(c, 'emb', query_timeout=ac.request_timeout(120))
    emb.encode(['조문'], 'document')
    emb.encode(['질문'], 'query')
    from lawbot.reranker import Reranker

    Reranker(c, 'rr', timeout=ac.request_timeout(120)).score('질문', ['조문'])
    (_, doc), (_, query), (_, rr) = seen
    assert doc['read'] is None and query['read'] == rr['read'] == 120 and query['connect'] == 5


def test_read_timeout_setting(env):
    assert service.read_timeout() == 300  # 기본
    env.setenv('LAWBOT_MODEL_READ_TIMEOUT', '60')
    assert service.read_timeout() == 60
    for bad in ('0', '-1', 'x'):
        env.setenv('LAWBOT_MODEL_READ_TIMEOUT', bad)
        with pytest.raises(ValueError, match='LAWBOT_MODEL_READ_TIMEOUT'):
            service.read_timeout()


def test_load_app_applies_read_timeout(env):
    from fakes import FakeLLM, FakeRetriever

    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setenv('LAWBOT_MODEL_READ_TIMEOUT', '90')
    a = service.load_app()
    assert a.retriever.args[0][0].query_timeout.read == 90  # 질문 임베딩
    oa = a.models.get('fast')[0].client  # 생성 (openai SDK)
    assert oa.kw['timeout'].read == 90 and oa.kw['timeout'].connect == 5


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


def test_request_bodies_pass_server_validation():
    # 웹앱이 만드는 요청 본문을 모델 서버가 쓰는 계약 모델(lawbot_common.api)이 그대로 받는다
    from lawbot_common import api as contract

    bodies = []

    def handler(req):
        bodies.append((req.url.path, req.read()))
        if req.url.path == contract.RERANK_PATH:
            return httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': 0.5}]})
        vec = base64.b64encode(np.zeros(2, '<f4').tobytes()).decode()
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': vec}]})

    c = mock_client(handler)
    c.embed('emb', ['가'], 'query', 2304)
    c.embed('emb', ['가'])
    c.rerank('rr', 'q', ['a'], 'TASK')
    c.rerank('rr', 'q', ['a'])
    models = {contract.EMBEDDINGS_PATH: contract.EmbeddingRequest, contract.RERANK_PATH: contract.RerankRequest}
    for path, body in bodies:
        models[path].model_validate_json(body)  # 모르는 필드·타입 오류면 ValidationError


@pytest.mark.parametrize(
    'path,body',
    [
        ('/v1/models', {'object': 'list'}),  # data 없음
        ('/v1/models', {'data': [{'id': 'x', 'type': 'llm', 'loaded': True}]}),  # 모르는 type
        ('/v1/embeddings', {'data': [{'embedding': 'AAAA'}]}),  # index 없음
        ('/v1/rerank', {'results': [{'index': 0}]}),  # 점수 없음
        ('/health', {}),
    ],
)
def test_malformed_server_response_is_model_server_error(path, body):
    c = mock_client(lambda req: httpx.Response(200, json=body))
    call = {
        '/v1/models': c.models,
        '/v1/embeddings': lambda: c.embed('emb', ['가']),
        '/v1/rerank': lambda: c.rerank('rr', 'q', ['a']),
        '/health': c.ready,
    }[path]
    with pytest.raises(ac.ModelServerError, match='응답 형식'):
        call()


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
    model_server.available = [{'id': 'lawbot-emb-v4', 'type': 'embedding'}]  # 서버에는 임베딩만
    a = service.load_app()
    assert a.llm_mode == 'external' and a.models.is_loaded('pro')  # 외부 API는 '불러오는 중' 단계 없음
    llm = a.models.get('pro')[0]
    assert llm.path == 'gpt-cloud' and llm.client.kw['base_url'] == 'https://api.example.com/v1'
    assert llm.client.kw['api_key'] == 'sk-test-only' and llm.client.kw['http_client']._trust_env is True


# ---- 두 서비스 계약: 챗봇 클라이언트 ↔ 실제 모델 서버 앱 (가짜 모델) ----
class Engine:
    """모델 서버 생성 엔진(BatchEngine)과 같은 인터페이스. 요청마다 Chat.stream을 한 토큰씩"""

    def __init__(self, model):
        self.model, self.items = model, {}

    def __len__(self):
        return len(self.items)

    def tickets(self):
        return list(self.items)

    def add(self, ticket, messages, kw):
        self.items[ticket] = self.model.stream(messages, **kw)

    def step(self):
        events = []
        for t, it in list(self.items.items()):
            r = next(it, None)
            if r is None:
                del self.items[t]
                events.append((t, 'end', None))
            else:
                events.append((t, 'item', r))
        return events

    def remove(self, ticket):
        self.items.pop(ticket, None)

    def close(self):
        self.items.clear()


class Chat:
    def __init__(self, path):
        self.calls = []

    def engine(self, max_concurrent):
        return Engine(self)

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
    """lawbot_model_server.api 앱 (v4 프로필이 쓰는 모델 id, 가짜 모델) + 챗봇이 이 앱으로 접속하게"""
    entries = [
        ModelEntry('lawbot-emb-v4', 'embedding', None),
        ModelEntry('qwen3-reranker-4b', 'rerank', None),
        ModelEntry('lawbot-fast-v4', 'chat', None),
        ModelEntry('qwen3.8-27b-4bit', 'chat', None),
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
    a = service.load_app()  # 415조문 인덱스를 서버 임베딩으로
    emb = real_server.fixed['lawbot-emb-v4']
    assert emb.calls == [(415, 'document', 2304)]
    assert len(a.retriever.ids) == 415 and a.retriever.D.shape == (415, 64)
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
    assert emb.calls[-1] == (1, 'query', 2304)
    messages, kw = real_server.chat['lawbot-fast-v4'].calls[0]
    assert messages[1]['content'].startswith('[참고 조문]\n[1] ') and messages[1]['content'].endswith(
        '[질문]\n수습 기간에도 해고 예고를 해야 하나요?'
    )
    assert kw == {'max_tokens': 1024, 'temperature': 0.0, 'top_p': None, 'enable_thinking': False}
    assert a.models.is_loaded('fast')
    text, src, stats = list(app_mod.make_chat(a)('해고 예고?', [], 'fast', 'concise'))[-1]
    assert text.startswith('예고해야 합니다.') and '25.0 tok/s' in stats


def test_request_id_reaches_model_server_log(env, real_server, capsys):
    # 질문 하나의 임베딩·리랭크·생성 요청이 같은 X-Request-Id를 보내고, 모델 서버 접근 로그 줄에 남는다 (질문 내용은 없음)

    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    a = service.load_app()
    capsys.readouterr()
    steps = list(service.ask(a, '수습 기간에도 해고 예고를 해야 하나요?', 'fast'))
    rid = steps[-1][3]['request_id']
    server = [line for line in capsys.readouterr().out.splitlines() if line.startswith('[model-server]')]
    paths = [line.split()[2] for line in server if ' POST ' in line and line.endswith(f'rid={rid}')]
    assert paths == ['/v1/embeddings', '/v1/rerank', '/v1/chat/completions']
    assert not any('수습' in line for line in server)  # 질문 내용은 모델 서버 로그에 없음
    assert all(line.endswith(f'rid={rid}') for line in server if ' /v1/' in line)  # 모델 목록 확인(GET)도 같은 id


def test_model_server_ignores_bad_request_id(env, real_server, capsys):
    c = TestClient(server_api.create_app(TEST_TOKEN, real_server))
    for rid in ('a b', 'x' * 33, '<script>', ''):
        c.get('/v1/models', headers={'Authorization': f'Bearer {TEST_TOKEN}', 'X-Request-Id': rid})
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith('[model-server] GET /v1/models')]
    assert len(lines) == 4 and all(line.endswith('rid=-') for line in lines)


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
