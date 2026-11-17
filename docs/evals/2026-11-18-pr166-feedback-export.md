# PR #166 학습 원문 누락 피드백 제외

## 변경

`feedback_export.export()`에서 검수된 기록 중 필수 필드(`question`, `label`, `reason`, `hits`, `model`, `fmt`, `system`, `answer_raw`)가 없거나 null인 기록을 제외한다. 정상적으로 null인 `reason`과 빈 `hits`는 허용한다. 제외는 조문 검증·분할·네 CSV 생성 전에 적용하며, 기존 CLI 요약에 제외 건수와 ID 최대 5개를 출력한다. 응답 출처 이름에 의존하지 않는다.

## 회귀 테스트와 Red

`test_export_skips_records_without_training_fields`에 정상 기록과 과거 기록을 함께 검수한 CSV·JSONL을 구성했다. `system` 누락·`answer_raw=null` 조합 및 각 필수 필드의 누락·null을 총 16개 사례로 검사한다. 네 CSV에 정상 기록만 남고 SFT·KTO의 system·completion 원문과 제외 건수가 보존되는지 확인한다. 정상 기록의 `reason=null`도 유지한다.

구현 전 명령:

```sh
uv run pytest -q services/chatbot/tests/test_feedback.py -k skips_records_without_training_fields -x
```

결과: 과거 기록 조합에서 `KeyError: 'system'`, 1 failed, 29 deselected. 의도한 회귀를 확인한 후 구현했다.

## 최종 검증

| 명령 | 결과 |
| --- | --- |
| `uv run ruff check` | 통과 |
| `uv run ruff format --check` | 통과, 572 files already formatted |
| `PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright` | 0 errors, 0 warnings |
| `uv run pytest -q services/chatbot/tests -k "feedback or export"` | 83 passed, 1288 deselected |

요청 범위의 검사만 실행했다. 전체 기본 테스트·실제 모델 E2E·모델 스모크·품질 평가는 실행 안 함. 실제 모델 호출, .env·운영 트리 접근, 커밋·푸시·브랜치 변경 없음.
