import re
import pandas as pd
from lawbot.paths import EVAL, RESULTS

o = pd.read_csv(RESULTS / 'scored_sllm_mlx_4bit.csv', encoding='utf-8-sig')
dev = pd.read_csv(EVAL, encoding='utf-8-sig').set_index('id')
bad = o[o['retrieved'] & (o['cite_ok'] == 0)]
print(len(bad), '문항: 검색은 맞았는데 정답 조문을 인용하지 않음')
for _, r in bad.iterrows():
    m = re.search(r'【근거 조문】(.*?)(?=【|$)', r['answer'], re.S)
    print(f"\n[{r['id']}] 정답 {dev.loc[r['id'], 'article_id']} | "
          f"답변 근거: {m.group(1).strip()[:80] if m else '(없음)'}")
    print('  결론:', r['answer'][:120].replace('\n', ' '))
