# 평가 기록: v4 웹앱 구성 test (7개 법률 415조문 · 리랭커 · Qwen3.8-27B 4비트)

- 날짜: 2026-10-25 · 맥 미니 32GB · `uv run lawbot-eval-v4` (test 2,075문항, 답변 표본 200)
- 결과 파일: `results/v4/*_pro_concise_rr.csv`, 리랭커 대조 `results/reranker_verify_val_mlx4bit.csv`
- 원본: README "성능"·"Results" 절에서 옮김 (2026-10-31, 내용은 그대로)

## 한국어

**v4 웹앱 구성 (7개 법률 415조문 · 리랭커 · Qwen3.8-27B 4비트)**

2026-10-25 맥 미니 32GB에서 `uv run lawbot-eval-v4`로 측정했습니다(문항별 결과 `results/v4/*_pro_concise_rr.csv`). 구성은 `.env.example` 그대로입니다: 튜닝 Qwen3-Embedding-0.6B 상위 10개 → Qwen3-Reranker-4B(튜닝 없음, MLX 4비트) 상위 3개 → Qwen3.8-27B(튜닝 없음, MLX 4비트, 간결형 답변). 문항은 학습·검증에 쓰지 않은 v4 test 2,075문항(근로기준법 650 + 신규 6개 법률 1,425)입니다.

*검색 (test 2,075문항, 415조문 대상)*

| 단계 | Recall@1 | Recall@3 | Recall@10 | MRR |
| --- | --- | --- | --- | --- |
| 튜닝 임베딩 | 0.765 | 0.913 | 0.976 | 0.845 (@10) |
| + 리랭커 (후보 10개 → 3개) | **0.829** | **0.940** | - | **0.880** (@3) |

근로기준법 650문항은 리랭킹 후 Recall@3 0.923, 신규 법률 1,425문항은 0.947입니다. Colab(L4, fp16) 실험의 같은 test 수치(임베딩 0.765·0.914, 리랭커 0.827·0.941·MRR@3 0.880)와 0.002 이내로 같습니다.

리랭커는 Colab과 같은 질문·임베딩 Top 10으로 따로 대조했습니다(val 1,110문항, `results/reranker_verify_val_mlx4bit.csv`).

| 리랭커 | Recall@1 | Recall@3 | MRR@3 | 질문당 시간 |
| --- | --- | --- | --- | --- |
| Colab (transformers fp16, L4) | 0.836 | 0.950 | 0.888 | 1.01초 |
| 맥 (MLX 4비트) | 0.819 | 0.946 | 0.877 | 1.93초 (p95 3.04) |

1위 조문은 90%가 Colab과 같지만, 4비트 양자화로 점수 크기는 크게 달라집니다(질문별 최대 점수 차 중앙값 0.28, 상위 3개 집합 일치 0.58). 그래도 정답 순위 지표는 Recall@1 0.017, Recall@3 0.004 차이에 그칩니다.

*답변 (test 표본 200문항: 근로기준법 100 + 신규 법률 100, 고정 시드)*

| 지표 | 전체 | 근로기준법 | 신규 6개 법률 |
| --- | --- | --- | --- |
| 검색 top3 적중률 | 0.915 | 0.93 | 0.90 |
| 조문 인용 정확도 (법령명까지) | 0.905 | 0.91 | 0.90 |
| 거짓 거절률 | 0.065 | 0.07 | 0.06 |
| 거짓 거절률 (검색 적중 문항) | 0.011 | 0.022 | 0.000 |
| 키워드 그룹 충족률 | - | 0.853 | - |
| 종합 점수 (검색 적중 문항) | - | 0.828 (0.891) | - |

- 검색이 정답을 놓친 17문항 중 11문항은 거절했고, 검색이 맞았는데 거절한 것은 2문항입니다. 검색이 맞고 거절하지 않은 문항은 모두 정답 조문을 인용했습니다.
- 키워드 채점은 기존 650문항 기준이 있는 근로기준법 문항만 합니다. 신규 법률 문항은 인용·거절만 봅니다.
- 2026.4.7 근로기준법 개정(임금체불 벌칙 제109조 → 제107조, 5년·5천만원)에 맞춰 키워드 2문항을 보정했습니다.
- v1(4B, 4항목 답변, 130조문, 무작위 100문항)의 종합 점수 0.812와는 답변 형식·조문 수·표본이 달라 직접 비교하기 어렵습니다.

*거절 동작*

| 지표 | 값 | 문항 |
| --- | --- | --- |
| 범위 밖 질문 거절률 | **0.96** | 75 |
| 범위 밖 질문 억지 적용률 (거절 않고 조문 인용) | 0.04 | 75 |
| 다른 법률 조문만 제공 시 거절률 | **1.00** | 100 |
| 비슷한 조문만 제공 시 거절률 | 0.69 | 100 |
| 범위 안이 된 7개 법률 질문: 거짓 거절률 / 해당 법률 인용률 | 0.111 / 0.889 | 27 |
| 범위 안 대조군(근로기준법): 거짓 거절률 / 정답 조문 인용률 | 0.10 / 0.80 | 20 |

- 범위 밖 102문항 중 출처가 7개 법률인 27문항은 이제 범위 안이므로 따로 셉니다. 그래서 v1의 범위 밖 거절률(82문항, 0.707)과 같은 문항이 아닙니다. 억지 적용 3건은 산재 신청(→ 근로기준법 제88조), 미성년자 계약 취소(→ 제67조), 직장폐쇄 임금(→ 제46조)입니다.
- 범위 안 27문항의 거짓 거절 3건 중 2건(올해 최저시급 금액, 육아기 근로시간 단축 신청 방법)은 그 내용이 조문이 아니라 고시·시행령에 있어 거절이 타당한 쪽입니다. 나머지 1건(1년 미만 퇴직금)은 검색이 근퇴법 제4조를 찾지 못했습니다.
- 비슷한 조문 중에는 실제로 답이 되는 조문이 섞일 수 있어 1.0이 목표는 아닙니다(v1 4B는 0.20, 맥 4비트 0.24).
- 튜닝하지 않은 27B는 v1 학습 문구("확인할 수 없") 대신 "근거를 찾을 수 없습니다", "답변할 수 없습니다"처럼 거절해, v4 평가는 이런 표현도 거절로 봅니다(`citation.REFUSAL`). 범위 안 답변 200문항에서는 이 확장으로 새로 거절로 잡힌 답변이 없었습니다. 웹앱 간결형 화면도 같은 판정을 써서, 이런 거절에는 근거 줄이나 경고를 붙이지 않습니다.

*속도·메모리 (답변 200문항, 맥 미니 32GB)*

| 지표 | 값 |
| --- | --- |
| 임베딩 검색 | 0.03초 |
| 리랭킹 (후보 10개) | 평균 1.95초, p95 3.1초 |
| 첫 토큰 (생성 시작부터) | 중앙값 2.8초 |
| 질문당 완료 (검색부터 답변 끝까지) | 평균 15.1초, p95 20.9초 |
| 생성 속도 | 9.7 토큰/초 |
| 답변 길이 | 평균 165자 (96토큰) |
| MLX 최대 메모리 (27B + 리랭커) | 20.4GB |

## English

**v4 web app setup (415 articles from 7 laws · reranker · Qwen3.8-27B 4-bit)**

Measured on 2026-10-25 on a 32GB Mac mini with `uv run lawbot-eval-v4` (per-question results in `results/v4/*_pro_concise_rr.csv`), using the `.env.example` setup: fine-tuned Qwen3-Embedding-0.6B top 10 → Qwen3-Reranker-4B (not fine-tuned, MLX 4-bit) top 3 → Qwen3.8-27B (not fine-tuned, MLX 4-bit, concise answers). Questions are the 2,075 v4 test questions (650 Labor Standards Act + 1,425 from the six added laws), never used for training or validation.

| Retrieval (2,075 test) | Recall@1 | Recall@3 | Recall@10 | MRR |
| --- | --- | --- | --- | --- |
| Fine-tuned embedding | 0.765 | 0.913 | 0.976 | 0.845 (@10) |
| + reranker (10 → 3) | **0.829** | **0.940** | - | **0.880** (@3) |

These match the Colab (L4, fp16) run on the same test set within 0.002. On the same val questions and embedding top 10 (1,110 questions), the MLX 4-bit reranker gives Recall@1 0.819 / Recall@3 0.946 vs Colab 0.836 / 0.950; the top-1 article agrees 90% of the time, but quantization changes the raw scores a lot (median max score difference 0.28). Reranking takes 1.93 s per question (p95 3.04 s) vs 1.01 s on Colab L4.

| Answers (200 sampled: 100 + 100) | All | Labor Standards Act | Six added laws |
| --- | --- | --- | --- |
| Gold article in retrieved top 3 | 0.915 | 0.93 | 0.90 |
| Citation accuracy (law name included) | 0.905 | 0.91 | 0.90 |
| False refusal rate | 0.065 | 0.07 | 0.06 |
| False refusal rate (retrieval hits) | 0.011 | 0.022 | 0.000 |
| Keyword group coverage | - | 0.853 | - |
| Overall score (retrieval hits) | - | 0.828 (0.891) | - |

| Refusal behavior | Value | Questions |
| --- | --- | --- |
| Out-of-scope refusal rate | **0.96** | 75 |
| Out-of-scope forced answers (cites an article instead of refusing) | 0.04 | 75 |
| Refusal with only other-law articles | **1.00** | 100 |
| Refusal with only similar articles | 0.69 | 100 |
| Former out-of-scope questions now in scope: false refusal / cites that law | 0.111 / 0.889 | 27 |
| In-scope controls: false refusal / gold citation | 0.10 / 0.80 | 20 |

The 27 questions whose source is one of the seven laws are now in scope, so the out-of-scope set differs from v1 (82 questions, 0.707). Two of the three false refusals there (this year's minimum wage amount, how to apply for reduced hours during childcare) concern details set by notice or decree rather than the statute text. The untuned 27B refuses in its own words ("근거를 찾을 수 없습니다", "답변할 수 없습니다"), so the v4 evaluation also counts those phrases (`citation.REFUSAL`); this added no refusals among the 200 in-scope answers. The concise web view uses the same check, so such refusals show no citation line or warning. Keyword scoring uses the existing 650-question keyword set, with two rows updated for the 2026-04-07 amendment. Scores are not directly comparable with v1 (4B, four-part answers, 130 articles, different sample).

Speed and memory (200 answers): embedding search 0.03 s, reranking 1.95 s (p95 3.1 s), first token 2.8 s (median, from generation start), 15.1 s per question end to end (p95 20.9 s), 9.7 tokens/s, 165 characters (96 tokens) per answer, MLX peak memory 20.4GB (27B + reranker).
