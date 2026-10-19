# src/lawbot/eval_oos.py  →  uv run python -m lawbot.eval_oos [결과파일명]
# 결과파일명 기본값 oos_v2_4bit.csv (oos_v1_4bit.csv는 82문항 기준선이므로 덮어쓰지 않음)
# 범위 밖 질문(data/oos_eval.csv)을 웹앱과 같은 방식(검색 상위 3개 → sLLM)으로 답하게 하고 거절했는지 본다
import os, sys
import pandas as pd
from lawbot.paths import DATA, LLM_4BIT, RESULTS
from lawbot.retriever import Retriever
from lawbot.llm import LLM
from lawbot.refusal import is_refusal, cited_articles, oos_verdict

oos = pd.read_csv(DATA / 'oos_eval.csv', encoding='utf-8-sig')
name = sys.argv[1] if len(sys.argv) > 1 else 'oos_v2_4bit.csv'
R, L = Retriever(), LLM(os.environ.get('LLM_PATH', LLM_4BIT))   # 웹앱과 같이 LLM_PATH로 다른 모델

rows = []
for i, r in enumerate(oos.itertuples(), 1):
    hits = R.search(r.question, k=3)
    answer = ''.join(g.text for g in L.stream(r.question, hits))
    rows.append({'id': r.id, 'top3': '|'.join(h['article_id'] for h in hits),
                 'top1_score': hits[0]['score'], 'answer': answer})
    print(f'{i}/{len(oos)}', end='\r')

out = oos.merge(pd.DataFrame(rows), on='id')
out['refusal'] = out['answer'].map(is_refusal)
out['cited'] = out['answer'].map(lambda a: '|'.join(cited_articles(a)))
out['near'] = out['near_lsa'].notna()          # 근로기준법에 비슷한 조문이 있는 질문 (README 0.250의 기준)
out['verdict'] = [oos_verdict(a, r) for a, r in zip(out['answer'], out['refer_lsa'])]
RESULTS.mkdir(exist_ok=True)
out.to_csv(RESULTS / name, index=False, encoding='utf-8-sig')

print('\n[표 B] 카테고리별 거절률')
print(out.groupby(['category', 'near']).agg(문항=('id', 'size'), 거절률=('refusal', 'mean'),
                                            유사도_중앙값=('top1_score', 'median')).round(3))
print('전체 거절률', round(out['refusal'].mean(), 3))
print('\n[표 B-2] 판정별 비율 (억지적용이 낮을수록 좋음, 안내는 다른 법에 맡기는 조문만 인용)')
out['near_v2'] = out['near_lsa_v2'].notna()
print(pd.crosstab([out['category'], out['near_v2']], out['verdict'], normalize='index', margins=True).round(3))
print('전체 억지적용률', round((out['verdict'] == '억지적용').mean(), 3))
print('\n[표 C] 거절하지 않은 문항')
print(out.loc[~out['refusal'], ['id', 'source', 'verdict', 'cited', 'question']].to_string(index=False))
