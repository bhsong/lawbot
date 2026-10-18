import pandas as pd
from mlx_lm import stream_generate
from lawbot.paths import EVAL, RESULTS
from lawbot.llm import LLM, SYSTEM
from lawbot.score_answers import score_one

dev = pd.read_csv(EVAL, encoding='utf-8-sig')
sub = dev.sample(100, random_state=42)            # eval_dev와 같은 100문항
L = LLM()
rows = []
for i, (_, r) in enumerate(sub.iterrows(), 1):
    p = L.tok.apply_chat_template([{'role': 'system', 'content': SYSTEM},
                                   {'role': 'user', 'content': r['oracle_prompt']}],
                                  add_generation_prompt=True, tokenize=False, enable_thinking=False)
    text = ''.join(g.text for g in stream_generate(L.model, L.tok, p, max_tokens=1024, sampler=L.sampler))
    rows.append({'id': r['id'], 'answer': text} | score_one(r, text))
    print(f'{i}/100', end='\r')
o = pd.DataFrame(rows)
o.to_csv(RESULTS / 'scored_oracle_4bit.csv', index=False, encoding='utf-8-sig')
print('\n[4비트 + 정답 조문, 100문항]')
print(o[['cite_ok', 'group_score', 'must_not_ok', 'total']].mean().round(3))
