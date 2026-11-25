# 법령 캘린더 정적 사이트 PR 1 검증

[계획](../plans/calendar-pages-static.md) · [공개 라이선스 근거](2026-11-25-calendar-public-license-check.md)

합성 DB와 ERP 공개 피드로 정적 내보내기와 브라우저 사용 흐름을 검증했다. 운영 DB·설정, 실제 모델, Pages 배포는 사용하지 않았다.

## 결과

| 검사 | 결과 |
| --- | --- |
| `uv run --offline --no-sync pytest -q` | 2,902개 통과, 기본 설정의 제외 10개. 단위·계약·통합·대역 E2E 포함 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests` | 218개 통과 |
| `uv run --offline --no-sync pytest -q services/law-calendar/tests/test_calendar_export.py` | 20개 통과. 공개 GET과 파일 목록·내용 대조, 월별 여러 페이지 병합, 철회 반영, 유해 문자열·스키마·빌드 제한 거부, 실패 시 기존 사이트 보존 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests/test_calendar_ui.py services/law-calendar/tests/test_calendar_erp_ui.py` | 19개 통과. 달 이동과 빈 달, 날짜 상세, 보류, 승인·재내보내기·철회, ERP 기준일 대체·갱신 지연, 기관 소식, 세무 배지, CSP·캐시 헤더, 라이선스 확인. CSP 위반 콘솔 오류 0건, `/api/` 요청 0건 |
| `npm run typecheck && npm test && npm run build` (캘린더 프런트엔드) | 타입·빌드 통과, 42개 테스트 통과 |
| `npm run typecheck && npm test && npm run build` (챗봇 프런트엔드) | 타입·빌드 통과, 55개 테스트 통과 |
| `PYRIGHT_PYTHON_IGNORE_WARNINGS=1`에서 `uv run --offline --no-sync pyright` 및 캘린더·ERP·Source Hub 개별 타입 검사 | 오류 0개 |
| `uv run --offline --no-sync python -m law_calendar.schema --check` 및 `python -m compileall -q services packages tools` | 통과 |
| `uv run --offline --no-sync ruff check --output-format=github` 및 `ruff format --check` | 통과 |

- Red: CLI 미구현(`export`를 서버 실행으로 해석), 기존 API URL을 요청하는 프런트엔드와 정적 파일이 없는 서버에서 새 테스트가 의도대로 실패했다.
- 첫 기본 전체 실행에서 브라우저용 헬퍼를 공유하던 보류 API 테스트가 정적 서버에서 404를 받았다. 그 테스트만 인프로세스 조회 앱으로 분리해 API 응답 비교와 CLI 시행 검증을 유지했다.
- Pretendard 고정 사본은 upstream v1.3.9 LICENSE 원문과 SHA-256 `d31ddd9f2bed32fd7e302a205cf2380ba0de6529152d239ef99cfb6f261bfc04`가 일치했다. 잠금에 없는 가상 런타임 모듈을 인라인하는 빌드도 라이선스 검사에서 실패했다.
- 명령의 독립 `--no-project --with` 환경은 오프라인 캐시에 Playwright 배포 파일이 없어 구성하지 못했다. 저장소 가상 환경에 같은 버전의 pytest·httpx·Playwright와 Chromium이 설치되어 있어 그 환경으로 같은 브라우저 테스트를 실행했다.
- 독립 서비스 타입 검사에서 처음에 Pyright 래퍼의 PyPI 버전 확인을 차단하지 못했다. `uv --offline`은 이 확인에 적용되지 않는다. 버전 확인을 끈 뒤 전체 타입 검사를 다시 실행했다.
- 실제 모델 E2E·모델 스모크·품질 평가·Pages 확인: 실행 안 함.
