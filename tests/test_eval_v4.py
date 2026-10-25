# tests/test_eval_v4.py  →  v4 재평가 스크립트 (eval_v4): 인용 판정·문항 구성·단계별 실행·요약
# 실제 모델 대신 가짜 검색기·리랭커·sLLM으로 모든 단계를 끝까지 돌린다 (data/는 읽기만)
import types
import numpy as np
import pandas as pd
import pytest
import lawbot.eval_v4 as ev
from lawbot import deploy, service
from lawbot.paths import DATA

E = dict(encoding='utf-8-sig')

# ---- 인용 판정 ----
@pytest.mark.parametrize('answer,expected', [
    ('【결론】답\n【근거 조문】\n근로기준법 제26조(해고의 예고), 제27조\n【설명】 제99조',
     [('근로기준법', '제26조'), ('근로기준법', '제27조')]),           # 법령명 없이 이어 쓴 번호는 앞 법령 · 설명은 제외
    ('【근거 조문】 남녀고용평등법 제19조(육아휴직) 제1항', [('남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률', '제19조')]),
    ('【근거 조문】 근로자퇴직급여 보장법 제8조의2, 최저임금법 제6조',
     [('근로자퇴직급여 보장법', '제8조의2'), ('최저임금법', '제6조')]),
    ('【근거 조문】 제26조', [(None, '제26조')]),                     # 법령명이 없으면 법령 미상
    ('【근거 조문】\n해당 없음 (제공된 조문: 근로기준법 제34조)', []),   # 거절 형식의 제공 조문 목록은 인용이 아님
    ('근로기준법 제36조에 따라 14일 이내에 지급해야 합니다.', [('근로기준법', '제36조')]),   # 섹션이 없으면 답변 전체
])
def test_citations(answer, expected):
    assert ev.citations(answer) == expected

def test_cite_gold_needs_law_and_no_refusal():
    assert ev.cite_gold('【결론】답\n【근거 조문】 퇴직급여법 제8조', '근로자퇴직급여 보장법 제8조')
    assert not ev.cite_gold('【결론】답\n【근거 조문】 최저임금법 제8조', '근로자퇴직급여 보장법 제8조')   # 번호만 같은 다른 법
    assert not ev.cite_gold('【근거 조문】 근로기준법 제8조의2', '근로기준법 제8조')
    assert not ev.cite_gold('【결론】확인할 수 없습니다.\n【근거 조문】 근로기준법 제8조', '근로기준법 제8조')

# ---- 문항 ----
def test_test_split_and_sample():
    t = ev.load_test()
    assert len(t) == 2075 and t['eval_cat'].value_counts().to_dict() == {'B_newlaw': 1425, 'A_lsa': 650}
    kw = ev.keywords()
    a = t[t.eval_cat == 'A_lsa']
    assert set(a['id']) <= set(kw.index)                                      # 기존 키워드 650문항과 같은 문항
    s = ev.answer_sample(t, 200)
    assert s['eval_cat'].value_counts().to_dict() == {'A_lsa': 100, 'B_newlaw': 100}
    assert s['id'].tolist() == ev.answer_sample(t, 200)['id'].tolist()       # 고정 시드
    articles = pd.read_csv(deploy.get_profile('v4').articles, **E)
    assert set(t['article_id']) <= set(articles['article_id'])

def test_keyword_fix_follows_2026_amendment():
    # 2026.4.7 개정으로 임금체불 벌칙이 제107조(5년·5천만원)로: v4 정답과 키워드를 맞춘다
    t = ev.load_test().set_index('id')
    arts = pd.read_csv(deploy.get_profile('v4').articles, **E).set_index('article_id')
    assert '5년 이하' in arts.loc['근로기준법 제107조', 'text'] and '제43조' in arts.loc['근로기준법 제107조', 'text']
    kw = ev.keywords()
    for i in ev.KEYWORD_FIX:
        assert t.loc[i, 'article_id'] == '근로기준법 제107조'
        assert '5년이하' in kw.loc[i, 'must_include']
    raw = pd.read_csv(DATA / 'labor_eval_keywords.csv', **E).set_index('id')
    assert '3년이하' in raw.loc['Q2443', 'must_include']                      # 평가 파일 자체는 그대로

def test_oos_reclassification():
    oos = pd.read_csv(DATA / 'oos_eval.csv', **E)
    inside = oos[oos['source'].isin(ev.OOS_IN_SCOPE)]
    assert len(inside) == 27 and len(oos) - len(inside) == 75
    assert set(ev.OOS_IN_SCOPE.values()) <= set(ev.LAW_ALIASES)
    assert set(ev.LAW_ALIASES) == set(pd.read_csv(deploy.get_profile('v4').articles, **E)['law'])

# ---- 단계 실행 (가짜 모델) ----
ARTS = [('근로기준법', '제26조', '해고의 예고'), ('근로기준법', '제36조', '금품 청산'), ('최저임금법', '제6조', '최저임금의 효력'),
        ('근로자퇴직급여 보장법', '제8조', '퇴직금제도의 설정 등'), ('임금채권보장법', '제7조', '퇴직한 근로자에 대한 대지급금의 지급'),
        ('파견근로자 보호 등에 관한 법률', '제5조', '근로자파견 대상 업무 등')]

class Ret:
    def __init__(self):
        self.laws = np.array([a[0] for a in ARTS])
        self.ids = np.array([a[1] for a in ARTS])
        self.titles = np.array([a[2] for a in ARTS])
        self.docs = [f'{l} {a}({t})\n본문' for l, a, t in ARTS]

    def rank_all(self, queries):
        return np.array([np.roll(np.arange(len(ARTS)), -(len(q) % len(ARTS))) for q in queries])

    def search(self, q, k=3):
        order = self.rank_all([q])[0][:k]
        return [{'article_id': self.ids[i], 'title': self.titles[i], 'doc': self.docs[i], 'score': 0.5, 'law': self.laws[i]}
                for i in order]

class RR:
    candidates = 6

    def rerank(self, q, cands, top_k=3):
        return [c | {'rerank_score': 0.5, 'emb_rank': i} for i, c in enumerate(reversed(cands), 1)][:top_k]

class Bot:
    systems = []

    def __init__(self, spec):
        self.spec = spec

    def stream(self, question, hits, system=None):
        Bot.systems.append(system)
        h = hits[0]
        text = ('【결론】\n확인할 수 없습니다.\n\n【근거 조문】\n해당 없음' if '날씨' in question else
                f"【결론】\n답입니다.\n\n【근거 조문】\n{h['law']} {h['article_id']}({h['title']})")
        for c in (text[:5], text[5:]):
            yield types.SimpleNamespace(text=c, generation_tps=10.0, finish_reason='stop', prompt_tokens=1,
                                        prompt_tps=1.0, generation_tokens=2, peak_memory=1.0)

def fake_app(rerank=True):
    p = deploy.get_profile('v4')
    models = service.ModelManager(p.models, Bot, 1)
    return service.LawbotApp(p, Ret(), models, False, RR() if rerank else None, 'FULL SYSTEM')

def frame(n=6):
    rows = [{'id': f'T{i}', 'question': '질문' * (i + 1), 'article_id': f'{l} {a}', 'eval_cat': 'A_lsa' if i % 2 else 'B_newlaw',
             'confusable_with': None} for i, (l, a, _) in enumerate(ARTS[:n])]
    return pd.DataFrame(rows)

def test_stages_end_to_end_and_resume(tmp_path, env):
    env.setattr(ev, 'OUT', tmp_path)
    app, t = fake_app(), frame()
    kw = pd.DataFrame({'must_include': '[["답"]]', 'must_not_include': '[]', 'must_not_in_conclusion': '[]',
                       'article_id': '제26조', 'confusable_with': ''}, index=t.loc[t.eval_cat == 'A_lsa', 'id'])
    env.setattr(ev, 'keywords', lambda: kw)
    for _ in range(2):                                           # 두 번째 실행은 이미 끝난 문항을 건너뜀
        ev.run_retrieval(app, t, tmp_path / 'retrieval_x.csv')
        ev.run_answers(app, 'pro', 'concise', t, tmp_path / 'answers_x.csv')
        ev.run_oos(app, 'pro', 'concise', tmp_path / 'oos_x.csv')
        ev.run_refusal(app, 'pro', 'full', t, tmp_path / 'refusal_x.csv')
    r = pd.read_csv(tmp_path / 'retrieval_x.csv', **E)
    assert len(r) == 6 and r['id'].is_unique
    assert ((r['emb_rank'] >= 1) & (r['emb_rank'] <= 6)).all() and (r['rr_rank'] <= 6).all()
    a = pd.read_csv(tmp_path / 'answers_x.csv', **E)
    assert len(a) == 6 and (a['cite_v4'] == a['top3'].str.split('|').str[0].eq(a['gold'])).all()
    assert a.loc[a.eval_cat == 'A_lsa', 'total_v4'].notna().all() and a.loc[a.eval_cat == 'B_newlaw', 'total_v4'].isna().all()
    assert {'search', 'rerank', 'ttft', 'total'} <= set(a.columns)              # 웹앱 경로(ask) 측정값
    o = pd.read_csv(tmp_path / 'oos_x.csv', **E)
    assert len(o) == 122 and o['scope'].value_counts().to_dict() == {'out': 75, 'in_reclassified': 27, 'in_control': 20}
    assert not o.loc[o['scope'] == 'out', 'cites_law'].any()                  # 범위 밖은 '해당 법률'이 없음
    f = pd.read_csv(tmp_path / 'refusal_x.csv', **E)
    assert len(f) == 12 and f['test'].value_counts().to_dict() == {'다른 법률 조문': 6, '비슷한 조문': 6}
    for _, x in f.iterrows():
        gold = t.set_index('id').loc[x['id'].split(':')[0], 'article_id']
        ctx = x['context'].split('|')
        assert len(ctx) == 3 and gold not in ctx
        if x['test'] == '다른 법률 조문':
            assert not any(c.startswith(gold.rsplit(' ', 1)[0] + ' ') for c in ctx)
    assert 'FULL SYSTEM' in Bot.systems                          # 간결형이 기본인 Pro의 '전체'는 4항목 system
    s = ev.summarize('x', t)
    got = {(r.section, r.group, r.metric): r.value for r in s.itertuples()}
    assert got[('검색', '전체', '임베딩 Recall@10')] == 1.0
    assert got[('답변', 'A_lsa', '종합 점수')] == a.loc[a.eval_cat == 'A_lsa', 'total_v4'].mean().round(4)
    assert ('범위 밖', '범위 밖 전체', '거절률') in got and ('정답 없는 참고 조문', '비슷한 조문', '거절률') in got
    assert (tmp_path / 'summary_x.csv').exists()

def test_retrieval_without_reranker(tmp_path, env):
    ev.run_retrieval(fake_app(rerank=False), frame(), tmp_path / 'r.csv')
    assert 'rr_rank' not in pd.read_csv(tmp_path / 'r.csv', **E)

def test_main_refuses_v1(env, monkeypatch):
    env.setenv('LAWBOT_PROFILE', 'v1')
    monkeypatch.setattr(service, 'load_app', lambda: fake_app().__class__(deploy.get_profile('v1'), Ret(), None))
    monkeypatch.setattr('sys.argv', ['eval_v4', 'answers'])
    with pytest.raises(SystemExit, match='v4 프로필'):
        ev.main()

def test_eval_concise_refuses_v4_test_files(env):
    import lawbot.eval_concise as ec
    for f in ('qa_full_v4.csv', 'split_v4.csv'):
        env.setattr('sys.argv', ['eval_concise', '--val', str(DATA / 'v4' / f), '--tag', 'x'])
        with pytest.raises(SystemExit, match='평가 전용'):
            ec.main()
