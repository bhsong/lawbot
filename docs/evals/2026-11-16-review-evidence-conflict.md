# 검수 근거 해시 충돌 이월과 적재 순서 검증

- 실행일 2026-11-16 KST. PR #156 후속, 기준 `7582be1`.
- 기존 `apply_release`는 두 검수 근거 해시가 충돌하면 `ValueError`로 전체 이월을 롤백했다. 같은 합성 DB에 정상 답변을 함께 넣은 뒤 첫 이월과 다음 이월에서 정상 답변은 정확 일치 적중, 충돌 답변은 `pending/review_evidence_conflict` 및 관리자 승인 거부를 확인했다. 같은 이벤트의 재실행 집계도 유지됐다.
- QA 지문이 바뀐 합성 DB에서 기존 활성화 이벤트의 이월은 재판정을 하지 않았고 `import-db`가 승인·활성 질문 때문에 거부됐다. 새 이벤트에서 이월한 뒤에도 P07 우선 재승인은 `protection_changed`로 거부됐다. `import-db`가 현재 지문을 채택한 뒤 P07 재승인이 통과했다. 운영 DB 사본 리허설은 배포 직전 단계로 [계획 §5](../plans/reviewed-question-suggestions.md#5-운영-반영-pr-ab-병합-후-사용자-승인)에 기록했다.
- `uv run --locked --offline pytest -q --tb=short` 2,036개 통과, 기본 선택 제외 13개. 단위·계약·통합·대역 E2E를 포함했다.
- Ruff 린트·포맷, Pyright, Python `compileall`, 독립 서비스 타입 검사와 캘린더 스키마 검사 통과. 챗봇과 캘린더의 타입·단위 테스트(55개·31개) 및 빌드 통과.
- 실제 모델 E2E·모델 스모크·품질 평가 실행 안 함. 실제 모델 호출 0회.
