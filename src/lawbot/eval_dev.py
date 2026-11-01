# src/lawbot/eval_dev.py  →  uv run lawbot-eval --n 100
#   검색 순위(650문항)는 results/retrieval_<임베딩폴더>.csv, 답변 채점은 results/scored_<모델폴더>.csv (--out으로 변경)
#   범위 안 대조군만: uv run lawbot-eval --ids data/scope_controls.csv --out scored_controls_4bit.csv
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from lawbot.llm import LLM
from lawbot.paths import EMB, EVAL, LLM_4BIT, RESULTS
from lawbot.retriever import Retriever
from lawbot.score_answers import score_one


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--llm', default=str(LLM_4BIT))
    ap.add_argument('--emb', default=str(EMB))
    ap.add_argument('--n', type=int, default=100, help='답변까지 생성할 질문 수, 0이면 검색만, 650이면 전체')
    ap.add_argument('--ids', help='답변을 생성할 문항 id 목록 CSV (id 열). 주면 --n 대신 이 문항만')
    ap.add_argument('--out', help='답변 채점 결과 파일명 (기본 scored_<모델폴더>.csv)')
    args = ap.parse_args()

    dev = pd.read_csv(EVAL, encoding='utf-8-sig')
    R = Retriever(args.emb)
    rank = R.rank_all(dev['question'])
    # 130개 조문 전체 순위 중 정답이 몇 등인지 (1등 = 1)
    pos = np.array([np.where(R.ids[r] == g)[0][0] + 1 for g, r in zip(dev['article_id'], rank)])
    old = dev['article_id'].isin(R.starter).to_numpy()
    groups = {'전체': np.ones(len(dev), bool), '기존40': old, '신규90': ~old}
    rec = {f'recall@{k}': {n: (pos[g] <= k).mean() for n, g in groups.items()} for k in (1, 3, 5, 10)}
    rec['MRR'] = {n: (1 / pos[g]).mean() for n, g in groups.items()}
    print('[표 A] dev 셋 검색 recall·MRR (650개)')
    print(pd.DataFrame(rec).round(3))
    RESULTS.mkdir(exist_ok=True)
    pd.DataFrame({'id': dev['id'], 'article_id': dev['article_id'], 'rank': pos}).to_csv(
        RESULTS / f'retrieval_{Path(args.emb).name}.csv', index=False, encoding='utf-8-sig'
    )

    if args.ids:
        sub = dev[dev['id'].isin(pd.read_csv(args.ids, encoding='utf-8-sig')['id'])]
    elif args.n:
        sub = dev if args.n >= len(dev) else dev.sample(args.n, random_state=42)
    else:
        return
    L = LLM(args.llm)
    rows = []
    for _, r in sub.iterrows():
        hits = R.search(r['question'], k=3)
        t0, first, text = time.time(), None, ''
        for g in L.stream(r['question'], hits):
            first = first or (time.time() - t0)
            text += g.text
        rows.append(
            {
                'id': r['id'],
                'answer': text,
                'retrieved': r['article_id'] in [h['article_id'] for h in hits],
                'ttft': first,
                'tps': g.generation_tps,
                'sec': time.time() - t0,
                'peak_gb': g.peak_memory,
            }
            | score_one(r, text)
        )
    o = pd.DataFrame(rows)
    name = Path(args.llm).name
    o.to_csv(RESULTS / (args.out or f'scored_{name}.csv'), index=False, encoding='utf-8-sig')
    print(f'\n[표 B·C] {name}, 질문 {len(o)}개')
    print(
        pd.Series(
            {
                '검색 top3 적중률': o['retrieved'].mean(),
                '조문 인용 정확도': o['cite_ok'].mean(),
                '키워드 그룹 충족률': o['group_score'].mean(),
                '금지 표현 없음': o['must_not_ok'].mean(),
                '종합 점수': o['total'].mean(),
                '종합 점수 (검색 적중 문항만)': o.loc[o['retrieved'], 'total'].mean(),
                '평균 길이(자)': o['answer_len'].mean(),
                '첫 토큰까지(초, 중앙값)': o['ttft'].median(),
                '생성 속도(tok/s, 평균)': o['tps'].mean(),
                '질문당 소요(초, 평균)': o['sec'].mean(),
                'MLX 최대 메모리(GB)': o['peak_gb'].max(),
            }
        ).round(3)
    )


if __name__ == '__main__':
    main()
