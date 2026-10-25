# tests/test_pipeline.py  →  검색(Retriever)·생성(LLM)·웹앱(app) 흐름 테스트
# 실제 임베딩·sLLM 대신 가짜 모델을 넣는다: 모델 파일·MPS·Metal·네트워크 불필요
import subprocess
import sys
import types
import numpy as np
import pandas as pd
import pytest
import lawbot.llm as llm_mod
import lawbot.retriever as retriever_mod
import lawbot.service as service
from conftest import set_auth
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot.llm import LLM, SYSTEM, build_prompt, build_prompt_v4
from lawbot.paths import ARTICLES, EVAL

# ---- 검색 ----
class FakeEncoder:
    """글자 해시 bag-of-chars 임베딩. 같은 글자를 많이 공유할수록 유사도가 높다"""
    calls = []

    def __init__(self, path, device=None, model_kwargs=None):
        self.path = path

    def encode(self, texts, prompt_name=None, normalize_embeddings=False, **kw):
        FakeEncoder.calls.append((len(texts), prompt_name))
        v = np.zeros((len(texts), 512), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 512] += 1
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

@pytest.fixture
def retriever(env):
    FakeEncoder.calls = []
    env.setattr(retriever_mod, 'SentenceTransformer', FakeEncoder)
    env.setattr(retriever_mod.torch.mps, 'empty_cache', lambda: None)
    return retriever_mod.Retriever()

def test_retriever_indexes_only_current_articles(retriever):
    law = pd.read_csv(ARTICLES, encoding='utf-8-sig')
    live = law.loc[law['deleted'] == 0, 'article_id']
    assert len(retriever.ids) == 130 == len(retriever.docs)
    assert set(retriever.ids) == set(live)
    assert retriever.D.shape == (130, 512)
    assert FakeEncoder.calls[0] == (130, None)    # 조문은 query 프롬프트 없이 인코딩

def test_search_returns_top_k_sorted(retriever):
    hits = retriever.search('수습 기간에도 해고 예고를 해야 하나요?', k=3)
    assert len(hits) == 3
    assert [set(h) for h in hits] == [{'article_id', 'title', 'doc', 'score'}] * 3
    scores = [h['score'] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert len({h['article_id'] for h in hits}) == 3
    for h in hits:
        assert h['doc'].startswith(f"{h['article_id']} {h['title']}\n")
    assert FakeEncoder.calls[-1] == (1, 'query')  # 질문은 query 프롬프트로 인코딩

def test_search_finds_exact_article(retriever):
    i = list(retriever.ids).index('제26조')
    assert retriever.search(retriever.docs[i], k=1)[0]['article_id'] == '제26조'

def test_search_unrelated_query_still_returns_k(retriever):
    # 검색은 항상 k개를 돌려주고, 근거 부족 판단(거절)은 sLLM이 한다
    assert len(retriever.search('', k=3)) == 3
    assert len(retriever.search('오늘 점심 메뉴 추천해줘', k=5)) == 5

def test_rank_all_is_full_ranking(retriever):
    rank = retriever.rank_all(['연차 유급휴가', '최저임금'])
    assert rank.shape == (2, 130)
    assert all(sorted(r) == list(range(130)) for r in rank)

# ---- 생성 ----
def test_prompt_matches_training_format():
    # 평가 파일의 oracle_prompt는 학습 때 쓴 형식: 650문항 모두 글자 단위로 같아야 함
    ev = pd.read_csv(EVAL, encoding='utf-8-sig', dtype=str)
    for r in ev.itertuples():
        assert build_prompt(r.oracle_context_ids.split('|'), r.question) == r.oracle_prompt, r.id

def test_build_prompt_unknown_article_fails():
    with pytest.raises(KeyError):
        build_prompt(['제999조'], '질문')

class FakeTokenizer:
    def __init__(self):
        self.calls = []

    def apply_chat_template(self, messages, **kw):
        self.calls.append((messages, kw))
        return 'PROMPT'

@pytest.fixture
def fake_llm(env):
    """mlx_lm.load·stream_generate를 가짜로 바꾼 LLM. gen에 생성 함수를 넣어 동작을 바꾼다"""
    tok, seen = FakeTokenizer(), {}
    def load(path):
        seen['path'] = path
        return 'MODEL', tok
    env.setattr(llm_mod, 'load', load)
    def stream_generate(model, tk, prompt, max_tokens, sampler):
        seen.update(model=model, prompt=prompt, max_tokens=max_tokens)
        yield from seen['gen']()
    env.setattr(llm_mod, 'stream_generate', stream_generate)
    seen['gen'] = lambda: iter([types.SimpleNamespace(text='【결론】'), types.SimpleNamespace(text='답')])
    return LLM('models/fake'), tok, seen

def test_llm_stream_success(fake_llm):
    m, tok, seen = fake_llm
    hits = [{'article_id': '제26조'}, {'article_id': '제36조'}]
    out = ''.join(r.text for r in m.stream('해고 예고?', hits))
    assert out == '【결론】답'
    assert seen['path'] == 'models/fake' and seen['prompt'] == 'PROMPT'
    messages, kw = tok.calls[0]
    assert messages == [{'role': 'system', 'content': SYSTEM},
                        {'role': 'user', 'content': build_prompt(['제26조', '제36조'], '해고 예고?')}]
    assert kw['add_generation_prompt'] is True and kw['enable_thinking'] is False

def test_llm_stream_failure_propagates(fake_llm):
    m, _, seen = fake_llm
    def broken():
        yield types.SimpleNamespace(text='【결론】')
        raise RuntimeError('generation failed')
    seen['gen'] = broken
    with pytest.raises(RuntimeError, match='generation failed'):
        ''.join(r.text for r in m.stream('질문', [{'article_id': '제1조'}]))

def test_llm_v4_prompt_and_system(fake_llm, env):
    # v4: 프로필의 system 문자열과 법령명이 붙은 참고 조문을 쓴다 (v1 기본값은 위 테스트)
    _, tok, _ = fake_llm
    m = LLM('models/fake', system='V4 SYSTEM', prompt_style='v4')
    hits = [{'law': '최저임금법', 'article_id': '제6조', 'title': '최저임금의 효력',
             'doc': '최저임금법 제6조(최저임금의 효력)\n① 사용자는 지급하여야 한다. <개정 2020.5.26>'}]
    list(m.stream('최저임금?', hits))
    messages, _ = tok.calls[-1]
    assert messages[0] == {'role': 'system', 'content': 'V4 SYSTEM'}
    assert messages[1]['content'] == ('[참고 조문]\n[1] 최저임금법 제6조(최저임금의 효력)\n① 사용자는 지급하여야 한다.'
                                      '\n\n[질문]\n최저임금?')
    assert messages[1]['content'] == build_prompt_v4(hits, '최저임금?')

def test_llm_load_failure_propagates(env):
    def missing(path):
        raise FileNotFoundError(path)
    env.setattr(llm_mod, 'load', missing)
    with pytest.raises(FileNotFoundError):
        LLM('models/없는폴더')

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

def test_load_models_llm_path_default(fake_models):
    r, m = service.load_models()
    assert isinstance(r, FakeRetriever) and m.path == service.LLM_4BIT
    assert FakeRetriever.created == 1 and FakeLLM.created == 1

def test_load_models_llm_path_env(fake_models, env):
    env.setenv('LLM_PATH', 'models/sllm_mlx_8bit')
    assert service.load_models()[1].path == 'models/sllm_mlx_8bit'

def test_answer_stream_returns_sources_then_chunks(fake_models):
    r, m = FakeRetriever(), FakeLLM('models/fake')
    hits, chunks = service.answer_stream(r, m, '수습 기간에도 해고 예고?')
    assert hits == HITS[:service.TOP_K]             # 생성 전에 근거 조문이 정해짐
    assert r.query == '수습 기간에도 해고 예고?'
    assert list(chunks) == ['【결론】', '예고해야', ' 합니다.']
    assert m.args == ('수습 기간에도 해고 예고?', hits)

def test_answer_stream_k(fake_models):
    r, m = FakeRetriever(), FakeLLM('models/fake')
    hits, chunks = service.answer_stream(r, m, '질문', k=1)
    list(chunks)
    assert hits == HITS[:1] and m.args == ('질문', HITS[:1])

def test_answer_stream_generation_failure_propagates(fake_models):
    FakeLLM.chunks = ['【결론】', RuntimeError('generation failed')]
    _, chunks = service.answer_stream(FakeRetriever(), FakeLLM('models/fake'), '질문')
    with pytest.raises(RuntimeError, match='generation failed'):
        list(chunks)

# ---- 웹앱 ----
@pytest.fixture
def app(env, fake_models):
    """가짜 모델을 쓰는 lawbot.app. launch는 실제 서버 대신 설정만 기록한다"""
    import lawbot.app as app
    env.setattr(app, 'launched', [], raising=False)   # 테스트가 끝나면 지워짐
    env.setattr(app.gr.Blocks, 'launch', lambda demo, **kw: app.launched.append((demo, kw)))
    return app

def test_import_does_not_check_auth_or_load_models(env):
    # import만으로는 인증 검사·모델 로딩을 하지 않는다 (둘 다 main에서)
    FakeRetriever.created = FakeLLM.created = 0
    env.setattr(retriever_mod, 'Retriever', FakeRetriever)
    env.setattr(llm_mod, 'LLM', FakeLLM)
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
    assert isinstance(demo, app.gr.Blocks) and demo.title == '근로기준법 챗봇'
    assert FakeRetriever.created == 0 and FakeLLM.created == 0   # 넘겨준 객체 외에 모델을 만들지 않음
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
    assert demo.title == '근로기준법 챗봇'
    assert launched == {'server_name': '127.0.0.1', 'server_port': 7860,
                        'auth': ('tester', 'test-only-pw'), 'share': False}
    assert FakeRetriever.created == 1 and FakeLLM.created == 2   # 검색기 1개 + Fast·Pro (기본 2개 상주)

def test_app_loads_models_via_service(app, env):
    # main은 service.load_app으로 불러온 검색기·모델(LAWBOT_FAST_PATH 반영)을 화면에 넘긴다
    set_auth(env)
    env.setenv('LAWBOT_FAST_PATH', 'models/sllm_mlx_8bit')
    build, apps = app.build_demo, []
    env.setattr(app, 'build_demo', lambda a: apps.append(a) or build(a))
    app.main()
    [a] = apps
    assert isinstance(a.retriever, FakeRetriever)
    assert a.models.loaded['fast'].path == service.deploy.paths.ROOT / 'models/sllm_mlx_8bit'
    assert len(app.launched) == 1

def test_chat_streams_answer_with_sources(app):
    lawbot = service.load_app()
    chat = app.make_chat(lawbot)
    assert chat.__name__ == 'chat'                  # Gradio API 이름(/gradio_api/call/chat)
    steps = list(chat('수습 기간에도 해고 예고?', [], 'fast', 'full'))
    # 토큰마다 누적 답변, 마지막은 생성 완료 후 측정값(완료 시간·tok/s)을 채운 같은 답변
    assert [t for t, _, _ in steps] == ['【결론】', '【결론】예고해야', '【결론】예고해야 합니다.', '【결론】예고해야 합니다.']
    src = steps[-1][1]
    assert all(s == src for _, s, _ in steps)       # 근거 조문 칸은 답변 내내 같음
    assert '**제26조 해고의 예고** · 유사도 0.912' in src
    assert '30일 전에 예고' in src
    assert src.count('제26조 해고의 예고') == 1     # 조문 본문 첫 줄(제목 줄)은 중복 표시 안 함
    assert lawbot.retriever.query == '수습 기간에도 해고 예고?'
    assert lawbot.models.loaded['fast'].args == ('수습 기간에도 해고 예고?', HITS)
    assert steps[-1][2].startswith('Lawbot Fast · 검색') and '완료' in steps[-1][2]

def test_chat_generation_failure_is_not_hidden(app):
    # 생성 실패를 빈 답변이나 부분 답변으로 끝내지 않고 예외로 올린다 (Gradio가 오류로 표시)
    FakeLLM.chunks = ['【결론】', RuntimeError('generation failed')]
    chat = app.make_chat(service.load_app())
    with pytest.raises(RuntimeError, match='generation failed'):
        list(chat('질문', [], 'fast', 'full'))

# ---- 생각 단계 끄기 (튜닝 전 모델의 chat template이 enable_thinking을 무시할 때) ----
from lawbot.llm import THINK, close_thinking, hide_thinking

def test_close_thinking_prompt():
    ok = '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert close_thinking(ok, THINK) == ok                         # Qwen3 템플릿이 이미 끈 경우: 프롬프트 그대로
    assert close_thinking('<|im_start|>assistant\n', THINK) == '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert close_thinking('<|im_start|>assistant\n<think>\n', THINK) == '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert close_thinking('<|im_start|>assistant\n', None) == '<|im_start|>assistant\n'   # 생각 토큰이 없는 모델

def chunks(*texts):
    return [types.SimpleNamespace(text=t, generation_tps=1.0) for t in texts]

def test_hide_thinking_block():
    out = list(hide_thinking(chunks('<thi', 'nk>\n음…', '고민', '</think>\n\n【결론】', ' 답'), THINK))
    assert len(out) == 5 and all(o.generation_tps == 1.0 for o in out)   # 토큰마다 응답 유지 (측정값 그대로)
    assert [o.text for o in out] == ['', '', '', '【결론】', ' 답']

def test_hide_thinking_passthrough():
    for texts in (('【결론】', '답'), (' ', '\n【결론】', '답'), ('<', 'b>굵게',), ('생각 <think> 아님',)):
        assert ''.join(o.text for o in hide_thinking(chunks(*texts), THINK)) == ''.join(texts)
    src = chunks('<think>', '끝나지 않음')
    assert ''.join(o.text for o in hide_thinking(src, None)) == '<think>끝나지 않음'   # 생각 토큰이 없는 모델은 그대로
    assert ''.join(o.text for o in hide_thinking(chunks('<think>', '끝나지 않음'), THINK)) == ''

def test_llm_closes_thinking_when_template_ignores_it(fake_llm, env):
    _, tok, seen = fake_llm
    tok.think_start, tok.think_end = THINK                          # Qwen 어휘, 템플릿은 'PROMPT'만 냄 (끄기 무시)
    seen['gen'] = lambda: iter(chunks('<think>', '생각', '</think>', '【결론】답'))
    m = LLM('models/fake', prompt_style='v1')
    assert ''.join(r.text for r in m.stream('질문', [{'article_id': '제1조'}])) == '【결론】답'
    assert seen['prompt'] == 'PROMPT<think>\n\n</think>\n\n'
    assert tok.calls[-1][1]['enable_thinking'] is False

def test_llm_system_override(fake_llm):
    m, tok, _ = fake_llm
    list(m.stream('질문', [{'article_id': '제1조'}], system='4항목 SYSTEM'))
    assert tok.calls[-1][0][0] == {'role': 'system', 'content': '4항목 SYSTEM'}
    list(m.stream('질문', [{'article_id': '제1조'}]))
    assert tok.calls[-1][0][0] == {'role': 'system', 'content': SYSTEM}   # 다음 요청은 기본 system
