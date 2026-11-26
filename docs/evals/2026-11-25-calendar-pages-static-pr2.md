# 법령 캘린더 정적 사이트 PR 2 검증

[계획](../archives/calendar-pages-static.md) · [운영 절차](../operations.md#법령-캘린더-정적-사이트-게시)

합성 DB·일일 스냅숏, 가짜 Wrangler, httpx MockTransport로 게시를 검증했다. 운영 트리·DB·환경 파일은 열지 않았고, 실제 Pages 업로드·삭제, 실제 모델 E2E, 모델 스모크, 품질 평가는 실행하지 않았다. Wrangler `4.160.0`은 npm 배포일이 11월 10일임을 확인해 정확한 버전으로 고정했다.

이전 실행 기록은 당시 정책을 설명한다. 현재 정책과 마지막 검증은 [PR #200 CI·Codex 리뷰 반영](#pr-200-ci와-codex-리뷰-반영)을 따른다.

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

## Astra 보안 리뷰 반영

- Red: 다른 DB를 인자로 넘긴 회귀 테스트에서 고정 입력 불일치 대신 입력 누락이 보고됐다. 수정 후 DB·피드·빌드·사이트 인자는 설정과 다르면 HTTP 호출 전에 거부된다. 합성 DB를 검토하는 자식은 게시 비활성 환경으로 실행한다.
- 대역은 같은 프로젝트 이름이라도 별도 ID 또는 운영 브랜치가 다르면 Wrangler를 시작하지 않는지, 이번 빌드 표식이 없으면 삭제하지 않는지 확인한다. 페이지·총계·행의 모순은 첫 삭제 전에 중단된다. 삭제 사이의 외부 롤백과 성공 응답 뒤 남아 있는 이력도 실패 처리한다.
- 철회 실패 후 다음 일반 게시가 이력을 모두 정리하고, 과거 빌드가 새 철회 요청을 지우지 않는지 확인한다. 요청 잠금이 바빠도 철회 표식은 남는다. 자격 증명 오류는 작업자 및 daily에 고정 코드로 전달된다.
- 게시기 전체 기한과 상위 종료를 각각 가짜 Wrangler의 자손 프로세스로 확인했다. 최초 기한 대역에서 macOS의 종료 그룹에 `SIGKILL`을 다시 보내면 발생할 수 있는 권한 오류를 확인해, 한 번만 보내고 종료 상태를 회수하도록 고쳤다.
- 고정 Wrangler `4.160.0` 소스에서 `WRANGLER_CACHE_DIR`과 `WRANGLER_WRITE_LOGS=false`의 적용 분기를 확인했다. 전용 파일과 상태 디렉터리는 `git check-ignore`로 확인했다.
- 실제 Pages API·Wrangler·모델 호출, 운영 경로나 실제 환경 파일 접근은 하지 않았다. 동일 UID의 파일 접근 및 외부 배포와 API 삭제 사이의 경쟁 한계는 운영 절차에 기록했다.

| 최종 명령 | 결과 |
| --- | --- |
| `uv run --offline --no-sync pytest -q` | 2,968개 통과, 기본 설정 제외 10개. 단위·계약·통합·대역 E2E 포함 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests` | 282개 통과 |
| `uv run --offline --no-sync pytest -q services/law-calendar/tests/test_calendar_export.py` | 41개 통과 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests/test_calendar_ui.py services/law-calendar/tests/test_calendar_erp_ui.py` | 독립 Chromium 22개 통과. 정적 화면의 `/api/` 요청 및 CSP 위반 0건 |
| `npm run typecheck && npm test && npm run build` (캘린더) | 타입·빌드 통과, 테스트 46개 통과 |
| `npm run typecheck && npm test && npm run build` (챗봇) | 타입·빌드 통과, 테스트 55개 통과. Vite 설정 로더의 향후 기본값 변경 안내 있음 |
| `uv run --offline --no-sync pyright` 및 `pyright services/law-calendar/src`, `pyright services/erp-updates/src`, `pyright services/source-hub/src` | 각각 오류·경고 0개 |
| `uv run --offline --no-sync python -m law_calendar.schema --check` 및 `uv run --offline --no-sync python -m compileall -q services packages tools` | 통과 |
| `uv run --offline --no-sync ruff check --output-format=github` 및 `uv run --offline --no-sync ruff format --check` | 통과. 포맷 검사 623개 파일 |
| `uv run --offline --frozen --no-sync --project services/law-calendar law-calendar publish --help` | 네 경로 옵션이 설정 일치 검사이며 철회 옵션도 있음을 확인 |

실제 모델 E2E·모델 스모크·품질 평가·실제 Pages 게시: 실행 안 함.

## PR 2 추가 정리

- Red: 수집 자식이 기존 환경을 잃었고, API CLI는 `--frontend`를 등록하고 있었으며, 프로젝트 ID 설정이 없으면 게시 대역이 `invalid_pages_config`로 중단됐다.
- 회귀 테스트는 수집 자식의 기존 환경 복원과 자격 증명 키 두 개 제외, 게시 자식의 허용 목록, API 루트 404, 기존 정적 제공 옵션 거절을 확인한다. 게시 대역은 프로젝트 이름·운영 브랜치 불일치 시 업로드 전 중단과 ID 없는 정상 게시를 확인한다.
- 첫 전체 실행은 실제 환경 파일 차단 규칙에 설정 예제까지 포함돼 실패했다. 커밋된 예제만 읽기 예외로 열었다. 이후 전체 실행과 단독 Chromium에서 서로 다른 정적 JSON 로딩 실패가 한 건씩 발생했다. 해당 파일은 정상 생성됐지만 일부 요청이 서버 로그에 남지 않았다. 정적 테스트 서버의 기본 연결 대기열이 Chromium의 동시 연결 수보다 작아 시스템 최대값으로 늘렸다. 배포 코드와 브라우저 검증 조건은 바꾸지 않았다.

| 최종 명령 | 결과 |
| --- | --- |
| `uv run --offline --no-sync pytest -q` | 2,968개 통과, 기본 설정 제외 10개. 단위·계약·통합·대역 E2E 포함 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests` | 282개 통과 |
| `uv run --offline --no-sync pytest -q services/law-calendar/tests/test_calendar_export.py` | 41개 통과 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests/test_calendar_ui.py services/law-calendar/tests/test_calendar_erp_ui.py` | 독립 Chromium 22개 통과. 정적 화면의 `/api/` 요청과 CSP 위반 0건 |
| `npm run typecheck && npm test && npm run build` (캘린더) | 타입·빌드 통과, 테스트 46개 통과 |
| `npm run typecheck && npm test && npm run build` (챗봇) | 타입·빌드 통과, 테스트 55개 통과. Vite 설정 로더의 향후 기본값 변경 안내 있음 |
| `uv run --offline --no-sync pyright` 및 `pyright services/law-calendar/src`, `pyright services/erp-updates/src`, `pyright services/source-hub/src` | 각각 오류·경고 0개 |
| `uv run --offline --no-sync python -m law_calendar.schema --check` 및 `uv run --offline --no-sync python -m compileall -q services packages tools` | 통과 |
| `uv run --offline --no-sync ruff check --output-format=github` 및 `uv run --offline --no-sync ruff format --check` | 통과. 포맷 검사 623개 파일 |
| `uv run --offline --frozen --no-sync --project services/law-calendar law-calendar publish --help` | 경로 일치 검사 옵션과 철회 옵션 확인 |

운영 파일·데이터와 실제 환경 파일 접근 없음. 실제 Cloudflare·Wrangler 호출, 실제 모델 E2E·모델 스모크·품질 평가: 실행 안 함.

## PR #200 CI와 Codex 리뷰 반영

- CI 실행 `45534834079`: macOS 러너에서 18개 실패, 2,950개 통과, 10개 제외. 실패는 게시 이력 정리 2건, 대상 사전 검사 2건, 무삭제 확인 3건, 배포 목록 검증 7건, 외부 롤백 중단 1건, 정리 실패 후 재실행 1건, 기한·자손 프로세스 종료 2건이었다.
- 게시 대역은 신뢰 경로의 임시 가짜 Node와 가짜 Wrangler를 사용한다. 운영의 파일·조상 디렉터리 소유권·쓰기 권한 검사는 그대로다.
- Red: daily의 실제 `uv` 실행은 `Failed to spawn: publish`로 끝났다. 기본 게시와 정리 재실행은 이전 운영 배포 하나를 남겨 실패했다. 관리자 워커의 기존 명령은 도움말까지 도달했다.
- daily는 `law-calendar publish`를 서비스 전용 프로젝트에서 실행한다. 게시 자식만 환경 허용 목록을 적용한다. CI는 테스트 환경과 별개로 독립 CLI 실행 환경을 설치한다.
- 성공한 게시에는 운영 배포 하나만 남는다. 운영 빌드나 배포 ID 확인에 실패하면 아무것도 지우지 않는다. 정리 자체가 실패하면 게시 실패로 보고하고 다음 게시에서 잔여 이력을 정리한다. 철회 여부에 따른 분기와 상태를 제거했다. 내용 복구는 [캘린더 DB에서 다시 게시](../operations.md#법령-캘린더-정적-사이트-게시)한다.
- 첫 전체 실행에서 스냅숏 재사용 테스트의 대역이 예전 인자열을 기대해 1건 실패했다. 새 명령의 실행 파일과 기존 DB·스냅숏 인자를 함께 요구하도록 대역을 수정했다.

| 명령 | 결과 |
| --- | --- |
| `uv run --offline --no-sync --no-env-file pytest -q` | 2,969개 통과, 기본 설정 제외 10개. 단위·계약·통합·대역 E2E 포함. daily와 관리자 워커 양쪽에서 실제 게시 도움말까지 도달 |
| `uv run --offline --no-sync --no-env-file pytest -c pyproject.toml -q services/law-calendar/tests` | 282개 통과 |
| `uv run --offline --no-sync --no-env-file pytest -q services/law-calendar/tests/test_calendar_export.py` | 41개 통과 |
| `uv run --offline --no-sync --no-env-file pytest -c pyproject.toml -q services/law-calendar/tests/test_calendar_ui.py services/law-calendar/tests/test_calendar_erp_ui.py` | 독립 Chromium 22개 통과. 정적 화면의 `/api/` 요청과 CSP 위반 0건 |
| `env PATH=/usr/bin:/bin <uv 절대 경로> run --offline --no-sync --no-env-file python -m pytest -q services/law-calendar/tests/test_calendar_publish.py` | `node`를 찾을 수 없는 PATH에서 30개 통과 |
| `uv run --offline --frozen --no-sync --no-env-file --project services/law-calendar law-calendar publish --help` | 종료 코드 0. 경로 일치 검사 옵션 네 개만 표시 |
| `uv sync --offline --locked --project services/law-calendar`, 같은 환경에서 `sync --check`와 `publish --help` | 빈 임시 가상환경에서 설치 및 검사 통과. 기존 로컬 독립 환경은 추가 패키지 등 잠금 파일과 차이가 있어 변경하지 않았다 |
| `uv run --offline --no-sync --no-env-file ruff check --output-format=github` 및 `ruff format --check` | 통과. 포맷 검사 623개 파일 |
| `uv run --offline --no-sync --no-env-file pyright` 및 `pyright services/{law-calendar,erp-updates,source-hub}/src` 각 실행 | 네 범위 모두 오류·경고 0개 |
| `uv run --offline --no-sync --no-env-file python -m law_calendar.schema --check` 및 `python -m compileall -q services packages tools` | 통과 |
| `npm run typecheck && npm test && npm run build` (캘린더) | 타입·빌드 통과, 테스트 46개 통과 |
| `npm run typecheck && npm test && npm run build` (챗봇) | 타입·빌드 통과, 테스트 55개 통과. Vite 설정 로더의 향후 기본값 변경 안내 있음 |

운영 트리·데이터 및 실제 환경 파일 접근 없음. 실제 Cloudflare·Wrangler 호출, 실제 모델 E2E·모델 스모크·품질 평가: 실행 안 함.
