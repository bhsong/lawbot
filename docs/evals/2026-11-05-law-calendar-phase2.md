# 캘린더 Phase 2 DB·공개 조회 API 검증

- 기준: #68 병합 main 9a54fc3, 작업 브랜치 codex/law-calendar-phase2-20261105.
- 별도 Python 프로젝트 law-calendar / law_calendar, 별도 uv.lock. 챗봇·모델 서버·monitor 런타임은 변경하지 않는다.
- SQLite v1은 명시적 CLI로만 생성하며 공개 조회는 mode=ro/query_only로 연다. 기존 외부 스키마에는 마이그레이션을 거부한다. 예시는 pytest 임시 DB에만 삽입한다.
- event와 article_change 모두 승인되고 검증 시각·공식 HTTPS 출처·유효한 날짜가 있는 항목만 같은 view를 통해 집계·목록·상세로 제공한다. 공개 쓰기 endpoint는 없다.
- API 응답 Pydantic schema로 api-types.ts를 생성하고 CI에서 최신성을 검사한다. 원문은 별도 열에 보존하며 표시용 diff는 상세 응답에서 계산한다.
- production UI는 same-origin 공개 API만 credentials:omit으로 읽는다. API 실패와 공개 0건을 구분하고 월 목록의 모든 페이지를 읽는다. 불완전 페이지는 오류로 처리한다. 상세는 열 때 재조회해 비공개/철회 항목을 다시 확인한다.

## 검사

- 캘린더 Vitest 14개 및 타입 검사·빌드 통과. 기존 챗봇 19개 및 타입 검사·빌드 통과.
- 신규 API 테스트: 승인·반려·철회·미검증·출처 누락 차단, 공포/게시/조문 시행일 구분, 집계/목록/상세 일치, 잘못된 날짜·유형·페이지 입력, DB 부재 503, read-only, migration idempotence·외부 DB 보존·backup을 검증한다.
- 브라우저: 기존 반응형·키보드·production 예시 차단에 더해 실제 임시 DB → 독립 API 서버 → 월간 건수/목록 → 상세 원문을 검증한다. 브라우저 날짜는 fixture 날짜에 고정해 실행 일자에 의존하지 않는다.
- ruff·pyright(기존 및 캘린더 API)·compileall·두 프로젝트 lock check·타입 생성 최신성 검사 통과.
- 전체 기본 pytest: 774 통과 / 기본 제외 12개 (122.58초). 신규 API 20개와 독립 UI E2E 5개 포함. CI는 구현 커밋 후 별도 확인한다.
- 실제 모델 호출, 실제 외부 법령 수집, 운영 데이터·배포·서비스 재시작 없음.

## 실행

지원 환경에서 두 frontend를 각각 npm ci/typecheck/test/build한 뒤 root uv run pytest를 실행한다. CI는 독립 API 패키지를 editable/no-deps로 root 검사 환경에 설치한다. API 단독 실행은 services/law-calendar의 별도 lock만 필요하며 모델 라이브러리를 설치하지 않는다. Linux 로컬 전체 회귀는 root lock 버전 및 CPU torch/MLX를 사용하는 임시 환경이며 실제 모델 검증이 아니다.

이 단계의 공개 조건은 기술적 게시 게이트이며 법률 검증 자체를 수행하지 않는다. 수집 adapter, 승인/반려 CLI와 감사 기록 작성, 원천 snapshot·법령 버전별 효력 범위 모델은 Phase 3에서 연결한다. public DB는 현재 비어 있으며 자동 수집·공개는 수행하지 않았다.
