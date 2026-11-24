# 되묻기 1회(A′): 구현·평가 후 취소 (2026-11-24)

검색이 끝난 뒤 writer 전에 Qwen을 한 번 불러, 사용자만 아는 사실이 빠져 결론이 달라지면 한 문장으로 되묻는 기능이다. Lawbot2 M5에서 지휘 에이전트(B)의 유일한 장점이 되묻기였기 때문에, 고정 흐름(A)에 되묻기만 더한 A′를 만들어 비교했다. 판정 기준 6개 중 3개를 통과하지 못해 **2026-11-24 소유자 결정으로 취소**했다. 다시 쓸 때를 위해 설계·코드 위치·결과·재평가 방법을 남긴다.

- 계획: [consult-trace-clarify](consult-trace-clarify.md) 4단계
- 평가: [기준선](../evals/2026-11-24-m5-baseline.md) · [A·A′](../evals/2026-11-24-m5-a-aprime.md)
- 구현: PR #193(`4b6959e`), 운영 v6.5.0에 코드는 있으나 꺼져 있음

## 상태

- 코드는 저장소에 남겨 둔다. `service.ask(..., clarify=True)`로만 켜지고, 웹앱·API·화면은 켜지 않는다. 운영 동작에는 영향이 없다.
- 남기는 이유: 더 나은 로컬 모델이나 판정 입력 축소를 시험할 때 같은 val 세트로 바로 다시 잴 수 있다. Lawbot2의 지휘 에이전트 B도 같은 이유로 코드를 남겼다.
- 지우기로 하면 아래 "코드 위치"를 함께 지우고, 평가 실행기의 `Aprime` 조건과 관련 테스트도 함께 정리한다.

## 설계 (구현된 그대로)

- 판정 호출: 검색·하위법령·정의 연결이 끝난 뒤 writer 전 1회. 비스트리밍, 온도 0, `enable_thinking=False`, 출력 최대 256토큰. user 메시지는 writer와 같은 `[참고 조문]`(조문 전문) + `[질문]`.
- 판정 system: `data/v5/system_clarify.txt`. 사용자만 아는 사실이 빠졌고 그 사실에 따라 결론이 달라질 때만 묻는다. 조건별로 나눠 답할 수 있거나 조문이 부족하거나 범위 밖이면 묻지 않는다.
- 출력: 엄격 JSON `{"ask": bool, "missing": [...], "question": "..."}`. `ask=true`면 `missing`·`question`이 비어 있으면 안 된다. `finish_reason=stop`의 JSON·스키마 오류만 1회 재요청한다. 그 밖의 종료·호출 오류·두 번째 형식 오류는 A와 같은 흐름으로 writer에 진행한다(fail-open).
- 생략: 계산기가 계산하거나 계산 안내로 끝난 질문(최종 라우팅 기준). 일반 조회(`lookup`)는 판정한다.
- 재개: `service.answer_after_clarification`. 재검색과 재판정 없이 같은 조문으로 writer만 실행한다. writer user 메시지는 `[참고 조문]` → `[질문]` → `[확인 질문]` → `[사용자 답]` 순서다. 상담당 되묻기 최대 1회.
- 되묻지 않거나 fail-open이면 writer 입력이 A와 바이트 단위로 같다(평가에서 226/226 확인).
- 요청 로그: 원문 없이 `clarify_status`(asked/not_asked/failed_open/skipped), 실패 사유, 판정 호출·형식 재요청 수, 판정 토큰·시간.
- 확인 질문 속 인용도 결론 인용과 같은 규칙으로 판정·표시한다.

## 코드 위치

| 파일 | 내용 |
| --- | --- |
| `data/v5/system_clarify.txt` | 판정 system 프롬프트 |
| `services/chatbot/src/lawbot/llm.py` | `LLM.clarify`, `_parse_clarification`, `CLARIFY_MAX_TOKENS` |
| `services/chatbot/src/lawbot/service.py` | `ask(clarify=…)`, `answer_after_clarification`, `write_from_context`, 요청 로그 `clarify_*` |
| `services/chatbot/src/lawbot/hits.py` | `Clarification`, `ClarifyAttempt` |
| `tools/lawbot-tools/src/lawbot_tools/eval_m5.py`·`eval_m5_score.py` | `--condition Aprime`, 고정 답 재개, 판정 기준 6개 |
| `data/eval/m5/fact_missing_replies.json` | 사실 부족 15문항의 고정 가상 답(A′ 질문을 보기 전에 작성) |
| `services/chatbot/tests/test_clarify.py`·`test_eval_m5.py`·`test_m5_recovery.py` | 대역 시험 |

## 결과 (262문항, 같은 문항 짝 비교)

| 기준(실행 전 고정) | A′ | 결과 |
|---|---|---|
| 미확인 인용 노출 0건 | 0 | 통과 |
| 사실 부족 확인 질문률 ≥ 50% | 15/15, 빠진 사실을 정확히 물은 것 12 | 통과 |
| 일반·추론 불필요한 되묻기 ≤ 3% | 21/232 (9.1%) | 불통과 |
| 일반 `total`·추론 `cite_ok` 짝 악화 ≤ 개선 | 일반 악화 16·개선 0 (불필요한 되묻기를 실패로 셈) | 불통과 |
| 검증된 답변 p95 ≤ 60초, p50 증가 ≤ 5초 | p95 66.4초, p50 +7.9초 | 불통과 |
| 판정 실패 ≤ 2% | 0 (형식 재요청 0) | 통과 |

- 잘한 점: 필요한 질문에서는 거의 다 물었다(Lawbot2 B는 5/15, 정확 3).
- 실패 원인 1 — 과잉 질문: 질문만으로 답할 수 있게 만든 문항에서 예외 요건(인가·등록·업종, 예 "고용노동부장관 인가를 받으셨나요?")이나 질문에 이미 있는 사실(T09159 "이천만원을 넘어요" → "2천만원 이하인가요?")을 되물었다. 조문의 예외 단서가 보이면 묻는 경향이 있다.
- 실패 원인 2 — 지연: 판정 호출이 조문 전문을 한 번 더 prefill한다. 판정 시간 p50 7.9 / p95 23.0 / 최대 42.2초, 생성 입력 토큰이 약 2배가 됐다. 긴 입력 문항에서 메모리 압력·스왑도 커졌다.
- 엉뚱한 질문 1건(FACT001), 빠진 사실을 짚지 못한 질문 2건(FACT007, FACT014).

## 다시 시도한다면

1. 판정 입력 축소: 조문 전문 대신 법령명·조문·제목과 짧은 발췌만 넣는다. p50 +5초 기준을 맞추려면 필수로 보인다.
2. 판정 프롬프트 조정(계획상 `fact_missing`·`reasoning`만 보고 1회): "조문의 예외 요건만으로는 묻지 않는다", "질문에 이미 있는 사실은 묻지 않는다", "조건별로 나눠 답할 수 있으면 묻지 않는다"를 예시 없이 규칙으로 넣는다.
3. 조정하면 새 사실 부족 15문항(기존 세트를 보고 베끼지 않음)을 써서 최종 확인한다.
4. 재평가: 같은 커밋·구성으로 `uv run lawbot-eval-m5 run --condition Aprime --output <새 폴더>` 후 A 결과와 `compare`. 평가 중 메모리 감시 기준(압력 수준 4, 스왑 2.6GB, 운영 요청 지연, 같은 문항 A 대비 writer 시간 2배)은 [A·A′ 평가](../evals/2026-11-24-m5-a-aprime.md#중단과-재실행)를 따른다. 되묻기 문항은 판정·재개 시간이 더해지므로 느림 판정에서 빼야 한다.
5. 운영에 쓰려면 웹 API·화면(확인 질문 표시, 답 입력, 상담 대기 상태)을 별도 PR로 만든다.
