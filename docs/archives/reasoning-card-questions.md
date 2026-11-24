# 법률 추론 검증셋에 카드 공제 2문항 추가

작성 2026-11-15 KST.

## 목표

법률 추론 검증셋(`data/v5/qa_reasoning_val.csv`)에 카드 공제 기간을 묻는 두 표현(CARD01·CARD02)을 넣는다. 첫 취업 전에 쓴 카드값의 공제 여부를 묻는 문항이고, 질문 표현에 따라 답이 바뀌는지 확인한다([대조 기록](../evals/2026-11-14-card-deduction-paraphrase.md)).

## 문제

운영 DB에는 `reasoning_v5`가 30문항으로 동결돼 있다. 같은 세트 ID로 CSV만 32문항으로 바꾸면 두 가지 문제가 생긴다(PR #148 Codex 리뷰).

- `migrate_csv_to_sqlite qa --set reasoning_v5`가 "같은 QA 세트 ID의 원본 내용이 다릅니다"로 실패한다.
- 관리자 API의 유출 방지 검사(`protection_rows`)는 세트가 있는지만 보기 때문에, 이전 30문항으로 계속 동작한다. 그래서 CARD01·CARD02와 같은 질문이 학습 데이터로 승격되는 것을 막지 못한다.

## 설계

동결된 세트는 고치지 않고 새 세트 ID로 올린다.

- `DATASETS`의 `reasoning_v5`를 `reasoning_v5_2`(32문항)로 바꾼다. CSV 경로는 그대로 쓴다.
- DB에 남은 `reasoning_v5`(30문항)는 지우지 않는다. `DATASETS`에 없는 세트는 `protection_rows`와 `verify`가 이미 건너뛴다. 30문항은 모두 새 세트에도 들어 있으므로 보호 범위는 줄지 않는다.
- 평가 도구(`eval_reasoning`)는 문항 ID 32개를 정해진 순서로 요구한다.
- 피드백 검토 도구가 붙이는 출처 이름도 새 세트 ID로 맞춘다.
- 카드 두 문항의 근거는 조문이 아니라 국세청 안내 PDF 132쪽이다. 그래서 자동 집계의 "기준 조문 제공·인용"은 항상 아님으로 나온다. 두 문항은 기록에서 문항별로 따로 판정한다(기존 README 설명대로).

## 운영 반영 때 할 일 (별도 승인)

관리자 계정이 켜진 운영 웹앱은 시작할 때 필요한 QA 세트가 모두 있는지 확인하고, 빠졌으면 시작을 거부한다. 그래서 웹앱을 재시작하기 전에 운영 DB에 새 세트를 넣어야 한다.

```bash
uv run python -m lawbot_tools.migrate_csv_to_sqlite qa --db <운영 LAWBOT_DB> --set reasoning_v5_2 --dry-run
uv run python -m lawbot_tools.migrate_csv_to_sqlite qa --db <운영 LAWBOT_DB> --set reasoning_v5_2
uv run python -m lawbot_tools.migrate_csv_to_sqlite verify --db <운영 LAWBOT_DB> --report <새 임시 파일>
```

- 추가만 하고 기존 행은 바꾸지 않는다.
- 이관 전에 운영 DB 백업을 만든다.
- 되돌리기: 이전 커밋으로 돌아가면 DB에 남은 `reasoning_v5_2`는 무시되므로 그대로 둔다.

## 완료 기준

- 테스트: 동결된 `reasoning_v5`(30문항)가 있는 DB에 `reasoning_v5_2`를 이관할 수 있다. 이관 결과는 32문항이고 CARD01·CARD02를 포함한다.
- 테스트: `verify`가 이전 세트를 실패로 보지 않는다.
- 기본 테스트 전체, ruff, pyright 통과.
- `docs/operations.md`의 세트 목록과 이관 명령을 갱신한다.
- 실제 모델 평가: 실행 안 함(별도 요청 시).
