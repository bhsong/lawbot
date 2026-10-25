# src/lawbot/eval_v4.py  →  uv run python -m lawbot.eval_v4 <단계> [--tag 이름] [--n 200] [--fmt concise|full]
# v4 웹앱 구성(.env 그대로: v4 검색 415조문 + 리랭커 + 답변 모델)을 다시 평가한다 (맥 전용, 모델 필요)
#   retrieval: test 2,075문항 전체. 임베딩 순위(415조문 중)와 리랭킹 순위(후보 10개 중) → Recall@1·3·10, MRR
#   answers  : test에서 A_lsa·B_newlaw 반씩 고정 시드 표본 (--n). 웹앱과 같은 service.ask로 답하고 채점
#              A_lsa(근로기준법 650 = 기존 labor_eval_keywords와 같은 문항)는 키워드 채점, B_newlaw는 인용·거절만
#   oos      : 범위 밖 102문항. 7개 법률이 출처인 문항은 범위 안으로 재분류 (답하고 그 법을 인용해야 함)
#              + 범위 안 대조군 20문항(scope_controls)
#   refusal  : answers 표본 중 100문항에 정답이 없는 참고 조문 3개(다른 법률 조문 / 비슷한 조문)를 주고 거절하는지
#   summary  : 위 결과 파일로 지표 표 → results/v4/summary_<tag>.csv
# 결과는 results/v4/<단계>_<tag>.csv에 문항마다 한 행씩 바로 쓴다: 중간에 멈추면 같은 명령으로 이어서 실행
# test 문항은 평가 전용 (README '평가 데이터 사용 제한'): 이 결과로 설정·프롬프트를 고르지 않는다
import argparse
import re
import time
import numpy as np
import pandas as pd
from lawbot import envfile
from lawbot.paths import DATA, EVAL, RESULTS, V4_QA, V4_SPLIT
from lawbot.refusal import is_refusal
from lawbot.score_answers import norm, score_one

OUT = RESULTS / 'v4'
SEED = 42
E = dict(encoding='utf-8-sig')

# 줄임말로 인용해도 인정할 법령 이름 (공백·가운뎃점은 norm으로 맞춤)
LAW_ALIASES = {
    '근로기준법': ('근로기준법',),
    '근로자퇴직급여 보장법': ('근로자퇴직급여 보장법', '근로자퇴직급여보장법', '퇴직급여법'),
    '최저임금법': ('최저임금법',),
    '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률': ('남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률', '남녀고용평등법', '고평법'),
    '기간제 및 단시간근로자 보호 등에 관한 법률': ('기간제 및 단시간근로자 보호 등에 관한 법률', '기간제법'),
    '파견근로자 보호 등에 관한 법률': ('파견근로자 보호 등에 관한 법률', '파견법'),
    '임금채권보장법': ('임금채권보장법',),
}
# oos_eval.csv source → v4 범위 안 법령 (나머지 source는 여전히 범위 밖)
OOS_IN_SCOPE = {'남녀고용평등법': '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률', '최저임금법': '최저임금법',
                '근로자퇴직급여 보장법': '근로자퇴직급여 보장법', '기간제법': '기간제 및 단시간근로자 보호 등에 관한 법률',
                '임금채권보장법': '임금채권보장법', '파견법': '파견근로자 보호 등에 관한 법률'}
# 2026.4.7 근로기준법 개정: 임금체불 등 벌칙이 제109조(3년·3천만원)에서 제107조(5년·5천만원)로 옮겨짐
# v4 정답 조문(qa_full_v4)은 개정 후 기준. 키워드가 개정 전 형량인 문항만 개정 후 형량으로 바꾼다 (평가 파일은 그대로 둠)
KEYWORD_FIX = {'Q2443': '[["5년이하"], ["5천만원", "5000만원"]]', 'Q2453': '[["5년이하"], ["5천만원", "5000만원"]]'}

_ALIAS = sorted(((norm(a), law) for law, al in LAW_ALIASES.items() for a in al), key=lambda x: -len(x[0]))
_TOKEN = re.compile('|'.join(re.escape(a) for a, _ in _ALIAS) + r'|제\d+조(?:의\d+)?')

def cite_section(answer):
    m = re.search(r'【근거 조문】(.*?)(?=【|$)', str(answer), re.S)
    return m.group(1) if m else str(answer)

def citations(answer):
    """【근거 조문】(없으면 답변 전체)에서 인용한 (법령, 제N조) 목록. 법령명 없이 이어 쓴 번호는 앞의 법령으로 본다
    ('근로기준법 제26조, 제27조' → 둘 다 근로기준법). 앞에 법령명이 전혀 없으면 (None, 번호). 거절 형식('해당 없음')은 빈 목록"""
    text = cite_section(answer)
    if text.strip().startswith('해당 없음'):
        return []
    alias = dict(_ALIAS)
    law, out = None, []
    for m in _TOKEN.finditer(norm(text)):
        t = m[0]
        if t in alias:
            law = alias[t]
        elif (law, t) not in out:
            out.append((law, t))
    return out

def cite_gold(answer, gold):
    """gold: '근로기준법 제26조'. 법령명까지 맞게 인용했고 거절이 아님"""
    law, no = gold.rsplit(' ', 1)
    return (law, no) in citations(answer) and not is_refusal(answer)

def key(h):
    return f"{h['law']} {h['article_id']}"

def load_test():
    qa, sp = pd.read_csv(V4_QA, **E), pd.read_csv(V4_SPLIT, **E)
    t = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    return t[(t.split_emb == 'test') & t.eval_cat.isin(['A_lsa', 'B_newlaw'])].reset_index(drop=True)

def answer_sample(test, n):
    """A_lsa·B_newlaw에서 n//2개씩 (고정 시드). 순서는 id 순"""
    parts = [g.sample(min(n // 2, len(g)), random_state=SEED) for _, g in test.groupby('eval_cat')]
    return pd.concat(parts).sort_values('id').reset_index(drop=True)

def keywords():
    ev = pd.read_csv(EVAL, dtype=str, **E).set_index('id')
    for i, v in KEYWORD_FIX.items():
        ev.loc[i, 'must_include'] = v
    return ev

def score_a(row, answer):
    """키워드 채점 (score_answers.score_one) + 법령명까지 맞춘 인용으로 total_v4"""
    s = score_one(row, answer)
    c = cite_gold(answer, row['gold'])
    s['cite_v4'] = int(c)
    s['total_v4'] = round(s['group_score'] * c * (1.0 if s['must_not_ok'] else 0.5), 4)
    return s

# ---- 실행 도우미 ----
def resume(path, items, fn, label):
    """items: (id, ...) 목록. path에 이미 있는 id는 건너뛰고 한 행씩 이어 쓴다"""
    done = set(pd.read_csv(path, **E)['id'].astype(str)) if path.exists() else set()
    todo = [x for x in items if str(x[0]) not in done]
    print(f'[{label}] {len(items) - len(todo)}/{len(items)} 완료 → 남은 {len(todo)}', flush=True)
    t0 = time.perf_counter()
    for j, x in enumerate(todo, 1):
        row = fn(*x)
        pd.DataFrame([row]).to_csv(path, mode='a', index=False, header=not path.exists(), **E)
        if j % 10 == 0 or j == len(todo):
            print(f'[{label}] {j}/{len(todo)} · 평균 {(time.perf_counter() - t0) / j:.1f}초', flush=True)

def answer(app, model, fmt, question):
    """웹앱과 같은 경로 (service.ask): 검색(+리랭킹) → 답변. (근거 조문, 원문, 측정값)"""
    from lawbot.service import ask
    for _, hits, raw, st in ask(app, question, model, fmt=fmt):
        pass
    return hits, raw, st

def answer_with(app, model, fmt, question, hits):
    """검색 없이 주어진 참고 조문으로 답변 (거절 평가)"""
    from lawbot.service import format_system
    llm, _ = app.models.get(model)
    system = format_system(app, model, fmt)
    t0 = time.perf_counter()
    raw = ''.join(r.text for r in llm.stream(question, hits, **({'system': system} if system else {})))
    return raw, time.perf_counter() - t0

def hit_table(app):
    """조문 키('근로기준법 제26조') → 검색 결과와 같은 형식의 dict"""
    R = app.retriever
    return {f'{l} {a}': {'article_id': a, 'title': t, 'doc': d, 'score': 0.0, 'law': l}
            for l, a, t, d in zip(R.laws, R.ids, R.titles, R.docs)}

def stats_cols(st):
    return {k: st.get(k) for k in ('search', 'rerank', 'ttft', 'total', 'tps', 'gen_tokens', 'peak_gb', 'finish')}

# ---- 단계 ----
def run_retrieval(app, test, path, rerank=True):
    R = app.retriever
    keys = np.array([f'{l} {a}' for l, a in zip(R.laws, R.ids)])
    rank = R.rank_all(test['question'])
    emb_pos = [int(np.where(keys[r] == g)[0][0]) + 1 for r, g in zip(rank, test['article_id'])]
    pos = dict(zip(test['id'], emb_pos))
    rr = app.reranker if rerank else None
    n = rr.candidates if rr else 10

    def one(qid, q, gold):
        cands = R.search(q, k=n)
        row = {'id': qid, 'emb_rank': pos[qid]}
        if rr:
            t0 = time.perf_counter()
            out = rr.rerank(q, cands, top_k=len(cands))
            row['rerank_sec'] = round(time.perf_counter() - t0, 3)
            row['rr_rank'] = next((i for i, h in enumerate(out, 1) if key(h) == gold), 999)
            row['rr_top3'] = '|'.join(key(h) for h in out[:3])
        return row
    resume(path, list(zip(test['id'], test['question'], test['article_id'])), one, 'retrieval')

def run_answers(app, model, fmt, sample, path):
    kw = keywords()

    def one(qid, q, gold, cat):
        hits, raw, st = answer(app, model, fmt, q)
        row = {'id': qid, 'eval_cat': cat, 'gold': gold, 'retrieved': gold in [key(h) for h in hits],
               'top3': '|'.join(key(h) for h in hits), 'refusal': is_refusal(raw), 'cite_v4': int(cite_gold(raw, gold)),
               'cited': '|'.join(f'{l} {a}' if l else a for l, a in citations(raw)), 'answer_len': len(raw)}
        keys = ('group_score', 'must_not_ok', 'total_v4', 'missing_groups', 'must_not_hits')   # 이어 쓰기: 열은 항상 같게
        s = score_a(kw.loc[qid].to_dict() | {'gold': gold}, raw) if cat == 'A_lsa' else {}
        row |= {k: s.get(k) for k in keys}
        return row | stats_cols(st) | {'answer': raw}
    resume(path, list(zip(sample['id'], sample['question'], sample['article_id'], sample['eval_cat'])), one, 'answers')

def run_oos(app, model, fmt, path):
    oos = pd.read_csv(DATA / 'oos_eval.csv', **E)
    ctrl = pd.read_csv(DATA / 'scope_controls.csv', **E)
    items = [(r.id, r.question, 'oos', r.category, r.source) for r in oos.itertuples()]
    items += [(r.id, r.question, 'control', 'control', f'근로기준법 {r.article_id}') for r in ctrl.itertuples()]

    def one(qid, q, kind, category, source):
        hits, raw, st = answer(app, model, fmt, q)
        cited = citations(raw)
        if kind == 'control':
            scope, law = 'in_control', '근로기준법'
        else:
            law = OOS_IN_SCOPE.get(source)
            scope = 'in_reclassified' if law else 'out'
        return {'id': qid, 'scope': scope, 'category': category, 'source': source, 'refusal': is_refusal(raw),
                'cited': '|'.join(f'{l} {a}' if l else a for l, a in cited),
                'cites_any': bool(cited) and not is_refusal(raw),
                'cites_law': bool(law) and any(l == law for l, _ in cited) and not is_refusal(raw),
                'cite_gold': int(kind == 'control' and cite_gold(raw, source)),
                'retrieved_law': bool(law) and any(h['law'] == law for h in hits),
                'top3': '|'.join(key(h) for h in hits)} | stats_cols(st) | {'answer': raw}
    resume(path, items, one, 'oos')

def refusal_cases(app, sample, n=100):
    """(id, 시험, 질문, 참고 조문 키 3개). 다른 법률 조문: 정답 법령이 아닌 법령에서 무작위 /
    비슷한 조문: 임베딩 순위에서 정답·헷갈리는 조문을 빼고 위에서 3개"""
    sub = sample.sample(min(n, len(sample)), random_state=SEED).sort_values('id')
    table = hit_table(app)
    keys = np.array(list(table))
    rng = np.random.default_rng(SEED)
    rank = app.retriever.rank_all(sub['question'])
    out = []
    for r, order in zip(sub.itertuples(), rank):
        ban = {r.article_id} | ({c for c in str(r.confusable_with).split(';') if c} if isinstance(r.confusable_with, str) else set())
        law = r.article_id.rsplit(' ', 1)[0]
        pool = [k for k in keys if not k.startswith(law + ' ') and k not in ban]
        out.append((f'{r.id}:other_law', '다른 법률 조문', r.question, list(rng.choice(pool, 3, replace=False))))
        out.append((f'{r.id}:similar', '비슷한 조문', r.question, [k for k in keys[order] if k not in ban][:3]))
    return out

def run_refusal(app, model, fmt, sample, path):
    table = hit_table(app)

    def one(cid, test, q, ctx):
        raw, sec = answer_with(app, model, fmt, q, [table[k] for k in ctx])
        return {'id': cid, 'test': test, 'context': '|'.join(ctx), 'refusal': is_refusal(raw),
                'cited': '|'.join(f'{l} {a}' if l else a for l, a in citations(raw)), 'sec': round(sec, 2), 'answer': raw}
    resume(path, refusal_cases(app, sample), one, 'refusal')

# ---- 요약 ----
def mean(s):
    return round(float(s.mean()), 4) if len(s) else None

def summarize(tag, test):
    rows = []
    def add(section, group, metric, value, n):
        rows.append({'section': section, 'group': group, 'metric': metric, 'value': value, 'n': n})
    p = OUT / f'retrieval_{tag}.csv'
    if p.exists():
        r = pd.read_csv(p, **E).merge(test[['id', 'eval_cat']], on='id')
        for g, d in [('전체', r)] + list(r.groupby('eval_cat')):
            e = d['emb_rank']
            for k in (1, 3, 10):
                add('검색', g, f'임베딩 Recall@{k}', mean(e <= k), len(d))
            add('검색', g, '임베딩 MRR@10', mean(np.where(e <= 10, 1 / e, 0)), len(d))
            if 'rr_rank' in d:
                x = d['rr_rank']
                for k in (1, 3):
                    add('검색', g, f'리랭킹 Recall@{k}', mean(x <= k), len(d))
                add('검색', g, '리랭킹 MRR@3', mean(np.where(x <= 3, 1 / x, 0)), len(d))
                add('검색', g, '리랭킹 초(평균)', mean(d['rerank_sec']), len(d))
                add('검색', g, '리랭킹 초(p95)', round(float(d['rerank_sec'].quantile(.95)), 3), len(d))
    p = OUT / f'answers_{tag}.csv'
    if p.exists():
        a = pd.read_csv(p, **E)
        for g, d in [('전체', a)] + list(a.groupby('eval_cat')):
            add('답변', g, '검색 top3 적중률', mean(d['retrieved']), len(d))
            add('답변', g, '조문 인용 정확도(법령명 포함)', mean(d['cite_v4']), len(d))
            add('답변', g, '거짓 거절률', mean(d['refusal']), len(d))
            add('답변', g, '거짓 거절률(검색 적중)', mean(d.loc[d['retrieved'], 'refusal']), int(d['retrieved'].sum()))
            if g == 'A_lsa':                 # 키워드 채점은 기존 650문항(근로기준법)만
                hit = d[d['retrieved']]
                add('답변', g, '키워드 그룹 충족률', mean(d['group_score']), len(d))
                add('답변', g, '금지 표현 없음', mean(d['must_not_ok']), len(d))
                add('답변', g, '종합 점수', mean(d['total_v4']), len(d))
                add('답변', g, '종합 점수(검색 적중)', mean(hit['total_v4']), len(hit))
            add('답변', g, '평균 길이(자)', mean(d['answer_len']), len(d))
            add('답변', g, '첫 토큰(초, 중앙값)', round(float(d['ttft'].median()), 2), len(d))
            add('답변', g, '완료(초, 평균)', mean(d['total']), len(d))
            add('답변', g, '생성 속도(tok/s)', mean(d['tps']), len(d))
            add('답변', g, '최대 길이 잘림', mean(d['finish'] == 'length'), len(d))
    p = OUT / f'oos_{tag}.csv'
    if p.exists():
        o = pd.read_csv(p, **E)
        out = o[o['scope'] == 'out']
        for g, d in [('범위 밖 전체', out)] + [(f'범위 밖 {c}', x) for c, x in out.groupby('category')]:
            add('범위 밖', g, '거절률', mean(d['refusal']), len(d))
            add('범위 밖', g, '억지적용률(거절 않고 조문 인용)', mean(d['cites_any']), len(d))
        r = o[o['scope'] == 'in_reclassified']
        add('범위 밖→범위 안', '7개 법률 출처', '거짓 거절률', mean(r['refusal']), len(r))
        add('범위 밖→범위 안', '7개 법률 출처', '해당 법률 인용률', mean(r['cites_law']), len(r))
        add('범위 밖→범위 안', '7개 법률 출처', '해당 법률 검색률(top3)', mean(r['retrieved_law']), len(r))
        c = o[o['scope'] == 'in_control']
        add('대조군', '근로기준법 20', '거짓 거절률', mean(c['refusal']), len(c))
        add('대조군', '근로기준법 20', '정답 조문 인용률', mean(c['cite_gold']), len(c))
    p = OUT / f'refusal_{tag}.csv'
    if p.exists():
        f = pd.read_csv(p, **E)
        for g, d in f.groupby('test'):
            add('정답 없는 참고 조문', g, '거절률', mean(d['refusal']), len(d))
    s = pd.DataFrame(rows)
    if len(s):
        s.to_csv(OUT / f'summary_{tag}.csv', index=False, **E)
        pd.set_option('display.width', 200, 'display.max_rows', 200)
        print(s.to_string(index=False))
    return s

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['retrieval', 'answers', 'oos', 'refusal', 'summary', 'all'])
    ap.add_argument('--tag', default=None, help='결과 파일 이름 (기본: <모델 key>_<형식>_rr 또는 _emb)')
    ap.add_argument('--n', type=int, default=200, help='답변 생성 표본 수 (A_lsa·B_newlaw 반씩)')
    ap.add_argument('--model', default=None, help='답변 모델 key (기본: .env의 첫 번째 모델)')
    ap.add_argument('--fmt', choices=['concise', 'full'], default=None, help='답변 형식 (기본: 그 모델의 기본 형식)')
    ap.add_argument('--no-rerank', action='store_true', help='retrieval 단계에서 리랭킹 순위를 재지 않음')
    args = ap.parse_args()
    envfile.load()
    test = load_test()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.stage == 'summary':
        if not args.tag:
            raise SystemExit('summary는 --tag가 필요합니다 (results/v4의 파일 이름)')
        summarize(args.tag, test)
        return
    from lawbot.service import load_app
    app = load_app()
    if app.profile.name != 'v4':
        raise SystemExit('v4 프로필에서 실행하세요 (LAWBOT_PROFILE=v4)')
    model = args.model or app.profile.models[0].key
    fmt = args.fmt or app.models.specs[model].fmt
    tag = args.tag or f"{model}_{fmt}_{'rr' if app.reranker else 'emb'}"
    sample = answer_sample(test, args.n)
    print(f'[eval_v4] tag {tag} · 모델 {model} · 형식 {fmt} · 리랭커 {"켜짐" if app.reranker else "꺼짐"} · '
          f'test {len(test)} · 표본 {len(sample)}')
    stages = ['retrieval', 'answers', 'oos', 'refusal'] if args.stage == 'all' else [args.stage]
    for s in stages:
        if s == 'retrieval':
            run_retrieval(app, test, OUT / f'retrieval_{tag}.csv', rerank=not args.no_rerank)
        elif s == 'answers':
            run_answers(app, model, fmt, sample, OUT / f'answers_{tag}.csv')
        elif s == 'oos':
            run_oos(app, model, fmt, OUT / f'oos_{tag}.csv')
        elif s == 'refusal':
            run_refusal(app, model, fmt, sample, OUT / f'refusal_{tag}.csv')
    summarize(tag, test)

if __name__ == '__main__':
    main()
