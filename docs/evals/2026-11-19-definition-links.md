# 정의 조문 연결 구현 검증

[계획](../plans/definition-links.md) · [근거 평가](2026-11-19-reasoning-format-definitions.md#정의-조문-연결)

## Red → Green

- 구현 전 공용 파서·발동 규칙과 CSV/DB 응답 경로가 실패했다. 브라우저의 대역 답변은 제공되지 않은 정의 조문을 인용해 거부됐다.
- 구현 후 실제 `data/v5`에서 근로자퇴직급여 보장법 제8조 뒤에 근로기준법 제2조 제1항 제6호와 관련 ②항만 붙었다. CSV/DB 모두에서 프롬프트 순서와 중복 제외, 하위법령의 1위 기준 유지, 카드 미표시, 인용 인정, 요청 로그·피드백을 확인했다. 대역 브라우저에서도 법률 카드 3개와 간결형 `근거: 근로기준법 제2조(정의)`를 확인했다.
- `chat_wire_contract.json` 변경은 JSONL에 미발동 건의 `n_definitions: 0` 추가뿐이다. SSE는 바이트 단위로 동일하다.

## 시작 시 연결 표

`data/v5` 비삭제 법률 1,534개에서 정의 항목 144개, 전체 법률 조문의 5%를 초과한 고유 정의 용어 16개, 연결이 있는 법률 조문 3개(연결 3개)였다. 법률 전용 CSV와 하위법령 포함 CSV에서 표의 크기는 같았다.

네트워크 없는 대역 인코더를 사용해 `Retriever` 초기화 중 조문 행을 넘겨 표를 만드는 추가 구간만 CSV별 12회 측정했다. 중앙값은 법률 전용 CSV 53.3ms, 하위법령 포함 CSV 53.5ms다. CSV 읽기·임베딩·HTTP 대기 시간은 포함하지 않는다.

## 검증

| 명령 | 결과 |
| --- | --- |
| `uv sync --locked` | 통과 |
| `uv pip install --no-deps -e services/law-calendar -e services/erp-updates -e services/source-hub` | 3개 설치 통과 |
| `uv run python -m compileall -q services packages tools` | 통과 |
| `uv run ruff check && uv run ruff format --check` | 통과, 575개 포맷 일치 |
| `uv run pyright && uv run pyright services/law-calendar/src && uv run python -m law_calendar.schema --check` | Pyright 오류 0, 스키마 일치 |
| `(cd services/chatbot/frontend && npm run typecheck && npm test && npm run build)` | 타입 검사·빌드 통과, Vitest 55개 통과 |
| `(cd services/law-calendar/frontend && npm run build)` | 통과 |
| `uv run pytest -q` | 2,774개 통과, 기본 마커에서 10개 제외 |

| 검증 층 | 결과 |
| --- | --- |
| 단위 및 기타 기본 Python 테스트 | 2,583개 통과 |
| 계약 (`test_api_client.py`, `test_ms_api.py`, 대화 와이어 계약) | 107개 통과 |
| 통합 (`integration`) | 8개 통과 |
| 대역 E2E (`e2e_stub`) | 76개 통과 |
| 실제 모델 E2E | 실행 안 함 — 승인 후 Claude가 계획서 검증 단계로 실행 |
| 모델 스모크 | 실행 안 함 — 승인 후 Claude가 계획서 검증 단계로 실행 |
| 품질 평가 | 실행 안 함 — 승인 후 Claude가 계획서 검증 단계로 실행 |

남은 검증: 일반 val 발동 빈도, 추론 val 32문항 생성 및 기준선 대비 하락 확인, 일반 val 표본 생성과 발동 문항 완료 지연 측정.

## PR #185 리뷰 반영 검증

- Red: 관리자 상세에서 정의 발췌가 `changed`에 들어갔고, CSV 내보내기에서 실제 변경 1건이 2건으로 집계됐다.
- Green: 정의 발췌는 두 비교에서 제외됐다. 관리자 응답의 원본 `hits`와 CSV의 학습 프롬프트에는 남고, 실제 변경 1건은 계속 집계된다.
- `uv run ruff check && uv run ruff format --check`: 통과(577개 포맷 일치). `uv run pyright`: 오류 0. `(cd services/chatbot/frontend && npm run build)`: 통과.
- `LAWBOT_ENV_FILE= uv run pytest -q`: 단위·계약·통합·대역 E2E를 포함해 2,776개 통과, 기본 마커로 10개 제외. 실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않았다.
