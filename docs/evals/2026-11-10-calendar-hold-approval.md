# 관리자 캘린더 보류 승인과 정기 가져오기 검증

2026-11-10 KST · 초기 기준 `cda1b33`, 최종 기준 `91e5c0d` · 합성 법령·국회 이벤트, 임시 SQLite와 루프백 대역 서버로 검증했다. [계획서](../plans/calendar-hold-approval.md)

## Red

- `.venv/bin/pytest -q services/law-calendar/tests/test_calendar_review_api.py`에서 새 검토 API 7건이 기존의 404로 실패했다. 재요청 시험에서는 다른 조문 승인, 같은 조문의 요약 정정, 국회 예고 철회 뒤 이전 요청을 재전송하면 이전 결정이 다시 적용되는 문제를 재현했다.
- `.venv/bin/pytest -q services/chatbot/tests/test_admin_api.py -k 'require_role or calendar_review_post_guards'`에서 POST가 기존의 405를 반환해 로그인·Origin 검증 기대값과 달랐다.
- `.venv/bin/pytest -q services/law-calendar/tests/test_calendar_sync.py -k 'missing_json or model_change or records_failed'`에서 이미 가져온 입력을 다시 열거나 검증하는 경우와 실패 이력 누락으로 9건이 실패했다.
- 웹앱 Vitest의 국회 보류·실패 목록 시험은 기존 화면에 검토 열기 버튼이 없어 실패했다. 종료일만 있는 예고는 승인 후 다른 달로 이동하지 않는 문제도 재현했다.

## 확인한 동작

- 서비스 토큰 미설정·누락·불일치와 비관리자·다른 Origin·허용 목록 밖 프록시 요청을 거절했다. 프록시는 로그인한 관리자 식별자를 사용하며 같은 키로 재전송하면 성공 응답과 감사 기록을 재사용했다.
- 오래된 시행일·국회 스냅숏 거부, 시행일 승인, 국회 승인·반려·철회를 확인했다. 같은 스냅숏·관리자·검토 내용으로 재전송해도 새 검토 이력이 생기거나 후속 결정이 되돌아가지 않았다.
- 법령·국회 의안·입법예고의 기존 등록 JSON이 없어도 정기 sync가 성공했다. 파서 버전과 국회 모델 필드가 바뀌어도 기존 저장 건을 다시 검증하지 않았다. 실패한 이벤트는 소스·ID·예외 종류·메시지가 남고 수정한 다음 실행에서 성공했다. #106 동일 revision 재관측과 #107 범위 밖 제외의 회귀 시험이 통과했다.
- Chromium 390px·1280px에서 실제 웹앱과 캘린더 서버를 띄워 관리자 로그인, 공포일 사전 입력, 근거 링크와 조문 전후 비교, 근거 URL 검증 오류, 승인 후 보류 목록과 날짜별 일정 갱신, 가져오기 실패 조회를 확인했다. 모델 서버에는 대역을 사용했다.

## 최종 검증

| 명령 또는 검증 층 | 결과 |
| --- | --- |
| `.venv/bin/pytest -q` | 1,272개 통과, 13개 선택 제외, 121.81초. 단위·계약·통합·Chromium 대역 E2E 포함 |
| `.venv/bin/pytest -q -m e2e_stub` | 대역 E2E 38개 통과. 최종 기준의 전체 실행에도 포함 |
| 두 프런트엔드의 `npm run typecheck`, `npm test`, `npm run build` | 모두 통과. 웹앱 Vitest 38개, 공개 캘린더 Vitest 19개 |
| `ruff check`, `ruff format --check`, `compileall`, 루트·캘린더 `pyright` | 통과 |
| `python -m law_calendar.schema --check`, 루트·캘린더 `uv lock --check`, `git diff --check` | 통과 |
| `e2e_live` | 실행 안 함 |
| `models` | 실행 안 함 |
| 품질 평가 | 실행 안 함 |
