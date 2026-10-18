# src/lawbot/summ_dev.py  →  uv run python -m lawbot.summ_dev scored_sllm_mlx_4bit.csv
# eval_dev 결과 파일에 거절 판정을 붙여 범위 안 기준선을 요약한다 (생성 없이 바로 끝남)
import sys
import pandas as pd
from lawbot.paths import RESULTS
from lawbot.refusal import is_refusal

o = pd.read_csv(RESULTS / sys.argv[1], encoding='utf-8-sig')
o['refusal'] = o['answer'].fillna('').map(is_refusal)
hit, miss = o[o['retrieved']], o[~o['retrieved']]
print('문항', len(o), '| 검색 top3 적중', len(hit))
print('종합 점수', round(o['total'].mean(), 3), '| 검색 적중만', round(hit['total'].mean(), 3))
print('거짓 거절률 (검색 적중)', f"{hit['refusal'].sum()}/{len(hit)}")
print('검색 실패 시 거절률', f"{miss['refusal'].sum()}/{len(miss)}")
