# 원자 시맨틱 캐시 PR 1 개발 검증

2026-11-15 KST. 개발 기준 `3acba50`, 브랜치 `feat/semantic-cache-core`. 합성 데이터와 가짜 모델을 사용했다. [계획서](../plans/atomic-semantic-cache.md)

## Red → Green

- 0003을 추가하기 전 신규 캐시 초기화가 0002를 반환했다. `on` 거부와 calibration 없는 `shadow` 거부도 실패했다. 0003 적용 후 0002에서 승인한 기존 exact 답변과 마이그레이션 이력, SQLite FK 무결성을 대조했다.
- 결정적 조건·선택 부모 점수·응답 인덱스 계약 테스트를 먼저 실패시켰다. 정확·시맨틱 분기 연결 전에는 shadow 가상 적중과 미스 사유가 요청 로그에 없었다.
- 전체 실행에서 SQLite import가 런타임에 들어간 계층 검사 실패를 확인했다. 카탈로그 읽기를 `semantic_store.py`로 옮긴 뒤 전체를 다시 실행했다.

## 최종 검증

| 층 | 결과 |
| --- | --- |
| 단위 | 통과. V2-05~12, V2-19~20, V2-37, 0002 → 0003, binding carry 상태·이력 |
| 계약 | 통과. 클라이언트와 실제 모델 서버 앱의 HTTP 요청·응답, 가짜 모델 |
| 통합 | 실제 웹앱 프로세스 7건 통과. 합성 카탈로그를 이용한 V2-01~04, V2-28 exact, V2-31 및 계산 질문 우회도 통과 |
| 대역 E2E | 53건 통과. Chromium에서 shadow 가상 적중과 기존 RAG 응답, exact 적중 시 조문 카드·피드백 및 추론 0회를 확인 |
| 실제 모델 E2E | 실행 안 함 |
| 모델 스모크 | 실행 안 함 |
| 품질 평가 | 실행 안 함 |

- 기본 `uv run pytest`에서 2,036건 통과, 13건 선택 제외, 실패·skip 0건. 로컬에서는 잠금 파일과 설치된 의존성을 사용하는 `uv --no-cache run --locked --no-sync`로 실행했다.
- `ruff check`, `ruff format --check`, `pyright`, `python -m compileall -q services packages tools` 통과. 독립 캘린더·ERP·수집 설정 서비스의 Pyright 검사와 캘린더 스키마 검사도 통과했다.
- 챗봇·캘린더 `npm run typecheck`, `npm test`, `npm run build` 통과. Vitest는 챗봇 55건, 캘린더 31건이다.
- V2-02는 상태와 조건을 만족한 후보의 가상 적중으로 검증했다. 브라우저는 기존 RAG 생성 응답을 받았으며, 시맨틱 답변 반환과 생성 생략은 `on` 지원 때 검증한다.
- 기본 `off`에서 고정 시간·고정 요청 ID로 받는 RAG 응답과 SSE, JSONL 요청 로그의 바이트 지문이 `main` 기준값과 일치했다.
- 실제 모델 호출 0회. 개발 트리의 두 handoff ZIP은 지정 경로에 없었다.
