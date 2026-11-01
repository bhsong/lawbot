# tests/test_pipeline.py  →  검색(Retriever)·생성(LLM: openai SDK 스트림 → 응답)·웹앱(app) 흐름 테스트
# 모델 서버 대신 가짜 인코더·가짜 OpenAI 클라이언트(SDK의 실제 응답 타입)를 쓴다: 네트워크 불필요
import subprocess
import sys
import types

import httpx
import numpy as np
import openai
import pandas as pd
import pytest
from openai.types.chat import ChatCompletionChunk

import lawbot.retriever as retriever_mod
import lawbot.service as service
from conftest import set_auth
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot.api_client import ModelLoadError, ModelServerError
from lawbot.llm import LLM, build_prompt
from lawbot.paths import V4_ARTICLES

SYSTEM = '테스트 4항목 system'


# ---- 검색 ----
class FakeEncoder:
    """api_client.Embedder 대역: 글자 해시 bag-of-chars. 같은 글자를 많이 공유할수록 유사도가 높다"""

    calls = []

    def encode(self, texts, input_type='document', max_seq_length=None):
        FakeEncoder.calls.append((len(texts), input_type, max_seq_length))
        v = np.zeros((len(texts), 512), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 512] += 1
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


@pytest.fixture
def retriever(env):
    FakeEncoder.calls = []
    return retriever_mod.Retriever(FakeEncoder(), V4_ARTICLES, 2304)


def test_retriever_indexes_only_current_articles(retriever):
    law = pd.read_csv(V4_ARTICLES, encoding='utf-8-sig')
    live = law[law['deleted'] == 0]
    assert len(retriever.ids) == 415 == len(retriever.docs)
    assert set(zip(retriever.laws, retriever.ids)) == set(zip(live['law'], live['article_no']))
    assert retriever.D.shape == (415, 512)
    assert FakeEncoder.calls == [(415, 'document', 2304)]  # 조문 목록 하나를 한 번에 (query 프롬프트 없이)


def test_search_returns_top_k_sorted(retriever):
    hits = retriever.search('수습 기간에도 해고 예고를 해야 하나요?', k=3)
    assert len(hits) == 3
    assert [set(h) for h in hits] == [{'article_id', 'title', 'doc', 'score', 'law'}] * 3
    scores = [h['score'] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert len({(h['law'], h['article_id']) for h in hits}) == 3
    for h in hits:
        assert h['doc'].startswith(f'{h["law"]} {h["article_id"]}({h["title"]})\n')
    assert FakeEncoder.calls[-1] == (1, 'query', 2304)  # 질문은 query 프롬프트, 조문과 같은 max_seq


def test_search_finds_exact_article(retriever):
    i = list(zip(retriever.laws, retriever.ids)).index(('근로기준법', '제26조'))
    top = retriever.search(retriever.docs[i], k=1)[0]
    assert (top['law'], top['article_id']) == ('근로기준법', '제26조')


def test_search_unrelated_query_still_returns_k(retriever):
    # 검색은 항상 k개를 돌려주고, 근거 부족 판단(거절)은 sLLM이 한다
    assert len(retriever.search('', k=3)) == 3
    assert len(retriever.search('오늘 점심 메뉴 추천해줘', k=5)) == 5


def test_rank_all_is_full_ranking(retriever):
    rank = retriever.rank_all(['연차 유급휴가', '최저임금'])
    assert rank.shape == (2, 415)
    assert all(sorted(r) == list(range(415)) for r in rank)


# ---- 생성 ----
def chunk(content=None, finish=None, usage=None, timings=None, role=False):
    """모델 서버가 보내는 SSE 청크와 같은 SDK 객체"""
    delta = ({'role': 'assistant'} if role else {}) | ({'content': content} if content is not None else {})
    d = {
        'id': 'chatcmpl-x',
        'object': 'chat.completion.chunk',
        'created': 0,
        'model': 'm',
        'choices': [] if usage else [{'index': 0, 'delta': delta, 'finish_reason': finish}],
    }
    if usage:
        d['usage'] = {'prompt_tokens': usage[0], 'completion_tokens': usage[1], 'total_tokens': sum(usage)}
    if timings:
        d['timings'] = timings
    return ChatCompletionChunk.model_validate(d)


SERVER_CHUNKS = [
    chunk('【결론】', role=True),
    chunk('답'),
    chunk(finish='length', timings={'prompt_tps': 100.0, 'generation_tps': 20.0, 'peak_memory_gb': 1.5}),
    chunk(usage=(11, 2)),
]


class FakeStream:
    def __init__(self, items):
        self.items, self.closed = items, False

    def __iter__(self):
        for x in self.items:
            if isinstance(x, Exception):
                raise x
            yield x

    def close(self):
        self.closed = True


class FakeClient:
    """openai.OpenAI 대역: chat.completions.create 인자를 기록하고 FakeStream을 돌려준다"""

    def __init__(self, items=SERVER_CHUNKS, error=None):
        self.calls, self.items, self.error, self.streams = [], items, error, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        self.streams.append(FakeStream(self.items))
        return self.streams[-1]


def test_llm_request_and_responses():
    c = FakeClient()
    m = LLM('qwen3.8-27b-4bit', client=c, system=SYSTEM)
    hits = HITS
    out = list(m.stream('해고 예고?', hits))
    assert ''.join(r.text for r in out) == '【결론】답' and [r.text for r in out] == ['【결론】', '답', '']
    last = out[-1]  # 측정값은 마지막 응답에 (service.ask가 읽음)
    assert (last.prompt_tokens, last.generation_tokens, last.finish_reason) == (11, 2, 'length')
    assert (last.prompt_tps, last.generation_tps, last.peak_memory) == (100.0, 20.0, 1.5)
    assert out[0].generation_tps is None  # 토큰 응답은 받는 즉시 (첫 토큰 시간 그대로)
    assert c.calls[0] == {
        'model': 'qwen3.8-27b-4bit',
        'messages': [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': build_prompt(HITS, '해고 예고?')},
        ],
        'max_tokens': 1024,
        'temperature': 0,
        'stream': True,
        'stream_options': {'include_usage': True},
    }
    assert c.streams[0].closed


def test_llm_prompt_and_system():
    # 프로필의 system 문자열과 법령명이 붙은 참고 조문, 개정 표시는 뺀다
    c = FakeClient()
    m = LLM('m', client=c, system='V4 SYSTEM')
    hits = [
        {
            'law': '최저임금법',
            'article_id': '제6조',
            'title': '최저임금의 효력',
            'doc': '최저임금법 제6조(최저임금의 효력)\n① 사용자는 지급하여야 한다. <개정 2020.5.26>',
        }
    ]
    list(m.stream('최저임금?', hits))
    messages = c.calls[-1]['messages']
    assert messages[0] == {'role': 'system', 'content': 'V4 SYSTEM'}
    assert messages[1]['content'] == (
        '[참고 조문]\n[1] 최저임금법 제6조(최저임금의 효력)\n① 사용자는 지급하여야 한다.\n\n[질문]\n최저임금?'
    )
    assert messages[1]['content'] == build_prompt(hits, '최저임금?')


def test_llm_system_override():
    c = FakeClient()
    m = LLM('m', client=c, system=SYSTEM)
    list(m.stream('질문', HITS[:1], system='4항목 SYSTEM'))
    assert c.calls[-1]['messages'][0] == {'role': 'system', 'content': '4항목 SYSTEM'}
    list(m.stream('질문', HITS[:1]))
    assert c.calls[-1]['messages'][0] == {'role': 'system', 'content': SYSTEM}  # 다음 요청은 기본 system


def test_llm_closing_stops_stream():
    # 정지 버튼: 생성기를 닫으면 SDK 스트림(HTTP 연결)을 닫는다 → 서버가 생성을 멈춤
    c = FakeClient([chunk('a', role=True)] + [chunk('b')] * 100)
    g = LLM('m', client=c, system=SYSTEM).stream('질문', HITS[:1])
    next(g)
    g.close()
    assert c.streams[0].closed


def test_llm_no_content_still_yields_one():
    out = list(
        LLM('m', client=FakeClient([chunk(finish='stop'), chunk(usage=(5, 0))]), system=SYSTEM).stream('q', HITS[:1])
    )
    assert len(out) == 1 and out[0].text == '' and out[0].finish_reason == 'stop'


REQ = httpx.Request('POST', 'http://127.0.0.1:9/v1/chat/completions')


def status_error(status, body):
    return openai.APIStatusError('x', response=httpx.Response(status, request=REQ, json=body), body=body.get('error'))


@pytest.mark.parametrize(
    'error,kind,match',
    [
        (openai.APIConnectionError(request=REQ), ConnectionError, '연결할 수 없습니다'),
        (
            status_error(
                500, {'error': {'message': '모델 폴더 없음', 'type': 'model_load_error', 'code': 'FileNotFoundError'}}
            ),
            ModelLoadError,
            '모델 폴더 없음',
        ),
        (status_error(401, {'error': {'message': 'x', 'type': 'authentication_error'}}), ModelServerError, '토큰'),
        (
            status_error(
                404, {'error': {'message': '없는 모델', 'type': 'invalid_request_error', 'code': 'model_not_found'}}
            ),
            ModelServerError,
            '없는 모델',
        ),
    ],
)
def test_llm_errors_before_stream(error, kind, match):
    with pytest.raises(kind, match=match):
        list(LLM('m', client=FakeClient(error=error), system=SYSTEM).stream('q', HITS[:1]))


def test_llm_error_mid_stream():
    err = openai.APIError('생성 실패', request=REQ, body={'message': '생성 실패'})
    c = FakeClient([chunk('【결론】', role=True), err])
    g = LLM('m', client=c, system=SYSTEM).stream('q', HITS[:1])
    assert next(g).text == '【결론】'
    with pytest.raises(RuntimeError, match='생성 오류: 생성 실패'):
        next(g)
    assert c.streams[0].closed


# ---- 질의응답 흐름 (service) ----
@pytest.fixture
def fake_models(env):
    FakeRetriever.created = FakeLLM.created = 0
    FakeLLM.chunks = ['【결론】', '예고해야', ' 합니다.']
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)


def test_service_does_not_import_gradio():
    # 핵심 흐름은 UI와 독립: 다른 UI·API 서버가 gradio 없이 가져다 쓸 수 있어야 함
    code = 'import sys, lawbot.service; print("gradio" in sys.modules)'
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == 'False'


# ---- 웹앱 ----
@pytest.fixture
def app(env, fake_models):
    """가짜 모델을 쓰는 lawbot.app. launch는 실제 서버 대신 설정만 기록한다"""
    import lawbot.app as app

    env.setattr(app, 'launched', [], raising=False)  # 테스트가 끝나면 지워짐
    env.setattr(app.gr.Blocks, 'launch', lambda demo, **kw: app.launched.append((demo, kw)))
    return app


def test_import_does_not_check_auth_or_load_models(env):
    # import만으로는 인증 검사·모델 로딩을 하지 않는다 (둘 다 main에서)
    FakeRetriever.created = FakeLLM.created = 0
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    sys.modules.pop('lawbot.app', None)
    try:
        import lawbot.app

        assert not hasattr(lawbot.app, 'demo')
        assert FakeRetriever.created == 0 and FakeLLM.created == 0
    finally:
        sys.modules.pop('lawbot.app', None)


def test_build_demo_with_fakes_needs_no_auth(app):
    lawbot = service.load_app()
    FakeRetriever.created = FakeLLM.created = 0
    demo = app.build_demo(lawbot)
    assert isinstance(demo, app.gr.Blocks) and demo.title == '노동법 챗봇'
    assert FakeRetriever.created == 0 and FakeLLM.created == 0  # 넘겨준 객체 외에 모델을 만들지 않음
    assert app.launched == []


def test_app_refuses_to_start_without_auth_before_loading_models(app):
    with pytest.raises(RuntimeError, match='LAWBOT_USER'):
        app.main()
    assert FakeRetriever.created == 0 and FakeLLM.created == 0
    assert app.launched == []


def test_app_launches_local_only(app, env):
    set_auth(env)
    env.setenv('GRADIO_SERVER_NAME', '0.0.0.0')
    env.setenv('GRADIO_SHARE', 'True')
    app.main()
    [(demo, launched)] = app.launched
    assert demo.title == '노동법 챗봇'
    assert launched == {
        'server_name': '127.0.0.1',
        'server_port': 7860,
        'auth': ('tester', 'test-only-pw'),
        'share': False,
    }
    assert FakeRetriever.created == 1 and FakeLLM.created == 0  # 검색기 1개. 답변 모델 클라이언트는 첫 질문 때


def test_app_loads_models_via_service(app, env):
    # main은 service.load_app으로 만든 검색기·모델(LAWBOT_FAST_MODEL 반영)을 화면에 넘긴다
    set_auth(env)
    env.setenv('LAWBOT_FAST_MODEL', 'lawbot-fast-8bit')
    build, apps = app.build_demo, []
    env.setattr(app, 'build_demo', lambda a: apps.append(a) or build(a))
    app.main()
    [a] = apps
    assert isinstance(a.retriever, FakeRetriever)
    assert a.models.get('fast')[0].path == 'lawbot-fast-8bit'  # FakeLLM의 첫 인자 = API 모델 id
    assert len(app.launched) == 1


def test_chat_streams_answer_with_sources(app):
    lawbot = service.load_app()
    chat = app.make_chat(lawbot)
    assert chat.__name__ == 'chat'  # Gradio API 이름(/gradio_api/call/chat)
    steps = list(chat('수습 기간에도 해고 예고?', [], 'fast', 'full'))
    # 먼저 처리 중 문구(조문 없음), 그다음 토큰마다 누적 답변, 마지막은 생성 완료 후 측정값(완료 시간·tok/s)을 채운 같은 답변
    assert steps[0] == (app.SEARCHING, '', '⏳ 조문 검색 중…')
    steps = steps[1:]
    assert [t for t, _, _ in steps] == [
        '【결론】',
        '【결론】예고해야',
        '【결론】예고해야 합니다.',
        '【결론】예고해야 합니다.',
    ]
    src = steps[-1][1]
    assert all(s == src for _, s, _ in steps)  # 근거 조문 칸은 답변 내내 같음 (인용이 없으면 끝나도 세 개 모두 펼침)
    assert '**근로기준법 제26조 해고의 예고** · 유사도 0.912' in src
    assert '30일 전에 예고' in src
    assert src.count('제26조 해고의 예고') == 1  # 조문 본문 첫 줄(제목 줄)은 중복 표시 안 함
    assert lawbot.retriever.query == '수습 기간에도 해고 예고?'
    assert lawbot.models.llms['fast'].args == ('수습 기간에도 해고 예고?', HITS)
    assert steps[-1][2].startswith('Lawbot Fast · 검색') and '완료' in steps[-1][2]


def test_chat_generation_failure_is_not_hidden(app):
    # 생성 실패를 빈 답변이나 부분 답변으로 끝내지 않고 예외로 올린다 (Gradio가 오류로 표시)
    FakeLLM.chunks = ['【결론】', RuntimeError('generation failed')]
    chat = app.make_chat(service.load_app())
    with pytest.raises(RuntimeError, match='generation failed'):
        list(chat('질문', [], 'fast', 'full'))
