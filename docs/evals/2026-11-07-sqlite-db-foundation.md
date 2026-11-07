# SQLite DB 기반 검증 (2026-11-07)

PR 2 범위에서 `0001_init.sql`과 계획서의 DDL이 일치하는지 확인했다. 레포 밖 임시 DB에 합성 데이터만 썼다. [계획](../plans/sqlite-admin-pipeline.md#pr-2--db-기반-featsqlite-db-foundation)

- Red에서 DB CLI가 없어 초기화 테스트가 실패했다. 설정 테스트 5개는 `LAWBOT_DB` 경로 처리 부재로 실패했고, 의존 규칙 테스트 2개는 금지된 import를 넣었을 때 이를 검출했다. 구현 후 기본 테스트 전체에서 통과했다.
- `lawbot-db init`, `migrate`, `backup`, `restore`, `status`를 설치된 명령으로 실행했다. 스키마 버전은 1이었고, 배포 패키지에서 초기 SQL을 확인했다. 같은 대상에 동시에 초기화 명령을 실행했을 때 한 명령만 성공했고, 결과 DB의 스키마와 파일 권한이 유효했다.
- 열린 WAL에서 커밋된 피드백의 행 수·내용 SHA-256을 복원본과 비교했다. 진행 중 트랜잭션의 데이터는 복사본에 들어가지 않았고 무결성 검사 결과는 `ok`였다.

| 검증 | 결과 |
|---|---|
| `uv run pytest` | 950개 통과, 기본 선택에서 13개 제외 |
| 단위·기타 기본 / 계약 / 통합 / 대역 E2E | 각각 914 / 5 / 7 / 24개 통과 |
| `uv run ruff check`, `uv run ruff format --check` | 통과 |
| `uv run pyright`, `uv run pyright services/law-calendar/src` | 각각 오류 0개 |
| `uv run python -m law_calendar.schema --check`, `uv run python -m compileall -q services packages tools` | 통과 |
| 챗봇·캘린더 `npm run typecheck && npm test && npm run build` | 모두 통과, Vitest 각각 19개 |
| 실제 모델 E2E·모델 스모크·품질 평가 | 실행 안 함 |
| 운영 확인 | 이 PC에서 확인 불가 |

테스트 층 집계를 위한 보조 수집은 샌드박스의 MLX import에서 중단됐다. 기본 pytest와 같은 로컬 실행 권한에서 수집을 다시 실행해 통합·대역 E2E 31개를 확인했다.
