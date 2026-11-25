# 캘린더 v6.7.1 Node 경로 권한 검증

가짜 Node와 Wrangler를 사용해 그룹 쓰기 가능한 조상 디렉터리 아래의 게시를 검증했다. 신뢰 그룹이면 Wrangler가 실행되고, 비신뢰 그룹 또는 다른 사용자에게 쓰기 권한이 있으면 Pages 요청 전에 `wrangler_unavailable`로 거부된다.

| 명령 | 결과 |
| --- | --- |
| `uv run --locked --no-sync pytest -q --tb=short services/law-calendar/tests/test_calendar_publish.py -k node_ancestor_writability` | Red: 신뢰 그룹 1건이 `wrangler_unavailable`로 실패, 거부 시나리오 2건 통과 |
| `uv run --offline --no-sync --no-env-file pytest -q services/law-calendar/tests/test_calendar_publish.py -k node_ancestor_writability` | Green: 3건 통과 |
| `uv run --offline --no-sync --no-env-file pytest -q` | 최종 2,972건 통과, 기본 설정 제외 10건. 단위·계약·통합·대역 E2E 포함 |
| `uv run --offline --no-sync --no-env-file ruff check --output-format=github` 및 `ruff format --check` | 모두 통과. 포맷 검사 623개 파일 |
| `uv run --offline --no-sync --no-env-file pyright` 및 `pyright services/{law-calendar,erp-updates,source-hub}/src` 각 실행 | 네 범위 모두 오류·경고 0건 |
| `uv run --offline --no-sync --no-env-file python -m compileall -q services packages tools` 및 `python -m law_calendar.schema --check` | 모두 통과 |
| `uv lock --offline --check` 및 `uv lock --offline --check --project services/{law-calendar,erp-updates,source-hub}` 각 실행 | 모두 통과 |

프런트엔드 자체 검사, 실제 모델 E2E, 모델 스모크, 품질 평가, 실제 Pages 게시: 실행 안 함. 운영 트리 접근 없음.
