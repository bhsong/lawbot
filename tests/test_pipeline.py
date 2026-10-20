# tests/test_pipeline.py  →  검색(Retriever)·생성(LLM)·웹앱(app) 흐름 테스트
# 실제 임베딩·sLLM 대신 가짜 모델을 넣는다: 모델 파일·MPS·Metal·네트워크 불필요
import hashlib
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
from fakes import HITS, SNAPSHOT, FakeLLM, FakeRetriever
from lawbot.lawdata import DataError, Snapshot, article_meta
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
    assert [set(h) for h in hits] == [{'article_id', 'title', 'doc', 'score', 'meta'}] * 3
    scores = [h['score'] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert len({h['article_id'] for h in hits}) == 3
    for h in hits:
        assert h['doc'].startswith(f"{h['article_id']} {h['title']}\n")
    assert FakeEncoder.calls[-1] == (1, 'query')  # 질문은 query 프롬프트로 인코딩

def test_search_hits_keep_source_metadata(retriever):
    # 검색 결과마다 그 조문의 출처 메타데이터가 붙고, 색인한 데이터 파일을 해시로 기록한다
    law = pd.read_csv(ARTICLES, encoding='utf-8-sig').set_index('article_id')
    sha = hashlib.sha256(ARTICLES.read_bytes()).hexdigest()
    for h in retriever.search('연장 근로 한도', k=5):
        m = h['meta']
        assert (m['article_id'], m['title']) == (h['article_id'], h['title'])
        assert m['law_name'] == '근로기준법' and m['as_of'] == '2026-10-01' and m['source'] == 'Legalize KR'
        assert m['data_sha256'] == sha and m['data_file'] == ARTICLES.name
        assert m == article_meta(h['article_id'], h['title'], law.loc[h['article_id'], 'text'], retriever.snapshot)
    assert retriever.snapshot.verified
    info = retriever.index_info
    assert (info['data_file'], info['data_sha256'], info['docs']) == (ARTICLES.name, sha, 130)
    assert info['model_dir'] == str(retriever_mod.EMB) and info['built_at']

def test_retriever_without_metadata_still_indexes_but_flags(retriever, tmp_path):
    # 메타데이터 없는 조문 파일: 검색은 되지만 출처·기준일은 비우고 사유를 남긴다
    csv = tmp_path / 'articles.csv'
    csv.write_bytes(ARTICLES.read_bytes())
    r = retriever_mod.Retriever(law_csv=csv)
    assert not r.snapshot.verified and r.snapshot.as_of is None and r.snapshot.law_name is None
    assert all(h['meta']['as_of'] is None for h in r.search('해고 예고', k=3))

def test_retriever_rejects_broken_data_before_loading_model(retriever, tmp_path):
    law = pd.read_csv(ARTICLES, encoding='utf-8-sig')
    law.loc[1, 'article_id'] = law.loc[0, 'article_id']
    csv = tmp_path / 'articles.csv'
    law.to_csv(csv, index=False, encoding='utf-8-sig')
    FakeEncoder.calls = []
    with pytest.raises(DataError, match='중복'):
        retriever_mod.Retriever(law_csv=csv)
    assert FakeEncoder.calls == []

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

def test_load_models_logs_index_and_data_issues(fake_models, env, capsys):
    service.load_models()
    out = capsys.readouterr().out
    assert '색인 2개 조문 · fake_articles.csv (sha256 000000000000) · 기준일 2026-10-01' in out
    assert '경고' not in out
    broken = Snapshot(data_file='x.csv', sha256='1' * 64, issues=('데이터 파일이 메타데이터에 기록된 해시와 다름',))
    env.setattr(FakeRetriever, 'snapshot', broken)
    service.load_models()
    out = capsys.readouterr().out
    assert '기준일 확인 불가' in out and '경고: 데이터 파일이 메타데이터에 기록된 해시와 다름' in out

@pytest.mark.parametrize('answer,expected', [
    ('【결론】\n예고해야 합니다.\n\n【근거 조문】\n근로기준법 제26조(해고의 예고)\n\n【설명】\n- 제36조와 무관', (['제26조'], [])),
    ('【근거 조문】\n근로기준법 제26조, 제26조제1항, 제36조', (['제26조', '제36조'], [])),
    ('【근거 조문】\n근로기준법 제26조, 제999조(없는 조문)', (['제26조'], ['제999조'])),
    ('【근거 조문】\n근로기준법 제36조의2', ([], ['제36조의2'])),    # 제36조와 구별
    ('【결론】\n제공된 조문만으로는 확인할 수 없습니다.\n\n【근거 조문】\n해당 없음 (제공된 조문: 제26조, 제36조)', ([], [])),
    ('형식 없는 답변 제26조', ([], [])),            # 【근거 조문】이 없으면 인용으로 보지 않음
])
def test_trace_citations(answer, expected):
    assert service.trace_citations(answer, HITS) == expected

def test_source_notes_uses_retriever_snapshot(env):
    import datetime
    r = FakeRetriever()
    assert service.source_notes(r, '해고 예고 기간은?', HITS, today=datetime.date(2026, 10, 20)) == []
    notes = service.source_notes(r, '2018년에 해고됐는데 예고 기간은?', HITS, today=datetime.date(2026, 10, 20))
    assert len(notes) == 2 and '2018년' in notes[0] and notes[1].startswith('제26조에는 2018년 이후 개정 표시(최근 2019-01-15)')

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
    demo = app.build_demo(FakeRetriever(), FakeLLM('models/fake'))
    assert isinstance(demo, app.gr.Blocks) and demo.title == '근로기준법 챗봇'
    assert FakeRetriever.created == 1 and FakeLLM.created == 1   # 넘겨준 객체 외에 모델을 만들지 않음
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
    assert FakeRetriever.created == 1 and FakeLLM.created == 1

def test_app_loads_models_via_service(app, env):
    # main은 service.load_models로 불러온 모델(LLM_PATH 반영)을 화면에 넘긴다
    set_auth(env)
    env.setenv('LLM_PATH', 'models/sllm_mlx_8bit')
    build, models = app.build_demo, []
    env.setattr(app, 'build_demo', lambda r, m: models.append((r, m)) or build(r, m))
    app.main()
    [(r, m)] = models
    assert isinstance(r, FakeRetriever) and m.path == 'models/sllm_mlx_8bit'
    assert len(app.launched) == 1

def test_chat_streams_answer_with_sources(app):
    r, m = FakeRetriever(), FakeLLM('models/fake')
    chat = app.make_chat(r, m)
    assert chat.__name__ == 'chat'                  # Gradio API 이름(/gradio_api/call/chat)
    steps = list(chat('수습 기간에도 해고 예고?', []))
    assert [t for t, _ in steps] == ['【결론】', '【결론】예고해야', '【결론】예고해야 합니다.']
    src = steps[-1][1]
    assert all(s == src for _, s in steps)          # 근거 조문 칸은 답변 내내 같음
    assert '**제26조 해고의 예고** · 유사도 0.912' in src
    assert '30일 전에 예고' in src
    assert src.count('제26조 해고의 예고') == 1     # 조문 본문 첫 줄(제목 줄)은 중복 표시 안 함
    assert r.query == '수습 기간에도 해고 예고?'
    assert m.args == ('수습 기간에도 해고 예고?', HITS)

def test_sources_panel_shows_provenance_and_notes(app):
    src = app.fmt_sources(HITS, SNAPSHOT)
    assert src.startswith('출처: 근로기준법 · 원 출처 미기록 `fake_articles.csv` · 기준일 2026-10-01')
    full = Snapshot(data_file='a.csv', sha256='0' * 64, law_name='근로기준법', law_number='법률 제21373호',
                    effective='2026-08-20', source='Legalize KR', as_of='2026-10-01')
    assert app.fmt_sources(HITS, full).startswith(
        '출처: 근로기준법 법률 제21373호 (2026-08-20 시행) · Legalize KR `a.csv` · 기준일 2026-10-01')
    assert '> ' not in src                          # 확인할 수 없는 시점이 없으면 안내 문장을 붙이지 않음
    src = app.fmt_sources(HITS, SNAPSHOT, ['안내 1', '안내 2'])
    assert '> - 안내 1\n> - 안내 2' in src and src.index('안내 2') < src.index('**제26조')
    broken = Snapshot(data_file='x.csv', sha256='1' * 64, issues=('출처 메타데이터 없음',))
    assert app.fmt_sources(HITS, broken).startswith('출처: `x.csv` · 출처·기준일 확인 불가')
    assert app.fmt_sources(HITS) == app.fmt_sources(HITS, None, ())   # 출처 정보 없이 부르던 방식 유지
    assert not app.fmt_sources(HITS).startswith('출처')

def test_chat_question_with_past_year_gets_time_note(app):
    chat = app.make_chat(FakeRetriever(), FakeLLM('models/fake'))
    src = list(chat('2018년에 해고 예고 없이 해고됐어요', []))[-1][1]
    assert '질문에 특정 시점(2018년)이 있습니다' in src
    assert '제26조에는 2018년 이후 개정 표시(최근 2019-01-15)가 있어' in src
    assert '**제26조 해고의 예고** · 유사도 0.912' in src

@pytest.mark.parametrize('cited,first_line', [
    ('근로기준법 제26조(해고의 예고)', '답변이 인용한 조문: 제26조 (아래 검색 결과)'),
    ('근로기준법 제26조, 제999조', '답변이 인용한 조문: 제26조 (아래 검색 결과)\n\n⚠ 검색 결과에 없는 조문 인용: 제999조.'),
    ('근로기준법 제999조', '⚠ 검색 결과에 없는 조문 인용: 제999조. 이 데이터로 근거를 확인하지 않은 인용입니다.'),
])
def test_chat_traces_citations_after_generation(app, cited, first_line):
    FakeLLM.chunks = ['【결론】\n예고해야 합니다.\n\n【근거 조문】\n', cited]
    steps = list(app.make_chat(FakeRetriever(), FakeLLM('models/fake'))('해고 예고?', []))
    assert len(steps) == 3                          # 조각 2개 + 생성 뒤 대조 결과 1번
    (_, during), _, (text, final) = steps
    assert text.endswith(cited) and steps[1][1] == during  # 생성 중에는 근거 칸이 바뀌지 않음
    assert final.startswith(first_line) and final.endswith(during)

def test_chat_refusal_adds_no_citation_trace(app):
    FakeLLM.chunks = ['【결론】\n제공된 조문만으로는 확인할 수 없습니다.\n\n【근거 조문】\n해당 없음 (제공된 조문: 제26조)']
    steps = list(app.make_chat(FakeRetriever(), FakeLLM('models/fake'))('점심 메뉴?', []))
    assert len(steps) == 1 and '답변이 인용한 조문' not in steps[0][1]

def test_chat_generation_failure_is_not_hidden(app):
    # 생성 실패를 빈 답변이나 부분 답변으로 끝내지 않고 예외로 올린다 (Gradio가 오류로 표시)
    FakeLLM.chunks = ['【결론】', RuntimeError('generation failed')]
    chat = app.make_chat(FakeRetriever(), FakeLLM('models/fake'))
    with pytest.raises(RuntimeError, match='generation failed'):
        list(chat('질문', []))
