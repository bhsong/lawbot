# 법령 캘린더 정적 사이트 PR 1 리뷰 반영 검증

[계획](../archives/calendar-pages-static.md) · [첫 구현 검증](2026-11-25-calendar-pages-static-pr1.md) · [공개 라이선스 근거](2026-11-25-calendar-public-license-check.md)

운영 사본에서 보고된 조문 형태를 합성 조문에 옮겨 확인했다. 운영 트리·운영 데이터 사본·Pages를 열지 않았고, 실제 모델을 호출하지 않았다.

## 결과

| 명령 | 결과 |
| --- | --- |
| `uv run --offline --no-sync pytest -q` | 2,926개 통과, 기본 설정 제외 10개. 단위·계약·통합·대역 E2E 포함 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests` | 242개 통과 |
| `uv run --offline --no-sync pytest -q services/law-calendar/tests/test_calendar_export.py` | 41개 통과 |
| `uv run --offline --no-sync pytest -c pyproject.toml -q services/law-calendar/tests/test_calendar_ui.py services/law-calendar/tests/test_calendar_erp_ui.py` | Chromium 22개 통과. 화면의 `/api/` 요청과 CSP 위반 0건 |
| `npm run typecheck && npm test && npm run build` (캘린더) | 타입·빌드 통과, 테스트 46개 통과 |
| `npm run typecheck && npm test && npm run build` (챗봇) | 타입·빌드 통과, 테스트 55개 통과 |
| `PYRIGHT_PYTHON_IGNORE_WARNINGS=1`에서 `uv run --offline --no-sync pyright` 및 `pyright services/law-calendar/src`, `pyright services/erp-updates/src`, `pyright services/source-hub/src` | 각각 오류 0개 |
| `uv run --offline --no-sync python -m law_calendar.schema --check` 및 `uv run --offline --no-sync python -m compileall -q services packages tools` | 통과 |
| `uv run --offline --no-sync ruff check --output-format=github` 및 `uv run --offline --no-sync ruff format --check` | 통과. 포맷 검사 612개 파일 |

- Red: 원문을 포함한 내보내기 실패, 기존 표식 없는 폴더 덮어쓰기, 고정 지연 값, 비 JSON 응답의 ERP 대체 실패, 대역 서버의 기본 오류 페이지와 누락된 글꼴 고지를 확인했다.
- Green: HTTP 첨부 URL·`</img>`·상대 참조·단위 표기가 상세 JSON의 원문과 비교 줄에 보존된다. 링크 필드의 허용 목록과 로컬 경로·이메일·비공개 IP 차단은 유지된다. 검증 실패 시 값이나 예외 전문 없이 논리 파일·JSON 경로·검사 코드가 나온다.
- 출력 표식과 빌드 허용 목록, 잠금 대기 중 DB 스냅숏 미생성, 설치 단계의 잠금 유지, 실패 시 기존 사이트 보존을 검증했다. 비공개 전용 월은 색인·파일에 없고 실제 보류 조문은 공개 메타데이터만 남았다. 일일 스냅숏에서 가져오는 `lawgo` 소식은 현재 공개 조문이 없는 이벤트를 제외한다.
- Chromium에서 없는 데이터 JSON과 예전 경로가 404 페이지를 받는 것을 확인했다. 오늘 ERP 기준일 파일이 404이면 직전 파일로 대체하고 갱신 지연을 표시한다. 브라우저 시각이 만료를 넘으면 기관 게시판이 수집 지연으로 표시된다. 배포 라이선스에 npm Pretendard 라이선스 바이트와 기반 글꼴 세 고지를 확인했다.
- 실제 모델 E2E·모델 스모크·품질 평가·Pages 확인: 실행 안 함.

## 운영 데이터 사본 시험 (오케스트레이터, 읽기 전용)

운영 캘린더 DB와 일일 스냅숏을 읽기 전용으로 열어, 출력은 저장소 밖 임시 폴더에 썼다. 운영 데이터는 바꾸지 않았다.

| 시점 | 결과 |
| --- | --- |
| 첫 구현(`da676b0`) | 실패(종료 코드 1, 원인 미표시). 조문 원문의 HTTP 첨부 URL 10건과 `</img>` 6건이 검사에 걸림 |
| 리뷰 반영(`b429fdc`) | 성공(종료 코드 0). 파일 236개·4.3MB, 시행 조문 70건·상세 70건(운영 공개 뷰와 같음), 기관 소식 국가법령정보센터 10·고용노동부 30·재정경제부 40·국세청 54건, ERP 기준일 4개 |
