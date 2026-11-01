# tests/test_eval_v4.py  →  v4 재평가 스크립트 (eval_v4): 인용 판정·문항 구성·단계별 실행·요약
# 실제 모델 대신 가짜 검색기·리랭커·sLLM으로 모든 단계를 끝까지 돌린다 (data/는 읽기만)
import json
import types

import numpy as np
import pandas as pd
import pytest

import lawbot.eval_v4 as ev
from lawbot import deploy, service
from lawbot.paths import DATA

E = dict(encoding='utf-8-sig')


# ---- 인용 판정 ----
@pytest.mark.parametrize(
    'answer,expected',
    [
        (
            '【결론】답\n【근거 조문】\n근로기준법 제26조(해고의 예고), 제27조\n【설명】 제99조',
            [('근로기준법', '제26조'), ('근로기준법', '제27조')],
        ),  # 법령명 없이 이어 쓴 번호는 앞 법령 · 설명은 제외
        (
            '【근거 조문】 남녀고용평등법 제19조(육아휴직) 제1항',
            [('남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률', '제19조')],
        ),
        (
            '【근거 조문】 근로자퇴직급여 보장법 제8조의2, 최저임금법 제6조',
            [('근로자퇴직급여 보장법', '제8조의2'), ('최저임금법', '제6조')],
        ),
        ('【근거 조문】 제26조', [(None, '제26조')]),  # 법령명이 없으면 법령 미상
        ('【근거 조문】\n해당 없음 (제공된 조문: 근로기준법 제34조)', []),  # 거절 형식의 제공 조문 목록은 인용이 아님
        (
            '근로기준법 제36조에 따라 14일 이내에 지급해야 합니다.',
            [('근로기준법', '제36조')],
        ),  # 섹션이 없으면 답변 전체
    ],
)
def test_citations(answer, expected):
    assert ev.citations(answer) == expected


def test_cite_gold_needs_law_and_no_refusal():
    assert ev.cite_gold('【결론】답\n【근거 조문】 퇴직급여법 제8조', '근로자퇴직급여 보장법 제8조')
    assert not ev.cite_gold(
        '【결론】답\n【근거 조문】 최저임금법 제8조', '근로자퇴직급여 보장법 제8조'
    )  # 번호만 같은 다른 법
    assert not ev.cite_gold('【근거 조문】 근로기준법 제8조의2', '근로기준법 제8조')
    assert not ev.cite_gold('【결론】확인할 수 없습니다.\n【근거 조문】 근로기준법 제8조', '근로기준법 제8조')


# ---- 문항 ----
def test_test_split_and_sample():
    t = ev.load_test()
    assert len(t) == 2075 and t['eval_cat'].value_counts().to_dict() == {'B_newlaw': 1425, 'A_lsa': 650}
    kw = ev.keywords()
    a = t[t.eval_cat == 'A_lsa']
    assert set(a['id']) <= set(kw.index)  # 기존 키워드 650문항과 같은 문항
    s = ev.answer_sample(t, 200)
    assert s['eval_cat'].value_counts().to_dict() == {'A_lsa': 100, 'B_newlaw': 100}
    assert s['id'].tolist() == ev.answer_sample(t, 200)['id'].tolist()  # 고정 시드
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
    assert '3년이하' in raw.loc['Q2443', 'must_include']  # 평가 파일 자체는 그대로


def test_oos_reclassification():
    oos = pd.read_csv(DATA / 'oos_eval.csv', **E)
    inside = oos[oos['source'].isin(ev.OOS_IN_SCOPE)]
    assert len(inside) == 27 and len(oos) - len(inside) == 75
    assert set(ev.OOS_IN_SCOPE.values()) <= set(ev.LAW_ALIASES)
    assert set(ev.LAW_ALIASES) == set(pd.read_csv(deploy.get_profile('v4').articles, **E)['law'])


# ---- 단계 실행 (가짜 모델) ----
ARTS = [
    ('근로기준법', '제26조', '해고의 예고'),
    ('근로기준법', '제36조', '금품 청산'),
    ('최저임금법', '제6조', '최저임금의 효력'),
    ('근로자퇴직급여 보장법', '제8조', '퇴직금제도의 설정 등'),
    ('임금채권보장법', '제7조', '퇴직한 근로자에 대한 대지급금의 지급'),
    ('파견근로자 보호 등에 관한 법률', '제5조', '근로자파견 대상 업무 등'),
]


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
        return [
            {'article_id': self.ids[i], 'title': self.titles[i], 'doc': self.docs[i], 'score': 0.5, 'law': self.laws[i]}
            for i in order
        ]


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
        text = (
            '【결론】\n확인할 수 없습니다.\n\n【근거 조문】\n해당 없음'
            if '날씨' in question
            else f'【결론】\n답입니다.\n\n【근거 조문】\n{h["law"]} {h["article_id"]}({h["title"]})'
        )
        for c in (text[:5], text[5:]):
            yield types.SimpleNamespace(
                text=c,
                generation_tps=10.0,
                finish_reason='stop',
                prompt_tokens=1,
                prompt_tps=1.0,
                generation_tokens=2,
                peak_memory=1.0,
            )


def fake_app(rerank=True):
    p = deploy.get_profile('v4')
    models = service.ChatModels(p.models, Bot)
    return service.LawbotApp(p, Ret(), models, RR() if rerank else None, 'FULL SYSTEM')


def frame(n=6):
    rows = [
        {
            'id': f'T{i}',
            'question': '질문' * (i + 1),
            'article_id': f'{l} {a}',
            'eval_cat': 'A_lsa' if i % 2 else 'B_newlaw',
            'confusable_with': None,
        }
        for i, (l, a, _) in enumerate(ARTS[:n])
    ]
    return pd.DataFrame(rows)


def test_stages_end_to_end_and_resume(tmp_path, env):
    env.setattr(ev, 'OUT', tmp_path)
    app, t = fake_app(), frame()
    kw = pd.DataFrame(
        {
            'must_include': '[["답"]]',
            'must_not_include': '[]',
            'must_not_in_conclusion': '[]',
            'article_id': '제26조',
            'confusable_with': '',
        },
        index=t.loc[t.eval_cat == 'A_lsa', 'id'],
    )
    env.setattr(ev, 'keywords', lambda: kw)
    for _ in range(2):  # 두 번째 실행은 이미 끝난 문항을 건너뜀
        ev.run_retrieval(app, t, tmp_path / 'retrieval_x.csv')
        ev.run_answers(app, 'pro', 'concise', t, tmp_path / 'answers_x.csv')
        ev.run_oos(app, 'pro', 'concise', tmp_path / 'oos_x.csv')
        ev.run_refusal(app, 'pro', 'full', t, tmp_path / 'refusal_x.csv')
    r = pd.read_csv(tmp_path / 'retrieval_x.csv', **E)
    assert len(r) == 6 and r['id'].is_unique
    assert ((r['emb_rank'] >= 1) & (r['emb_rank'] <= 6)).all() and (r['rr_rank'] <= 6).all()
    a = pd.read_csv(tmp_path / 'answers_x.csv', **E)
    assert len(a) == 6 and (a['cite_v4'] == a['top3'].str.split('|').str[0].eq(a['gold'])).all()
    assert (
        a.loc[a.eval_cat == 'A_lsa', 'total_v4'].notna().all()
        and a.loc[a.eval_cat == 'B_newlaw', 'total_v4'].isna().all()
    )
    assert {'search', 'rerank', 'ttft', 'total'} <= set(a.columns)  # 웹앱 경로(ask) 측정값
    o = pd.read_csv(tmp_path / 'oos_x.csv', **E)
    assert len(o) == 122 and o['scope'].value_counts().to_dict() == {'out': 75, 'in_reclassified': 27, 'in_control': 20}
    assert not o.loc[o['scope'] == 'out', 'cites_law'].any()  # 범위 밖은 '해당 법률'이 없음
    f = pd.read_csv(tmp_path / 'refusal_x.csv', **E)
    assert len(f) == 12 and f['test'].value_counts().to_dict() == {'다른 법률 조문': 6, '비슷한 조문': 6}
    for _, x in f.iterrows():
        gold = t.set_index('id').loc[x['id'].split(':')[0], 'article_id']
        ctx = x['context'].split('|')
        assert len(ctx) == 3 and gold not in ctx
        if x['test'] == '다른 법률 조문':
            assert not any(c.startswith(gold.rsplit(' ', 1)[0] + ' ') for c in ctx)
    assert 'FULL SYSTEM' in Bot.systems  # 간결형이 기본인 Pro의 '전체'는 4항목 system
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


def test_default_stage_is_all(env, monkeypatch, tmp_path):
    # 단계를 생략하면 네 단계를 차례로 실행하고 요약까지
    calls = []
    env.setenv('LAWBOT_MODELS', 'pro')  # 맥 미니 .env와 같은 구성
    monkeypatch.setattr(ev, 'OUT', tmp_path)
    monkeypatch.setattr(service, 'load_app', fake_app)
    for name in ('run_retrieval', 'run_answers', 'run_oos', 'run_refusal'):
        monkeypatch.setattr(ev, name, lambda *a, n=name, **k: calls.append(n))
    monkeypatch.setattr(ev, 'summarize', lambda tag, test: calls.append(('summary', tag)))
    monkeypatch.setattr('sys.argv', ['lawbot-eval-v4'])
    ev.main()
    assert calls == ['run_retrieval', 'run_answers', 'run_oos', 'run_refusal', ('summary', 'pro_concise_rr')]


def test_rescore_updates_only_scores(tmp_path, env):
    env.setattr(ev, 'OUT', tmp_path)
    ans = '【결론】\n제공된 조문은 노동법 내용이라 날씨에 대한 근거를 찾을 수 없습니다.\n\n【근거 조문】\n근로기준법 제26조(해고의 예고)'
    pd.DataFrame(
        [
            {
                'id': 'OOS001',
                'scope': 'out',
                'category': 'unrelated',
                'source': '날씨',
                'refusal': False,
                'cited': '근로기준법 제26조',
                'cites_any': True,
                'cites_law': False,
                'cite_gold': 0,
                'retrieved_law': False,
                'top3': 'x',
                'total': 3.5,
                'answer': ans,
            }
        ]
    ).to_csv(tmp_path / 'oos_t.csv', index=False, **E)
    ev.rescore('t')
    o = pd.read_csv(tmp_path / 'oos_t.csv', **E).iloc[0]
    assert bool(o['refusal']) and not bool(o['cites_any'])  # 거절하며 제공 조문을 나열한 것은 억지적용 아님
    assert o['answer'] == ans and o['total'] == 3.5  # 답변·측정값은 그대로


def test_rescore_keeps_measured_floats(tmp_path, env):
    """채점 규칙이 같으면 재채점해도 파일이 바이트 단위로 같다 (측정값 마지막 자리 포함, pandas 버전과 무관)"""
    env.setattr(ev, 'OUT', tmp_path)
    ans = '【결론】\n거절합니다. 근거를 찾을 수 없습니다.'
    pd.DataFrame(
        [
            {
                'id': 'X:similar',
                'test': '비슷한 조문',
                'context': 'a|b|c',
                'refusal': True,
                'cited': '',
                'sec': 0.025005457981023937,
                'answer': ans,
            }
        ]
    ).to_csv(tmp_path / 'refusal_t.csv', index=False, **E)
    before = (tmp_path / 'refusal_t.csv').read_bytes()
    ev.rescore('t')
    assert (tmp_path / 'refusal_t.csv').read_bytes() == before
    assert '0.025005457981023937' in (tmp_path / 'refusal_t.csv').read_text(encoding='utf-8-sig')


# ---- 실패 유형 (fail_<tag>.csv) ----
def failed_independent(stage, d):
    """실패 문항 수를 실패 유형과 따로 센다: 지표 정의 그대로"""
    if stage == 'answers':
        kw = d['total_v4'].notna() & (d['eval_cat'] == 'A_lsa')
        return int(((d['cite_v4'] == 0) | (kw & (d['total_v4'] < 1))).sum())
    if stage == 'oos':
        return int(
            ((d['scope'] == 'out') & ~d['refusal']).sum()
            + ((d['scope'] == 'in_reclassified') & ~d['cites_law']).sum()
            + ((d['scope'] == 'in_control') & (d['cite_gold'] == 0)).sum()
        )
    return int((~d['refusal']).sum())


def test_classify_saved_test_results(tmp_path, env):
    """10-25 test 결과 재채점: 실패 유형 합계 = 실패 문항 수, 결과 CSV는 바뀌지 않음"""
    import shutil

    src = ev.RESULTS / 'v4'
    for f in src.glob('*_pro_concise_rr.csv'):
        shutil.copy(f, tmp_path / f.name)
    before = {f.name: f.read_bytes() for f in tmp_path.iterdir()}
    env.setattr(ev, 'OUT', tmp_path)
    f = ev.classify('pro_concise_rr')
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name in before} == before
    assert set(f['fail_type']) - {''} <= set(ev.FAIL_TYPES)
    for stage in ('answers', 'oos', 'refusal'):
        d = pd.read_csv(tmp_path / f'{stage}_pro_concise_rr.csv', **E)
        got = f[(f['stage'] == stage) & (f['fail_type'] != '')]
        assert len(f[f['stage'] == stage]) == len(d) and len(got) == failed_independent(stage, d)
    counts = f[f['fail_type'] != ''].groupby(['stage', 'fail_type']).size().to_dict()
    assert counts == {
        ('answers', 'retrieval_miss'): 7,
        ('answers', 'rerank_drop'): 10,
        ('answers', 'false_refusal'): 2,
        ('answers', 'missing_keyword'): 16,
        ('answers', 'forbidden_phrase'): 1,
        ('oos', 'answered_without_basis'): 3,
        ('oos', 'false_refusal'): 4,
        ('oos', 'not_retrieved'): 1,
        ('oos', 'cite_missing'): 2,
        ('refusal', 'answered_without_basis'): 31,
    }
    assert (tmp_path / 'fail_pro_concise_rr.csv').exists()


def row(**kw):
    base = {
        'id': 'Q1',
        'eval_cat': 'B_newlaw',
        'gold': '근로기준법 제26조',
        'retrieved': True,
        'refusal': False,
        'cite_v4': 0,
        'total_v4': float('nan'),
        'group_score': float('nan'),
        'missing_groups': '',
        'must_not_hits': '',
        'top3': '근로기준법 제26조|최저임금법 제6조|근로기준법 제43조',
        'cited': '',
        'finish': 'stop',
        'answer': '【결론】\n답입니다.\n\n【근거 조문】\n근로기준법 제43조',
    }
    return pd.Series(base | kw)


@pytest.mark.parametrize(
    'kw,ranks,expected',
    [
        ({'cite_v4': 1}, {}, ''),  # 통과
        ({'retrieved': False}, {'Q1': (16, 999)}, 'retrieval_miss'),
        ({'retrieved': False}, {'Q1': (4, 5)}, 'rerank_drop'),
        ({'retrieved': False}, {}, 'not_retrieved'),  # retrieval 결과 없음
        ({'refusal': True}, {}, 'false_refusal'),
        ({'answer': '【결론】\n답\n【근거 조문】\n최저임금법 제26조'}, {}, 'cite_wrong_law'),
        ({'answer': '【결론】\n답\n【근거 조문】\n근로기준법 제36조'}, {}, 'cite_outside'),
        ({'answer': '【결론】\n답\n【근거 조문】\n근로기준법 제35조'}, {}, 'cite_nonexistent'),  # 삭제 조문
        ({'eval_cat': 'A_lsa', 'cite_v4': 1, 'total_v4': 0.5, 'group_score': 0.5}, {}, 'missing_keyword'),
        ({'eval_cat': 'A_lsa', 'cite_v4': 1, 'total_v4': 0.5, 'group_score': 1.0}, {}, 'forbidden_phrase'),
        ({'eval_cat': 'A_lsa', 'cite_v4': 1}, {}, ''),  # val A_lsa: 키워드 기준 없음
        ({'answer': '답만 씀'}, {}, 'format'),
        ({'answer': '【결론】\n답이', 'finish': 'length'}, {}, 'truncated'),
        ({}, {}, 'cite_missing'),  # 검색 결과의 다른 조문만 인용
    ],
)
def test_fail_answer_rules(kw, ranks, expected):
    assert ev.fail_answers(row(**kw), ranks, ev.article_keys())[0] == expected


# ---- val 분할 ----
def test_val_split_and_sample():
    v = ev.load_test('val')
    assert len(v) == 1110 and v['eval_cat'].value_counts().to_dict() == {'B_newlaw': 719, 'A_lsa': 391}
    assert not set(v['id']) & set(ev.load_test()['id'])
    s = ev.answer_sample(v, 200)
    assert set(s['id']) == set(pd.read_csv(DATA / 'v4' / 'decree_ctx_val200.csv', **E)['id'])  # 시행령 실험과 같은 표본
    a = s[s.eval_cat == 'A_lsa'].iloc[0]
    sc = ev.score_answer(
        a['id'], 'A_lsa', a['article_id'], f'【결론】답\n【근거 조문】{a["article_id"]}', ev.keywords()
    )
    assert sc['cite_v4'] == 1 and sc['total_v4'] is None  # val에는 키워드 기준이 없음


def test_val_skips_oos_and_records_run(env, monkeypatch, tmp_path):
    calls = []
    env.setenv('LAWBOT_MODELS', 'pro')
    monkeypatch.setattr(ev, 'OUT', tmp_path)
    monkeypatch.setattr(service, 'load_app', fake_app)
    for name in ('run_retrieval', 'run_answers', 'run_oos', 'run_refusal'):
        monkeypatch.setattr(ev, name, lambda *a, n=name, **k: calls.append((n,)))
    monkeypatch.setattr(ev, 'summarize', lambda tag, test: calls.append(('summary', tag, len(test))))
    monkeypatch.setattr('sys.argv', ['lawbot-eval-v4', '--split', 'val'])
    ev.main()
    assert [c[0] for c in calls] == ['run_retrieval', 'run_answers', 'run_refusal', 'summary']  # 범위 밖은 평가 전용
    assert calls[-1] == ('summary', 'pro_concise_rr_val', 1110)
    meta = json.loads((tmp_path / 'run_pro_concise_rr_val.json').read_text(encoding='utf-8'))
    run = meta['runs'][0]
    assert (run['mode'], run['split'], run['model'], run['fmt']) == ('api', 'val', 'pro', 'concise')
    assert run['model_id'] == 'qwen3.8-27b-4bit' and run['emb_model'] == 'lawbot-emb-v4'
    assert run['info'] is None  # 가짜 검색기에는 모델 서버 클라이언트가 없음 (실제는 /v1/models 목록)
    assert run['dataset']['dataset_version'] == 'v4' and 'commit' in run
    ev.main()  # 이어서 실행하면 실행 기록이 하나 더
    assert len(json.loads((tmp_path / 'run_pro_concise_rr_val.json').read_text(encoding='utf-8'))['runs']) == 2
    monkeypatch.setattr('sys.argv', ['lawbot-eval-v4', 'oos', '--split', 'val'])
    with pytest.raises(SystemExit, match='평가 전용'):
        ev.main()


# ---- compare ----
def write_summary(path, cite, n=200, refusal_hit=0.01):
    pd.DataFrame(
        [
            {'section': '답변', 'group': '전체', 'metric': '조문 인용 정확도(법령명 포함)', 'value': cite, 'n': n},
            {'section': '답변', 'group': '전체', 'metric': '거짓 거절률(검색 적중)', 'value': refusal_hit, 'n': n},
            {'section': '검색', 'group': '전체', 'metric': '리랭킹 Recall@3', 'value': 0.94, 'n': 1110},
        ]
    ).to_csv(path, index=False, **E)


def test_compare_warns(tmp_path, env, capsys):
    env.setattr(ev, 'OUT', tmp_path)
    write_summary(tmp_path / 'summary_a.csv', 0.90)
    write_summary(tmp_path / 'summary_b.csv', 0.89)  # 0.01 하락: 기준(0.02) 안
    assert ev.compare('a', 'b') == []
    write_summary(tmp_path / 'summary_c.csv', 0.85, refusal_hit=0.03)
    warn = ev.compare('a', 'c')
    assert any('조문 인용 정확도' in w for w in warn) and any('거짓 거절률(검색 적중)' in w for w in warn)
    write_summary(tmp_path / 'summary_d.csv', 0.90, n=100)
    assert any('문항 수가 다름' in w for w in ev.compare('a', 'd'))
    for t, ft in (('a', 'cite_missing'), ('c', 'false_refusal')):
        pd.DataFrame([{'stage': 'answers', 'id': 'Q9', 'group': 'A_lsa', 'fail_type': ft, 'detail': ''}]).to_csv(
            tmp_path / f'fail_{t}.csv', index=False, **E
        )
    capsys.readouterr()
    ev.compare('a', 'c')
    out = capsys.readouterr().out
    assert '[실패 유형별 건수]' in out and 'c answers false_refusal: Q9' in out
    with pytest.raises(SystemExit, match=r'summary_x.csv이 없습니다'):
        ev.compare('a', 'x')
    env.setattr('sys.argv', ['lawbot-eval-v4', 'compare', '--base', 'a', '--new', 'c', '--strict'])
    with pytest.raises(SystemExit) as e:
        ev.main()
    assert e.value.code == 1


def test_resume_concurrent_keeps_order_and_resumes(tmp_path, monkeypatch):
    """--concurrency: 동시에 풀어도 파일에는 문항 순서대로, 이미 있는 id는 건너뜀, 처리량 기록"""
    import threading
    import time as _time

    monkeypatch.setattr(ev, 'WORKERS', 3)
    monkeypatch.setattr(ev, 'STAGE_TIMES', {})
    running, peak, lock = [0], [0], threading.Lock()

    def fn(i, delay):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        _time.sleep(delay)  # 앞 문항이 더 늦게 끝남
        with lock:
            running[0] -= 1
        return {'id': i, 'v': int(i[1:]) * 10}

    path = tmp_path / 'r.csv'
    items = [(f'q{i}', 0.03 * (6 - i)) for i in range(6)]
    ev.resume(path, items[:4], fn, 'answers')
    ev.resume(path, items, fn, 'answers')
    df = pd.read_csv(path, encoding='utf-8-sig')
    assert df['id'].tolist() == [f'q{i}' for i in range(6)] and df['v'].tolist() == [0, 10, 20, 30, 40, 50]
    assert peak[0] == 3
    assert ev.STAGE_TIMES['answers']['n'] == 2  # 두 번째 실행에서 남은 2개
