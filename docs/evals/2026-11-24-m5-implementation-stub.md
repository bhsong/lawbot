# M5 A/A′ 구현 대역 검증 (2026-11-24)

[계획](../plans/consult-trace-clarify.md) 2·4단계 및 평가 실행기 구현 검증. [실측 기준선](2026-11-24-m5-baseline.md)과 달리 공유 추론 서버에는 요청하지 않았다. 운영 트리와 운영 설정은 변경하지 않았다.

## 대역에서 확인한 동작

- 생성 중에는 기존 초안을 표시하고, 완료 뒤 결론·근거의 미확인 인용 바로 뒤에 경고를 붙인다. 확인된 조문만 근거 줄과 인용 카드에 남긴다. 확인된 인용이 하나도 없으면 답변 대신 안내한다.
- A′에서 묻지 않거나 판정에 실패하면 A와 writer 송신 메시지 바이트가 같다. 되물으면 원 질문과 같은 조문을 유지해 확인 질문·가상 답을 붙여 재개하며, 검색과 판정은 반복하지 않는다. 형식 오류만 한 번 재요청하고, 계산·안내 경로는 되묻기를 건너뛴다. 요청 로그와 콘솔에 판정 원문을 남기지 않는다.
- M5 실행기는 고정 262문항 순서, preflight 실패 시 무질의, 요청 오류 기록 후 중단, SIGINT 시 현재 문항까지만 기록, 구성 불일치 시 재개 거부, 같은 구성에서 남은 문항만 실행, 진단 답변의 주 채점 제외를 검증했다. 오프라인 `score`·`compare` 경로에서 분모·짝 비교·판정 기준 6개를 가짜 앱으로 확인했다.

## 명령과 결과

- 첫 기본 전체 실행: 2,803개 통과, 실패 1개. 유효·미확인 인용이 섞인 답을 차단하던 기존 `test_webapp.py` 계약을 새 정책에 맞춰 갱신했다. 확인된 인용이 없는 경우의 차단 계약은 유지했다.
- 최종 `uv run pytest -q --show-capture=no --junitxml="$scratch/pytest-report.xml"`: **2,805개 통과**, 실패·skip 없음. 단위·계약·통합·대역 브라우저를 포함하며, 기본 설정에서 제외한 실제 모델·외부 네트워크 등 10개는 통과로 세지 않았다.
- `uv sync --locked`, 별도 3개 서비스의 CI 설치·잠금 검사, `uv run playwright install chromium`, 챗봇·캘린더 화면 `npm ci` 통과.
- `uv run python -m compileall -q services packages tools`, `uv run ruff check --output-format=github`, `uv run ruff format --check`, `uv run pyright`와 별도 3개 서비스의 Pyright, `uv run python -m law_calendar.schema --check` 통과.
- 챗봇·캘린더 화면 `npm run typecheck`, `npm test`, `npm run build` 통과. Vitest 챗봇 55개, 캘린더 36개 통과.
- `uv run lawbot-eval-m5 --help`에서 `run`, `score`, `compare` 진입점을 확인했다. 실제 `run`은 호출하지 않았다.

실제 모델 E2E, 모델 스모크, A/A′ 품질 평가: **실행 안 함**. 실제 미확인 인용 노출 건수, 판단 품질, 지연 시간과 A′ 채택 기준은 미측정이다.
