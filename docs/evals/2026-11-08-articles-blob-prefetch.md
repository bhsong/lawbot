# 조문 부분 클론 blob 사전 수신 검증 (2026-11-08)

임시 bare 원격과 `file://` 부분·전체 클론을 사용했다. 합성 법령 2개, 문서 6개에 5개 버전을 넣고 `GIT_TRACE2_EVENT`의 fetch 시작 이벤트를 셌다. [계획서](../plans/articles-blob-prefetch.md)

| 로컬 검증 | 수정 전 fetch | 수정 후 fetch |
|---|---:|---:|
| 부분 클론에서 `show_many`로 없는 blob 20개 읽기 | 20 | 1 |
| 부분 클론에서 `build()` 실행 | 18 | 3 |
| 부분 클론에서 변경 이력 24건 읽기 | 24 | 1 |
| 이미 받은 blob 재사용 및 전체 클론 | 0 | 0 |

- packet 추적으로 로컬에 없는 OID만 한 번씩 요청했고, 시행 중인 마지막 전부개정 이전의 본문을 받지 않았음을 확인했다.
- 끊어진 원격에서 fetch 한 번 뒤 Git 오류를 보존한 예외로 실패했다. 없는 경로와 fetch 이후에도 없는 객체는 본문에서 개별 fetch를 시도하지 않고 실패했다.
- 수정 전 `f730ecd`의 생성기와 수정 후 생성기를 같은 합성 이력에서 실행했다. CSV 2개, 매니페스트 2개, 버전 기록과 `articles_check` 비교 결과가 일치했다. 양쪽 합성 요약은 `articles same=no changed=6 pending=6 next=2027-01-01 new=12`였다.

| 검증 | 결과 |
|---|---|
| 단위·기타 기본 / 계약 / 통합 / 대역 E2E | 각각 989 / 5 / 7 / 26개 통과 |
| `uv run pytest` | 1,027개 통과, 기본 선택에서 13개 제외 |
| `uv run ruff check`, `uv run ruff format --check` | 통과 |
| `uv run pyright`, `uv run pyright services/law-calendar/src` | 각각 오류 0개 |
| 챗봇·캘린더 `npm run typecheck && npm test && npm run build` | 통과, Vitest 20개·19개 |
| 캘린더 스키마 검사, Python 문법 검사 | 통과 |
| 실제 legalize-kr 신규 다운로드와 점검 | 실행 안 함 |
| 실제 모델 E2E·모델 스모크·품질 평가·운영 확인 | 각각 실행 안 함 |
