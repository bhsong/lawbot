# DB 코퍼스 자동 갱신 검증 (2026-11-08)

개발 트리에서 `tmp_path`의 DB, 가짜 Legalize KR Git 저장소, 가짜 모델 서버와 재시작 함수로 검증했다. [PR 12 계획](../plans/sqlite-admin-pipeline.md#pr-12--조문-자동-갱신을-릴리스와-연동-featarticles-update-db)

- 초기 Red 11개는 DB 설정에도 CSV 경로를 타서 실패했다. 초기화와 활성 릴리스 검증, DB 폴더 잠금, DB 상태 전이와 실패 이벤트를 구현한 뒤 통과했다.
- 변경 없는 경우 보고서를 쓰고 종료 0을 반환했다. 생성 전 사전 검사에서는 DB 설정 누락, 활성 릴리스 누락, 복원 해시 불일치를 검출했다. 실제 조문 생성과 임베딩은 이 오류들 뒤에 시작하지 않았다.
- 빈 본문은 릴리스 ID 없는 `failed` 이벤트를 남겼다. 검색 검증 실패는 후보 상태를 `failed`로 바꿨다. 같은 날 실패한 ID를 포함해 `_2`, `_3`을 배정했다. 임베딩 중 외부에서 활성 릴리스를 바꾼 경우 새 활성 포인터를 보존했다.
- 가짜 Git 저장소의 시행일별 조문을 실제 생성기로 만들었다. 각 문서·질문 임베딩 호출 시점에 별도 DB 연결의 `BEGIN IMMEDIATE`가 즉시 성공했고, 그 시점에 후보는 `staged`였다. 검색 검증 뒤 `validated`·`activated` 순서와 이전 릴리스 ID를 확인했다.
- 첫 재시작이 응답하지 않으면 `restart_failed`·`rolled_back`을 차례로 기록하고 이전 릴리스에서 다시 시작했다. 건너뛴 재시작은 새 릴리스가 활성으로 남았다. 수동 롤백은 현재 릴리스의 마지막 활성화 이벤트에서 대상을 골랐고 대상이 없을 때 실패했다.
- v5 `--init`은 기본 CSV·매니페스트 네 파일을 바이트 단위로 보존했다. 두 번째 초기화는 거부했다. DB 모드와 기존 CSV 테스트에서 monitor가 사용하는 종료 코드와 마지막 줄, 보고서와 파일 보존을 확인했다.

| 검증 | 결과 |
|---|---|
| 단위·기타 기본 | 1,094개 통과 |
| 계약 | 5개 통과 |
| 통합 | 9개 통과 |
| 대역 E2E | 29개 통과(챗봇 17개, 공개 캘린더 12개) |
| `uv run pytest` | 총 1,137개 통과, 기본 선택에서 13개 제외 |
| `uv run ruff check`, `uv run ruff format --check` | 통과 |
| `uv run pyright`, `uv run pyright services/law-calendar/src` | 각각 오류 0개 |
| 두 프론트엔드 `npm run typecheck`·`npm test`·`npm run build` | 통과, Vitest 챗봇 37개·캘린더 19개 |
| `uv run python -m law_calendar.schema --check`, `uv run python -m compileall -q services packages tools` | 통과 |
| 실제 모델 E2E | 실행 안 함 |
| 모델 스모크 | 실행 안 함 |
| 품질 평가 | 실행 안 함 |
| 운영 DB 갱신 및 `launchctl` | 실행 안 함 |
