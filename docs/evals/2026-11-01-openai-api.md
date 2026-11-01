# 평가 기록: OpenAI 호환 모델 서버 전환 검증

- 날짜: 2026-11-01
- 장비: 맥 미니 32GB
- 코드: `feat/openai-api` (PR #34, 커밋 b319cd7)
- 대상: 브랜치의 모델 서버(`lawbot-model-server`)를 임시 포트로 실행. 운영 서비스는 내려 둠
  - chat `qwen3.8-27b-4bit`(`models/v4/qwen_27b_mlx_4bit`), embedding `lawbot-emb-v4`, rerank `qwen3-reranker-4b`, 리랭커 후보 10, 하위법령 첨부 켜짐
- 계획: [`docs/archives/monorepo-openai-api.md`](../archives/monorepo-openai-api.md) §3.5
- 기준: `results/v4/*_pro_concise_rr_dec_val.csv` (로컬 모드, 2026-10-31, [기록](2026-10-31-decree-attach.md))

## 요약
챗봇이 OpenAI 호환 API(`/v1/embeddings`·`/v1/rerank`·`/v1/chat/completions`)로 모델 서버를 부르는 새 구조에서, val 답변 600개가 기준과 글자 단위로 모두 같았어요(answers 200 · refusal 200 · 하위법령 dqanswers 200). 검색 순위·점수 1,110행과 하위법령 첨부 444행도 모두 같았어요. 지연 차이는 문항당 0.3초 이내였고, MLX 최대 메모리는 같았어요(20.53GB).

이 결과로 10-31 val 정확도 수치를 새 구조의 기준선으로 계속 써요.

## 결과

| # | 항목 | 명령 | 기대 | 결과 |
| --- | --- | --- | --- | --- |
| 1 | 모델 폴더 점검 | `uv run python -m lawbot_model_server.check` | FAIL 없음 | **OK**. 모두 OK |
| 2 | 실제 모델 스모크 | `uv run pytest -m models services/model-server` | 4 passed | **OK**. 4 passed (40.8초) |
| 3 | 하위법령 스모크 3문항 | `smoke_decree --out …_api.json` → `--compare results/v4/smoke_decree_remote.json` | 조문·답변 동일 | **OK**. 조문·답변 동일: True. MLX peak 19.96GB |
| 4 | val 전체 재평가 | `lawbot-eval-v4 --split val --tag pro_concise_rr_dec_val_api` | 검색 순위·첨부·답변이 기준과 글자 단위로 같음 | **OK**. 아래 표 |
| 5 | 지연·메모리 | `lawbot-eval-v4 compare --base pro_concise_rr_dec_val --new pro_concise_rr_dec_val_api` | HTTP·SSE 경로만큼의 차이 | **OK**. 아래 표 |

### 동등성 (4번)

| 파일 | 비교 | 결과 |
| --- | --- | --- |
| answers | `diff_answers` | 같음 200/200 |
| refusal | `diff_answers` | 같음 200/200 |
| dqanswers (하위법령 질문) | `diff_answers` | 같음 200/200 |
| retrieval | 시간 열(`*_sec`) 외 모든 열 | 1,110행 동일 (순위·점수·첨부 포함) |
| dqretrieval | 시간 열 외 모든 열 | 444행 동일 |

거절 판정 열은 3건 달라요(answers Q0990·Q2343, dqanswers D2060: 기준 거절 → 새 결과 답변). 원인은 모델 출력이 아니라 채점 규칙이에요.
- 기준 파일을 만든 뒤 main에 b8e325a('다만…'으로 시작하는 단서 문장은 거절로 보지 않음)가 들어갔어요.
- 3건 모두 결론을 말한 뒤 "다만, … 판단할/확인할 수 없습니다"를 덧붙인 답변이에요.
- 기준 답변을 현재 `citation.refused`로 다시 채점하면 새 결과의 거절·인용 열과 정확히 같아요.
- 그래서 `fail` 파일의 `false_refusal` 3건이 빠졌어요.

### 지연·메모리 (5번)

| 항목 | 기준 (로컬) | API | 차이 |
| --- | --- | --- | --- |
| 법률 리랭킹 평균 / p95 | 1.910 / 2.966초 | 1.914 / 2.973초 | +0.004 / +0.007초 |
| 하위법령 리랭킹 평균 (하위법령 단계) | 2.221초 | 2.225초 | +0.004초 |
| 답변 완료 평균 | 19.64초 | 19.75초 | +0.11초 |
| 답변 첫 토큰 중앙값 | 3.89초 | 3.96초 | +0.07초 |
| 하위법령 답변 완료 평균 | 21.60초 | 21.63초 | +0.03초 |
| 하위법령 첫 토큰 중앙값 | 5.09초 | 5.09초 | 0 |
| 생성 속도 중앙값 | 9.73 tok/s | 9.70 tok/s | −0.03 |
| MLX peak 최대 | 20.53GB | 20.53GB | 0 |
| 전체 평가 시간 | 282.7분 | 283.6분 | +0.9분 |

## 메모
- 결과 파일(`results/v4/*_pro_concise_rr_dec_val_api.*`, `smoke_decree_api.json`)은 커밋하지 않았어요. 답변·검색 열이 기준 파일과 같아서 새 정보가 없고, 시간 수치는 이 문서에 남겼어요. 다시 만들려면 위 명령을 실행하면 돼요.
- 동시 추론은 아직 없어요(락 하나로 한 번에 하나씩). PR 3에서 배치 생성으로 바꾸고 다시 측정해요.
