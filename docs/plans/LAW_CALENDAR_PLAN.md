# Lawbot 법령 변경 캘린더: Phase 0 조사 및 Phase 1 계획

- 조사일: 2026-11-05 (UTC)
- 저장소: https://github.com/bhsong/lawbot
- 조사 기준: main 커밋 77e7aa0af8bbb5f43be245e988f5e326014e9184 (tree: 7913f64953241195b550760defaacb39d52961b8). 문서 기록 전 main과 작업 브랜치가 이 커밋에 있음을 재확인.
- 요구사항 기준: 대화에 제공된 PLAN.md 전문. 별도 LAW_CALENDAR_PLAN.md 첨부는 현재 작업 공간에서 확인되지 않음.
- 범위: Phase 0 실사와 Phase 1 UI 목업·대역 검증. 실제 데이터 연결·배포·실제 모델 호출 없음.
- 상태: Phase 1 UI 구현·로컬 검증 완료. Phase 0의 실제 데이터 전체 추적은 미완료.

## 1. 작업 상태와 제약

신규 브랜치 `codex/law-calendar-phase0-20261105`를 main에서 생성했고, 문서 기록 전 HEAD가 조사 기준 커밋과 같음을 확인했다. 초기 GitHub 403 오류는 연결 권한 수정 후 해소되었다. 로컬 clone은 환경 proxy 연결 오류로 실패하여 GitHub 연결 도구로 조사 및 문서 기록을 수행했다.

Phase 0 기록 커밋은 `0030cda`다. Phase 1 시작 시 추가 네트워크 권한으로 `/workspace/lawbot` 체크아웃에 성공했고, 해당 브랜치 HEAD `0030cda`와 깨끗한 status, 개발 worktree 하나를 확인했다. 운영 worktree가 아닌 분리된 클라우드 개발 환경에서 작업한다. 기존 다른 계획서·학습 작업·운영 데이터는 변경하지 않는다.

사용자 합의:
- 계획 보고 후 사용자가 Phase 1 UI 구현을 명시적으로 승인했다. Phase 2 이후 DB·API·수집 연결은 이번 구현 범위가 아니다.
- PR 병합은 해당 PR에 대한 사용자 명시적 승인 후에만 수행한다.
- 실제 모델 호출은 수행하지 않는다. 대역 검증과 실제 모델 미실행을 구분해 기록한다.
- 운영 배포·서비스 재시작은 수행하지 않는다. 사용자가 진행 중인 모델 학습 작업과 함께 배포할 수 있도록 후속 배포 절차만 정리한다.

## 2. 실제 구조

```text
AGENTS.md                         공통 작업 지침
pyproject.toml / uv.lock          uv workspace, 공통 검사 설정
services/chatbot/
  frontend/                      Vite + React + TypeScript
  src/lawbot/                    FastAPI 웹앱, 검색·응답·인증
  tests/                         단위·통합·Playwright E2E
services/monitor/
  config/targets.yaml            법령·행정규칙·서식 대상
  src/law_monitor/               Python CLI 수집·비교·보강·요약
  tests/fixtures/lawgo/          축약한 실제 법제처 응답
services/model-server/           OpenAI 호환 모델 서버
packages/lawbot-common/          서비스 공통 코드
tools/lawbot-tools/              조문 생성·갱신·평가 CLI
deploy/launchd/                  웹앱·모델 서버 실행 템플릿
docs/operations.md               운영·설치·검사 명령
```

재귀 트리에는 CONTEXT.md, LAW_CALENDAR_PLAN.md, 루트 package.json이 없다. CONTEXT.md를 읽었다고 보고할 수 없다. 대신 AGENTS.md, README.md, 각 pyproject.toml, frontend/package.json, 실제 코드·운영 문서를 확인했다. 기존 docs/plans와 실습 가이드는 수정하지 않았다.

## 3. 계획서와 다른 점 및 수정 방향

| 항목 | 실제 확인 | 적용 방향 |
| --- | --- | --- |
| Python 위치 | 루트 src/lawbot이 아니라 서비스별 패키지 | 웹 조회는 services/chatbot/src/lawbot, 수집은 services/monitor/src/law_monitor를 유지 |
| 서비스 연결 | 루트 pyproject는 서비스끼리 import하지 않고 lawbot_common만 공유하도록 명시 | 웹앱이 monitor를 직접 import하지 않도록 공개 데이터 계약으로 연결 |
| React 라우터 | App.tsx는 로그인 확인 후 Chat 렌더링. 라우터 의존성 없음 | 인증 앱 바깥에서 /law-calendar를 분기하고 기존 App 흐름 보존 |
| 서버 라우팅 | web.mount_spa는 /assets와 SPA fallback 지원, /api의 미등록 경로는 404 | /law-calendar 직접 접속 가능. 새 API는 fallback 등록 전에 추가 |
| UI 시스템 | 자체 CSS 변수, Pretendard, 라이트·다크 모드. Tailwind/shadcn 없음 | CSS 변수 재사용, 캘린더 CSS 범위 격리. 중복 UI 프레임워크 도입 보류 |
| 조회 서버 | 이미 FastAPI 사용 | 새 서버 프레임워크 불필요 |
| 입법예고 | 설정 키워드는 있으나 default_collectors는 legalize_law/legalize_admrule 둘뿐 | 수집기와 원천 계약을 후속 단계에서 추가해야 함 |
| 변경 이벤트 | law_amended/new/repealed 등 감지 이벤트, 공포·시행 날짜 메타데이터 | notice_published/effective 공개 일정과 별도 모델로 변환 |
| 기존 DB | SQLite state.db + JSON 이벤트·파일 스냅샷·diff | 수집 상태 DB를 공개용 DB로 그대로 노출하지 않음 |
| 승인 | processed_at은 요약 처리 여부, 승인 상태가 아님 | 별도의 검토·승인·감사 모델 필요 |
| diff | difflib unified diff 및 조문·부칙 블록 diff 존재 | 항·호 및 효력 시점별 비교는 추가 작업 |
| 요약 | 모델로 이벤트 요약 Markdown 작성 | 공개용 한 줄 요약·검토 이력·원문을 분리 |
| 공개 배포 | 문서상 Mac mini + launchd + Tailscale Serve, tailnet 내부. Funnel 사용 안 함 | 같은 사이트 경로는 재사용 가능하지만 일반 공개는 별도 배포 설계 필요 |
| RAG 최신성 | daily checks가 조문 생성·임베딩·전환·웹앱 재시작 가능 | Phase 5는 백지 구현이 아니라 기존 자동 갱신과 승인 정책 조정 필요 |

web.main은 load_app(cfg)를 거쳐 챗봇을 초기화한 뒤 앱을 만든다. 공개 경로 추가만으로 모델 서버 없이 독립 기동할 수 있게 되는 것은 아니다. 공개 배포 시 이 결합의 운영 영향을 따로 검토한다. 챗봇 인증을 전체 해제해서 캘린더를 공개하면 안 된다.

## 4. 확인한 패키지와 실행 방식

- frontend/package.json: Node >=26, React/React DOM 19.3.1, TypeScript 7.0.2, Vite 8.3.4, Vitest 5.0.5. scripts는 dev/build/typecheck/test. FullCalendar·React Router·diff 라이브러리 없음.
- chatbot/pyproject.toml: Python >=3.14, FastAPI >=0.143.3, uvicorn >=0.54.0. 위 Python 의존성은 선언 범위이며 이번 보고서에서 잠금 버전으로 단정하지 않음.
- 루트 workspace는 macOS 기준 해석 설정. 모델 서버는 MLX 사용. Linux에서 전체 uv sync가 된다고 가정하지 않음.
- 웹앱: `uv run lawbot-app`. monitor: `uv run law-monitor bootstrap|collect|replay|summarize|check|daily`.
- 수집 모듈 실행이 필요하면 `uv run python -m law_monitor.cli ...`. 계획서의 모든 Python 명령을 lawbot.<module>로 통일하는 것은 실제 구조와 맞지 않음.

## 5. 모니터링 입출력과 재사용 범위

수집은 legalize-kr/legalize-kr 및 legalize-kr/admrule-kr Git 이력을 읽고 법제처 API로 개정문·개정 이유·시행 메타데이터·서식을 보강한다. 법률 대상은 13개 상위 폴더이며 그 안의 법률·시행령·시행규칙을 추적한다. 소득세법·부가가치세법 등 조세도 포함해 챗봇 README의 7개 노동법 범위보다 넓다. 행정규칙과 별표·서식도 대상이다.

기본 실행 데이터는 services/monitor/data (LAW_MONITOR_HOME으로 분리 가능). 입력 커서부터 새 커밋별로 이벤트를 만들고 내용 기반 event_id로 중복을 막는다. 원문 old/new 파일 사본, unified diff와 조문별 chunks를 보존한다. state.db에는 git_cursor/items/seen_posts/events/runs가 있다. seen_posts 테이블 존재만으로 게시물 수집 완료를 의미하지 않는다.

이벤트의 detected_at은 감지 시각이고 저장 폴더도 그 날짜다. dates.promulgation/effective는 원문 메타데이터다. Git commit 날짜가 공포일일 수 있어 감지·게시 날짜로 쓰면 안 된다. old/new는 Git 개정판 비교이며 해당 시행일 직전·직후 유효 조문을 자동 보증하지 않는다.

재사용 가능: 원문 사본, 내용 해시, 중복 제거, 실패 격리, 실행 로그, KST 시각, 실행 잠금, retry HTTP 경로, 부칙 원문과 조문시행일자문자열 보강. 추가 필요: 입법예고 수집, 안정 조문 식별자/버전 효력 구간, 조항별 시행 이벤트, 승인 CLI와 감사 로그, 공개 조회 API.

문서상 launchd는 매일 06:30 실행한다. 실제 운영 등록·최종 성공 시각·DB 백업 여부는 이번 환경에서 확인하지 못했다. daily는 collect → checks → summarize이며 checks에는 lawbot-articles-update --restart가 있으므로 이번 조사에서는 실행하지 않았다.

## 6. 실제 데이터 추적 결과와 미완료 기준

### 시행 후보: 소득세법 시행규칙

기존 스파이크 기록과 fixtures/lawgo/law_286379.json을 확인했다. 신판 MST 286379, 법령ID 007507, 공포일 2026-05-22, 기본 시행일도 2026-05-22지만 조문시행일자문자열은 `20260701:제100조제26호`다. 비교 구판 fixture는 law_284987.json이다. 별표시행일도 5월 22일/7월 1일로 나뉜다.

코드 경로는 LegalizeGitCollector.emit → old/new 원문·조문 diff → LawGoEnricher._law의 article_effective 보강 → EventStore JSON/diff → State.events 저장이다. 이는 머리말 시행일 하나로 모든 조문을 달력에 배치하면 틀릴 수 있음을 입증한다. fixture는 축약 응답이며 기존 조사 기록을 이번 라이브 호출 결과로 취급하지 않았다. 실제 운영 이벤트 JSON/DB와 공식 직전·직후 조문을 연결한 끝까지의 검증은 미완료다. 서식 변경을 조문 시행 검증 완료로 대신하지 않는다.

### 입법예고

수집기·공개 API·실제 이벤트 fixture가 없어 한 건의 전체 추적을 완료할 수 없다. docs/evals/2026-11-02-monitor-spike-lawmaking.md에 따르면 국민참여입법센터는 기존 LAW_OC와 별개 신청 계정이 필요하고, 과거 호출은 HTTP 200 본문에 401이었다. 국회는 열린국회정보 키가 필요하며 정확한 API·응답 필드는 미확인이다. 현재 키 발급 여부는 미확인이다.

따라서 계획서의 “입법예고/시행 각각 실제 1건을 끝까지 추적” 기준은 충족하지 못했다. Phase 0을 전체 완료로 표시하지 않는다.

## 7. Phase 1 구현 계획 및 적용 사항

1. 정상 개발 체크아웃에서 원격·HEAD·status·worktree와 지침을 확인하고 기존 신규 브랜치 codex/law-calendar-phase0-20261105를 이어간다. 이 문서는 대화의 원 계획을 바탕으로 작성한 실사·적용 계획이며 원문 전체를 복제한 문서는 아니다. 원문 첨부 확보 시 내용을 보존해 별도 수록한다.
2. frontend/src/main.tsx에 최상위 경로 분기 도입. /law-calendar만 독립 페이지로 렌더링하고 기존 App.tsx의 로그인·Chat·SSE·피드백 동작을 보존한다. 우선 일반 링크와 pathname 분기로 두 경로를 지원하고, 향후 라우트 증가 시 라우터 도입을 검토한다.
3. 월간 달력, 월 이동/오늘/날짜 선택, KST 기준 오늘, 파란 입법예고/주황 시행 표시와 텍스트 건수, 선택일별 두 목록 구현. 모바일 세로 순서와 데스크톱 2열 목록 지원.
4. 상세는 native dialog 기반으로 초기 포커스·모달 탭 이동·Escape·닫은 뒤 포커스 복귀를 검증한다. Before/After는 데스크톱 2열, 모바일 세로 스택. 원문 텍스트와 표시용 강조를 분리하고 비교 불가 상태를 둔다.
5. 로컬·테스트 전용 합성 fixture에 “예시 / 실제 법령 아님”을 명시한다. production 경로에서는 fixture 모듈을 동적으로 가져오지 않도록 개발 모드 분기를 두고, 승인 데이터 미연결 안내와 빈 화면을 제공한다. 단순 배지만으로 가상 법령을 운영에 노출하지 않는다.
6. 상·하단 고지, 출처·게시/공포/시행/의견기한·수집/검증 시각, 빈 날짜, 로딩·오류·갱신 지연·더 보기 상태를 테스트용 데이터 공급자로 재현한다. 실제 API·외부 수집·LLM 호출은 Phase 1에 연결하지 않는다.

Phase 1 변경 파일:

| 위치 | 변경 |
| --- | --- |
| services/chatbot/frontend/src/main.tsx, Root.tsx, Root.test.tsx | 경로별 진입점과 기존 로그인 회귀 |
| services/chatbot/frontend/src/law-calendar/ | LawCalendarPage, MonthCalendar, EventList, ChangeDialog, types, dates, data, 개발 전용 demo, 범위 지정 CSS와 Vitest 테스트 |
| services/chatbot/tests/test_e2e_law_calendar.py | 비로그인 달력·모바일·키보드·상세·기존 인증 회귀 |
| services/chatbot/tests/test_web.py | /law-calendar fallback과 기존 보호 API 회귀 보완 |
| docs/plans/LAW_CALENDAR_PLAN.md | 실제 구조 차이, 적용 사항 및 결과 기록 |
| docs/evals/2026-11-05-law-calendar-phase1.md 및 README.md | 실제 검증 후 결과와 목록 기록 |

FullCalendar·Tailwind·shadcn은 우선 추가하지 않는다. 선택 중심의 월간 grid를 기존 스타일로 구현하되 날짜 키보드 이동(방향키, Home/End, 월 이동), 윤년·월 경계·포커스 처리를 완료 기준에 넣는다. 접근성을 만족하지 못하면 달력 라이브러리 채택을 다시 판단한다.

적용 사항:

- 월간 grid와 두 목록, 개발용 상태 미리보기, 더 보기, 전체화면 모바일 dialog와 데스크톱 2열 비교를 구현했다.
- 날짜 계산은 UTC 연산으로 KST 일자 문자열을 유지하고, 오늘은 Asia/Seoul에서 구한다. 날짜 클릭은 외부 수집이나 LLM을 호출하지 않는다.
- `import.meta.env.DEV` 분기 안에서만 demo를 동적 import한다. production은 빈 데이터를 사용하며 query string으로 예시를 켤 수 없다. production 자산에서 합성 원문·고유 마커 부재를 브라우저 테스트로 검사한다.
- 현재 diff는 원문을 그대로 보존한 단일 변경 구간 강조다. 항·호별 구조화 diff와 실제 법적 효력 검증은 후속 단계이며 이번 화면 타입은 공개 API 스키마가 아니다.
- StrictMode의 effect 정리 중 발생하는 native close 이벤트가 새 dialog를 닫던 문제를 수정했다. native modal에 명시적 Tab 경계 처리를 더해 주소창으로 포커스가 빠지지 않도록 했다.
- 기존 챗봇의 App.tsx, 인증·SSE·모니터링 런타임, 모델 서버 코드는 변경하지 않았다. main.tsx가 Root를 렌더링하며 /law-calendar(/) 외의 기존 경로는 App으로 간다.

로컬 UI 확인은 frontend 폴더에서 `npm ci`, `npm run dev`를 실행하고 Vite가 표시하는 개발 주소의 `/law-calendar`로 접속한다. API 서버와 모델 서버는 필요하지 않다. 개발 예시는 KST 오늘에 배치한다. 일반 production 빌드는 예시 없는 빈 화면이며 실제 API 연결은 Phase 2 범위다.

## 8. Phase 2 이후 DB/API 전략 초안

Phase 1 DB migration은 없다. 후속 단계에서는 별도 calendar DB를 사용해 monitor state.db와 큐 의미를 보존한다. schema version을 기록하는 순방향 마이그레이션과 적용 전 백업·복원 절차를 두고, 개발 DB는 운영 DB와 분리한다. WAL, busy timeout, FK, 인덱스는 운영 쓰기/읽기 방식과 함께 검증한다.

수집 측 adapter가 calendar의 laws/versions/article_versions/change_events/article_changes/source_snapshots/review_audit에 정규화·upsert한다. 웹은 공개 읽기 계약만 이용한다. 기본은 검토 대기이며 event와 article_change 양쪽 승인, 철회·정정 상태를 함께 필터링한다. processed_at을 승인으로 변환하지 않는다. 승인 후 원문·요약 수정 시 재검토가 필요하다.

월 집계·일 목록·상세는 같은 공개 조건을 사용한다. 한 이벤트가 여러 조문을 바꿀 때 notice_count는 예고 문서 수, effective_count는 당일 시행 조문 변경 수로 정의하는 안을 권장하며 Phase 2 계약에 명시해야 한다. 날짜/시각/날짜 미확정을 구분하고, 검증되지 않은 시행일은 달력에 배치하지 않는다. OpenAPI가 현재 비활성화되어 있으므로 타입 생성은 별도 schema export 또는 명시 TS 계약 테스트 방식으로 검토한다.

## 9. 검증 계획과 결과 기록

아래는 저장소 지원 환경의 검증 명령이다. Phase 0에서는 명령만 확인했고 Phase 1에서는 별도 Linux 테스트 venv로 동일 검사를 실행했다. 상세 환경 차이와 결과는 [검증 기록](../evals/2026-11-05-law-calendar-phase1.md)에 있다. fish에서 heredoc 없이 실행할 수 있다.

```fish
cd services/chatbot/frontend
npm ci
npm run typecheck
npm test
npm run build
cd ../../..
uv run pytest
uv run ruff check
uv run ruff format --check
uv run pyright
```

검증 항목: UI-01~04, 월 경계·윤년·KST 오늘, production 예시 차단, 키보드·dialog 포커스, / 직접 로그인·SSE·조문·피드백 회귀, 미등록 /api 404. 실제 모델 E2E·모델 스모크·품질 평가는 이번 범위에서 미실행이다. 명령 확인은 테스트 통과를 뜻하지 않는다.

| 단계 | 작업 브랜치/커밋 | 주요 변경 파일 | 테스트 명령 | 결과 | 미해결 |
| --- | --- | --- | --- | --- | --- |
| Phase 0 | codex/law-calendar-phase0-20261105 / 0030cda | docs/plans/LAW_CALENDAR_PLAN.md | 코드·문서 읽기 및 기준 커밋 재확인 | 실사 기록 완료 / 전체 완료 기준 미충족 | CONTEXT/첨부 파일 부재, 실제 이벤트 2유형 전체 추적 |
| Phase 1 | codex/law-calendar-phase0-20261105 / 이 결과 기록과 같은 구현 커밋 | 위 변경 목록 | npm typecheck/test/build, pytest, ruff, pyright, compileall | UI·로컬 검사 통과: frontend 32개, Python 753개(대역 E2E 포함) | 실제 API 미연결, 실제 모델 미실행, macOS CI 별도 확인, 기존 스케줄러 집계 검사 간헐 실패 이력 |
| Phase 2 | 미시작 | 미정 | 미실행 | 미실행 | 공개 DB·승인·조회 계약 확정 |
| Phase 3 | 미시작 | 미정 | 미실행 | 미실행 | 입법예고 키·응답 및 adapter |
| Phase 4 | 미시작 | 미정 | 미실행 | 미실행 | 조항별 효력·공개 배포·백업·이용 조건 |
| Phase 5 | 미시작 | 미정 | 미실행 | 미실행 | 기존 RAG 자동 갱신과 승인 정책 조정 |

## 10. 남은 결정

Phase 1 합성 UI 작업에는 대상 법령 전체 확정이 필요하지 않다. 실제 데이터 연결 전에는 공개 범위(기존 13개 폴더와 행정규칙/서식까지인지 노동법만인지), 입법예고 원천과 인증, 별도 공개 호스트/경로 프록시, CLI 승인 운영자, 요약 생성 정책과 이용 조건·고지 검토를 확정해야 한다. 현재 tailnet 제한을 임의로 해제하지 않는다.
