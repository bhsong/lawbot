# 평가 기록: 시행령·시행규칙 첨부 실험 (val, 참고 조문 고정)

- 날짜: 2026-10-29~31 · Colab fp16 점수 + 맥 MLX 4비트 점수, Pro 27B 답변 (val만)
- 코드: `lawbot.decree_filter`·`build_decree_ctx`·`eval_decree` · 결과 `data/decree_exp/filter_rules*.csv`, `results/v4/decree_answers_pro*.csv`
- 웹앱 반영 뒤 검증: [`2026-10-31-decree-attach.md`](2026-10-31-decree-attach.md)
- 원본: README "성능"·"Results" 절에서 옮김 (2026-10-31, 내용은 그대로)

## 한국어

*시행령·시행규칙 첨부 실험 (val, 웹앱 반영 전, 2026-10-29~31)*

고시·시행령에 있는 내용(예: 평균임금 계산에서 빼는 기간, 융자 이자율)은 법률 조문만으로 답할 수 없어서, 7개 법률의 시행령·시행규칙 397개 조문을 더하는 실험을 했습니다. 812개를 한 번에 검색하면 법률 정답 Recall@3이 0.950 → 0.927로 떨어져, 법률 검색은 그대로 두고 하위법령을 따로 찾아 붙이는 방식(구성 B)을 골랐습니다.

```
법률 415개: 임베딩 상위 10개 → 리랭커 → 상위 3개 (지금과 같음)
하위법령 397개: 임베딩 상위 10개 → 리랭커 → 필터 → 상위 2개 첨부
  필터: 법률 1위와 같은 법의 하위법령은 그대로, 다른 법의 하위법령은 리랭커 점수 0.9 이상일 때만
```

필터 규칙은 저장된 리랭커 점수로 비교해 골랐습니다(`lawbot.decree_filter`). 하위법령 정답 val 444문항의 정답 포함률과, 법률 정답 val 1,110문항에 다른 법 하위법령이 붙는 비율을 함께 봤습니다.

| 필터 (첨부 2개) | 하위법령 정답 포함 (fp16 / 맥 MLX 4비트) | 법률 질문에 붙은 다른 법 하위법령 비율 (fp16 / MLX) | 프롬프트 토큰 |
| --- | --- | --- | --- |
| 없음 | 0.928 / 0.923 | 0.238 / 0.236 | 1,406 |
| 법률 1위와 같은 법만 | 0.824 / 0.820 | 0.031 / 0.044 | 1,366 |
| **같은 법 + 다른 법은 점수 0.9 이상 (채택)** | **0.921 / 0.912** | **0.055 / 0.064** | **1,376** |

답변은 Qwen3.8-27B(맥 미니, 참고 조문 고정)로 val 표본을 평가했습니다(`lawbot.eval_decree`, `results/v4/decree_answers_pro*.csv`).

| 문항 (val 200) | 지표 | 법률만 | 같은 법만 | 채택 규칙 |
| --- | --- | --- | --- | --- |
| 하위법령이 정답인 질문 | 정답 하위법령 인용 | 0.000 | 0.820 | **0.900** |
| | 참고 조문 밖 인용 | 0.010 | 0.005 | 0.000 |
| | 완료 시간 (초) | 14.1 | 17.1 | 17.1 |
| 법률이 정답인 질문 | 정답 인용 (검색 적중) | 0.947 | 0.947 | 0.947 |
| | 거짓 거절 (검색 적중) | 0.048 | 0.043 | 0.043 |
| | 종합 점수 (검색 적중) | 0.894 | 0.896 | 0.896 |

- 법률 조문만 주면 하위법령 질문의 62%를 거절합니다. 하위법령을 붙이면 법률 질문 답변은 그대로이고, 하위법령 질문의 정답 인용이 0.900이 됩니다.
- 구어체 질문은 같은 법 필터에서 0.647로 가장 낮았습니다. '체불'·'이자'처럼 법령명 없는 질문에서 법률 리랭커 1위가 근로기준법으로 쏠렸기 때문입니다. 채택 규칙에서는 0.765입니다.
- 임베딩은 다시 학습하지 않았습니다(하위법령 397개 중 상위 10개에 정답 0.964).
- 이 방식과 규칙을 그대로 웹앱에 넣었습니다(아래 "시행령·시행규칙 첨부"). 위 수치는 참고 조문을 고정한 실험 값이고, 웹앱 경로(MLX 4비트 리랭커 점수로 고른 첨부)로 `lawbot-eval-v4 --split val`을 다시 잰 값은 맥 미니 수동 검증 뒤 이 절에 더합니다. MLX 점수는 fp16과 첨부 집합이 일부 문항에서 달라 정확히 같지는 않습니다.

## English

*Enforcement decrees and rules (validation before web integration, 2026-10-29 to 31)*

Some answers live in enforcement decrees and rules rather than the Acts (e.g. periods excluded from average wages, loan interest rates), so the 397 decree and rule articles of the seven laws were added as an experiment. Searching all 812 articles together lowered Act Recall@3 from 0.950 to 0.927, so Act retrieval stays as it is and decree articles are retrieved separately and attached (setup B):

```
415 Act articles: embedding top 10 → reranker → top 3 (unchanged)
397 decree/rule articles: embedding top 10 → reranker → filter → attach top 2
  filter: keep decrees of the same law as the top Act article; decrees of other laws only with reranker score ≥ 0.9
```

The filter was chosen from saved reranker scores (`lawbot.decree_filter`), weighing the gold-decree hit rate on 444 decree-answer validation questions against how often decrees of another law get attached to the 1,110 Act-answer validation questions.

| Filter (attach 2) | Gold decree attached (fp16 / Mac MLX 4-bit) | Other-law decrees on Act questions (fp16 / MLX) | Prompt tokens |
| --- | --- | --- | --- |
| None | 0.928 / 0.923 | 0.238 / 0.236 | 1,406 |
| Same law as the top Act article only | 0.824 / 0.820 | 0.031 / 0.044 | 1,366 |
| **Same law + other laws at score ≥ 0.9 (chosen)** | **0.921 / 0.912** | **0.055 / 0.064** | **1,376** |

Answers were evaluated with Qwen3.8-27B on the Mac mini with fixed contexts (`lawbot.eval_decree`, `results/v4/decree_answers_pro*.csv`):

| Questions (val 200) | Metric | Acts only | Same law only | Chosen rule |
| --- | --- | --- | --- | --- |
| Decree is the answer | Gold decree cited | 0.000 | 0.820 | **0.900** |
| | Citations outside the context | 0.010 | 0.005 | 0.000 |
| | Time to completion (s) | 14.1 | 17.1 | 17.1 |
| Act is the answer | Gold cited (retrieval hit) | 0.947 | 0.947 | 0.947 |
| | False refusal (retrieval hit) | 0.048 | 0.043 | 0.043 |
| | Total score (retrieval hit) | 0.894 | 0.896 | 0.896 |

With Act articles only, the model refuses 62% of decree questions. Attaching decrees leaves Act answers unchanged and brings gold-decree citation to 0.900. Colloquial questions were lowest under the same-law filter (0.647) because the Act reranker leaned toward the Labor Standards Act on questions without a law name; the chosen rule reaches 0.765. The embedding model was not retrained (gold decree in the top 10 of 397: 0.964). The web app now uses this setup and rule (see "Enforcement decrees and rules (optional, v4)" below). The numbers above come from fixed contexts; web-app-path numbers from `lawbot-eval-v4 --split val` (attachments chosen with MLX 4-bit reranker scores) will be added here after manual verification on the Mac mini. MLX scores pick a different attachment set than fp16 on some questions, so they will not match exactly.
