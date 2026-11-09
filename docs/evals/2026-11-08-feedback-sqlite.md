# SQLite 피드백 저장 검증 (2026-11-08)

개발 트리에서 가짜 모델과 임시 DB로 PR 5를 확인했다. [계획서](../archives/sqlite-admin-pipeline.md)

- SQLite 저장, 스키마 오류, 사용한 system 보존과 새 턴 필드, `feedback_review --db`의 실패를 먼저 재현했다.
- 저장된 `record` JSON에 기존 키와 새 필드가 모두 들어갔다. 웹앱 시작 후 CSV와 system 파일을 교체해도 이미 읽은 CSV와 실제 생성에 사용한 system의 해시를 기록했다.
- 정상 응답, 잘못된 요청 400, 모르는 턴 404, 2초 안에 반복한 선택의 `saved: false`를 확인했다. SQLite 쓰기를 실패시켰을 때 유효한 첫 선택은 `saved: true`였고, DB에 행이 생기거나 JSONL로 쓰이지 않았다. 로그에는 질문 없이 경고가 남았다.
- `LAWBOT_DB`가 비어 있으면 기존 설정에 따라 JSONL을 저장했다. DB를 지정하면 파일 누락·오래된 스키마·더 높은 버전·손상 파일을 시작 시 거부했다.
- 같은 시각의 피드백과 선택 해제가 포함된 합성 기록으로 검수 CSV와 동반 JSONL의 바이트가 DB 입력과 JSONL 입력에서 각각 일치함을 확인했다.
- Chromium에서 로그인 → 질문 → 좋아요 버튼을 실행했다. 임시 DB에 저장된 행의 요청 ID가 웹앱 요청 로그와 일치했다.
- PR #87 리뷰에서 지적한 순서 역전을 재현했다. 같은 초에 좋아요를 지연시키고 해제를 보내면 기존 코드에서 해제가 먼저 기록됐다. 수정 뒤 두 행이 요청 순서로 저장되고 검수 대상에서 빠졌다. 첫 쓰기를 기다리는 동안 로그인 확인 API가 응답했다.

| 검증 | 결과 |
|---|---|
| 단위·기타 기본 / 계약 / 통합 / 대역 E2E | 각각 961 / 5 / 7 / 25개 통과 |
| `uv run pytest` | 998개 통과, 기본 선택에서 13개 제외 |
| `uv run ruff check`, `uv run ruff format --check` | 통과 |
| `uv run pyright`, `uv run pyright services/law-calendar/src` | 각각 오류 0개 |
| 챗봇·캘린더 `npm run typecheck && npm test && npm run build` | 통과, Vitest 각각 19개 |
| 캘린더 스키마 검사, Python 문법 검사 | 통과 |
| `git status --short -- data/` | 변경 없음 |
| 실제 모델 E2E·모델 스모크·품질 평가 | 각각 실행 안 함 |
| 운영 반영·운영 확인 | 실행 안 함 |
