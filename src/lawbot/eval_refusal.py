# src/lawbot/eval_refusal.py  →  uv run python -m lawbot.eval_refusal [--hard] [결과파일명]
# 정답 조문을 뺼 참고 조문 3개를 주고 거절하는지 본다 (코랩 bf16 결과를 맥 4비트로 다시 재기)
#   다른 장 조문: lawbot_2 셀 11과 같은 방식 / 비슷한 조문: lawbot_5 셀 2와 같은 방식
#   --hard: data/refusal_hard_cases.csv에서 근거 없이 주장한 사례(억지적용·지어냄)를 기록된 참고 조문 그대로 다시 묻는다
# 결과파일명 기본값 refusal_v2_4bit.csv / refusal_hard_4bit.csv (refusal_v1_4bit.csv는 기준선이므로 덮어쓰지 않음)
import os
import sys

import numpy as np
import pandas as pd

from lawbot.llm import LLM
from lawbot.paths import ARTICLES, DATA, EVAL, LLM_4BIT, RESULTS
from lawbot.refusal import is_refusal

HARD_LABELS = ('억지적용', '지어냄')
args = sys.argv[1:]
hard = '--hard' in args
names = [a for a in args if not a.startswith('--')]
name = names[0] if names else ('refusal_hard_4bit.csv' if hard else 'refusal_v2_4bit.csv')
dev = pd.read_csv(EVAL, encoding='utf-8-sig')


def banned(gold, conf):
    """정답 + 헷갈리는 조문('제55조;제60조' 형태)"""
    return {gold} | (set(conf.split(';')) if isinstance(conf, str) else set())


if hard:
    h = pd.read_csv(DATA / 'refusal_hard_cases.csv', encoding='utf-8-sig')
    h = h[h['label'].isin(HARD_LABELS)]
    q = dict(zip(dev['id'], dev['question']))
    cases = [(t, i, q[i], c.split('|')) for t, i, c in zip(h['test'], h['id'], h['context_ids'])]
else:
    from lawbot.retriever import Retriever

    law = pd.read_csv(ARTICLES, encoding='utf-8-sig')
    chap = dict(zip(law['article_id'], law['chapter']))
    live = law.loc[law['deleted'] == 0, 'article_id'].tolist()
    ref = dev.sample(100, random_state=42)  # eval_dev --n 100과 같은 100문항

    # ① 다른 장 조문: 정답·헷갈리는 조문이 속한 장을 빼고 무작위 3개
    rng = np.random.default_rng(42)
    other = []
    for g, c in zip(ref['article_id'], ref['confusable_with']):
        ban_chap = {chap[a] for a in banned(g, c)}
        pool = [a for a in live if chap[a] not in ban_chap]
        other.append([str(a) for a in rng.choice(pool, 3, replace=False)])

    # ② 비슷한 조문: 검색 순위에서 정답·헷갈리는 조문을 빼고 위에서 3개
    R = Retriever()
    rank = R.rank_all(ref['question'])
    similar = [
        [a for a in R.ids[r] if a not in banned(g, c)][:3]
        for r, g, c in zip(rank, ref['article_id'], ref['confusable_with'])
    ]
    cases = [
        (t, i, qq, ids)
        for t, contexts in [('다른 장 조문', other), ('비슷한 조문', similar)]
        for i, qq, ids in zip(ref['id'], ref['question'], contexts)
    ]

L = LLM(os.environ.get('LLM_PATH', LLM_4BIT))  # 웹앱과 같이 LLM_PATH로 다른 모델
rows = []
for test, rid, q, ids in cases:
    answer = ''.join(g.text for g in L.stream(q, [{'article_id': a} for a in ids]))
    rows.append(
        {'test': test, 'id': rid, 'context_ids': '|'.join(ids), 'answer': answer, 'refusal': is_refusal(answer)}
    )
    print(f'{test} {len(rows)}/{len(cases)}', end='\r')

out = pd.DataFrame(rows)
out.to_csv(RESULTS / name, index=False, encoding='utf-8-sig')
print(f'\n[표 D] 거절 정확도 ({name})')
print(out.groupby('test')['refusal'].agg(['size', 'mean']).round(3))
