# src/lawbot/eval_oos.py  →  uv run python -m lawbot.eval_oos
# 범위 밖 질문 82개를 웹앱과 같은 방식(검색 상위 3개 → sLLM)으로 답하게 하고 거절했는지 본다
import pandas as pd
from lawbot.paths import DATA, RESULTS
from lawbot.retriever import Retriever
from lawbot.llm import LLM
from lawbot.refusal import is_refusal, cited_articles

oos = pd.read_csv(DATA / 'oos_eval.csv', encoding='utf-8-sig')
R, L = Retriever(), LLM()

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
out['near'] = out['near_lsa'].notna()          # 근로기준법에 비슷한 조문이 있는 질문
RESULTS.mkdir(exist_ok=True)
out.to_csv(RESULTS / 'oos_v1_4bit.csv', index=False, encoding='utf-8-sig')

print('\n[표 B] 카테고리별 거절률')
print(out.groupby(['category', 'near']).agg(문항=('id', 'size'), 거절률=('refusal', 'mean'),
                                            유사도_중앙값=('top1_score', 'median')).round(3))
print('전체 거절률', round(out['refusal'].mean(), 3))
print('\n[표 C] 거절하지 않은 문항')
print(out.loc[~out['refusal'], ['id', 'source', 'cited', 'question']].to_string(index=False))
