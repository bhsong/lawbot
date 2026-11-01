# tests/test_decree.py  →  시행령·시행규칙(하위법령) 첨부 (docs/archives/decree-attach.md). 모델 파일 불필요
#   필터가 val 실험(same1+other0.9 규칙, 저장된 fp16 점수·참고 조문)과 같은지, 첨부를 끈 구성·Fast가 지금과 같은지,
#   812조문 검색기·인용 판정·화면·평가 경로
import dataclasses
import json
import types

import numpy as np
import pandas as pd
import pytest

import lawbot.app as app_mod
import lawbot.eval_v4 as ev
import lawbot.retriever as retriever_mod
import lawbot.service as service
from lawbot import answer_format, citation, decree, deploy, manifest
from lawbot.llm import build_prompt
from lawbot.paths import DATA, V4_ARTICLES, V4_DECREE_ARTICLES

E = dict(encoding='utf-8-sig')
DIR = DATA / 'decree_exp'


# ---- 1. 필터: 저장된 fp16 점수로 실험 값 재현 ----
def load_articles():
    """812조문 표 (실험 후보 번호 j = 이 표의 순번). 실험 때 토큰 수 파일과 순서가 같은지 확인"""
    art = pd.read_csv(V4_DECREE_ARTICLES, **E)
    art = art[art.deleted == 0].reset_index(drop=True)
    tok = pd.read_csv(DIR / 'doc_tokens.csv', **E)
    assert len(art) == 812 and (art.article_id.values == tok.article_id.values).all(), '조문 순서 불일치'
    return art


def article_table():
    """실험(eval_decree) 프롬프트용 조문 dict: Retriever.search가 돌려주는 형식, 키는 '법령명 제N조'"""
    a = pd.read_csv(V4_DECREE_ARTICLES, **E)
    a = a[a['deleted'] == 0]
    return {
        r.article_id: {
            'article_id': r.article_no,
            'title': r.title,
            'law': r.law,
            'score': 0.0,
            'doc': f'{r.law} {r.article_no}({r.title})\n{r.text}',
        }
        for r in a.itertuples()
    }


@pytest.fixture(scope='module')
def exp():
    """(조문 표, 하위법령 val 444, 법률 val 1,110) 각 문항의 (법률 후보, 하위법령 후보) — Colab 임베딩 순서·fp16 점수"""
    art = load_articles()
    qa, sp = pd.read_csv(DIR / 'qa_decree_v4.csv', **E), pd.read_csv(DIR / 'split_decree_v4.csv', **E)
    dv = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    dv = dv[dv.split_emb == 'val'].reset_index(drop=True)
    rp = pd.read_csv(DIR / 'rr_per_question.csv', **E)

    def load(scores, n, npz):
        sc = {(int(q), int(j)): float(x) for q, j, x in pd.read_csv(DIR / scores).itertuples(index=False)}
        z = np.load(DIR / npz)
        return [
            ([(int(j), sc[(q, int(j))]) for j in z['T_law'][q]], [(int(j), sc[(q, int(j))]) for j in z['T_dec'][q]])
            for q in range(n)
        ]

    return (
        art,
        dv,
        load('dq_rr_scores.csv', len(dv), 'dq_candidates.npz'),
        rp,
        load('rr_scores.csv', len(rp), 'rr_candidates.npz'),
    )


def webapp_attach(art, law, dec, tau=0.9, k=2):
    """웹앱과 같은 순서로: 법률 후보(임베딩 순) → rank_order → 1위 법령, 하위법령 후보(임베딩 순) → decree.attach
    (법률 top3 순번, 첨부 순번)"""
    parent = art.parent_law.values
    law_order = [law[i][0] for i in decree.rank_order([s for _, s in law])]
    cands = [{'j': j, 'rerank_score': s, 'emb_rank': r, 'parent_law': parent[j]} for r, (j, s) in enumerate(dec, 1)]
    return law_order[:3], [c['j'] for c in decree.attach(cands[::-1], parent[law_order[0]], tau, k)]  # 입력 순서와 무관


def test_filter_reproduces_val_numbers(exp):
    art, dv, dc, rp, lc = exp
    idx = {a: i for i, a in enumerate(art.article_id)}
    parent = art.parent_law.values
    hit = [idx[g] in webapp_attach(art, a, b)[1] for g, (a, b) in zip(dv.article_id, dc)]
    assert len(hit) == 444 and round(np.mean(hit), 3) == 0.921
    att = [webapp_attach(art, a, b)[1] for a, b in lc]
    other = sum(parent[j] != parent[idx[g]] for g, js in zip(rp.gold, att) for j in js)
    assert len(att) == 1110 and round(other / sum(map(len, att)), 3) == 0.055


def test_filter_matches_saved_contexts(exp):
    art, dv, dc, rp, lc = exp
    aid = art.article_id.values
    ctx = lambda law, dec: '|'.join(aid[j] for j in webapp_attach(art, law, dec)[0] + webapp_attach(art, law, dec)[1])
    pos = {q: i for i, q in enumerate(dv.id)}
    d = pd.read_csv(DATA / 'v4' / 'decree_ctx_dq_val200.csv', dtype=str, **E)
    assert len(d) == 200 and [ctx(*dc[pos[q]]) for q in d.id] == d.ctx_o09.tolist()
    pos = {q: i for i, q in enumerate(rp.id)}
    v = pd.read_csv(DATA / 'v4' / 'decree_ctx_val200_rules.csv', dtype=str, **E)
    assert len(v) == 200 and [ctx(*lc[pos[q]]) for q in v.id] == v.ctx_o09.tolist()


# ---- 2. 동점·통과 없음 ----
def test_attach_ties_and_empty():
    c = [
        {'a': 'x', 'rerank_score': 0.95, 'emb_rank': 3, 'parent_law': 'B'},
        {'a': 'y', 'rerank_score': 0.95, 'emb_rank': 1, 'parent_law': 'B'},
        {'a': 'z', 'rerank_score': 0.2, 'emb_rank': 2, 'parent_law': 'A'},
        {'a': 'w', 'rerank_score': 0.5, 'emb_rank': 4, 'parent_law': 'C'},
    ]
    assert [h['a'] for h in decree.attach(c, 'A', 0.9, 2)] == ['y', 'x']  # 동점은 임베딩 순위 먼저
    assert [h['a'] for h in decree.attach(c, 'A', 0.9, 3)] == ['y', 'x', 'z']  # 같은 법은 점수와 관계없이
    assert [h['a'] for h in decree.attach(c, 'A', 0.99, 2)] == ['z']
    assert decree.attach(c, 'D', 0.99, 2) == [] and decree.attach([], 'A', 0.9, 2) == []
    assert decree.rank_order([0.5, 0.9, 0.5]) == [1, 0, 2]


# ---- 3. 검색기: 법률 임베딩은 그대로, 하위법령은 따로 3,072 ----
class Encoder:
    """api_client.Embedder 대역: (문서 목록, max_seq, 'query' 또는 None)을 기록"""

    calls = []

    def encode(self, texts, input_type='document', max_seq_length=None):
        Encoder.calls.append((list(texts), max_seq_length, 'query' if input_type == 'query' else None))
        v = np.zeros((len(texts), 64), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 64] += 1
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


@pytest.fixture
def enc(env):
    Encoder.calls = []
    return Encoder()


def test_retriever_law_encoding_unchanged(enc):
    off = retriever_mod.Retriever(enc, law_csv=V4_ARTICLES, max_seq_length=2304)
    off_calls, Encoder.calls = Encoder.calls, []
    on = retriever_mod.Retriever(enc, law_csv=V4_DECREE_ARTICLES, max_seq_length=2304, decree_max_seq=3072)
    assert len(off_calls) == 1 and len(Encoder.calls) == 2
    assert Encoder.calls[0] == off_calls[0] and off_calls[0][1] == 2304  # 같은 문서 목록·같은 max_seq
    assert len(Encoder.calls[1][0]) == 397 and Encoder.calls[1][1] == 3072
    assert np.array_equal(off.D, on.D) and on.D_dec.shape[0] == 397 and on.max_seq_length == 2304
    assert (on.n_law, len(on.ids)) == (415, 812) and list(on.ids[:415]) == list(off.ids)
    assert set(on.kinds[:415]) == {'법률'} and set(on.kinds[415:]) == {'시행령', '시행규칙'}
    q = '체불 융자 이자 몇프로 이하에요?'
    assert on.search(q, 10) == off.search(q, 10)
    d = on.search_decrees(q, 10)
    assert len(d) == 10 and all(
        h['kind'] in ('시행령', '시행규칙') and ' 시행' in h['law'] and h['law'].startswith(h['parent_law']) for h in d
    )
    assert d[0]['doc'].startswith(f'{d[0]["law"]} {d[0]["article_id"]}({d[0]["title"]})\n')
    assert (on.rank_all([q]) < 415).all()
    with pytest.raises(ValueError, match='하위법령 검색 인덱스'):
        off.search_decrees(q)
    law_only = retriever_mod.Retriever(enc, law_csv=V4_DECREE_ARTICLES, max_seq_length=2304)
    assert len(law_only.ids) == 415 and np.array_equal(law_only.D, off.D)  # 하위법령을 끄면 812 CSV도 법률만


def test_prompt_format_matches_experiment(enc):
    """웹앱 hit(법률 3 + 하위법령 2)의 프롬프트가 실험(eval_decree)의 조문 표로 만든 프롬프트와 글자 단위로 같다"""
    R = retriever_mod.Retriever(enc, law_csv=V4_DECREE_ARTICLES, max_seq_length=2304, decree_max_seq=3072)
    keys = [f'{l} {a}' for l, a in zip(R.laws, R.ids)]
    table = article_table()
    row = pd.read_csv(DATA / 'v4' / 'decree_ctx_dq_val200.csv', dtype=str, **E).iloc[0]
    ctx = row.ctx_o09.split('|')
    hits = []
    for k in ctx:
        i = keys.index(k)
        h = R._hit(i, 0.5) | {'rerank_score': 0.9, 'emb_rank': 1}
        hits.append(h | {'kind': R.kinds[i], 'parent_law': R.parent_laws[i]} if i >= R.n_law else h)
    assert build_prompt(hits, row.question) == build_prompt([table[k] for k in ctx], row.question)
    assert f'\n\n[4] {ctx[3]}(' in build_prompt(hits, row.question)
    assert build_prompt(hits[:3], 'q') == build_prompt([table[k] for k in ctx[:3]], 'q')


# ---- 4. 서비스: 꺼짐·Fast는 지금과 같음, Pro만 첨부 ----
LAWS = [
    ('근로기준법', '제2조', '정의'),
    ('근로기준법', '제26조', '해고의 예고'),
    ('임금채권보장법', '제7조', '퇴직한 근로자에 대한 대지급금의 지급'),
    ('최저임금법', '제6조', '최저임금의 효력'),
]
DECS = [
    ('근로기준법 시행령', '제2조', '평균임금의 계산에서 제외되는 기간과 임금', '시행령', '근로기준법'),
    ('임금채권보장법 시행령', '제24조', '융자 이자', '시행령', '임금채권보장법'),
    ('근로기준법 시행규칙', '제2조', '서식', '시행규칙', '근로기준법'),
    ('최저임금법 시행령', '제5조', '환산', '시행령', '최저임금법'),
]


class DecRet:
    """812조문 검색기와 같은 속성 (법률 먼저, 하위법령 뒤). 질문 길이로 순서를 정하는 결정적 검색"""

    def __init__(self, *a, decree=True, **kw):
        rows = LAWS + ([d[:3] for d in DECS] if decree else [])
        self.n_law = len(LAWS)
        self.laws = np.array([r[0] for r in rows], dtype=object)
        self.ids = np.array([r[1] for r in rows], dtype=object)
        self.titles = np.array([r[2] for r in rows], dtype=object)
        self.docs = [f'{l} {a}({t})\n본문 {a}' for l, a, t in rows]
        self.kinds = np.array(['법률'] * len(LAWS) + ([d[3] for d in DECS] if decree else []), dtype=object)
        self.parent_laws = np.array([r[0] for r in LAWS] + ([d[4] for d in DECS] if decree else []), dtype=object)
        self.decree_calls = 0

    def rank_all(self, queries):
        return np.array([np.roll(np.arange(self.n_law), -(len(q) % self.n_law)) for q in queries])

    def _hit(self, i, s):
        return {
            'article_id': self.ids[i],
            'title': self.titles[i],
            'doc': self.docs[i],
            'score': s,
            'law': self.laws[i],
        }

    def search(self, q, k=3):
        return [self._hit(int(i), 0.9 - 0.1 * r) for r, i in enumerate(self.rank_all([q])[0][:k])]

    def search_decrees(self, q, k=10):
        self.decree_calls += 1
        order = np.roll(np.arange(len(DECS)), -(len(q) % len(DECS)))[:k] + self.n_law
        return [
            self._hit(int(i), 0.8 - 0.1 * r) | {'kind': self.kinds[i], 'parent_law': self.parent_laws[i]}
            for r, i in enumerate(order)
        ]


class RR:
    """Reranker.rerank와 같은 출력. 점수는 SCORES(조문 키), 없으면 0.5. ERROR를 넣으면 하위법령 리랭킹에서 오류"""

    candidates = 4
    SCORES = {}
    ERROR = None

    def rerank(self, q, cands, top_k=3):
        if self.ERROR and 'kind' in cands[0]:
            raise self.ERROR
        s = [self.SCORES.get(f'{c["law"]} {c["article_id"]}', 0.5) for c in cands]
        return [cands[i] | {'rerank_score': s[i], 'emb_rank': i + 1} for i in decree.rank_order(s)[:top_k]]


class Bot:
    """받은 hits를 기록하고, 마지막 hit(첨부가 있으면 하위법령)을 인용하는 답변"""

    def __init__(self, spec):
        self.spec, self.calls = spec, []

    def stream(self, question, hits, system=None, **kw):
        self.calls.append(hits)
        h = hits[-1]
        text = f'【결론】\n답입니다.\n\n【근거 조문】\n{h["law"]} {h["article_id"]}({h["title"]})'
        for c in (text[:5], text[5:]):
            yield types.SimpleNamespace(
                text=c,
                generation_tps=10.0,
                finish_reason='stop',
                prompt_tokens=100,
                prompt_tps=1.0,
                generation_tokens=2,
                peak_memory=1.0,
            )


DC = deploy.DecreeConfig(0.9, 2, ('pro',))


def make(dc=DC, ret=None):
    p = deploy.get_profile()
    models = service.ChatModels(p.models, Bot)
    return service.LawbotApp(p, ret or DecRet(decree=dc is not None), models, RR(), 'FULL', dc)


def ask(a, q, model, **kw):
    return list(service.ask(a, q, model, **kw))[-1]


@pytest.fixture
def rr(env):
    env.setattr(RR, 'SCORES', {})
    env.setattr(RR, 'ERROR', None)
    return env


def test_off_and_fast_unchanged(rr, tmp_path):
    log = tmp_path / 'req.jsonl'
    rr.setenv('LAWBOT_REQUEST_LOG', str(log))
    off, on = make(None), make()
    for q in ('해고 예고?', '체불 융자 이자 몇프로?', '평균임금'):
        _, h_off, raw_off, st_off = ask(off, q, 'fast')
        _, h_on, raw_on, st_on = ask(on, q, 'fast')
        assert h_on == h_off and raw_on == raw_off and len(h_on) == 3
        assert 'decree_rerank' not in st_on and 'n_decree' not in st_on
        _, h_pro_off, _, _ = ask(off, q, 'pro')
        _, h_pro, _, st = ask(on, q, 'pro')
        assert h_pro[:3] == h_pro_off == h_off  # 법률 3개는 하위법령을 켜도 같음
        assert all(decree.is_decree(h) for h in h_pro[3:]) and len(h_pro) == 3 + st['n_decree']
        assert all(not decree.is_decree(h) and 'kind' not in h for h in h_pro[:3])
    assert on.retriever.decree_calls == 3 and off.retriever.decree_calls == 0  # Fast는 하위법령을 찾지 않음
    recs = [json.loads(x) for x in log.read_text().splitlines()]
    fast = [r for r in recs if r['model'] == 'fast']
    assert all(r['decree'] is False and r['n_decree'] is None and r['decree_rerank_ms'] is None for r in fast)
    pro_on = [r for r in recs if r['model'] == 'pro' and r['decree']]
    assert len(pro_on) == 3 and all(r['decree_rerank_ms'] is not None and r['n_decree'] >= 0 for r in pro_on)
    assert all('질문' not in json.dumps(r, ensure_ascii=False) for r in recs)


def test_attached_order_and_prompt(rr):
    rr.setattr(
        RR,
        'SCORES',
        {
            '임금채권보장법 시행령 제24조': 0.97,
            '근로기준법 시행령 제2조': 0.1,
            '최저임금법 시행령 제5조': 0.95,
            '근로기준법 시행규칙 제2조': 0.2,
        },
    )
    on = make()
    q = '해고 예고?'  # 법률 1위: 근로기준법 (점수 동점 → 임베딩 순서)
    _, hits, _, st = ask(on, q, 'pro')
    top1 = hits[0]['law']
    dec = hits[3:]
    want = decree.attach(st['decree_candidates'], top1, 0.9, 2)
    assert dec == want and len(dec) == 2
    assert [h['parent_law'] == top1 or h['rerank_score'] >= 0.9 for h in dec] == [True, True]
    assert on.models.llms['pro'].calls[-1] == hits  # LLM에 넘긴 참고 조문 = 화면 hits
    prompt = build_prompt(hits, q)
    assert prompt.index('[3] ') < prompt.index('[4] ' + dec[0]['law']) < prompt.index('[5] ' + dec[1]['law'])


def test_no_decree_passes_prompt_unchanged(rr):
    """다른 법 하위법령만 있고 점수가 τ 미만 → 첨부 없음. hits·LLM 입력·프롬프트가 꺼짐과 글자 단위로 같음"""
    rr.setattr(RR, 'SCORES', {f'{d[0]} {d[1]}': 0.3 for d in DECS})
    on, off = make(), make(None)
    on.retriever.parent_laws[on.retriever.n_law :] = '다른 법'  # 어떤 하위법령도 법률 1위와 같은 법이 아님
    q = '최저임금'
    _, h_on, _, st = ask(on, q, 'pro')
    _, h_off, _, _ = ask(off, q, 'pro')
    assert st['n_decree'] == 0 and len(st['decree_candidates']) == 4 and h_on == h_off
    assert build_prompt(h_on, q) == build_prompt(h_off, q)
    assert on.models.llms['pro'].calls[-1] == off.models.llms['pro'].calls[-1]


def test_decree_rerank_error_not_hidden(rr, tmp_path):
    log = tmp_path / 'req.jsonl'
    rr.setenv('LAWBOT_REQUEST_LOG', str(log))
    rr.setattr(RR, 'ERROR', RuntimeError('리랭커 고장'))
    with pytest.raises(RuntimeError, match='리랭커 고장') as e:
        ask(make(), '해고', 'pro')
    assert e.value.lawbot_error == 'rerank'
    assert json.loads(log.read_text())['error_type'] == 'rerank'


# ---- 5. 설정 ----
def test_decree_config(env):
    assert deploy.decree(deploy.get_profile()) is None and deploy.get_profile().articles == V4_ARTICLES
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    with pytest.raises(ValueError, match='리랭커가 필요'):
        deploy.decree(deploy.get_profile())
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    p = deploy.get_profile()
    assert p.articles == V4_DECREE_ARTICLES and p.max_seq == 2304
    assert deploy.decree(p) == deploy.DecreeConfig(0.9, 2, ('pro',), 10, 3072)
    for var, bad in (
        ('LAWBOT_DECREE_TAU', '1.5'),
        ('LAWBOT_DECREE_TAU', 'x'),
        ('LAWBOT_DECREE_K', '0'),
        ('LAWBOT_DECREE_MODELS', 'pro,big'),
        ('LAWBOT_DECREE_ENABLED', 'yes'),
    ):
        env.setenv(var, bad)
        with pytest.raises(ValueError, match=var):
            deploy.decree(deploy.get_profile())
        env.delenv(var)
        env.setenv('LAWBOT_DECREE_ENABLED', '1')
    env.setenv('LAWBOT_DECREE_TAU', '0.8')
    env.setenv('LAWBOT_DECREE_K', '3')
    env.setenv('LAWBOT_DECREE_MODELS', 'fast,pro')
    assert deploy.decree(deploy.get_profile()) == deploy.DecreeConfig(0.8, 3, ('fast', 'pro'))


def test_load_app_with_decree(env, tmp_path):
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    made = []
    env.setattr(service, 'Retriever', lambda *a, **kw: made.append(kw) or DecRet())
    env.setattr(service, 'LLM', lambda *a, **kw: Bot(None))
    env.setattr(service, 'Reranker', lambda *a, **kw: RR())
    with pytest.raises(ValueError, match='리랭커가 필요'):  # 모델 서버에 접속하기 전에 거부
        service.load_app()
    assert made == []
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    a = service.load_app()
    assert made == [{'max_seq_length': 2304, 'decree_max_seq': 3072}]
    assert a.decree == DC and service.uses_decrees(a, 'pro') and not service.uses_decrees(a, 'fast')
    assert manifest.summary(manifest.load(a.profile.articles))['dataset_version'] == 'v4-decree'
    env.setenv('LAWBOT_DECREE_ENABLED', '0')
    made.clear()
    assert service.load_app().decree is None and made == [{'max_seq_length': 2304}]


# ---- 7. 인용 판정·화면 ----
def test_citation_with_decrees():
    R = DecRet()
    keys = citation.article_keys(R)
    assert ('근로기준법 시행령', '제2조') in keys and ('근로기준법', '제2조') in keys and len(keys) == 8
    law_only = citation.article_keys(DecRet(decree=False))
    hits = make().retriever.search('해고 예고?', 3) + [R._hit(4, 0.5) | {'kind': '시행령', 'parent_law': '근로기준법'}]
    cites = citation.citations(
        '【근거 조문】\n근로기준법 시행령 제2조, 근로기준법 시행령 제999조, 최저임금법 시행령 제5조'
    )
    assert [v for _, _, v, _ in citation.check(cites, hits, keys)] == ['ok', 'nonexistent', 'outside']
    assert [v for _, _, v, _ in citation.check(cites, hits, law_only)] == ['ok', 'nonexistent', 'nonexistent']
    assert citation.check(cites, hits, keys)[0][3] is hits[3]


def test_concise_and_cards_with_decree():
    R = DecRet()
    law = [R._hit(i, 0.9) | {'rerank_score': 0.8, 'emb_rank': i + 1} for i in range(3)]
    dec = [
        R._hit(4, 0.7) | {'kind': '시행령', 'parent_law': '근로기준법', 'rerank_score': 0.95, 'emb_rank': 2},
        R._hit(6, 0.6) | {'kind': '시행규칙', 'parent_law': '근로기준법', 'rerank_score': 0.4, 'emb_rank': 1},
    ]
    hits = law + dec
    raw = '【결론】\n제외됩니다.\n\n【근거 조문】\n근로기준법 시행령 제2조, 근로기준법 제2조\n\n【설명】\n…'
    out = answer_format.concise(raw, hits)
    assert (
        '근거: 근로기준법 시행령 제2조(평균임금의 계산에서 제외되는 기간과 임금), 근로기준법 제2조(정의)' in out
    )  # 인용 순서
    assert '⚠' not in out
    bad = raw.replace('근로기준법 제2조\n', '근로기준법 시행령 제9조\n')
    assert '⚠ 검색된 조문에서 확인되지 않은 인용: 제9조' in answer_format.concise(bad, hits)
    used, unknown, no = answer_format.answer_citations(raw, hits)
    assert used == [dec[0], law[0]] and unknown == [] and not no
    cards = app_mod.fmt_sources(hits, used, no)
    shown, folded = cards.split('검색만 된 조문')
    assert '**근로기준법 제2조 정의**' in shown and '**〔시행령〕 근로기준법 시행령 제2조' in shown
    assert shown.index('근로기준법 제2조 정의') < shown.index('〔시행령〕')  # 인용 그룹에서도 법률 먼저
    assert '〔시행규칙〕 근로기준법 시행규칙 제2조' in folded and '<details>' in folded
    assert folded.index('근로기준법 제26조') < folded.index('〔시행규칙〕')
    assert '(하위법령 임베딩 2위)' in shown and '(임베딩 1위)' in shown
    allcards = app_mod.fmt_sources(hits)  # 생성 중: 모두 펼침, 법률 → 하위법령
    assert '<details>' not in allcards and allcards.index('정의') < allcards.index('〔시행령〕') < allcards.index(
        '〔시행규칙〕'
    )


def test_screen_texts(env):
    on, off = make(), make(None)
    assert '시행령·시행규칙' in app_mod.searching_stats(on, 'pro') and '시행령' not in app_mod.searching_stats(
        on, 'fast'
    )
    assert app_mod.sources_heading(on) == '### 검색된 조문 (법률 3개 + 시행령·시행규칙 최대 2개)'
    assert app_mod.sources_heading(off) == '### 검색된 조문 (상위 3개)'
    assert 'Lawbot Pro는 관련 시행령·시행규칙을 최대 2개' in app_mod.intro_text(on)
    assert app_mod.intro_text(off) == '질문과 관련된 조문 3개를 찾아 그 조문을 근거로 답합니다. '
    st = {'search': 0.1, 'rerank': 1.9, 'decree_search': 0.05, 'decree_rerank': 2.0, 'n_decree': 2, 'load': None}
    assert '시행령·시행규칙 2.05초 (2개 첨부)' in app_mod.fmt_stats('Pro', 'generate', st)
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    a = dataclasses.replace(on, profile=deploy.get_profile())
    assert app_mod.data_notice(a) == '법령 기준일 2026-10-20 (데이터 v4-decree · 법률 415 · 시행령 273 · 시행규칙 124)'


def test_check_deploy_decree(env):
    from lawbot import check_deploy

    out = []
    check_deploy.check_decree(deploy.get_profile(), out)
    assert out[0][0] == 'INFO' and '꺼짐' in out[0][1]
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    out = []
    check_deploy.check_decree(deploy.get_profile(), out)
    assert out[0][0] == 'FAIL' and '리랭커' in out[0][1]
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    p, out = deploy.get_profile(), []
    check_deploy.check_decree(p, out)
    check_deploy.check_articles(p, out)
    check_deploy.check_manifest(p, out)
    assert {lv for lv, _ in out} <= {'OK', 'INFO'}, out
    msgs = ' '.join(m for _, m in out)
    assert '법률 조문이 articles_v4.csv와 같음' in msgs and '비삭제 법률 조문 415개' in msgs and 'v4-decree' in msgs


# ---- 8. 평가 ----
def test_eval_answers_and_refusal_with_decree(rr, tmp_path):
    rr.setattr(ev, 'OUT', tmp_path)
    rr.setattr(RR, 'SCORES', {'임금채권보장법 시행령 제24조': 0.97})
    on, off = make(), make(None)
    t = pd.DataFrame(
        [
            {
                'id': f'T{i}',
                'question': '질문' * (i + 1),
                'article_id': f'{l} {a}',
                'eval_cat': 'A_lsa' if i % 2 else 'B_newlaw',
                'confusable_with': None,
            }
            for i, (l, a, _) in enumerate(LAWS)
        ]
    )
    rr.setattr(ev, 'keywords', lambda: pd.DataFrame(columns=['must_include']))
    ev.run_answers(on, 'pro', 'concise', t, tmp_path / 'answers_dec.csv')
    a = pd.read_csv(tmp_path / 'answers_dec.csv', keep_default_na=False, **E)
    assert a['top3'].str.count(r'\|').eq(2).all() and a['decrees'].str.len().gt(0).all()
    assert {'decree_search', 'decree_rerank', 'n_decree', 'prompt_tokens'} <= set(a.columns)
    assert ev.hit_table(on) == ev.hit_table(off) and len(ev.hit_table(on)) == 4  # refusal·retrieval은 법률만
    sub = t.iloc[2:]  # 다른 법률 조문이 3개 이상인 문항 (가짜 법률 4개)
    assert ev.refusal_cases(on, sub, n=2) == ev.refusal_cases(off, sub, n=2)
    ev.run_answers(off, 'pro', 'concise', t.head(2), tmp_path / 'answers_x.csv')  # 꺼짐: 지금과 같은 열
    assert 'decrees' not in pd.read_csv(tmp_path / 'answers_x.csv', **E)
    with pytest.raises(SystemExit, match='없는 열'):  # 열이 다른 기존 파일에 이어 쓰지 않음
        ev.run_answers(on, 'pro', 'concise', t, tmp_path / 'answers_x.csv')
    ev.run_answers(off, 'pro', 'concise', t, tmp_path / 'answers_x.csv')  # 같은 구성은 이어서 실행
    assert len(pd.read_csv(tmp_path / 'answers_x.csv', **E)) == 4
    ev.run_retrieval(on, t, tmp_path / 'retrieval_dec.csv', decree=True)
    r = pd.read_csv(tmp_path / 'retrieval_dec.csv', **E)
    assert {'dec_attached', 'dec_n', 'dec_other', 'dec_rerank_sec'} <= set(r.columns)
    ev.run_retrieval(off, t, tmp_path / 'retrieval_x.csv')
    x = pd.read_csv(tmp_path / 'retrieval_x.csv', **E)
    assert r[x.columns].drop(columns='rerank_sec').equals(x.drop(columns='rerank_sec'))
    ev.summarize('dec', t)
    s = pd.read_csv(tmp_path / 'summary_dec.csv', **E)
    assert '첨부 하위법령 중 다른 법 비율' in set(s['metric'])


def test_eval_decree_stage(rr, tmp_path):
    rr.setattr(ev, 'OUT', tmp_path)
    rr.setattr(RR, 'SCORES', {'임금채권보장법 시행령 제24조': 0.97})
    dq = pd.DataFrame(
        [
            {
                'id': f'D{i}',
                'question': '질' * (i + 1),
                'article_id': f'{d[0]} {d[1]}',
                'question_type': 'situation' if i % 2 else 'keyword',
                'eval_cat': 'C_decree',
            }
            for i, d in enumerate(DECS)
        ]
    )
    sample = tmp_path / 'dq200.csv'
    dq[['id']].head(3).to_csv(sample, index=False, **E)
    rr.setattr(ev, 'DQ_SAMPLE', sample)
    on = make()
    for _ in range(2):  # 두 번째는 이어서 실행 (건너뜀)
        ev.run_decree_retrieval(on, 'pro', dq, tmp_path / 'dqretrieval_t.csv')
        ev.run_decree_answers(on, 'pro', 'concise', dq, tmp_path / 'dqanswers_t.csv')
    r = pd.read_csv(tmp_path / 'dqretrieval_t.csv', **E)
    a = pd.read_csv(tmp_path / 'dqanswers_t.csv', keep_default_na=False, **E)
    assert len(r) == 4 and r['in_cands'].all() and (r['n_att'] <= 2).all() and len(a) == 3
    assert {'cite_gold', 'refusal', 'cited_outside_ctx', 'decrees', 'decree_rerank'} <= set(a.columns)
    assert (a['cited_outside_ctx'].astype(int) == 0).all()  # 가짜 답변은 참고 조문의 마지막 조문을 인용
    for _, x in a.iterrows():
        assert x['cite_gold'] in (0, 1, '0', '1') and bool(int(x['cite_gold'])) == (x['cited'] == x['gold'])
    ev.summarize('t', pd.DataFrame(columns=['id', 'eval_cat']))
    s = pd.read_csv(tmp_path / 'summary_t.csv', **E)
    assert {'하위법령 검색', '하위법령 답변'} <= set(s['section'])
    assert '정답 하위법령 첨부율' in set(s['metric']) and '정답 하위법령 인용률' in set(s['metric'])
    f = ev.classify('t')
    assert set(f['stage']) == {'dqanswers'}
    ev.rescore('t')
    assert pd.read_csv(tmp_path / 'dqanswers_t.csv', **E)['cite_gold'].tolist() == a['cite_gold'].astype(int).tolist()


def test_score_dq():
    s = ev.score_dq(
        '근로기준법 시행령 제2조',
        '【결론】\n돼요.\n\n【근거 조문】\n근로기준법 시행령 제2조, 제3조',
        ['근로기준법 제2조', '근로기준법 시행령 제2조'],
    )
    assert (s['cite_gold'], s['refusal'], s['cites_decree'], s['cited_outside_ctx']) == (1, False, 1, 1)
    s = ev.score_dq(
        '근로기준법 시행령 제2조', '【결론】\n확인할 수 없습니다.\n\n【근거 조문】\n해당 없음', ['근로기준법 제2조']
    )
    assert (s['cite_gold'], s['refusal'], s['cited_outside_ctx']) == (0, True, 0)


def test_log_summary_old_and_new_lines():
    """하위법령 필드가 없는 이전 로그와 새 로그를 함께 요약"""
    from lawbot import log_summary

    base = {
        'status': 'ok',
        'mode': 'local',
        'model': 'pro',
        'search_ms': 50,
        'rerank_ms': 1900,
        'load_ms': None,
        'ttft_ms': 2000,
        'first_text_ms': 2100,
        'total_ms': 15000,
        'peak_gb': 20.0,
        'finish': 'stop',
    }
    old = log_summary.summarize(pd.DataFrame([base]))
    assert any('rerank' in x for x in old) and not any('decree' in x for x in old)
    new = log_summary.summarize(
        pd.DataFrame([base, base | {'decree_search_ms': 40, 'decree_rerank_ms': 2100, 'decree': True, 'n_decree': 2}])
    )
    assert any(x.strip().startswith('decree_rerank') for x in new) and any('첨부 평균 2.00개' in x for x in new)
