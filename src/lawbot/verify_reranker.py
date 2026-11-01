# src/lawbot/verify_reranker.py  →  uv run python -m lawbot.verify_reranker --colab <v4 폴더> [--split val] [--limit 0]
# MLX 리랭커가 Colab 실험(lawbot_v4_reranker.ipynb)과 같은 입력에서 같은 순위를 내는지 대조한다 (맥 전용, 모델 필요)
#   입력: Colab의 질문과 임베딩 Top 10(retrieval_v4.csv)을 그대로 씀 → 리랭커 차이만 본다 (맥 임베딩 검색 차이는 섞지 않음)
#   조문 문서: 앱과 같은 retriever.DOC_STYLES['v4'] 문자열. Colab의 DOC와 글자 단위로 같은지도 검사
#   비교: Colab reranker_exp/r4_<split>.csv의 순위·점수 (top1·top3 일치, 점수 차이, Recall@1·3, MRR@3, 구제·이탈)
# --colab 폴더: law_articles_v4.csv, qa_full_v4.csv, split_v4.csv, retrieval_v4.csv, reranker_exp/r4_<split>.csv
# 기본 val. test는 노트북에 기록된 수치와 대조할 때만 쓰고, 이 결과로 설정(후보 수·문구 등)을 바꾸지 않는다
# 결과: results/reranker_verify_<split>_<tag>.csv (질문마다 한 행)
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from lawbot import deploy, envfile
from lawbot.paths import RESULTS
from lawbot.reranker import Reranker, pair_text, rank_order
from lawbot.retriever import DOC_STYLES


def read(path):
    return pd.read_csv(path, encoding='utf-8-sig')

def colab_frame(v, split):
    """노트북 셀 2와 같은 문항 선택 (A_lsa·B_newlaw, split_emb)"""
    qa, sp, ret = read(v / 'qa_full_v4.csv'), read(v / 'split_v4.csv'), read(v / 'retrieval_v4.csv')
    base = (qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
              .merge(ret[['id', 'top10']], on='id', validate='one_to_one'))
    base = base[base.eval_cat.isin(['A_lsa', 'B_newlaw']) & (base.split_emb == split)].copy()
    base['ids10'] = base.top10.str.split('|')
    return base

def docs(v, articles):
    """(앱 문서 {조문 키: 문자열}, Colab과 다른 조문 키 목록). 키는 Colab article_id('근로기준법 제26조')"""
    law = read(articles)
    law = law[law['deleted'] == 0].reset_index(drop=True)
    if 'article_no' in law:
        law['article_id'] = law['article_no']                    # retriever와 같은 규칙
    app = dict(zip(law['law'] + ' ' + law['article_id'], DOC_STYLES['v4'](law)))
    cl = read(v / 'law_articles_v4.csv')
    cl = cl[cl['deleted'].astype(int) == 0]
    colab = {r.article_id: f'{r.article_id}({r.title})\n{r.text}' for r in cl.itertuples(index=False)}
    diff = sorted(k for k in colab.keys() | app.keys() if colab.get(k) != app.get(k))
    return app, diff

def positions(frame, col):
    return np.array([next((i + 1 for i, k in enumerate(r[col]) if k == r['article_id']), 999)
                     for _, r in frame.iterrows()])

def metrics(p):
    return {'recall@1': round(float(np.mean(p <= 1)), 5), 'recall@3': round(float(np.mean(p <= 3)), 5),
            'mrr@3': round(float(np.where(p <= 3, 1 / p, 0).mean()), 5)}

def rescued_lost(frame, col):
    old = frame.apply(lambda r: r['article_id'] in r['ids10'][:3], axis=1)
    new = frame.apply(lambda r: r['article_id'] in r[col][:3], axis=1)
    return int((~old & new).sum()), int((old & ~new).sum())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--colab', required=True, type=Path, help='Colab v4 폴더 (Drive labor_law/v4를 내려받은 곳)')
    ap.add_argument('--split', default='val', choices=['val', 'test'])
    ap.add_argument('--limit', type=int, default=0, help='앞에서부터 N문항만 (0: 전체)')
    ap.add_argument('--tag', default='mlx4bit')
    args = ap.parse_args()
    envfile.load()
    v = args.colab
    frame = colab_frame(v, args.split)
    colab = read(v / 'reranker_exp' / f'r4_{args.split}.csv').set_index('id')
    if args.limit:
        frame = frame.head(args.limit)
    app_docs, diff = docs(v, deploy.get_profile('v4').articles)
    print(f'문항 {len(frame)} ({args.split}) · 조문 문서 Colab과 다른 조문 {len(diff)}개 {diff[:5]}')
    missing = {k for ids in frame.ids10 for k in ids} - app_docs.keys()
    if missing:
        raise SystemExit(f'앱 조문 CSV에 없는 후보 조문: {sorted(missing)[:5]}')

    t0 = time.perf_counter()
    rr = Reranker(deploy.reranker_path())
    print(f'리랭커 {rr.path} 불러오기 {time.perf_counter() - t0:.1f}초')
    rows = []
    for j, r in enumerate(frame.itertuples(index=False), 1):
        cand = [app_docs[k] for k in r.ids10]
        st = time.perf_counter()
        scores = rr.score(r.question, cand)
        sec = time.perf_counter() - st
        order = rank_order(scores)
        c = colab.loc[r.id]
        cs = json.loads(c.scores10)
        n_tok = [len(rr.tok.encode(pair_text(r.question, d), add_special_tokens=False)) for d in cand]
        top = sorted(cs, reverse=True)
        rows.append({'id': r.id, 'seconds': round(sec, 4),
                     'mlx10': '|'.join(r.ids10[i] for i in order), 'colab10': c.reranked10,
                     'mlx_scores10': json.dumps([round(s, 8) for s in scores]), 'colab_scores10': c.scores10,
                     'max_abs_diff': max(abs(a - b) for a, b in zip(scores, cs)),
                     'spearman': float(pd.Series(scores).corr(pd.Series(cs), method='spearman')),
                     'colab_margin3': top[2] - top[3],   # Colab 3위와 4위 점수 차: 작으면 양자화 오차로 뒤바뀌기 쉬움
                     'truncated': sum(n > rr.max_mid for n in n_tok)})
        if j % 25 == 0 or j == len(frame):
            print(f'{j}/{len(frame)} 평균 {np.mean([x["seconds"] for x in rows]):.3f}초', flush=True)
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'reranker_verify_{args.split}_{args.tag}.csv', index=False, encoding='utf-8-sig')

    m = frame.set_index('id').loc[o.id].reset_index()
    m['mlx'] = o.mlx10.str.split('|').values
    m['colab'] = o.colab10.str.split('|').values
    same1 = (m.mlx.str[0] == m.colab.str[0]).mean()
    same3 = (m.mlx.str[:3].map(tuple) == m.colab.str[:3].map(tuple)).mean()
    set3 = (m.mlx.str[:3].map(frozenset) == m.colab.str[:3].map(frozenset)).mean()
    same10 = (m.mlx.map(tuple) == m.colab.map(tuple)).mean()
    print(f'\n[Colab 대조] top1 일치 {same1:.4f} · top3 순서 일치 {same3:.4f} · top3 집합 일치 {set3:.4f} · top10 전체 일치 {same10:.4f}')
    print(f"점수 최대 차이 중앙값 {o.max_abs_diff.median():.4f} · 최대 {o.max_abs_diff.max():.4f} · "
          f"Spearman 평균 {o.spearman.mean():.4f} · 잘린 후보 {o.truncated.sum()}개")
    table = [{'run': 'embedding', **metrics(positions(m, 'ids10'))},
             {'run': 'colab_r4', **metrics(positions(m, 'colab')), **dict(zip(('rescued', 'lost'), rescued_lost(m, 'colab')))},
             {'run': 'mlx_r4', **metrics(positions(m, 'mlx')), **dict(zip(('rescued', 'lost'), rescued_lost(m, 'mlx'))),
              'mean_s': round(o.seconds.mean(), 4), 'p95_s': round(o.seconds.quantile(.95), 4)}]
    print(pd.DataFrame(table).to_string(index=False))
    differ = (m.mlx.str[:3].map(frozenset) != m.colab.str[:3].map(frozenset)).to_numpy()
    flips = m[differ].apply(lambda r: int(r['article_id'] in r['colab'][:3]) - int(r['article_id'] in r['mlx'][:3]), axis=1)
    print(f'top3 집합 불일치 {differ.sum()}건: 정답이 Colab에만 {int((flips == 1).sum())} · MLX에만 {int((flips == -1).sum())} · '
          f'Colab 3·4위 점수 차 중앙값 불일치 {o.colab_margin3[differ].median():.4f} / 전체 {o.colab_margin3.median():.4f}')

if __name__ == '__main__':
    main()
