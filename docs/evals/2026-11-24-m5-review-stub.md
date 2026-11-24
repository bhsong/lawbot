# M5 PR 1 리뷰 반영 대역 검증 (2026-11-24)

[계획](../plans/consult-trace-clarify.md)의 2·4단계 및 평가 실행기 리뷰 12개를 반영했다. 공유 추론 서비스에 보낸 요청은 없다. 검색 동작, 운영 트리, 운영 설정은 변경하지 않았다.

| 항목 | 대역 검증 |
|---|---|
| 1·2·6·7 | 원문 위치·판정·귀속 근거를 손으로 지정한 표로 대조했다. 미등록 법령 뒤 조사, 선행사 만료와 근거 줄 독립, 인용 전체를 감싼 괄호, 줄바꿈으로 갈린 조문, 병렬 법령·항, 등록 긴 법령명, 공포번호와 해석 불가 부칙을 포함한다. |
| 3 | 판정 후 형식 재요청·초기 writer·재개 writer·진단 writer 송신 차단과 검색 송신 경계를 검증했다. 가짜 writer 프로세스를 SIGKILL하고 저널로 복구했다. 종료 구성을 못 확인한 구간과 미완료 문항은 무효 처리하고 남은 문항만 송신했다. CLI `--resume`에서도 무효 구간을 보존했으며 채택 기준 비교는 거부했다. |
| 4 | 가상 사용자 답을 그대로 반사하는 writer HTTP 400 오류를 주입했다. 콘솔·`failure.txt`·요청 로그의 실패 진단에는 단계·요청 ID·예외 타입·HTTP 상태만 남았고 답 원문은 없었다. |
| 5 | 판정 연결 실패 뒤 정상 표시된 writer 답은 만점 및 검증된 답변 지연 표본으로 남았다. 판정 실패는 별도로 집계하고 3회 연속 실패 직후 다음 문항을 차단했다. |
| 8·9 | 검색되지 않았지만 계산기가 검증한 조문은 경고나 검색 카드 없이 근거로 잡았다. 확인 질문에도 경고를 붙이고 초기 질문과 재개 답을 합산했다. 진단 답은 노출 분모에 넣지 않았다. |
| 10 | 호환 캐시가 있거나, 없거나, 손상되거나, revision이 불일치하는 경우 모두 인덱스 추론 호출은 0건이었다. 법률 캐시만 맞고 하위법령 캐시가 빠진 경우도 추론 없이 중단했다. |
| 11·12 | `stop`의 JSON·스키마 오류만 재요청하고, 비어 있는 `missing`을 거부했다. `length`·`content_filter`·`tool_calls`·종료 사유 없음은 재요청하지 않았다. 최종 `unsupported`·실제 계산 성공은 판정을 생략하고 `lookup`은 판정했다. |

## 검증 명령

- `uv sync --locked`, 캘린더·ERP·수집 설정 서비스 설치, ERP·수집 설정 잠금 확인, `uv run playwright install chromium`, 양쪽 화면 `npm ci` 완료.
- 챗봇·캘린더 `npm run typecheck`, `npm test`, `npm run build` 통과. 화면 단위 시험은 챗봇 55개, 캘린더 36개 통과.
- 첫 기본 전체 실행: 2,841개 통과, 실패 4개. 원래 예외 메시지를 콘솔에 노출하던 기존 시험 계약을 안전 메타데이터 계약으로 갱신했다. 화면 오류 안내와 내부 예외 체인은 유지했다.
- 최종 `uv run pytest -q --show-capture=no --junitxml="$scratch/pytest-report.xml"`: 단위·계약·통합·대역 브라우저 **2,845개 통과**, 실패·skip 없음. 기본 제외 10개는 통과로 세지 않았다.
- `uv run python -m compileall -q services packages tools`, `uv run ruff check --output-format=github`, `uv run ruff format --check`, `uv run pyright`와 독립 서비스별 Pyright, `uv run python -m law_calendar.schema --check` 통과.

실제 모델 E2E, 모델 스모크, 실제 M5 A/A′ 평가: **실행 안 함**. 실측 인용 노출과 A′ 채택 기준은 미판정이다.
