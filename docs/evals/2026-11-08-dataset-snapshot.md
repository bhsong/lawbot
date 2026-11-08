# 데이터셋 스냅샷 검증 (2026-11-08)

개발 트리에서 `tmp_path`의 DB에 v5 CSV를 이관하고 합성 피드백을 넣어 검증했다. [PR 10 계획](../plans/sqlite-admin-pipeline.md#pr-10--스냅샷-내보내기와-분할-배정-featdataset-snapshot)

- Red 테스트 11개는 두 CLI가 없어 실패했다. 구현 후 새 테스트 14개가 통과했다. 첫 전체 실행에서 도구의 직접 `sqlite3` import가 계층 검사에 걸려 기존 DB 오류 타입을 사용하도록 수정했다.
- v5 QA·분할 CSV 각 13,680행과 정답 키 1,745행이 원본과 바이트 단위로 같았다. `eval_v4.load_tax`에서 고정 Test 3,420개가 원본과 같았고, `keywords('v5')`와 `score_answers.score_one`이 내보낸 파일을 읽었다. 기존 분할 동결 트리거 테스트도 통과했다.
- 연결된 유사 질문의 목적별 배정, 새 키의 해시와 기존 분할이 다를 때의 상속, 취소된 문항의 배정 상속을 확인했다. Train·Val을 연결하는 문항은 미배정 상태로 남고 종료 코드 1을 반환했다. 배정 도중 상태가 바뀌거나 쓰기가 실패하면 종료 코드 2를 반환했다. 기존 관리자 API 테스트에서 `checks.overlap` 경고를 확인했다.
- 이름을 바꾼 두 조문 CSV는 원본과 같았고 `manifest.load`와 `manifest.check`를 통과했다. `manifest.json`의 `snapshot`에 원본 매니페스트 해시와 산출물 메타데이터를 넣고, `manifest.json` 자체의 해시는 `dataset_releases`에 저장했다. 데이터 추출은 하나의 읽기 트랜잭션에서 수행했고, 발행 시에는 등록 행을 별도 트랜잭션에서 저장했다.
- 같은 버전의 재실행과 다른 경로 재추출은 동일한 바이트를 만들었다. 데이터가 바뀌거나 출력이 변조되면 실패했고, 등록 오류 때는 새 출력이 정리됐다. 활성 문항의 목적별 분할, 미배정 경고, 취소 문항 제외, 빈 `negative`, 오답 교정 선호 쌍, BOM과 권한 `0600`을 확인했다.
- 과거 기록에 검색 조문이 없어 프롬프트를 만들 수 없으면 해당 생성용 행을 제외했다. 기록에 없는 `fmt`·`system`은 공란으로 두고 누락 수를 매니페스트와 표준 출력에 기록했다.

| 검증 | 결과 |
|---|---|
| 단위·기타 기본 | 1,059개 통과 |
| 계약 | 5개 통과 |
| 통합 | 9개 통과 |
| 대역 E2E | 28개 통과(챗봇 16개, 공개 캘린더 12개) |
| `uv run pytest` | 총 1,101개 통과, 기본 선택에서 13개 제외 |
| `uv run ruff check`, `uv run ruff format --check` | 통과 |
| `uv run pyright`, `uv run pyright services/law-calendar/src` | 각각 오류 0개 |
| 두 프론트엔드 `npm run typecheck`·`npm test`·`npm run build` | 통과, Vitest 챗봇 37개·캘린더 19개 |
| `uv run python -m law_calendar.schema --check`, `uv run python -m compileall -q services packages tools` | 통과 |
| 실제 모델 E2E | 실행 안 함 |
| 모델 스모크 | 실행 안 함 |
| 품질 평가 | 실행 안 함 |
