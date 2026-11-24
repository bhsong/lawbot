# 법률 추론 검증셋 카드 2문항 추가 (세트 `reasoning_v5_2`)

2026-11-15 KST. [계획](../archives/reasoning-card-questions.md)

- 검증셋: 법률 추론 30문항(ERR001–ERR030)에 카드 공제 기간 두 표현(CARD01·CARD02)을 더해 32문항이다. 세트 ID는 `reasoning_v5_2`이다.
- 이관: 동결된 `reasoning_v5`(30문항)가 있는 DB에도 `reasoning_v5_2`를 이관할 수 있다. 이관 결과는 32문항이고 CARD01·CARD02를 포함한다. `verify`는 이전 세트를 건너뛴다(신규 테스트).
- CSV 줄바꿈을 저장소 기준(LF)으로 맞췄다.

| 층 | 결과 |
| --- | --- |
| 단위·계약·통합·대역 E2E (`uv run pytest`) | 1,999건 통과 |
| ruff check · format --check, pyright | 통과(pyright 오류 0) |
| 실제 모델 E2E·모델 스모크·품질 평가 | 실행 안 함 |
| 운영 DB 이관 | 실행 안 함(운영 반영 때 별도 승인) |
