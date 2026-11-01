# tests/test_reranker.py  →  리랭커 클라이언트 (reranker.py: 모델 서버 /v1/rerank 점수 → 정렬)와 service·app·check_deploy 연결
# 채점 계산(작은 MLX Qwen3)은 모델 서버 테스트 (services/model-server/tests/test_ms_models.py)
import json

import pytest

import lawbot.app as app_mod
import lawbot.reranker as rr_mod
import lawbot.service as service
from fakes import FakeLLM, FakeRetriever, FakeServerClient
from lawbot import check_deploy, deploy
from lawbot.llm import build_prompt_v4


def cands(n):
    return [
        {
            'law': '근로기준법',
            'article_id': f'제{i}조',
            'title': f'제목{i}',
            'score': 1 - i / 100,
            'doc': f'근로기준법 제{i}조(제목{i})\n본문 {i}',
        }
        for i in range(1, n + 1)
    ]


class ScoreClient:
    """ModelServerClient.rerank 대역: 문서별 고정 점수, 받은 인자 기록"""

    def __init__(self, table=None):
        self.table, self.calls = table or {}, []

    def rerank(self, model, query, documents, instruction=None):
        self.calls.append((model, query, list(documents), instruction))
        return [self.table.get(d, 0.5) for d in documents]


def with_scores(table):
    return rr_mod.Reranker(ScoreClient(table), 'qwen3-reranker-4b')


# ---- 실험 노트북과 같은 작업 설명 ----
def test_task_matches_notebook():
    # lawbot_v4_reranker.ipynb 셀 3의 작업 설명을 글자 그대로 (바뀌면 Colab 결과와 비교할 수 없음)
    assert rr_mod.TASK == (
        'Given a Korean labor-law question, retrieve the statutory article that directly '
        'provides the legal basis for answering it.'
    )
    assert rr_mod.CANDIDATES == 10


def test_sends_documents_and_task_to_server():
    c = cands(3)
    r = with_scores({})
    r.rerank('질문', c, top_k=3)
    assert r.client.calls == [('qwen3-reranker-4b', '질문', [x['doc'] for x in c], rr_mod.TASK)]


# ---- 정렬 규칙 ----
def test_ten_candidates_return_three_sorted():
    c = cands(10)
    r = with_scores({x['doc']: s for x, s in zip(c, [0.1, 0.5, 0.2, 0.9, 0.3, 0.8, 0.05, 0.7, 0.4, 0.6])})
    out = r.rerank('질문', c, top_k=3)
    assert [h['article_id'] for h in out] == ['제4조', '제6조', '제8조']
    assert [h['rerank_score'] for h in out] == [0.9, 0.8, 0.7]
    assert [h['emb_rank'] for h in out] == [4, 6, 8]


def test_ties_keep_embedding_order():
    c = cands(5)
    r = with_scores({x['doc']: s for x, s in zip(c, [0.5, 1.0, 0.5, 1.0, 1.0])})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=3)] == ['제2조', '제4조', '제5조']
    r = with_scores({x['doc']: 0.5 for x in c})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=5)] == [x['article_id'] for x in c]


def test_few_candidates():
    c = cands(2)
    r = with_scores({c[0]['doc']: 0.1, c[1]['doc']: 0.9})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=3)] == ['제2조', '제1조']
    c = cands(3)
    r = with_scores({x['doc']: s for x, s in zip(c, [0.2, 0.3, 0.1])})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=3)] == ['제2조', '제1조', '제3조']


def test_metadata_preserved_and_input_untouched():
    c = cands(4)
    before = json.dumps(c, ensure_ascii=False)
    r = with_scores({x['doc']: s for x, s in zip(c, [0.1, 0.4, 0.3, 0.2])})
    out = r.rerank('질문', c, top_k=3)
    assert json.dumps(c, ensure_ascii=False) == before  # 입력 dict를 바꾸지 않음
    for h in out:
        orig = c[h['emb_rank'] - 1]
        assert {k: h[k] for k in orig} == orig  # 조문·법령·제목·임베딩 유사도 그대로
        assert set(h) - set(orig) == {'rerank_score', 'emb_rank'}


def test_invalid_inputs():
    r = with_scores({})
    for q, c, k in (
        ('', cands(3), 3),
        ('  ', cands(3), 3),
        (None, cands(3), 3),
        ('질문', [], 3),
        ('질문', [{'article_id': '제1조'}], 3),
        ('질문', ['문자열'], 3),
        ('질문', cands(3), 0),
        ('질문', cands(3), 2.5),
    ):
        with pytest.raises(ValueError):
            r.rerank(q, c, top_k=k)
    assert r.client.calls == []  # 잘못된 입력은 서버에 보내지 않음


@pytest.mark.parametrize('bad', [float('nan'), 1.5, -0.1, None])
def test_bad_scores_raise(bad):
    c = cands(3)
    r = with_scores({c[1]['doc']: bad})
    with pytest.raises(RuntimeError, match='점수가 올바르지 않습니다'):
        r.rerank('질문', c)


# ---- 설정 ----
def test_reranker_config(env):
    v4, v1 = deploy.get_profile('v4'), deploy.get_profile('v1')
    assert deploy.reranker(v4) is None and deploy.reranker(v1) is None  # 기본 꺼짐
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    assert deploy.reranker(v4) == deploy.RerankerConfig('qwen3-reranker-4b', 10)
    env.setenv('LAWBOT_RERANK_MODEL', 'rr-8bit')
    env.setenv('LAWBOT_RERANKER_CANDIDATES', '20')
    assert deploy.reranker(v4) == deploy.RerankerConfig('rr-8bit', 20)
    with pytest.raises(ValueError, match='v4 프로필에서만'):
        deploy.reranker(v1)
    env.setenv('LAWBOT_RERANKER_ENABLED', 'yes')
    with pytest.raises(ValueError, match='0 또는 1'):
        deploy.reranker(v4)


# ---- 질의응답 흐름 ----
class CandRetriever(FakeRetriever):
    """v4 형식 후보 10개를 임베딩 순으로 돌려주고 요청한 k를 기록"""

    def search(self, query, k=3):
        self.query = query
        self.ks = getattr(self, 'ks', []) + [k]
        return cands(10)[:k]


class FakeReranker:
    created = 0
    fail = None

    def __init__(self, client, model, candidates=10):
        FakeReranker.created += 1
        self.client, self.model, self.candidates, self.calls = client, model, candidates, []

    def rerank(self, question, candidates, top_k=3):  # 임베딩 역순이 리랭킹 순서라고 가정
        if FakeReranker.fail:
            raise FakeReranker.fail
        self.calls.append((question, [c['article_id'] for c in candidates], top_k))
        rev = list(reversed(candidates))
        return [c | {'rerank_score': 1 - i / 10, 'emb_rank': len(candidates) - i} for i, c in enumerate(rev[:top_k])]


def v4_app(env, tmp_path, enabled, **extra):
    FakeRetriever.created = FakeLLM.created = FakeReranker.created = 0
    FakeReranker.fail = None
    FakeLLM.chunks = ['【결론】', '예고해야', ' 합니다.']
    env.setattr(service, 'Retriever', CandRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(service, 'Reranker', FakeReranker)
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1' if enabled else '0')
    for k, v in extra.items():
        env.setenv(k, v)
    return service.load_app()


def test_disabled_is_embedding_top3(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=False)
    assert a.reranker is None and FakeReranker.created == 0  # 꺼져 있으면 리랭커를 만들지 않음
    *_, (stage, hits, raw, st) = service.ask(a, '해고 예고?', 'fast')
    assert stage == 'done' and a.retriever.ks == [3]
    assert hits == cands(3)  # 기존 검색 결과 그대로 (키 추가 없음)
    assert a.models.llms['fast'].args == ('해고 예고?', cands(3))
    assert 'rerank' not in st


def test_disabled_v1_unchanged(env):
    # v1 기본 실행: 리랭커 없이 기존 검색 결과를 그대로 답변 모델에 넘김
    FakeReranker.created = 0
    FakeLLM.chunks = ['【결론】', '예고해야', ' 합니다.']
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(service, 'Reranker', FakeReranker)
    a = service.load_app()
    from fakes import HITS

    *_, (_, hits, _, st) = service.ask(a, '질문', 'fast')
    assert a.reranker is None and FakeReranker.created == 0 and hits == HITS and 'rerank' not in st


def test_enabled_reranks_top10_to_top3(env, tmp_path, capsys):
    a = v4_app(env, tmp_path, enabled=True)
    assert FakeReranker.created == 1 and a.reranker.model == 'qwen3-reranker-4b'
    assert isinstance(a.reranker.client, FakeServerClient)  # 임베딩과 같은 모델 서버
    *_, (stage, hits, raw, st) = service.ask(a, '해고 예고?', 'fast')
    assert a.retriever.ks == [10]
    assert a.reranker.calls == [('해고 예고?', [f'제{i}조' for i in range(1, 11)], 3)]
    assert [h['article_id'] for h in hits] == ['제10조', '제9조', '제8조']  # 리랭킹 점수순
    assert a.models.llms['fast'].args == ('해고 예고?', hits)  # 최종 3개만 답변 모델로
    assert st['search'] >= 0 and st['rerank'] >= 0
    assert '리랭킹' in capsys.readouterr().out  # 단계별 시간 로그


def test_reranker_loaded_once(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=True)
    chat = app_mod.make_chat(a)
    for key in ('fast', 'pro', 'fast'):
        list(chat('질문', [], key, 'full'))
    assert FakeReranker.created == 1 and len(a.reranker.calls) == 3


def test_enabled_candidates_env(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=True, LAWBOT_RERANKER_CANDIDATES='5')
    list(service.ask(a, '질문', 'fast'))
    assert a.retriever.ks == [5]
    with pytest.raises(ValueError, match='3 이상'):
        v4_app(env, tmp_path, enabled=True, LAWBOT_RERANKER_CANDIDATES='2')


def test_enabled_refuses_before_loading(env, tmp_path, model_server):
    env.setattr(service, 'Retriever', CandRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(service, 'Reranker', FakeReranker)
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    FakeRetriever.created = FakeLLM.created = FakeReranker.created = 0
    p = deploy.get_profile()
    model_server.available = [{'id': p.emb_model, 'type': 'embedding'}] + [
        {'id': s.model, 'type': 'chat'} for s in p.models
    ]  # 서버에 리랭커 모델 없음
    with pytest.raises(RuntimeError, match='모델 서버에 필요한 모델이 없습니다: rerank qwen3-reranker-4b'):
        service.load_app()
    env.setenv('LAWBOT_PROFILE', 'v1')
    with pytest.raises(ValueError, match='v4 프로필에서만'):
        service.load_app()
    assert FakeRetriever.created == FakeLLM.created == FakeReranker.created == 0


def test_reranker_failure_is_not_hidden(env, tmp_path):
    # 리랭커 오류를 임베딩 순위로 몰래 되돌리지 않고 오류로 올린다. 답변 생성도 하지 않음
    a = v4_app(env, tmp_path, enabled=True)
    FakeReranker.fail = RuntimeError('리랭커 점수가 올바르지 않습니다')
    chat = app_mod.make_chat(a)
    with pytest.raises(RuntimeError, match='점수가 올바르지'):
        list(chat('질문', [], 'fast', 'full'))
    assert 'fast' not in a.models.llms  # 답변 모델을 부르지 않음


def test_chat_shows_rerank_info(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=True)
    text, src, stats = list(app_mod.make_chat(a)('해고 예고?', [], 'fast', 'full'))[-1]
    assert text == '【결론】예고해야 합니다.'  # 스트리밍 답변 그대로
    assert src.index('제10조') < src.index('제9조') < src.index('제8조')
    assert '**근로기준법 제10조 제목10** · 유사도 0.900 · 리랭커 1.000 (임베딩 10위)' in src
    assert '리랭킹' in stats and stats.index('검색') < stats.index('리랭킹')
    off = v4_app(env, tmp_path, enabled=False)
    _, src, stats = list(app_mod.make_chat(off)('해고 예고?', [], 'fast', 'full'))[-1]
    assert '리랭커' not in src and '리랭킹' not in stats


def test_prompt_format_unchanged_by_rerank_keys():
    # 리랭커가 더한 키(rerank_score·emb_rank)는 답변 모델의 [참고 조문]에 영향이 없다
    hits = cands(3)
    tagged = [h | {'rerank_score': 0.9, 'emb_rank': 7} for h in hits]
    assert build_prompt_v4(tagged, '질문') == build_prompt_v4(hits, '질문')


# ---- 배포 검사 (리랭커 폴더 검사는 모델 서버 lawbot_model_server.check) ----
def test_check_deploy_reranker(env, model_server):
    def run():
        return [(lv, m) for lv, m in check_deploy.run('v4') if '리랭커' in m]

    assert run() == [('INFO', '리랭커 꺼짐 (LAWBOT_RERANKER_ENABLED=1이면 모델 서버의 리랭커 모델도 확인)')]
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    assert ('OK', '리랭커 qwen3-reranker-4b: 후보 10개 → 3개 (v4 실험 10개)') in run()
    env.setenv('LAWBOT_RERANKER_CANDIDATES', '2')
    assert any(lv == 'FAIL' and '후보 2개' in m for lv, m in run())
    env.delenv('LAWBOT_RERANKER_CANDIDATES')
    env.setenv('LAWBOT_RERANK_MODEL', '없는-리랭커')
    p = deploy.get_profile('v4')
    model_server.available = [
        {'id': p.emb_model, 'type': 'embedding'},
        {'id': 'qwen3-reranker-4b', 'type': 'rerank'},
    ] + [{'id': s.model, 'type': 'chat'} for s in p.models]
    assert ('FAIL', '모델 서버에 rerank 모델 없는-리랭커 없음 (LAWBOT_SERVE_*와 맞추기)') in check_deploy.run('v4')
    env.setenv('LAWBOT_PROFILE', 'v1')
    assert any(lv == 'FAIL' and 'v4 프로필에서만' in m for lv, m in check_deploy.run('v1'))
