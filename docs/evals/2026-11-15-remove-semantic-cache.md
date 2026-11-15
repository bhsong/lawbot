# 시맨틱 캐시·원자 질문 RAG 제거 개발 검증

- 실행일 `2026-11-15` KST. 기준선 `fe30077` (#155), 작업 브랜치 `refactor/remove-semantic-cache`.
- `uv run --locked --offline pytest` 2,023개 통과. 단위·계약·통합과 대역 E2E를 포함했다. 통합 7개, 대역 E2E 53개. 기본 선택에서 제외된 항목은 13개다.
- 제거 전 기준선에서 고정 시각과 요청 ID로 캡처한 웹 응답의 상태·콘텐츠 유형과 SSE·요청 로그 바이트를 대조했다. 정확 일치 조회와 이월, 관리자 검수, ZIP 없는 `set-mode` 회귀도 통과했다.
- `uv lock --check --offline`, Python `compileall`, Ruff 린트·포맷, Pyright, 독립 캘린더 스키마 검사 통과. 챗봇과 캘린더에서 각각 `npm run typecheck && npm test && npm run build` 통과. 프론트 단위 테스트는 55개와 31개다.
- 추적 대상 소스와 테스트 사본에서 `grep -ri semantic services/chatbot/src services/chatbot/tests` 일치 0건.
- 개발 v5 조문과 QA 8세트를 이관한 코퍼스, 원래 스키마 2로 초기화한 캐시를 각각 백업해 `python -m lawbot.cache_seed_pass`를 시험했다. 승인 행과 감사의 실행 주체·사유·ZIP 해시·보호 지문, 무결성 및 외래 키를 대조했다. 재실행 시 제어행·감사 건수와 집계가 동일했다. 승인·제외 집계는 PR 본문에 기록한다.
- 합성 P07 기존 승인 테스트에서 호환 리비전 연결과 충돌 보존, 보호 지문·승인 ID 유지, 보호 질문 제외, 코퍼스 변경 시 롤백을 확인했다.
- 실제 모델 E2E, 모델 스모크, 품질 평가는 각각 실행 안 함.
