# 법령 변경 캘린더 Phase 1 UI 검증

- 작성일: 2026-11-05 (UTC)
- 브랜치 / PR: `codex/law-calendar-phase0-20261105` / #67
- 범위: 별도 서브도메인의 `/`에서 실행하는 독립 공개 UI. DB·조회 API·수집 연결은 후속 단계다.
- 계획: [LAW_CALENDAR_PLAN.md](../plans/LAW_CALENDAR_PLAN.md)
- 실행 안내: [services/law-calendar/README.md](../../services/law-calendar/README.md)
- 실제 모델 호출·모델 가중치 다운로드·학습/평가셋 수정·운영 배포·서비스 재시작은 수행하지 않았다.

## 최종 서비스 경계

사용자 지시에 따라 처음의 같은 사이트 `/law-calendar` 통합안을 대체했다. `services/law-calendar/frontend`는 별도의 package.json·lock·Vite 설정·main·CSS·정적 dist를 사용한다. 챗봇 소스나 CSS를 import하지 않고 챗봇 링크·세션 확인·인증 요청도 없다. 캘린더 서브도메인에는 이 정적 앱만 제공하면 된다. 실제 호스트명·DNS·TLS·호스팅 작업은 수행하지 않았다.

초기 통합 변경은 챗봇 폴더에서 제거했다. `git diff 77e7aa0 --exit-code -- services/chatbot`로 기준 main과 동일함을 확인했다. 기존 챗봇의 로그인, 모델 서버 연결, 쿠키, SSE, 피드백은 이번 PR에서 변경하지 않는다.

Phase 1 캘린더 실행에는 Node 또는 정적 호스팅만 필요하다. 후속 조회 API도 calendar 서비스에 별도로 추가할 계획이며 Mac mini 챗봇 프로세스나 모델·RAG API를 노출하지 않는다. frontend-only 서비스 폴더는 uv workspace에서 제외하고 독립 E2E 경로만 기본 pytest에 등록했다. `uv lock --check`가 통과하며 uv.lock은 바뀌지 않았다.

## 독립 검증

캘린더 브라우저 검사는 먼저 **pytest·httpx·Playwright만 설치된 최소 venv**에서 실행했다. 이때 Lawbot Python 패키지·MLX·torch와 모델 서버를 설치하거나 실행하지 않은 상태로 4개가 통과했다. 최종 테스트 서버는 개발·production 모두 Vite를 사용하며 production은 빌드된 dist를 preview로 제공한다. 테스트 도우미가 다른 서비스 패키지나 챗봇 fixture를 import하지 않는다.

확인한 항목:

- `/` 및 `/?demo=true`에서 바로 열리고 로그인 화면·인증 API 요청이 없다.
- 캘린더 방문 시 API 요청 0건, Set-Cookie 없음, 브라우저 쿠키 없음.
- production JS에 `/api/me`, `/api/login`, `/api/chat`, `lawbot_session`과 합성 원문·예시 마커가 없다.
- Vite 개발 예시의 날짜 선택, 빈 날짜, 더 보기, 로딩·오류·재시도·갱신 지연 표시가 동작한다.
- 모바일 단일 열과 데스크톱 병렬 목록·Before/After 비교, 가로 넘침 없음, 상세 초기 포커스·Tab 경계·Escape·포커스 복귀를 검증했다.
- 원문 줄바꿈·유니코드 보존, 원문 HTML 실행 방지, 비교 불가·신설·삭제 표시를 유지했다.

## 환경 및 결과

Linux 개발 체크아웃, Node 26, Python 3.14.8, Playwright 1.64.1 Chromium을 사용했다. 두 frontend의 lock은 독립적이며 챗봇 package/lock은 바뀌지 않았다. 전체 회귀 검사는 별도 venv에서 uv.lock 버전, torch 2.14.1 CPU 및 MLX 0.32.4 CPU backend로 실행한다. HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE을 설정해 운영 모델과 가중치 다운로드를 사용하지 않는다.

| 검사 | 서비스 분리 후 결과 |
| --- | --- |
| 캘린더 typecheck / Vitest / build | 통과, 단위 12개 |
| 챗봇 typecheck / Vitest / build | 통과, 기존 단위 19개 |
| 캘린더 독립 브라우저 E2E | 최소 환경에서 4개 통과 |
| uv lock --check | 통과, uv.lock 변경 없음 |
| ruff check / ruff format --check | 통과 |
| pyright / compileall | 통과 |
| 전체 기본 pytest | 분리 후 753 통과 / 기본 제외 12개 (92.44초). 단위·계약·통합·대역 및 독립 UI E2E 포함 |
| macOS GitHub CI | b185111은 752 통과 / production 정적 테스트 서버 준비 시간 초과 1건. 수정 후 확인 예정 |
| 실제 모델 E2E / 모델 스모크 / 품질 평가 | 실행 안 함 |
| 운영 반영 / DNS·일반 공개 배포 | 수행 안 함 |

전체 회귀 venv 구성 시 pydantic과 pydantic-core 버전 불일치 및 python-dateutil의 전이 의존성 six 누락으로 수집 실패가 있었다. 패키지 이름의 하이픈/밑줄을 정규화해 버전을 uv.lock과 일치시키고 six를 설치했다. uv pip check에서 호환성을 확인한 뒤 전체 검사를 재실행했으며 환경 오류 결과를 통과로 세지 않았다.

macOS CI의 b185111 실행에서는 Python 표준 라이브러리 production 정적 테스트 서버가 살아 있어도 준비 확인이 30초 안에 끝나지 않았다. 다른 752개는 통과했다. 준비 제한을 늘리거나 테스트를 건너뛰지 않고, 이미 CI에서 동작한 Vite의 정적 preview로 production 검사를 통일했다. 타임아웃에는 서버 로그도 포함하도록 수정했다. 이 서버는 테스트용이며 실제 배포에 Vite 프로세스가 필요한 것은 아니다.

## 이전 구현 검증 이력

초기 통합 커밋 `d7244c2`는 프론트엔드 32개·Python 753개 및 macOS CI가 통과했다. 이는 서비스 분리 후의 결과로 재사용하지 않는다. 당시 변경하지 않은 스케줄러의 `test_two_streams_interleave_within_limit`이 한 차례 `pending() == 0` 검사에서 2를 받아 실패했고, 단독·기준 코드 단독·최종 전체 재검사는 통과했다. 집계 갱신 시점에 따른 간헐 실패 가능성을 기록하며 해당 코드와 테스트는 수정하지 않았다.

날짜/페이지 테스트는 초기 미구현 상태에서 실패를 먼저 확인한 뒤 구현했고, 브라우저에서 발견한 StrictMode의 상세창 즉시 닫힘과 Tab 경계 이탈을 고쳤다. 이번 분리 작업에서도 챗봇 링크가 남아 있는 상태의 독립성 테스트 실패를 먼저 확인한 뒤 제거했다.

## 실행 및 후속 작업

캘린더만 개발하거나 빌드하려면 `services/law-calendar/frontend`에서 `npm ci`, `npm run typecheck`, `npm test`, `npm run build`를 실행한다. `npm run dev`로 표시된 로컬 개발 주소의 `/`에서 예시를 확인한다. 독립 E2E의 모델 라이브러리가 필요 없는 실행 명령은 서비스 README에 있다.

전체 회귀는 두 frontend를 각각 빌드한 뒤 루트에서 `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run python -m compileall -q services packages tools`로 실행한다. CI도 두 frontend를 각각 검증한다.

Phase 2 이후 공개 데이터·API·승인·실제 시행일을 검증해야 한다. 실제 법령 정보는 아직 연결하지 않아 production은 승인 데이터 연결 전 빈 화면이다. 고지문 법률 검토·재이용 조건 검토도 미완료다. PR 병합은 별도 사용자 명시적 승인 후에만 수행하고 운영 배포는 진행 중인 모델 학습 작업과 함께 추후 진행한다.

## 병합 후 CI 수정 검증

- 기준: PR #67 병합 커밋 d0fb7e7. 수정 브랜치: `codex/fix-law-calendar-ci-20261105`.
- 원 PR CI 41954818300과 병합된 main CI 41964288413 모두 같은 production 정적 테스트 서버 준비 시간 초과로 752 통과 / 1 오류였다.
- Python http.server 대신 Vite의 정적 preview로 테스트 서버를 통일하고 준비 실패 시 서버 로그를 포함했다. 검사 내용·30초 준비 제한·승인 데이터 보호 조건은 그대로다. 제품·챗봇·모델 서버 코드는 변경하지 않았다.
- README의 단계·후속 계획 설명은 PLAN으로 옮겼다. 이 절은 실제 검증 결과만 기록한다.

| 수정 후 검사 | 결과 |
| --- | --- |
| 캘린더 / 챗봇 typecheck·단위·build | 통과, 각각 12개 / 19개 |
| 전체 기본 pytest | 753 통과 / 기본 제외 12개 (106.21초), 독립 캘린더 E2E 4개 포함 |
| ruff check / format | 통과 |
| pyright / compileall / uv lock --check | 통과 |
| 챗봇 변경 없음·캘린더 코드 이동 완료 | 기준 main과 비교해 재확인 |
| 수정 PR의 macOS CI | 새 커밋 후 확인 예정 |

병합과 운영 배포, 실제 모델 호출은 수행하지 않았다.
