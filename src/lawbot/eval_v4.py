# src/lawbot/eval_v4.py  →  uv run lawbot-eval-v4            (전체: retrieval → answers → oos → refusal → summary)
#                             uv run lawbot-eval-v4 <단계> [--tag 이름] [--n 200] [--fmt concise|full] [--split test|val]
#                             uv run lawbot-eval-v4 compare --base <tag> --new <tag>
# v4 웹앱 구성(.env 그대로: v4 검색 415조문 + 리랭커 + 답변 모델)을 다시 평가한다 (맥 전용, 모델 필요)
#   retrieval: 분할 전체(test 2,075 / val 1,110). 임베딩 순위(415조문 중)와 리랭킹 순위(후보 10개 중) → Recall@1·3·10, MRR
#   answers  : A_lsa·B_newlaw 반씩 고정 시드 표본 (--n). 웹앱과 같은 service.ask로 답하고 채점
#              A_lsa test 650(= 기존 labor_eval_keywords와 같은 문항)은 키워드 채점, 나머지(B_newlaw, val)는 인용·거절만
#   oos      : 범위 밖 102문항. 7개 법률이 출처인 문항은 범위 안으로 재분류 (답하고 그 법을 인용해야 함)
#              + 범위 안 대조군 20문항(scope_controls). 평가 전용 문항이라 --split val에서는 돌리지 않는다
#   refusal  : answers 표본 중 100문항에 정답이 없는 참고 조문 3개(다른 법률 조문 / 비슷한 조문)를 주고 거절하는지
#   decree   : 시행령·시행규칙 첨부(LAWBOT_DECREE_ENABLED=1, 그 모델이 첨부 대상)를 켠 구성만, --split val만
#              ① 하위법령 정답 val 444(data/decree_exp/qa_decree_v4.csv + split_decree_v4.csv): 정답 하위법령 후보·첨부 포함률
#              ② 그중 고정 표본 200(data/v4/decree_ctx_dq_val200.csv의 id)을 service.ask로 답하고 정답 하위법령 인용·거절 채점
#              하위법령을 켜면 retrieval에 '다른 법 하위법령 첨부 비율', answers에 첨부한 하위법령(decrees 열)이 더해지고
#              기본 tag 끝에 _dec가 붙는다 (예: pro_concise_rr_dec_val). 기존 tag 파일과 열이 섞이지 않게
#   summary  : 위 결과 파일로 지표 표 → results/v4/summary_<tag>.csv, 실패 유형 → results/v4/fail_<tag>.csv
#   compare  : 두 tag의 summary·실패 유형을 나란히 놓고 report.py 경고 기준으로 나빠진 지표를 경고
# 결과는 results/v4/<단계>_<tag>.csv에 문항마다 한 행씩 바로 쓴다: 중간에 멈추면 같은 명령으로 이어서 실행
# 실행마다 설정(모드·서버 /info·모델 폴더·데이터 버전·커밋)을 results/v4/run_<tag>.json에 남긴다
# test 문항은 평가 전용 (README '평가 데이터 사용 제한'): 설정·프롬프트·규칙은 --split val 결과로 고른다
import argparse
import datetime
import json
import subprocess
import sys
import time
import numpy as np
import pandas as pd
from lawbot import deploy, envfile, manifest
from lawbot.decree import is_decree
from lawbot.paths import DATA, EVAL, RESULTS, ROOT, V4_QA, V4_SPLIT
# 인용·거절 판정은 웹앱 화면과 같은 citation 모듈 (ev.citations·ev.refused·ev.key 등 기존 이름은 그대로 둠)
from lawbot.citation import LAW_ALIASES, REFUSAL, cite_section, citations, refused
from lawbot.citation import NONEXISTENT, OUTSIDE, WRONG_LAW, check
from lawbot.citation import hit_key as key
from lawbot.score_answers import score_one

OUT = RESULTS / 'v4'
SEED = 42
E = dict(encoding='utf-8-sig')

# oos_eval.csv source → v4 범위 안 법령 (나머지 source는 여전히 범위 밖)
OOS_IN_SCOPE = {'남녀고용평등법': '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률', '최저임금법': '최저임금법',
                '근로자퇴직급여 보장법': '근로자퇴직급여 보장법', '기간제법': '기간제 및 단시간근로자 보호 등에 관한 법률',
                '임금채권보장법': '임금채권보장법', '파견법': '파견근로자 보호 등에 관한 법률'}
# 2026.4.7 근로기준법 개정: 임금체불 등 벌칙이 제109조(3년·3천만원)에서 제107조(5년·5천만원)로 옮겨짐
# v4 정답 조문(qa_full_v4)은 개정 후 기준. 키워드가 개정 전 형량인 문항만 개정 후 형량으로 바꾼다 (평가 파일은 그대로 둠)
KEYWORD_FIX = {'Q2443': '[["5년이하"], ["5천만원", "5000만원"]]', 'Q2453': '[["5년이하"], ["5천만원", "5000만원"]]'}

def cite_gold(answer, gold):
    """gold: '근로기준법 제26조'. 법령명까지 맞게 인용했고 거절이 아님"""
    law, no = gold.rsplit(' ', 1)
    return (law, no) in citations(answer) and not refused(answer)

SPLITS = ('test', 'val')

DQ_QA, DQ_SPLIT = DATA / 'decree_exp' / 'qa_decree_v4.csv', DATA / 'decree_exp' / 'split_decree_v4.csv'
DQ_SAMPLE = DATA / 'v4' / 'decree_ctx_dq_val200.csv'   # 하위법령 답변 표본 200 (실험 eval_decree와 같은 id)

def load_decree_questions(split='val'):
    """하위법령이 정답인 문항 (split_emb가 split). val 444"""
    qa, sp = pd.read_csv(DQ_QA, **E), pd.read_csv(DQ_SPLIT, **E)
    t = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    return t[t.split_emb == split].reset_index(drop=True)

def load_test(split='test'):
    """split_emb가 split인 A_lsa·B_newlaw 문항 (test 2,075 / val 1,110)"""
    qa, sp = pd.read_csv(V4_QA, **E), pd.read_csv(V4_SPLIT, **E)
    t = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    return t[(t.split_emb == split) & t.eval_cat.isin(['A_lsa', 'B_newlaw'])].reset_index(drop=True)

def answer_sample(test, n):
    """A_lsa·B_newlaw에서 n//2개씩 (고정 시드). 순서는 id 순"""
    parts = [g.sample(min(n // 2, len(g)), random_state=SEED) for _, g in test.groupby('eval_cat')]
    return pd.concat(parts).sort_values('id').reset_index(drop=True)

def keywords():
    ev = pd.read_csv(EVAL, dtype=str, **E).set_index('id')
    for i, v in KEYWORD_FIX.items():
        ev.loc[i, 'must_include'] = v
    return ev

def cited_text(answer):
    return '|'.join(f'{l} {a}' if l else a for l, a in citations(answer))

KW_COLS = ('group_score', 'must_not_ok', 'total_v4', 'missing_groups', 'must_not_hits')

def score_answer(qid, cat, gold, raw, kw):
    """answers 채점 열 (생성 때와 summary 재채점에 같이 씀). 키워드 열은 키워드 기준이 있는 A_lsa(test 650)만 값,
    나머지(B_newlaw, val의 A_lsa)는 빈 값 (이어 쓰기: 열은 항상 같게)"""
    s = score_a(kw.loc[qid].to_dict() | {'gold': gold}, raw) if cat == 'A_lsa' and qid in kw.index else {}
    return {'refusal': refused(raw), 'cite_v4': int(cite_gold(raw, gold)), 'cited': cited_text(raw),
            'answer_len': len(raw)} | {k: s.get(k) for k in KW_COLS}

def score_oos(scope, source, raw):
    """oos 채점 열. source: 범위 밖은 oos_eval의 source, 대조군은 정답 조문('근로기준법 제N조')"""
    law = '근로기준법' if scope == 'in_control' else OOS_IN_SCOPE.get(source)
    cited, no = citations(raw), refused(raw)
    return {'refusal': no, 'cited': cited_text(raw), 'cites_any': bool(cited) and not no,
            'cites_law': bool(law) and any(l == law for l, _ in cited) and not no,
            'cite_gold': int(scope == 'in_control' and cite_gold(raw, source))}

def score_refusal(raw):
    return {'refusal': refused(raw), 'cited': cited_text(raw)}

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
    cols = pd.read_csv(path, nrows=0, **E).columns.tolist() if path.exists() else None
    todo = [x for x in items if str(x[0]) not in done]
    print(f'[{label}] {len(items) - len(todo)}/{len(items)} 완료 → 남은 {len(todo)}', flush=True)
    t0 = time.perf_counter()
    for j, x in enumerate(todo, 1):
        row = fn(*x)
        if cols is not None:             # 이어 쓰기: 기존 파일의 열 순서에 맞춘다 (열이 다르면 값이 밀리므로 멈춤)
            extra = [c for c in row if c not in cols]
            if extra:
                raise SystemExit(f'{path.name}에 없는 열 {extra}: 다른 설정(예: 하위법령 켜짐)으로 만든 파일입니다. '
                                 '다른 --tag로 실행하세요')
            row = {c: row.get(c) for c in cols}
        pd.DataFrame([row]).to_csv(path, mode='a', index=False, header=cols is None and not path.exists(), **E)
        cols = cols or list(row)
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

def n_law(R):
    """검색기 조문 배열 중 법률 수 (법률 먼저, 하위법령 뒤)"""
    return getattr(R, 'n_law', None) or len(R.ids)

def hit_table(app):
    """법률 조문 키('근로기준법 제26조') → 검색 결과와 같은 형식의 dict
    법률만: refusal(정답 없는 법률 조문 3개)·retrieval 순위는 하위법령을 켜도 기준선과 같은 후보에서 고른다"""
    R = app.retriever
    m = n_law(R)
    return {f'{l} {a}': {'article_id': a, 'title': t, 'doc': d, 'score': 0.0, 'law': l}
            for l, a, t, d in zip(R.laws[:m], R.ids[:m], R.titles[:m], R.docs[:m])}

DECREE_STATS = ('decree_search', 'decree_rerank', 'n_decree', 'prompt_tokens')

def stats_cols(st, decree=False):
    """측정값 열. decree: 하위법령 첨부 구성이면 하위법령 시간·첨부 수·프롬프트 토큰도 (기존 tag 파일 열은 그대로)"""
    keys = ('search', 'rerank', 'ttft', 'total', 'tps', 'gen_tokens', 'peak_gb', 'finish') + (DECREE_STATS if decree else ())
    return {k: st.get(k) for k in keys}

def split_hits(hits):
    """(법률 hit, 첨부한 하위법령 hit)"""
    return [h for h in hits if not is_decree(h)], [h for h in hits if is_decree(h)]

def uses_decrees(app, model):
    from lawbot.service import uses_decrees as f
    return f(app, model)

# ---- 단계 ----
def run_retrieval(app, test, path, rerank=True, decree=False):
    """decree: 하위법령 첨부 구성. 법률 리랭킹 1위로 하위법령을 거른 첨부 목록과 다른 법(정답 법률과 parent_law가 다름) 수"""
    R = app.retriever
    keys = np.array([f'{l} {a}' for l, a in zip(R.laws[:n_law(R)], R.ids[:n_law(R)])])
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
            if decree:
                from lawbot.service import retrieve_decrees
                st = {}
                att = retrieve_decrees(app, q, out[0]['law'], st)
                gold_law = gold.rsplit(' ', 1)[0]
                row |= {'dec_attached': '|'.join(key(h) for h in att), 'dec_n': len(att),
                        'dec_other': sum(h['parent_law'] != gold_law for h in att),
                        'dec_rerank_sec': round(st['decree_rerank'], 3)}
        return row
    resume(path, list(zip(test['id'], test['question'], test['article_id'])), one, 'retrieval')

def run_answers(app, model, fmt, sample, path):
    kw = keywords()
    decree = uses_decrees(app, model)

    def one(qid, q, gold, cat):
        hits, raw, st = answer(app, model, fmt, q)
        law, dec = split_hits(hits)          # top3·retrieved는 법률 3개 (하위법령을 켜도 같은 뜻)
        return ({'id': qid, 'eval_cat': cat, 'gold': gold, 'retrieved': gold in [key(h) for h in law],
                 'top3': '|'.join(key(h) for h in law)} | ({'decrees': '|'.join(key(h) for h in dec)} if decree else {})
                | score_answer(qid, cat, gold, raw, kw) | stats_cols(st, decree) | {'answer': raw})
    resume(path, list(zip(sample['id'], sample['question'], sample['article_id'], sample['eval_cat'])), one, 'answers')

def run_oos(app, model, fmt, path):
    oos = pd.read_csv(DATA / 'oos_eval.csv', **E)
    ctrl = pd.read_csv(DATA / 'scope_controls.csv', **E)
    items = [(r.id, r.question, 'oos', r.category, r.source) for r in oos.itertuples()]
    items += [(r.id, r.question, 'control', 'control', f'근로기준법 {r.article_id}') for r in ctrl.itertuples()]

    decree = uses_decrees(app, model)

    def one(qid, q, kind, category, source):
        hits, raw, st = answer(app, model, fmt, q)
        hits, dec = split_hits(hits)
        scope = 'in_control' if kind == 'control' else ('in_reclassified' if source in OOS_IN_SCOPE else 'out')
        law = '근로기준법' if kind == 'control' else OOS_IN_SCOPE.get(source)
        return ({'id': qid, 'scope': scope, 'category': category, 'source': source} | score_oos(scope, source, raw)
                | {'retrieved_law': bool(law) and any(h['law'] == law for h in hits),
                   'top3': '|'.join(key(h) for h in hits)} | ({'decrees': '|'.join(key(h) for h in dec)} if decree else {})
                | stats_cols(st, decree) | {'answer': raw})
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
        return {'id': cid, 'test': test, 'context': '|'.join(ctx)} | score_refusal(raw) | {'sec': round(sec, 2), 'answer': raw}
    resume(path, refusal_cases(app, sample), one, 'refusal')

def score_dq(gold, raw, ctx):
    """하위법령 정답 문항 채점 (생성 때와 summary 재채점). ctx: 참고 조문 키 목록 (법률 3 + 첨부 하위법령)
    cited_outside_ctx: 참고 조문에 없는 조문을 인용 (번호만 쓴 인용 포함, eval_decree와 같은 정의)"""
    cited, no = citations(raw), refused(raw)
    keys = [f'{l} {a}' if l else a for l, a in cited]
    return {'refusal': no, 'cite_gold': int(cite_gold(raw, gold)), 'cited': '|'.join(keys), 'n_cited': len(keys),
            'cites_decree': int(any(l and ' 시행' in l for l, _ in cited) and not no),
            'cited_outside_ctx': int(any(k not in set(ctx) for k in keys)), 'answer_len': len(raw)}

def run_decree_retrieval(app, model, dq, path):
    """하위법령 정답 문항: 법률 검색·리랭킹(1위 법령) → 하위법령 후보 → 리랭킹 → 필터. 웹앱과 같은 service.retrieve"""
    from lawbot.service import retrieve

    def one(qid, q, gold, qtype):
        st = {}
        law, att = retrieve(app, q, 3, st, model)
        order, att = [key(h) for h in st['decree_candidates']], [key(h) for h in att]
        return {'id': qid, 'question_type': qtype, 'gold': gold, 'top1_law': law[0]['law'],
                'in_cands': gold in order, 'cand_rank': order.index(gold) + 1 if gold in order else 999,
                'attached': '|'.join(att), 'n_att': len(att), 'in_attached': gold in att,
                'decree_rerank_sec': round(st['decree_rerank'], 3)}
    resume(path, list(zip(dq['id'], dq['question'], dq['article_id'], dq['question_type'])), one, 'decree retrieval')

def run_decree_answers(app, model, fmt, dq, path):
    """하위법령 정답 표본 200 (decree_ctx_dq_val200.csv의 id)을 웹앱 경로(service.ask)로 답하고 채점"""
    ids = set(pd.read_csv(DQ_SAMPLE, dtype=str, **E)['id'])
    sample = dq[dq['id'].isin(ids)].sort_values('id')
    assert len(sample) == len(ids), f'{DQ_SAMPLE.name}의 id가 하위법령 val 문항에 없음'

    def one(qid, q, gold, qtype):
        hits, raw, st = answer(app, model, fmt, q)
        law, dec = split_hits(hits)
        ctx = [key(h) for h in hits]
        return ({'id': qid, 'question_type': qtype, 'gold': gold, 'retrieved': gold in ctx,
                 'top3': '|'.join(key(h) for h in law), 'decrees': '|'.join(key(h) for h in dec), 'n_ctx': len(ctx)}
                | score_dq(gold, raw, ctx) | stats_cols(st, True) | {'answer': raw})
    resume(path, list(zip(sample['id'], sample['question'], sample['article_id'], sample['question_type'])), one,
           'decree answers')

def rescore(tag):
    """저장된 답변 원문으로 채점 열만 다시 계산해 덮어쓴다 (답변·측정값은 그대로). 채점 규칙을 고친 뒤 재생성 없이 반영"""
    kw = keywords()
    for stage, fn in (('answers', lambda r: score_answer(r['id'], r['eval_cat'], r['gold'], r['answer'], kw)),
                      ('oos', lambda r: score_oos(r['scope'], r['source'], r['answer'])),
                      ('refusal', lambda r: score_refusal(r['answer'])),
                      ('dqanswers', lambda r: score_dq(r['gold'], r['answer'],
                                                       [k for k in (str(r['top3']) + '|' + str(r['decrees'])).split('|') if k]))):
        p = OUT / f'{stage}_{tag}.csv'
        if not p.exists():
            continue
        # round_trip: 측정값(초)을 읽고 다시 쓸 때 마지막 자리가 바뀌지 않게 (pandas 3.0 기본 파서는 반올림함)
        d = pd.read_csv(p, keep_default_na=False, float_precision='round_trip', **E)
        d['answer'] = d['answer'].astype(str)
        new = pd.DataFrame([fn(r) for _, r in d.iterrows()], index=d.index)
        if 'must_not_ok' in new:                 # 정수 열 (B_newlaw는 빈 값): 1이 1.0으로 바뀌지 않게
            new['must_not_ok'] = new['must_not_ok'].astype('Int64')
        changed = int((d['refusal'].astype(str) != new['refusal'].astype(str)).sum())
        for c in new:
            d[c] = new[c]
        d.to_csv(p, index=False, **E)
        print(f'[rescore] {p.name}: 거절 판정이 바뀐 행 {changed}')

# ---- 실패 유형 ----
# 문항마다 실패한 첫 원인 하나 (위에서부터 먼저 맞는 것). 통과한 문항은 빈 값. 결과 CSV는 바꾸지 않고 fail_<tag>.csv에 쓴다
#   retrieval_miss  : 임베딩 상위 10개에 정답 없음 (retrieval 결과가 있을 때)
#   rerank_drop     : 임베딩 상위 10개에는 있으나 리랭킹 top3 밖
#   not_retrieved   : top3에 정답(범위 안 문항은 그 법률) 없음, 임베딩 순위를 모름 (retrieval 결과 없음, oos 문항)
#   false_refusal   : 정답이 top3에 있는데 거절
#   cite_wrong_law / cite_outside / cite_nonexistent : 정답을 인용하지 않았고 citation.check 판정이 그 인용
#   missing_keyword / forbidden_phrase : 정답은 인용했으나 키워드 그룹 누락 / 금지 표현 (키워드 기준이 있는 A_lsa만)
#   format          : 【결론】 없음
#   truncated       : 최대 길이에서 잘림
#   cite_missing    : 정답을 인용하지 않음 (인용 없음, 또는 검색 결과의 다른 조문만 인용)
#   answered_without_basis : 거절해야 하는 문항(범위 밖, 정답 없는 참고 조문)에 거절하지 않음
FAIL_TYPES = ('retrieval_miss', 'rerank_drop', 'not_retrieved', 'false_refusal', 'cite_wrong_law', 'cite_outside',
              'cite_nonexistent', 'missing_keyword', 'forbidden_phrase', 'format', 'truncated', 'cite_missing',
              'answered_without_basis')
BAD_CITE = {WRONG_LAW: 'cite_wrong_law', OUTSIDE: 'cite_outside', NONEXISTENT: 'cite_nonexistent'}

def article_keys():
    """전체 조문 {(법령, 제N조)} (v4 프로필 조문 CSV의 비삭제 조문 = 검색 대상과 같음)"""
    law = pd.read_csv(deploy.get_profile('v4').articles, **E)
    law = law[law['deleted'] == 0]
    return set(zip(law['law'], law['article_no'] if 'article_no' in law else law['article_id']))

def key_hits(keys):
    """'근로기준법 제26조|최저임금법 제6조' → citation.check에 넘길 검색 결과 dict"""
    return [{'law': k.rsplit(' ', 1)[0], 'article_id': k.rsplit(' ', 1)[1]} for k in str(keys).split('|') if ' ' in k]

def answer_failure(answer, finish, keys, articles):
    """정답을 인용하지 않은 답변의 원인 (cite_* → format → truncated). (유형, 문제 인용) 또는 None(→ cite_missing)"""
    for law, no, verdict, _ in check(citations(answer), key_hits(keys), articles):
        if verdict in BAD_CITE:
            return BAD_CITE[verdict], f'{law} {no}' if law else no
    if '【결론】' not in str(answer):
        return 'format', ''
    if finish == 'length':
        return 'truncated', ''
    return None

def ctx_keys(r):
    """답변에 준 참고 조문 키: top3(법률) + 첨부한 하위법령 (decrees 열이 있는 하위법령 구성)"""
    dec = r.get('decrees')
    return str(r['top3']) + ('|' + str(dec) if isinstance(dec, str) and dec else '')

def fail_answers(r, ranks, articles):
    """answers 행 → (유형, 상세). ranks: id → (emb_rank, rr_rank 또는 None) (retrieval 결과)"""
    has_kw = r['eval_cat'] == 'A_lsa' and not pd.isna(r.get('total_v4'))
    passed = bool(r['cite_v4']) and (not has_kw or float(r['total_v4']) >= 1)
    if passed:
        return '', ''
    if not r['retrieved']:
        if r['id'] not in ranks:
            return 'not_retrieved', ''
        emb, rr = ranks[r['id']]
        return ('rerank_drop' if rr is not None and emb <= 10 else 'retrieval_miss'), f'임베딩 {emb}위'
    if r['refusal']:
        return 'false_refusal', ''
    if bool(r['cite_v4']):                # 정답은 인용, 키워드에서 실패
        return ('missing_keyword', r['missing_groups']) if float(r['group_score']) < 1 else ('forbidden_phrase', r['must_not_hits'])
    return answer_failure(r['answer'], r.get('finish'), ctx_keys(r), articles) or ('cite_missing', r['cited'])

def fail_oos(r, articles):
    if r['scope'] == 'out':
        return ('', '') if r['refusal'] else ('answered_without_basis', r['cited'])
    if r['scope'] == 'in_control':
        passed, retrieved = bool(r['cite_gold']), r['source'] in str(r['top3']).split('|')
    else:                                 # in_reclassified: 그 법률을 인용해야 정답
        passed, retrieved = bool(r['cites_law']), bool(r['retrieved_law'])
    if passed:
        return '', ''
    if not retrieved:
        return 'not_retrieved', ''
    if r['refusal']:
        return 'false_refusal', ''
    return answer_failure(r['answer'], r.get('finish'), ctx_keys(r), articles) or ('cite_missing', r['cited'])

def fail_dq(r, dq_ranks, articles):
    """하위법령 정답 문항 (dqanswers). dq_ranks: id → 후보 10개 안에 있었는지 (dqretrieval 결과)"""
    if bool(r['cite_gold']):
        return '', ''
    if not r['retrieved']:
        if r['id'] not in dq_ranks:
            return 'not_retrieved', ''
        return ('rerank_drop' if dq_ranks[r['id']] else 'retrieval_miss'), '하위법령 필터·리랭킹' if dq_ranks[r['id']] else ''
    if r['refusal']:
        return 'false_refusal', ''
    return answer_failure(r['answer'], r.get('finish'), ctx_keys(r), articles) or ('cite_missing', r['cited'])

def fail_refusal(r):
    return ('', '') if r['refusal'] else ('answered_without_basis', r['cited'])

def classify(tag):
    """저장된 결과로 문항마다 실패 유형 → results/v4/fail_<tag>.csv (stage, id, group, fail_type, detail). 결과 CSV는 그대로"""
    articles, rows = article_keys(), []
    p = OUT / f'retrieval_{tag}.csv'
    ranks = {}
    if p.exists():
        r = pd.read_csv(p, **E)
        ranks = {i: (int(e), int(x) if 'rr_rank' in r else None)
                 for i, e, x in zip(r['id'], r['emb_rank'], r['rr_rank'] if 'rr_rank' in r else r['emb_rank'])}
    p = OUT / f'dqretrieval_{tag}.csv'
    dq_ranks = {}
    if p.exists():
        r = pd.read_csv(p, **E)
        dq_ranks = dict(zip(r['id'], r['in_cands'].astype(str) == 'True'))
    for stage, group, fn in (('answers', 'eval_cat', lambda r: fail_answers(r, ranks, articles)),
                             ('oos', 'scope', lambda r: fail_oos(r, articles)),
                             ('refusal', 'test', fail_refusal),
                             ('dqanswers', 'question_type', lambda r: fail_dq(r, dq_ranks, articles))):
        p = OUT / f'{stage}_{tag}.csv'
        if not p.exists():
            continue
        d = pd.read_csv(p, keep_default_na=False, **E)
        for c in ('retrieved', 'refusal', 'cites_law', 'retrieved_law'):
            if c in d:
                d[c] = d[c].astype(str) == 'True'
        for c in ('cite_v4', 'cite_gold', 'total_v4', 'group_score'):   # 문자열로 읽었으므로 '0'이 참이 되지 않게
            if c in d:
                d[c] = pd.to_numeric(d[c], errors='coerce')
        for _, r in d.iterrows():
            t, detail = fn(r)
            rows.append({'stage': stage, 'id': r['id'], 'group': r[group], 'fail_type': t, 'detail': detail})
    f = pd.DataFrame(rows, columns=['stage', 'id', 'group', 'fail_type', 'detail'])
    if len(f):
        f.to_csv(OUT / f'fail_{tag}.csv', index=False, **E)
        print_failures(f)
    return f

def print_failures(f):
    for stage, d in f.groupby('stage', sort=False):
        bad = d[d['fail_type'] != '']
        counts = bad['fail_type'].value_counts().reindex(FAIL_TYPES).dropna().astype(int)
        print(f'[실패 유형] {stage}: 실패 {len(bad)}/{len(d)} · ' + (' · '.join(f'{k} {v}' for k, v in counts.items()) or '없음'))

# ---- 두 실행 비교 ----
def compare_rules():
    """(section, group, metric, 좋아지는 방향, 경고 기준) — 기준은 v1 report.py와 같은 값"""
    from lawbot.report import METRICS
    return [('검색', '전체', '리랭킹 Recall@3', +1, METRICS['recall@3'][2]),
            ('검색', '전체', '임베딩 Recall@3', +1, METRICS['recall@3'][2]),
            ('답변', 'A_lsa', '종합 점수', +1, METRICS['total'][2]),
            ('답변', 'A_lsa', '종합 점수(검색 적중)', +1, METRICS['total_retrieved'][2]),
            ('답변', '전체', '조문 인용 정확도(법령명 포함)', +1, METRICS['cite_ok'][2]),
            ('답변', '전체', '거짓 거절률(검색 적중)', -1, METRICS['false_refusal'][2]),
            ('하위법령 검색', '전체', '정답 하위법령 첨부율', +1, METRICS['recall@3'][2]),
            ('하위법령 답변', '전체', '정답 하위법령 인용률', +1, METRICS['cite_ok'][2])]

def compare(base, new, samples=5):
    """경고 목록. summary_<tag>.csv 두 개를 나란히 출력하고, fail_<tag>.csv가 있으면 실패 유형별 건수와 새 실행의 샘플 id"""
    paths = [OUT / f'summary_{t}.csv' for t in (base, new)]
    for p in paths:
        if not p.exists():
            raise SystemExit(f'{p.name}이 없습니다: uv run lawbot-eval-v4 summary --tag {p.stem[8:]}')
    b, n = (pd.read_csv(p, **E) for p in paths)
    m = b.merge(n, on=['section', 'group', 'metric'], how='outer', suffixes=('_base', '_new'), sort=False)
    m['diff'] = (m['value_new'] - m['value_base']).round(4)
    pd.set_option('display.width', 200, 'display.max_rows', 300)
    print(m[['section', 'group', 'metric', 'value_base', 'value_new', 'diff', 'n_base', 'n_new']]
          .rename(columns={'value_base': base, 'value_new': new}).to_string(index=False))
    warn = []
    for section, group, metric, sign, limit in compare_rules():
        row = m[(m['section'] == section) & (m['group'] == group) & (m['metric'] == metric)]
        if not len(row) or row[['value_base', 'value_new']].isna().any(axis=None):
            continue
        r = row.iloc[0]
        if sign * (r['value_new'] - r['value_base']) < -limit:
            warn.append(f"{group} {metric}: {r['value_base']:.4f} → {r['value_new']:.4f} (기준 {limit})")
        if r['n_base'] != r['n_new']:
            warn.append(f"{group} {metric}: 문항 수가 다름 ({int(r['n_base'])} / {int(r['n_new'])}). 같은 분할·표본인지 확인")
    fails = [OUT / f'fail_{t}.csv' for t in (base, new)]
    if all(p.exists() for p in fails):
        fb, fn = (pd.read_csv(p, keep_default_na=False, **E) for p in fails)
        count = lambda f: f[f['fail_type'] != ''].groupby(['stage', 'fail_type']).size()
        t = pd.concat([count(fb).rename(base), count(fn).rename(new)], axis=1).fillna(0).astype(int)
        print('\n[실패 유형별 건수]\n' + t.to_string())
        for (stage, ft), g in fn[fn['fail_type'] != ''].groupby(['stage', 'fail_type']):
            print(f"  {new} {stage} {ft}: {', '.join(g['id'].astype(str).head(samples))}")
    print('\n경고 없음' if not warn else '\n[경고]\n- ' + '\n- '.join(warn))
    return warn

# ---- 실행 기록 ----
def git_state():
    """(커밋 해시, 작업 트리 변경 여부). git이 없으면 (None, None)"""
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src', 'data'], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip() != ''
        return head, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None

def run_meta(app, model, fmt, split, n, stages):
    """이번 실행의 설정: 모드(local/remote), 서버 /info(원격) 또는 같은 형식의 로컬 값, 답변 모델 폴더, 데이터 버전, 커밋"""
    client = getattr(app.retriever, 'client', None)
    if client is not None:
        info = client.get_json('/info')
    else:
        from lawbot.model_server import info as local_info
        info = local_info(app)
    head, dirty = git_state()
    return {'date': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
            'mode': 'remote' if client is not None else 'local', 'split': split, 'n': n, 'stages': stages,
            'model': model, 'fmt': fmt, 'model_dir': app.models.specs[model].path.name, 'info': info,
            'dataset': manifest.summary(manifest.load(app.profile.articles)), 'commit': head, 'dirty': dirty,
            'decree': None if getattr(app, 'decree', None) is None else
            {'tau': app.decree.tau, 'k': app.decree.k, 'models': list(app.decree.models), 'applied': uses_decrees(app, model)},
            'argv': sys.argv[1:]}

def write_run_meta(tag, meta):
    """results/v4/run_<tag>.json의 runs 목록에 추가 (이어서 실행하면 실행마다 한 항목)"""
    p = OUT / f'run_{tag}.json'
    runs = json.loads(p.read_text(encoding='utf-8'))['runs'] if p.exists() else []
    p.write_text(json.dumps({'tag': tag, 'runs': runs + [meta]}, ensure_ascii=False, indent=2, default=str) + '\n',
                 encoding='utf-8')

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
            if 'dec_n' in d:                  # 하위법령 첨부 구성: 법률 질문에 붙은 하위법령 (실험 law_other_ratio와 같은 정의)
                add('검색', g, '하위법령 첨부 수(평균)', mean(d['dec_n']), len(d))
                add('검색', g, '첨부 하위법령 중 다른 법 비율', round(float(d['dec_other'].sum() / max(d['dec_n'].sum(), 1)), 4), len(d))
                add('검색', g, '다른 법 하위법령이 붙은 문항 비율', mean(d['dec_other'] > 0), len(d))
                add('검색', g, '하위법령 리랭킹 초(평균)', mean(d['dec_rerank_sec']), len(d))
    p = OUT / f'answers_{tag}.csv'
    if p.exists():
        a = pd.read_csv(p, **E)
        for g, d in [('전체', a)] + list(a.groupby('eval_cat')):
            add('답변', g, '검색 top3 적중률', mean(d['retrieved']), len(d))
            add('답변', g, '조문 인용 정확도(법령명 포함)', mean(d['cite_v4']), len(d))
            add('답변', g, '거짓 거절률', mean(d['refusal']), len(d))
            add('답변', g, '거짓 거절률(검색 적중)', mean(d.loc[d['retrieved'], 'refusal']), int(d['retrieved'].sum()))
            if g == 'A_lsa' and d['group_score'].notna().any():   # 키워드 채점은 기존 650문항(근로기준법 test)만
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
    p = OUT / f'dqretrieval_{tag}.csv'
    if p.exists():
        r = pd.read_csv(p, **E)
        for g, d in [('전체', r)] + list(r.groupby('question_type')):
            add('하위법령 검색', g, '정답 하위법령 후보 10개 안', mean(d['in_cands']), len(d))
            add('하위법령 검색', g, '정답 하위법령 첨부율', mean(d['in_attached']), len(d))
            add('하위법령 검색', g, '하위법령 첨부 수(평균)', mean(d['n_att']), len(d))
        add('하위법령 검색', '전체', '하위법령 리랭킹 초(평균)', mean(r['decree_rerank_sec']), len(r))
    p = OUT / f'dqanswers_{tag}.csv'
    if p.exists():
        a = pd.read_csv(p, **E)
        for g, d in [('전체', a)] + list(a.groupby('question_type')):
            hit = d[d['retrieved']]
            add('하위법령 답변', g, '정답 하위법령이 참고 조문에 있음', mean(d['retrieved']), len(d))
            add('하위법령 답변', g, '정답 하위법령 인용률', mean(d['cite_gold']), len(d))
            add('하위법령 답변', g, '정답 하위법령 인용률(검색 적중)', mean(hit['cite_gold']), len(hit))
            add('하위법령 답변', g, '거짓 거절률(검색 적중)', mean(hit['refusal']), len(hit))
            add('하위법령 답변', g, '참고 조문 밖 인용', mean(d['cited_outside_ctx']), len(d))
        add('하위법령 답변', '전체', '프롬프트 토큰(평균)', mean(a['prompt_tokens']), len(a))
        add('하위법령 답변', '전체', '첫 토큰(초, 중앙값)', round(float(a['ttft'].median()), 2), len(a))
        add('하위법령 답변', '전체', '완료(초, 평균)', mean(a['total']), len(a))
    s = pd.DataFrame(rows)
    if len(s):
        s.to_csv(OUT / f'summary_{tag}.csv', index=False, **E)
        pd.set_option('display.width', 200, 'display.max_rows', 200)
        print(s.to_string(index=False))
    return s

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', nargs='?', default='all',
                    choices=['retrieval', 'answers', 'oos', 'refusal', 'decree', 'summary', 'compare', 'all'],
                    help='생략하면 all (네 단계를 차례로 실행한 뒤 요약. 하위법령 구성 + val이면 decree도)')
    ap.add_argument('--tag', default=None, help='결과 파일 이름 (기본: <모델 key>_<형식>_rr 또는 _emb, val은 끝에 _val)')
    ap.add_argument('--split', choices=SPLITS, default='test',
                    help='문항 분할 (기본 test: 평가 전용. 설정·규칙 선택은 val로)')
    ap.add_argument('--base', default=None, help='compare: 기준 tag')
    ap.add_argument('--new', default=None, help='compare: 비교 tag')
    ap.add_argument('--strict', action='store_true', help='compare: 경고가 있으면 종료 코드 1')
    ap.add_argument('--n', type=int, default=200, help='답변 생성 표본 수 (A_lsa·B_newlaw 반씩)')
    ap.add_argument('--model', default=None, help='답변 모델 key (기본: .env의 첫 번째 모델)')
    ap.add_argument('--fmt', choices=['concise', 'full'], default=None, help='답변 형식 (기본: 그 모델의 기본 형식)')
    ap.add_argument('--no-rerank', action='store_true', help='retrieval 단계에서 리랭킹 순위를 재지 않음')
    args = ap.parse_args()
    envfile.load()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.stage == 'compare':
        if not (args.base and args.new):
            raise SystemExit('compare는 --base와 --new가 필요합니다 (results/v4/summary_<tag>.csv의 tag)')
        warn = compare(args.base, args.new)
        if args.strict and warn:
            raise SystemExit(1)
        return
    if args.split == 'val' and args.stage == 'oos':
        raise SystemExit('범위 밖·대조군 문항은 평가 전용이라 val이 없습니다 (--split test로만 실행)')
    if args.split != 'val' and args.stage == 'decree':
        raise SystemExit('decree 단계는 --split val만 (고정 표본 decree_ctx_dq_val200.csv가 val 문항)')
    test = load_test(args.split)
    if args.stage == 'summary':
        if not args.tag:
            raise SystemExit('summary는 --tag가 필요합니다 (results/v4의 파일 이름)')
        rescore(args.tag)
        summarize(args.tag, test)
        classify(args.tag)
        return
    from lawbot.service import load_app
    app = load_app()
    if app.profile.name != 'v4':
        raise SystemExit('v4 프로필에서 실행하세요 (LAWBOT_PROFILE=v4)')
    model = args.model or app.profile.models[0].key
    fmt = args.fmt or app.models.specs[model].fmt
    decree = uses_decrees(app, model)
    if args.stage == 'decree' and not decree:
        raise SystemExit(f'decree 단계는 하위법령 첨부 구성에서만: LAWBOT_DECREE_ENABLED=1, LAWBOT_DECREE_MODELS에 {model}')
    tag = args.tag or (f"{model}_{fmt}_{'rr' if app.reranker else 'emb'}" + ('_dec' if decree else '')
                       + ('_val' if args.split == 'val' else ''))
    sample = answer_sample(test, args.n)
    print(f'[eval_v4] tag {tag} · 모델 {model} · 형식 {fmt} · 리랭커 {"켜짐" if app.reranker else "꺼짐"} · '
          f'하위법령 {"첨부" if decree else "없음"} · '
          f'{args.split} {len(test)} · 표본 {len(sample)}')
    stages = ['retrieval', 'answers', 'oos', 'refusal'] if args.stage == 'all' else [args.stage]
    if args.split == 'val':
        stages = [s for s in stages if s != 'oos'] + (['decree'] if args.stage == 'all' and decree else [])
    write_run_meta(tag, run_meta(app, model, fmt, args.split, args.n, stages))
    t0 = time.perf_counter()
    for s in stages:
        if s == 'retrieval':
            run_retrieval(app, test, OUT / f'retrieval_{tag}.csv', rerank=not args.no_rerank,
                          decree=decree and not args.no_rerank)
        elif s == 'answers':
            run_answers(app, model, fmt, sample, OUT / f'answers_{tag}.csv')
        elif s == 'oos':
            run_oos(app, model, fmt, OUT / f'oos_{tag}.csv')
        elif s == 'refusal':
            run_refusal(app, model, fmt, sample, OUT / f'refusal_{tag}.csv')
        elif s == 'decree':
            dq = load_decree_questions('val')
            run_decree_retrieval(app, model, dq, OUT / f'dqretrieval_{tag}.csv')
            run_decree_answers(app, model, fmt, dq, OUT / f'dqanswers_{tag}.csv')
    summarize(tag, test)
    classify(tag)
    print(f'[eval_v4] {", ".join(stages)} 완료 · {(time.perf_counter() - t0) / 60:.1f}분 · 결과 {OUT}/*_{tag}.csv')

if __name__ == '__main__':
    main()
