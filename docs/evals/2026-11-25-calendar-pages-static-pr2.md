# 법령 캘린더 정적 사이트 PR 2 검증

[계획](../plans/calendar-pages-static.md) · [운영 절차](../operations.md#법령-캘린더-정적-사이트-게시)

합성 DB·일일 스냅숏, 가짜 Wrangler, httpx MockTransport로 게시를 검증했다. 운영 트리·DB·환경 파일은 열지 않았고, 실제 Pages 업로드·삭제, 실제 모델 E2E, 모델 스모크, 품질 평가는 실행하지 않았다. Wrangler `4.160.0`은 npm 배포일이 11월 10일임을 확인해 정확한 버전으로 고정했다.

| 명령 | 결과 |
| --- | --- |
| `uv run --offline --no-sync pytest -q` | 2,938개 통과, 기본 설정 제외 10개. 단위·계약·통합·대역 E2E 포함 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests` | 254개 통과 |
| `uv run --offline --no-sync pytest -q services/law-calendar/tests/test_calendar_export.py` | 41개 통과 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests/test_calendar_ui.py services/law-calendar/tests/test_calendar_erp_ui.py` | 단독 실행 Chromium 22개 통과. 화면의 `/api/` 요청과 CSP 위반 0건 |
| `npm run typecheck && npm test && npm run build` (캘린더) | 타입·빌드 통과, 테스트 46개 통과 |
| `npm run typecheck && npm test && npm run build` (챗봇) | 타입·빌드 통과, 테스트 55개 통과 |
| `PYRIGHT_PYTHON_IGNORE_WARNINGS=1`에서 `uv run --offline --no-sync pyright` 및 `pyright services/law-calendar/src`, `pyright services/erp-updates/src`, `pyright services/source-hub/src` | 각각 오류·경고 0개 |
| `uv run --offline --no-sync python -m law_calendar.schema --check` 및 `uv run --offline --no-sync python -m compileall -q services packages tools` | 통과 |
| `uv run --offline --no-sync ruff check --output-format=github` 및 `uv run --offline --no-sync ruff format --check` | 통과. 포맷 검사 619개 파일 |
| `uv run --offline --frozen --no-sync --project services/law-calendar law-calendar publish --help` | 설치된 CLI의 하위 명령과 경로·철회 옵션 확인 |

- Red: 미구현 `publish`가 서버 옵션 오류를 반환했다. daily가 게시를 호출하지 않았고, 보류 승인·CLI 철회 뒤에 게시가 예약되지 않았다. 배포 API가 `total_count`만 준 경우 정리가 첫 페이지에서 끝났다.
- Green: 설정 누락 시 로컬 파일만 갱신한다. Wrangler의 작업 위치·옵션·내보내기 잠금, 점 파일 제외와 Functions 입력 금지, 운영 상태 파일 바이트 대조, 운영 ID 불일치 시 무삭제, 여러 페이지의 일반·철회 정리를 확인했다. 게시 실패 출력에는 고정 코드만 남는다.
- daily는 정상·부분 실패·재사용 시 한 번 게시하고 busy는 건너뛴다. 관리자 경로는 커밋 후에만 예약하고, 중복 승인 요청은 추가 게시를 예약하지 않는다. 연속 요청은 합치고 실행 중의 쓰기는 철회 표식을 유지해 한 번 더 실행한다. 별도 프로세스에서 가짜 게시기의 실패가 고정 로그 한 줄로 끝나는 것도 확인했다.
- 첫 전체 실행의 테스트 도우미 이름 충돌은 캘린더 전용 모듈 이름과 명시적 가져오기로 수정했다. 서버 로그가 CLI JSON에 섞이는 문제는 게시 전용 로거를 분리해 수정했다. 전체 검사와 병행한 Chromium 실행은 보류 화면의 데이터 로딩 1건이 실패했다. 단독 Chromium 22개와 기본 전체·캘린더 독립 실행은 통과했다. 그 일시 실패의 원인은 확정하지 못했다.
