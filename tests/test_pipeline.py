# tests/test_pipeline.py  →  검색(Retriever)·생성(LLM)·웹앱(app) 흐름 테스트
# 실제 임베딩·sLLM 대신 가짜 모델을 넣는다: 모델 파일·MPS·Metal·네트워크 불필요
import sys
import types
import numpy as np
import pandas as pd
import pytest
import lawbot.llm as llm_mod
import lawbot.retriever as retriever_mod
from conftest import set_auth
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot.llm import LLM, SYSTEM, build_prompt
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

def test_llm_load_failure_propagates(env):
    def missing(path):
        raise FileNotFoundError(path)
    env.setattr(llm_mod, 'load', missing)
    with pytest.raises(FileNotFoundError):
        LLM('models/없는폴더')

# ---- 웹앱 ----
@pytest.fixture
def load_app(env):
    """가짜 모델로 lawbot.app을 새로 import한다 (app은 import 시 설정 확인·모델 로딩을 함)"""
    FakeRetriever.created = FakeLLM.created = 0
    FakeLLM.chunks = ['【결론】', '예고해야', ' 합니다.']
    env.setattr(retriever_mod, 'Retriever', FakeRetriever)
    env.setattr(llm_mod, 'LLM', FakeLLM)
    def load():
        sys.modules.pop('lawbot.app', None)
        import lawbot.app
        return lawbot.app
    yield load
    sys.modules.pop('lawbot.app', None)

def test_app_refuses_to_start_without_auth_before_loading_models(load_app):
    with pytest.raises(RuntimeError, match='LAWBOT_USER'):
        load_app()
    assert FakeRetriever.created == 0 and FakeLLM.created == 0

def test_app_launches_local_only(load_app, env):
    set_auth(env)
    env.setenv('GRADIO_SERVER_NAME', '0.0.0.0')
    env.setenv('GRADIO_SHARE', 'True')
    app = load_app()
    launched = {}
    env.setattr(app.demo, 'launch', lambda **kw: launched.update(kw))
    app.main()
    assert launched == {'server_name': '127.0.0.1', 'server_port': 7860,
                        'auth': ('tester', 'test-only-pw'), 'share': False}

def test_app_llm_path_env(load_app, env):
    set_auth(env)
    env.setenv('LLM_PATH', 'models/sllm_mlx_8bit')
    assert load_app().L.path == 'models/sllm_mlx_8bit'

def test_chat_streams_answer_with_sources(load_app, env):
    set_auth(env)
    app = load_app()
    steps = list(app.chat('수습 기간에도 해고 예고?', []))
    assert [t for t, _ in steps] == ['【결론】', '【결론】예고해야', '【결론】예고해야 합니다.']
    src = steps[-1][1]
    assert all(s == src for _, s in steps)          # 근거 조문 칸은 답변 내내 같음
    assert '**제26조 해고의 예고** · 유사도 0.912' in src
    assert '30일 전에 예고' in src
    assert src.count('제26조 해고의 예고') == 1     # 조문 본문 첫 줄(제목 줄)은 중복 표시 안 함
    assert app.R.query == '수습 기간에도 해고 예고?'
    assert app.L.args == ('수습 기간에도 해고 예고?', HITS)

def test_chat_generation_failure_is_not_hidden(load_app, env):
    # 생성 실패를 빈 답변이나 부분 답변으로 끝내지 않고 예외로 올린다 (Gradio가 오류로 표시)
    set_auth(env)
    app = load_app()
    FakeLLM.chunks = ['【결론】', RuntimeError('generation failed')]
    with pytest.raises(RuntimeError, match='generation failed'):
        list(app.chat('질문', []))
