# Fast 모델 제거 검증

[계획](../archives/remove-fast-model.md)

## Red → Green

- 구현 전 Python 6개, 프런트엔드 2개가 의도대로 실패했다. v4 기본 system, 없어진 모델 설정, 모델 없는 요청과 응답 설정, 화면 모델 선택, 피드백 및 스냅샷 계약을 확인했다. 검수 CSV의 `fmt`가 남는 실패 1개도 별도로 확인했다.
- 전체 첫 실행에서 평가 도구 대역 호출부의 이전 모델 인자로 3개가 실패했다. 호출부를 단일 모델 계약에 맞추고 전체를 재실행했다.
- 승인 계획 커밋 `a6a59d4`와 비교한 와이어 픽스처 차이: SSE `stats` 문구에서 `Lawbot Fast` → `Lawbot Pro` 2곳, JSONL 요청 로그의 `model`에서 `fast` → `pro` 1곳. 나머지 SSE 이벤트·답변·조문 카드 및 로그 필드는 바이트 단위로 동일.

## 검증

| 명령 | 결과 |
| --- | --- |
| `uv sync --locked` | 잠금 파일 기준 동기화 통과 |
| `uv pip install --no-deps -e services/law-calendar -e services/erp-updates -e services/source-hub` | 3개 패키지 설치 통과 |
| `uv run python -m compileall -q services packages tools` | 통과 |
| `uv run ruff check && uv run ruff format --check` | 통과, 571개 파일 포맷 일치 |
| `uv run pyright && uv run pyright services/law-calendar/src && uv run python -m law_calendar.schema --check` | Pyright 2회 오류 0, 캘린더 스키마 일치 |
| `(cd services/chatbot/frontend && npm run typecheck && npm test && npm run build)` | 타입 검사·빌드 통과, Vitest 55개 통과 |
| `(cd services/law-calendar/frontend && npm run build)` | 빌드 통과 |

| 검증 층 | 결과 |
| --- | --- |
| 단위 | 통과. 단위가 포함된 나머지 기본 Python 테스트 2,570개, 챗봇 프런트엔드 55개 |
| 계약 | 통과. `test_api_client.py`, `test_ms_api.py`, 와이어 픽스처 검사 합계 107개 |
| 통합 (`integration`) | 8개 통과 |
| 대역 E2E (`e2e_stub`) | 75개 통과 |
| 실제 모델 E2E | 실행 안 함 |
| 모델 스모크 | 실행 안 함 |
| 품질 평가 | 실행 안 함 |

`uv run pytest -q` 총 2,760개 통과, 기본 마커에서 제외된 항목 10개.

실제 모델·외부 수집 테스트와 평가 명령은 실행하지 않았다. 실제 `.env` 및 운영 트리는 읽거나 바꾸지 않았다.
