# Lawbot 전체 코드베이스 리뷰 및 개선 계획

## A. 검토 요약

2026-11-12 재검토에서 발견사항 33개를 개발 트리 `main`의 동일 커밋과 대조했다. B·D절의 전체 테스트 수치와 검토 이력은 최초 리뷰 기록이다. 재검토에서는 코드·테스트·잠금 파일·인용한 공식 문서를 확인하고 일부 동작을 합성 입력으로 재현했다. 재검토의 직접 실행 결과는 G절에 있다.

최초 리뷰는 2026-11-12T00:10:52+09:00에 시작했다. 검토 기준은 개발 트리 `~/Work/lawbot`, `main`, `193c8d9f789fc7a24a7a4a0ebcdfc3b6910cbf9a`다.

프런트엔드는 React 19.3.1, TypeScript 7.0.2, Vite 8.3.4다. 챗봇 SPA와 독립 법령 캘린더 SPA, 공유 UI가 있다. FastAPI 웹앱이 같은 origin의 SPA와 `/api/*`를 제공하고, 별도 MLX 모델 서버를 HTTP Bearer 인증으로 호출한다. 수집기는 별도 CLI다. 현재 코드의 Gradio 실행 경로는 확인되지 않았다. 모델 도구 실행, 일반 사용자 파일 업로드, 사용자 URL을 목적지로 삼는 챗봇 HTTP 기능도 확인되지 않았다.

주요 결과는 계산 금액 중복 반영, 타법 부칙에 의한 조문 만료 오판, 다른 법령 인용의 오채점, 피드백 저장 실패의 성공 처리, SPA 중복 추론·취소·로그아웃 상태 불일치, 수집기의 재등장 변경 누락과 누락 파일의 성공 처리다. 평가 지표 누락 및 실행 대상 없이 통과하는 테스트도 확인했다. 보안상 핵심은 본문·대기열 제한의 빈틈, 관리자 설정에 종속된 로그인 제한, 모델 응답의 외부 이미지 요청, upstream 오류 본문의 인증키 재노출 가능성이다. Vite는 공식 권고 영향 버전이며 개발 서버 노출 조건을 별도로 확인해야 한다. 확인된 결함, 조건부 위험, 유지보수 제안을 각 항목에서 구분했다. P0로 단정할 운영 침해 근거는 없다.

최초 리뷰에서는 Python 검사 1,319개, 별도 모델 API·revision 47개, 프런트엔드 61개가 통과했다. Ruff, Pyright, 양쪽 TypeScript 검사와 production build도 통과했다. 통과 수는 서로 다른 최종 대상들의 합이다. HTTP 통합 검사는 루프백 bind 권한 제한으로, 일부 모델 단위 검사는 MLX 초기화 중 종료로 완료하지 못했다. 대역 E2E, 실제 모델 E2E, 모델 스모크, 외부 네트워크 테스트, 전체 품질 평가는 실행하지 않았다.

**운영 모델 서버 사용 안 함.** 운영 HTTP 요청, 추론 시나리오 및 추론 HTTP 요청 수는 모두 0이다. 확인한 문제는 정적 분석과 합성 데이터·가짜 모델로 재현 가능했다.

**외부 공개 준비도는 배포 구성이 확인될 때까지 판정을 보류한다.** 챗봇을 공개할 경우 I절의 코드 보완과 원본 접근·인증·캐시·SSE 조건을 실제 공개 경로에서 확인해야 한다. 독립 캘린더의 공개 읽기 의도는 README에 명시되어 있다. 실제 도메인, 공개 대상과 접근 정책은 이 검토 범위에서 확인하지 않았다.

## B. 검토 범위 및 실행 기록

### 기준선과 검토 깊이

추적 파일 613개를 목록화했다. Python 230개, TS/TSX 53개, SQL 4개, CSS 5개, HTML 2개, Markdown 162개, CSV 97개 및 설정·잠금·매니페스트 등이 포함된다. 자체 작성 소스·테스트·실행 설정은 모듈별 정적 검토와 호출 경로 추적을 수행했다. 모든 라인의 모든 입력 조합을 검증한 것으로 해석해서는 안 된다. 세부 목록은 아래 모듈 검토 기록과 마지막 파일 목록에 남겼다.

| 영역 | 검토 내용과 깊이 | 동적 검증 및 한계 |
| --- | --- | --- |
| chatbot Python | 웹·인증·설정·API client·검색·리랭크·SSE·계산·캐시·DB·피드백·관리 API, 실제 진입점과 실패 경로 중심 정독 | 단위·계약, 합성 TestClient·MockTransport 재현. 실제 프로세스 통합 미완료 |
| chatbot SPA·공유 UI | 전체 TS/TSX·CSS·설정, 상태·이벤트·Markdown·SSE·admin 경계 | 기존 Vitest, 추가 표적 테스트, 변형 검사, 타입·빌드. 실제 브라우저 연결 미실행 |
| model-server | 인증 Guard·요청 schema·registry·scheduler·MLX wrapper·server/check | API·revision 가짜 모델 47개. GPU 관련 단위 환경 오류, 가중치 로딩 없음 |
| law-calendar | 독립 SPA·API·관리 토큰·DB·ingest·review·sync·공개 projection | Python·Vitest·타입·schema·빌드. 실제 서비스·브라우저 미확인 |
| monitor | CLI·collector·법제처·국회·Git·첨부·상태·요약·비밀정보 마스킹 | 가짜 응답·합성 저장소 재현 및 기존 단위. 외부 수집 실행 없음 |
| tools·common | CLI entry points·평가·채점·조문 생성·DB import/export·환경 파싱·API 계약 | 기존 회귀·데이터 정합성 테스트 및 선택 변형. 실제 평가 생성 없음 |
| 데이터 | CSV 헤더·ID·분할·매니페스트·대표 사례와 로더/테스트 연결 | 기존 데이터 검증 실행. 대규모 원문과 평가 답변 전수 법률 채점 제외 |
| 배포·CI·문서 | launchd 예시·CI 전체, README·architecture·operations·현재 계획·최근 평가 기록 | 실행 명령과 주장 대조. 배포·재시작·DNS·방화벽 변경 없음 |
| 과거 문서·생성물 | archives/evals 목록·검색과 관련 문서 선택 정독 | 전체 162 Markdown과 97 CSV의 문장별 전수 정독은 하지 않음 |

가상환경·node_modules·가중치·캐시·운영 DB·개인 로그는 소스 전수 리뷰 대상에서 제외했다. 의존성은 잠금 버전, 필요한 구현의 기본 동작과 공식 권고를 확인했다. 운영 트리의 실제 프로세스·환경변수는 조사하지 않았다. `git worktree list`에서 운영 worktree가 detached `91715a0`을 가리키는 것만 확인했고, 이것을 실행 중 프로세스 버전의 증거로 사용하지 않았다.

### 실제 실행 진입점과 SPA 구조

| 실행 이름 | 실제 진입점·책임 |
| --- | --- |
| lawbot-app | services/chatbot/pyproject.toml → lawbot.web:main, env/config 검사·load_app·FastAPI/SPA |
| lawbot-model-server | services/model-server/pyproject.toml → lawbot_model_server.server:main, 설정·모델 registry/scheduler·API |
| law-calendar | services/law-calendar/pyproject.toml → law_calendar.server:main, 공개 조회/관리 API와 독립 SPA |
| law-monitor | services/monitor/pyproject.toml → law_monitor.cli:main, 수집·상태·요약·daily 작업 |
| lawbot-db 및 lawbot-eval-v4/reindex/build-articles/articles-check/articles-update/max-seq/qa-split | 각 pyproject의 명시 CLI. DB/데이터·평가·갱신은 tools 및 db 모듈의 독립 수동 진입점 |

- 챗봇은 `services/chatbot/frontend/src/main.tsx → App.tsx → Chat/useChatStream`로 진입한다. React 19.3.1, TypeScript 7.0.2, Vite 8.3.4, Vitest 5.0.5, npm package-lock을 사용한다. Router, 전역 store, context 라이브러리는 없다. 로그인 확인과 관리자 화면 전환은 App의 상태로 처리한다. 두 SPA 모두 StrictMode를 사용한다. 개발 effect 재실행과 이번에 확인한 이벤트 재전송 결함은 다른 경로다.
- 독립 공개 캘린더는 `services/law-calendar/frontend/src/main.tsx → LawCalendarPage`이다. `credentials: 'omit'`으로 공개 이벤트와 보류 목록을 읽는다. 보류 목록은 검토 중인 입법예고의 제한된 요약 필드도 제공한다. 관리자 캘린더는 챗봇의 `/api/admin/calendar/*`를 사용한다. 두 프런트는 `packages/lawbot-ui/src`의 변경 상세·달력·목록·모달·날짜 함수를 공유한다.
- 챗봇 API URL은 같은 origin의 `/api/*` 상대 경로다. 인증정보를 localStorage/sessionStorage나 URL에 기록하는 코드를 발견하지 않았다. `fetch`의 기본 same-origin cookie 전달을 사용한다. 서버용 모델 토큰을 SPA에서 읽는 경로도 없다. 설정은 로그인 후 `/api/config`로 받는다.
- 스트림은 EventSource가 아닌 POST fetch streaming이다. `TextDecoderStream`으로 UTF-8 chunk를 해석하고 자체 SSE parser에 보낸다. 자동 재연결·재시도는 없다. 정상 LF chunk 분할, 정상 종료, 수동 stop, 401/400, 네트워크 실패, done 없는 EOF 테스트가 있다. unmount·재진입·parser 예외 시 reader cleanup·timeout 검증은 없다.
- `react-markdown` 10.1.0 + remark-gfm + remark-cjk-friendly로 답변을 표시한다. raw HTML을 활성화하는 rehype-raw와 dangerouslySetInnerHTML은 없다. 합성 `<script>`와 javascript URL 검증에서 script element/실행 가능한 URL이 생성되지 않았다. 외부 이미지 허용은 별도 위험으로 아래에 기록했다.
- Vite dev host는 두 설정 모두 loopback이다. `allowedHosts: true`, 광범위 CORS, SSR/custom middleware 설정을 발견하지 않았다. `LAWBOT_WEB_URL`, `LAW_CALENDAR_API_URL`은 Vite 개발 proxy의 서버 설정이다. `VITE_*`로 비밀을 읽는 자체 작성 소스는 없다. production build sourcemap은 false다. 독립 캘린더의 합성 demo는 `import.meta.env.DEV`로 제한되고 실제 임시 production 산출물에서 `LAW_CALENDAR_SYNTHETIC_ONLY` marker가 없었다.
- 챗봇 `web.mount_spa`는 `/assets`와 dist 내부 파일, 그 외 index fallback을 제공한다. `/api` 및 `/api/*`의 없는 경로는404다. 독립 캘린더는 StaticFiles(html=True)를 mount하며 현재 URL 라우터가 없으므로 루트 화면이 정상 지원 진입점이다. 새로운 path route를 요구하는 구조로 추측하지 않았다.
- frontend build fingerprint에는 shared source도 포함된다. TypeScript는 noEmit/noUnusedLocals/noUnusedParameters/strict다. API의 `as Config`, `as Sources` 등은 런타임 검증이 아니다. 다만 현재 서버의 일치하는 payload와 별개로 이를 곧바로 현재 오류로 분류하지 않았다.
- CSS·아이콘·정적 HTML을 포함한 자체 작성 프런트 파일을 읽었다. package-lock은 버전·직접 의존성과 lifecycle 메타데이터 중심으로 확인했다. 외부 node_modules의 전체 구현은 제외했다. `public/`의 추적 파일은 두 SPA 모두 없다.

챗봇의 일반 POST도 application/json을 요구하며 쿠키는 SameSite=Strict다. 관리 POST에는 별도의 정확한 Origin 검사도 있다. 현재 앱에 광범위 CORS를 여는 설정은 확인되지 않았다. 이 실제 브라우저 인증 구조를 기준으로 판단했으며, 모든 API에 새로운 CSRF 프레임워크를 일괄 요구하지 않는다.

### Git 이력 범위

출발점은 `git log -20`의 아래 20개 커밋과 시작 작업 트리다. 각 커밋의 이름·변경 경로와 관련 구현 diff를 검토했다. 20개 커밋 자체 diff에서 73개 경로가 나타났다. merge가 포함되므로 `HEAD~19`는 같은 범위가 아니다. 추가로 `HEAD~19..HEAD`의 163개 변경 경로 개요를 확인하여 캐시·캘린더의 앞선 구현을 추적했다. 소스 위치의 근거는 현재 HEAD 작업 트리를 기준으로 한다.

```text
193c8d9 48cea85 91715a0 bc61de1 efd7bf0 acb3d7e 5c519ac 48deec0
798ccea ea1171d 06b1c69 6933eed 4f06551 eb9e5e0 5900e9d 3ec1f9d
f20f993 0091b7b 59d594c 0eac5af
```

계산 라우터의 금액 span 변경 `5900e9d`, 결론 보완 `f20f993`·`0091b7b`, 조문 유효기간 `6933eed`·`ea1171d`, 평가 후보 기록 `798ccea`, 확장 조문 `59d594c`를 현재 구현과 연결했다. 추가 `git log -20 --no-merges`에서 `6ebda78`, `ff4f2ac`, `77a60d7`, `3658c08`, `26d4dcd`, `c95997e`, `2526959`의 제목·연관성을 확인했다. 모든 과거 커밋을 전수 리뷰했다는 주장은 하지 않는다.

### 최초 리뷰의 실행 환경과 명령

macOS Apple Silicon, zsh, Python 3.14.8, pytest 9.1.1, Node v26.13.0, npm 11.20.0, uv 0.13.6, Git 2.56.0을 사용했다. Python의 설치된 의존성을 재사용했다. 루트 lock의 FastAPI 0.143.3, Starlette 1.8.0, Uvicorn 0.54.0, HTTPX 0.28.1, Pydantic 2.14.1, OpenAI SDK 3.42.0, MLX 0.32.4/MLX-LM 0.32.2를 확인했다. 캘린더 별도 lock은 Starlette 1.10.0, Pydantic 2.14.2다. 캘린더 검사는 루트 기존 환경을 사용하므로 별도 lock 환경과 동일하다고 할 수 없다.

아래 `COPY`는 `/tmp/lawbot-review-20261112/repo`다. tracked 파일을 복사하고 `.env`, 운영 데이터, 가중치를 제외했다. 평가 fixture인 tracked results는 필요한 후속 검사에 추가했다. Git ignore·commit 조회를 위해 temp Git metadata를 복사하고 object store는 읽기 참조했다. 새 branch/worktree를 등록하지 않았다. `PYTHONPATH`는 COPY의 여섯 Python source 디렉터리로 지정했고 `PYTHONDONTWRITEBYTECODE=1`, `LAWBOT_ENV_FILE=''`를 사용했다. 로그·pytest cache·basetemp는 `/tmp/lawbot-review-20261112/` 아래에 두었다.

| 명령/대상 | 종료와 결과 | 판정 |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q -m 'not models and not network and not e2e_live and not e2e_stub and not integration'` | MLX scheduler 테스트 수집 중 abort | 실행 환경 오류, 통과 수로 사용하지 않음 |
| 위 명령에서 ms_models/ms_scheduler/ms_real_models 파일 제외 | registry.free_memory 호출 시 MLX abort | 실행 환경 오류, 중간 점 진행률을 통과 수로 사용하지 않음 |
| chatbot/monitor/calendar/common 네 test 디렉터리, 같은 marker 제외 | 1300 passed, 21 failed, 54 deselected | 19개는 COPY의 results/Git 조회 자료 누락, 2개는 bind EPERM |
| 자료를 보완한 뒤 동일 cache의 `--lf`, `--ignore=services/model-server`, `-k 'not test_port_from_env and not test_port_same_as_model_server_rejected'` | 19 passed, 2 deselected, exit 0 | 최종 위 네 영역의 검증된 기존 테스트 1319개 |
| 모델 API·revision, `-k 'not test_lru_switch'` | 47 passed, 1 deselected, exit 0 | MLX 해제 경로 1개 제외, 가짜 모델 |
| `pytest -q services/chatbot/tests/test_web_integration.py` | 2 failed, 5 errors, exit 1 | 전부 localhost bind EPERM, 제품 assertion 도달 전 환경 오류 |
| `ruff check --no-cache`, 추적 Python 경로 230개를 명시한 최종 재검사 포함 | exit 0 | 통과 |
| `ruff format --check --no-cache` | 초기 전체 대상392개, 최종 추적 Python230개 명시 재검사 모두 formatted, exit 0 | 통과, 쓰기 없음. 최종 소스 범위는 추적230개 |
| `pyright`, `pyright services/law-calendar/src` | 각각 0 errors/0 warnings, exit 0 | 통과 |
| `python -m law_calendar.schema --check` | exit 0 | 통과 |
| 추적 Python 파일에 `ast.parse` | 230개 구문 분석 성공, exit 0 | 파일 쓰기·import 없음 |
| 두 frontend의 `npm run typecheck`, `npm test`, `npm run build` | chatbot 42, calendar/shared 19 passed, 모든 명령 exit 0 | 외부 복사본에 산출, 원본 dist 보존 |
| `npm test -- src/review-probes.test.tsx` (격리 복사본에만 생성) | 7개 중 6 failed/1 passed, exit 1 | 각 오류 조건에 대한 기대 assertion 실패, F012/F013/F015/F016/F017 및 부가 안전 렌더링 조건. 기존 기준61개와 별도 |
| `npm test -- src/App.test.tsx -t 'review:'` (격리 복사본에만 추가) | 표적 1 failed, 기존 25 skipped, exit 1 | F014 로그아웃 실패 안내 assertion 실패. 25개는 이름 필터 제외 |
| C절 나머지 표적 검증·변형 | 각 항목에 정상/변형 및 실패 이유 명시 | 가짜 의존성·합성 데이터 |
| e2e_stub·e2e_live·models·network·전체 품질 평가 | 실행 안 함 | bind/MLX 환경 제약 또는 운영 호출 불필요 |

기존 기준 실행의 최종 대상에 skip, xfail, xpass로 보고된 항목은 없다. 프런트엔드 표적 logout 재현의 필터에서 제외된 25개는 Vitest의 skipped 표기였으며 통과 수에 포함하지 않았다. deselected는 실행되지 않은 항목이다. CI는 macOS 환경에서 frontend build와 Chromium 설치 후 기본 pytest를 실행하며 모델·network·live를 제외한다. 이 리뷰에서 그 CI 환경 전체를 재현하지 못했다. 기본 가짜 모델 테스트에도 MLX array/GPU 초기화를 요구하는 파일이 있으므로 “모델 가중치 불필요”와 “MLX 실행 환경 불필요”를 구별해야 한다.

### 확인된 방어와 적용 한계

로그인 필수 설정 누락 시 웹 시작이 거부되고 모델 서버에 접속하려면 토큰이 필요하다. 챗봇의 채팅·피드백·설정 조회는 서버 세션을 확인한다. 관리자 POST는 role, same-origin, JSON, 64KiB streaming body, 요청 제한 및 멱등키를 거친다. feedback turn은 세션 ID와 연결된다. 세션은 메모리 저장, 7일 TTL, 최대 200개이며 쿠키는 HttpOnly·SameSite=Strict이고 HTTPS로 인식한 요청에는 Secure가 붙는다. SPA는 토큰을 localStorage/sessionStorage에 저장하지 않는다.

모델 공통 schema(`packages/lawbot-common/src/lawbot_common/api.py`)는 max_tokens 32,768, embedding input 배열 4,096개, rerank documents 1,000개, max_seq_length 32,768을 상한으로 둔다. 웹 답변 생성은 별도의 기본 생성량을 사용하며 사용자가 모델 서버 파라미터를 그대로 지정하는 API가 아니다. 이 상한의 존재와 현재 장비에서 안전한 한 요청 비용·대기열 정책은 별개다. 임베딩/리랭크 문자열 하나의 비용과 body 8MiB의 실제 누적 적용은 F018/F019와 함께 판단한다.

설치된 Uvicorn 0.54.0의 `uvicorn/config.py:225,362-365,543-544`를 읽어 proxy_headers=True, FORWARDED_ALLOW_IPS 미설정 시 trusted source가 loopback 두 주소인 기본 동작을 확인했다. 실제 운영 프로세스의 환경변수 값을 읽거나 이 기본값이 적용됐다고 단정하지 않았다. 앱이 CF-Connecting-IP 헤더만으로 인증하는 경로는 확인되지 않았다.


합성 정적 파일 검증에서 `/`, `/deep/link`, `/docs/code_review.md`, 외부 파일 symlink, percent-encoded 상위 경로 요청은 합성 비밀 파일을 반환하지 않았다. 미등록 `/api/missing`은 404였다. 챗봇의 HTML fallback 때문에 `/docs/...`가 200이어도 반환 내용은 SPA HTML이다. 이 보고서는 정상 dist 제공 경로에 포함되지 않는다. 실제 별도 정적 서버나 잘못된 reverse proxy root는 저장소 밖 확인 사항이다.

현재 tracked 파일과 최근 20개 diff에 대해 private-key, GitHub token, 일부 API key/AWS access-ID 패턴을 값 출력 없이 검사했고 후보는 0이었다. `.env`와 `services/monitor/.env`의 경로 이력도 없었다. 실제 .env 값, 전체 과거 Git blob, 무시된 개인 로그·DB를 검사하지 않았으므로 모든 비밀정보의 부재를 보증하지 않는다. `yaml.safe_load`를 사용하며 검색 범위에서 제품의 임의 eval/exec·shell=True·pickle 로딩 경로는 확인되지 않았다.

## C. 발견사항 상세

읽기 순서는 계산·인용·조문 생성(F001/F031/F032), 데이터 저장·수집(F002/F021/F022), 인증·요청 수명·자원 제한(F003/F004/F012–F020), 평가·테스트 보장(F007–F009/F029/F030) 순으로 권장한다. 나머지는 해당 수정에 연결된 경계 또는 유지보수 항목이다.

우선순위는 현재 코드의 영향으로 평가했다. P1은 영향이 큰 결함, P2는 일반 결함 또는 중요한 개선, P3는 작은 개선이다. 외부 공개 차단 여부는 별도로 표시한다. 보안 항목의 “현재”는 코드 경로의 존재를 의미하며 실제 인터넷 노출의 확인을 의미하지 않는다.
### F001. 계산 입력의 서로 다른 발췌 문자열이 같은 금액 구간을 재사용한다

- 분류/판정/우선순위: 버그, 확인된 결함, **P1**. 확신 높음: 실제 `route_calculation`을 가짜 classifier로 실행하여 잘못된 확정 계산을 재현했다. 실제 모델이 해당 JSON을 얼마나 자주 내는지는 측정하지 않았다.
- 위치: `services/chatbot/src/lawbot/calc_router.py:263-279`, `route_calculation`; `services/chatbot/src/lawbot/calc_numbers.py:126-150`, `complete_amount_span`. `docs/archives/calc-router.md:55-59`도 겹치는 span 재사용을 남은 문제로 기록한다.
- 원인: 같은 문자열의 횟수만 Counter로 합산한다. `700만원`과 `700만`은 서로 다른 키이므로 원문의 한 금액을 두 필드에 할당해도 각각 유효한 전체 금액 발췌라고 통과한다. 추출 문자열 검증은 있지만 원문 위치의 배타성이 없다.
- 재현 입력: `2026년 총급여 5천만원, 일반 의료비와 난임 시술비 합계 700만원이면 의료비 세액공제는 얼마인가요?`. 가짜 classifier 반환: `{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5천만원","general_expenses":"700만원","infertility_expenses":"700만","year":"2026년"}}`.
- 호출은 `route_calculation(question, lambda _: json.dumps(data), {MEDICAL: ()}, reference_year=2026)`이다. `status='calculated'`, `reason='selected'`, `results=[{'label': '의료비 세액공제', 'value': 2925000}]`를 재확인했다. 이 합성 사례에서는 일반 의료비와 난임 시술비에 각각 700만원이 배정됐다. 합계만 주어졌으므로 구분별 금액을 요청하는 안내가 필요하다.
- 최소 수정은 각 입력에 서로 겹치지 않는 원문 금액 구간을 할당할 수 있는지 확인하는 것이다. 독립 구간이 부족하거나 금액의 의미가 모호하면 안내한다.
- 유지 계약: 서로 다른 원문 위치에 같은 금액이 실제 두 번 있는 질문은 허용. 통화 단위 경계·날짜 검증·모호한 입력 fail closed 유지.
- 회귀: `test_calc_amount_spans.py`, `test_calc_router_ambiguity.py`에 위 실패 사례, 공백/원 접미사 차이, 같은 숫자가 독립 위치에 두 번 존재하는 정상 사례 추가. 기존 동일 문자열 중복 테스트만으로는 이 변형을 막지 못한다. `test_calculators.py`의 수식 테스트는 보존.
- 공개 관련: 보안 권한 상승은 아니다. 현재 사용자도 잘못된 확정 계산을 받는다. 계산 기능을 외부에 제공하기 전 수정 권장(정확성 차단 항목). 다른 발견사항과 독립.

### F002. 피드백 저장 실패를 성공으로 반환하고 즉시 재시도까지 중복으로 처리한다

- 분류/판정/우선순위: 예외 처리·데이터 손실, 확인된 결함, **P2**, 확신 높음.
- 위치: `services/chatbot/src/lawbot/feedback.py:234-256,282-301`의 `duplicate`, `save_row`, `write`; `services/chatbot/src/lawbot/feedback_store.py:56-62`의 `write`; `services/chatbot/src/lawbot/web.py:550-568`의 `feedback_`.
- 흐름: `save_row`가 저장 전에 recent 중복 상태를 기록 → `write`가 JSONL OSError/DB sqlite 오류를 경고만 하고 반환 → row를 정상 반환 → API가 `saved:true`를 반환한다. 최근 요청 상태가 이미 기록되어 바로 재시도하면 `saved:false`다.
- 격리 재현: 임시 디렉터리의 존재하지 않는 DB를 `Feedback(db=...)`로 설정. 실제 Sessions로 로그인 후 `remember_turn`한 합성 턴에 `/api/feedback` POST. 저장소 경고 발생, 응답 `{"saved":true}`; 같은 요청 재시도 `{"saved":false}`. 실제 운영 DB는 열지 않았다.
- 영향: 검토·교정용 기록이 유실된다. `App.tsx`는 선택 상태를 즉시 바꾸고 HTTP 오류가 날 때만 되돌린다. 응답의 `saved` 값은 읽지 않으므로 저장에 실패해도 사용자의 선택이 화면에 남는다.
- 최소 수정: write 성공 여부 또는 구체적 저장 실패 예외를 전달하고, commit 성공 뒤에 dedup 상태를 확정. API는 내용 없는 안전한 5xx/재시도 가능 오류를 반환한다. 오류 로그에는 질문/응답/인증정보를 넣지 않는다.
- 회귀/완료: JSONL 쓰기 실패와 DB write 실패 각각 `saved:true`가 아님, 정상 복구 후 동일 요청이 저장됨, 정상 중복 억제는 유지. `test_feedback.py`, `test_web.py` 보강. 기존 성공 경로·동시 append 테스트 유지. F003와 저장 경계는 별개.

### F003. 일반 JSON API의 본문 크기와 웹앱 채팅의 대기 요청 수·시간에 상한이 없다

- 분류/판정/우선순위: 보안·자원 사용, 확인된 결함(구현상 제한 누락), **P2**. 인터넷 노출 여부는 미확인이다.
- 위치: `services/chatbot/src/lawbot/web.py:379-383`의 `read_json`, `386-401`의 `valid_chat`, `487-489`의 login, `522-548`의 chat/stream, `550-568` feedback. 비교: 관리자 POST는 `read_admin_body:355-362`에서 stream byte 제한을 실제 시행한다.
- 근거: 로그인·채팅·피드백의 `read_json`은 본문 전체를 읽어 파싱한다. 채팅의 질문 2,000자 검사와 모델·형식 검사는 파싱 뒤에 실행된다. `stream`의 semaphore는 동시 실행 수를 제한하며 웹앱의 대기 요청 수·대기 시간에는 상한이 없다.
- 검증: 가짜 generator를 연결한 실제 앱에 작은 유효 question과 70KiB `unused` 문자열을 POST하여 HTTP200과 generator 1회 호출 확인. 이는 제한 누락을 보인 최소 표본이며 부하·퍼징은 안 했다. 대기열은 정적 경로 확인만 수행했다.
- 영향 환경/주체: 현재 접근 가능한 내부 사용자(로그인 본문은 인증 없는 네트워크 접근자); 제한 공개에서는 허용된 사용자; 불특정 공개에서는 각 네트워크 접근자. 메모리/파싱 CPU/열린 연결/추론 용량에 영향. 로그인 전에 추론이 실행된다는 결론은 아님: chat의 `Who` 인증 dependency는 모델 전 수행된다.
- 공개 전 필수 해결: SPA에서만 제한하지 말고 API에 stream byte 상한·필드 타입/길이·대기 수/대기시간·명확한 413/429/503을 둔다. 현재 장비 측정에 맞는 값은 별도 결정. Cloudflare rate limit은 보조이고 단일 요청 비용 제한을 대체하지 않는다. 이 문서는 새 유료 제품 도입을 요구하지 않는다.
- 회귀: 모델 spy를 써서 거대/초과 요청은 모델 호출 전 거부, 정상 작은 입력은 허용; 작은 fake gate로 대기 포화·timeout·취소 해제 검증. 운영 부하 시험 없이 가능.
- 해결 위치: API 백엔드 우선, 선택된 ingress body/rate 제한은 외부 설정 확인. 원본 네트워크 범위 확인 필요. F004와 함께 admission 경계 정리하되 관리자 origin/권한 검증을 제거하지 않는다.

### F004. 관리자 계정이 없는 지원 구성에서 로그인 시도 제한이 작동하지 않는다

- 분류/판정/우선순위: 보안, 확인된 결함, **P2**, 확신 높음. 현재 인터넷 공격 가능 여부와 별개.
- 위치: `services/chatbot/src/lawbot/web.py:442-454` failures 구성, `463-484` verify_login, `487-500` login. 특히 `494`의 `if admin is not None`.
- 원인: 기본 사용자 비밀번호 비교는 항상 하지만 실패 횟수 제한은 관리자 비밀번호 검증 helper와 묶여 있다. admin 미설정은 정상 지원 모드이므로 이 경우 1초 sleep 외 횟수 제한이 없다. sleep은 비동기 병렬 요청을 막지 않는다.
- 안전한 재현: `create_app(..., admin=None)` + 합성 자격증명 + 인프로세스 TestClient. 테스트 시간만 줄이려고 `LOGIN_FAIL_DELAY=0` 메모리 patch. 같은 합성 user에 여섯 번 틀린 pw 모두401; 뒤의 올바른 pw200. 운영 서버 요청 없음.
- 기존 보호: 관리자 계정 구성의 `test_web.test_login_limit_expires_and_counts_by_user`는 실제 login 경로를 검사하며 유지할 가치가 있다. 그 fixture가 admin_app여서 기본 구성 누락을 놓친다.
- 최소 수정: 일반/관리자 둘 다 공유하는 rate admission을 admin hash 검증과 분리. 실패창/해제/시간 경계 동작 유지. 실사용자 계정 하나를 전역 잠그는 현재 정책의 서비스 거부 가능성도 실제 공개 대상에 맞게 판단하되 자체 보안 프레임워크 확장 불필요.
- 회귀: admin=None/설정 모두 실패 횟수 경계429, cooldown 후 복구, 실패 때 세션 미발급. fake clock 사용. 악성/실제 계정 brute force 금지.
- 공개 전: 비밀번호 login을 노출하면 필수 수정. 검증된 upstream 접근 제어로 보호하기로 정할 경우 원본 우회 차단과 보호 경로의 누락을 외부 설정에서 입증해야 한다. Access 미도입 그 자체를 현재 취약점으로 부르지 않는다.

### F005. 잘못된 model 타입이 400 대신 500을 발생시킨다

- 분류/판정/우선순위: 입력 검증·예외 처리, 확인된 결함, **P2**, 확신 높음.
- 위치: `services/chatbot/src/lawbot/web.py:386-401` `valid_chat`, 특히397. 질문은 string 검사하지만 model은 사전 membership 전에 타입 검사하지 않는다.
- 재현: 정상 로그인 후 `{"question":"synthetic","model":[],"format":"concise"}`로 실제 TestClient POST →500(TypeError unhashable list). `{}`도 같은 경로. 모델 호출 전이며 인증 우회는 아니다.
- 최소 수정: model str 확인 후 허용 key 검사(기존 작은 구조로 충분); 빈 model/format/잘못된 JSON root 각각 기존 오류 계약 유지. 런타임 검증을 프런트 TS 타입으로 대신하지 말 것.
- 회귀: list/dict/int/null/unknown str table 테스트,4xx와 generator 호출0 확인. `test_web.py` 입력 테스트 보강. F003 본문 제한과 한 세션에서 처리 가능.

### F006. 모델 응답의 index 의미를 검증하지 않아 음수 rerank index가 정상 점수로 수용된다

- 분류/판정/우선순위: API 계약/검색 정확성, 확인된 결함, **P2**. 잘못된 응답 처리 결함은 확실하나 현재 정상 모델 서버가 이런 데이터를 만든다는 근거는 없다.
- 위치: `services/chatbot/src/lawbot/api_client.py:150-161` `ModelServerClient.rerank`; `packages/lawbot-common/src/lawbot_common/api.py:114-117` RerankResult. 응답 index는 제한 없는 int이고 `scores[item.index]` 대입.
- 재현: `httpx.MockTransport`가 documents2개 요청에 results `[index0,score0.9],[index-1,score0.1]` 반환 → `[0.9,0.1]`로 정상 수용. Python 음수 인덱싱으로 잘못된 upstream 문서를 마지막 문서와 연결한다. 범위 초과는 IndexError가 된다. 중복 때문에 빈 슬롯이 생기면 `None` 검사에서 실패하지만 모든 슬롯이 채워진 상태에서 중복 응답이 추가되면 마지막 점수가 채택된다.
- 인접 경계: embed의 `services/chatbot/src/lawbot/api_client.py:143-148`도 index 정렬만 하고 요청 입력 수와 index의 정확한 순열 일치 검증 없음. 잘못된 base64/빈 data가 통일된 모델 서버 형식 오류로 변환되는지 별도 보강 대상. 이 인접 변형들은 이번에 전부 재현하지 않았다.
- 수정: index가 `0..len(documents)-1`의 정확한 순열인지 먼저 확인 후 score 연결. embed에서도 요청 수/index 매칭, vector 길이·유한성의 필요한 계약을 명시. 허용된 OpenAI 추가 필드는 유지. 외부 의존성 격리 seam은 제거하지 않는다.
- 테스트: `test_api_client.py`의 구조 오류 검사 유지, 음수·범위 밖·중복·누락·정상 역순 응답 추가. 가짜 서버면 충분. 공격자가 모델 서버 자격 없이 이 경로를 조작한다는 주장은 하지 않음.

### F007. 평가 메타데이터의 변경 검사가 이전 소스 경로에 머물러 있다

- 분류/판정/우선순위: 평가 재현성·규칙 정합성, 확인된 결함, **P2**, 확신 높음(명확한 git pathspec).
- 위치: `tools/lawbot-tools/src/lawbot_tools/eval_v4.py:780-798` `git_state`, `801-843` run_meta.
- 근거: `git status --porcelain -- src data` 실행. 현재 자체 코드는 `services`, `packages`, `tools`에 있으므로 해당 코드·테스트만 수정한 실행을 `dirty:false`로 기록한다. HEAD는 동일하더라도 결과를 만든 실행 코드가 다를 수 있다.
- 검증: 소스 경로/실제 git 인자 정적 확인. 원본 변경이나 새 branch/worktree를 만들어 재현하지 않았다. 성능 평가/실제 모델 호출하지 않았다.
- 최소 수정: 전체 작업 트리 또는 명시된 현 모노레포 실행 영향 경로/lock/config를 포함. untracked 로그/결과 때문에 오염되는 것을 피할 경우 어떤 범위를 제외했는지 metadata에 기록. shell 메타문자나 env 값을 기록하지 않는다.
- 회귀: subprocess mock으로 명령 계약 검증에 더해 격리 fixture status가 서비스 파일 변경을 보고할 때 dirty=True 확인; 다음 코드 세션에서 임시 git fixture를 허용하면 실제 경로 테스트 가능. 기존 커밋 기록/실패시None 계약 유지.

### F008. 답변 비교 도구가 빈 결과 두 개를 성공으로 처리한다

- 분류/판정/우선순위: 평가 검증, 확인된 결함, **P2**, 확신 높음.
- 위치: `tools/lawbot-tools/src/lawbot_tools/diff_answers.py:73-81` `main`.
- 재현: 저장소 밖 `a.csv`, `b.csv` 모두 `id,answer` 헤더만 작성 후 `main([a,b])`. 출력 `겹치는 id가 없습니다`, `같음 0/0`이지만 SystemExit0. command wrapper는 정상 종료하므로 종전 수치 보존용 검사(모듈 주석의 용도)가 잘못 성공한다.
- 수정: n>0 필수, 예상 평가 ID집합/완주 수를 알 수 있다면 옵션으로 검증. 비어 있는 두 파일의 수학적 동등성 자체가 문제가 아니라 평가 성공 exit 계약의 문제.
- 회귀: `test_diff_answers.py`에 header-only 양쪽/한쪽/no-common, 정상 allsame/차이/ID 누락 검증. 기존 `test_saved_results_equal_themselves`는 실제 CSV 파싱·기대행수 검사(200/122/200)를 하는 양성 대조군이므로 삭제하지 않는다.
- F009와 평가 결과 완전성 개선으로 함께 다루되 production 도구와 테스트의 독립 원인 구분.

### F009. 기록 재현 회귀 테스트 5개가 결과 행이 0개여도 통과한다

- 분류/판정/우선순위: 테스트 실효성, 확인된 보호 누락, **P2**(테스트 보강), 확신 높음.
- 위치: `services/chatbot/tests/test_scoring.py:58-62` test_refusal_rule_matches_recorded_results(2개);165-175 test_scorer_reproduces_recorded_scores(3개).
- 보호/누락: 답변의 채점 판정이 바뀌면 검출한다. 필요한 헤더를 갖춘 결과가 0행이면 `.all()`은 참이고 `iterrows()`에는 검사할 행이 없다. 파일이 없거나 헤더가 없어 CSV 읽기 자체가 실패하는 경우는 별개다.
- 정상 실행: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest services/chatbot/tests/test_scoring.py -k 'scorer_reproduces_recorded_scores or refusal_rule_matches_recorded_results' -q -p no:cacheprovider --basetemp=/tmp/lawbot-backend-baseline` → **5passed,38deselected**,0.33s.
- 변형 실행: `/tmp/lawbot_empty_results_plugin.py`의 `pytest_runtest_setup(trylast=True)`에서 해당 module의 results 함수를 `lambda name: original(name).iloc[:0]`로 메모리 교체. 위 명령에 `PYTHONPATH=/tmp -p lawbot_empty_results_plugin`, 별도 basetemp 추가 → **5passed,38deselected**,0.30s. import/환경 실패가 아닌 기존 assertion 통과임.
- 한계: 이것은 이5개 테스트의 빈 결과 감지 실패만 증명. 다른 edgecase 테스트는 특정 ID 접근으로 일부 파일 누락을 감지할 수 있으며 전체 suite가 빈 데이터에 통과한다고 주장하지 않는다. 모범답안650개 점수 테스트는 len650+min 검증이 있으므로 같은 결함 아님. 오래된 scorer의 다른 법 동일 조번호 허용은 이름 자체가 known_limit인 역사적 기준선이며 현재 v4 scorer의 결함으로 확대하지 않는다.
- 방향: **유지+보강**. 결과별 예상 ID집합/행수 및 중복 검사를 넣고 현재 실제 score assertion 유지. 삭제/통합하면 과거 수치 재현 보호를 잃음. 최소 count만으로는 일부 누락/다른 ID 치환을 못 잡으므로 범위를 명시.

### F010. 문서대로 reindex를 실행할 때 .env의 모델 설정이 자동으로 로드되지 않는다

- 분류는 설정·CLI 계약이며, 우선순위는 P2다. 셸에서 환경 변수를 이미 내보냈거나 `uv run --env-file`로 읽는 경우에는 그 값을 사용할 수 있다. 실제 운영 실행 환경은 확인하지 않았다.
- `docs/operations.md:361-366`은 `uv run lawbot-reindex`로 매니페스트와 임베딩 캐시를 준비하도록 안내한다. `tools/lawbot-tools/src/lawbot_tools/reindex.py:96-150`은 매니페스트를 쓴 뒤 `build_cache`에서 `settings.load()`를 호출한다. `settings.load`는 기본적으로 프로세스 환경을 읽고 이 경로에는 프로젝트의 `envfile.load()` 호출이 없다. `.env`에만 필수 값이 있으면 설정 검증에서 실패할 수 있다.
- `--check`는 검사만 하고, `--no-cache`는 매니페스트를 쓰고 캐시 준비를 생략한다. 설정 로딩을 고칠 때 이 두 경로의 동작을 보존하고, 환경 파일만 제공한 경우와 이미 내보낸 변수의 우선순위를 가짜 모델 클라이언트로 확인한다.

### F011. 계산 결론 검증은 결과 금액을 해당 항목명에 연결하지 않는다

`services/chatbot/src/lawbot/calc_router.py:288-303`의 `check_conclusion`은 결론에 결과 금액이 모두 있고, 등장한 금액이 결과·입력·계산 단계의 허용 집합 안에 있으면 `match`를 반환한다. 실제 함수를 호출해 결정세액 75,000원과 환급액 10,000원의 항목명을 서로 바꿔도 `match`임을 재확인했다. 허용된 입력 금액이 결론에 추가되어도 통과한다. 계산 카드는 `calculation.results`의 항목명과 값을 그대로 표시한다. 설계가 금액 집합 검사로 정의되어 있으므로 **유지보수 개선 P3**으로 남긴다. 항목명별 검증이 제품 요구가 되면 같은 금액에 다른 항목명을 붙인 회귀 사례부터 추가한다.

### F012. 채팅 재진입과 AbortController 소유권이 일치하지 않음

- 분류는 버그·자원 관리, 판정은 확인된 결함, P2, 확신도 높음이다. 현재 로컬 사용에서도 발생하며 제한/불특정 공개에서 실수로 추론 요청을 늘릴 수 있다.
- 위치는 `services/chatbot/frontend/src/useChatStream.ts:18-40`의 `send`, 같은 파일46의 `stop`, `components/TurnView.tsx:61-67`의 다시 묻기 버튼, `App.tsx:97-100`과171의 재질문 연결이다.
- 이미 완료된 답변의 재질문 버튼을 누르면 `chat.busy`와 관계없이 `send`를 실행한다. send 자체에도 active request 가드가 없다. 두 번째 요청이 abort.current를 덮어쓰며, 첫 요청이 먼저 끝나면 `finally`가 무조건 busy=false를 만든다. stop은 마지막 controller만 중지할 수 있다. 정상 UI로 도달할 수 있는 경로다.
- 재현은 한 답변 완료 후 다음 답변 스트리밍 중 앞 답변의 다시 묻기를 누른다. fake fetch를 pending 상태로 두고 hook.send를 연속 호출한 표적 테스트에서는 요청2개가 발생했고, 첫 요청만 완료했을 때 다음 요청이 대기하는데도 busy=false였다. 환경 오류 없이 기대 assertion에서 실패했다.
- 영향은 중복 모델 작업, 잘못된 전송 가능 표시, 일관되지 않은 stop이다. 서버의 동시성 제한 유무와 관계없이 클라이언트의 한 요청 계약은 깨진다. 운영 모델 요청은0이었다.
- 최소 수정은 send 시작에서 ref 기반으로 진행 중 요청을 차단하고, 현재 요청이 소유한 busy/controller만 해제하게 한다. TurnView에 busy를 전달해 재질문 버튼도 비활성화한다. 기존 stop, 완료된 질문의 재질문, 서버 error 표현과 재시도 선택은 보존한다.
- 회귀 테스트는 fetch가 pending일 때 같은 tick 및 나중 tick의 send를 호출해 전체 HTTP 요청1개인지 확인한다. 실제 App에서 과거 답변 재질문 버튼을 누르는 경로도 추가한다. stop 후 재전송, 오래된 finally가 최신 busy를 바꾸지 않는지도 확인한다.
- F013와 같은 hook을 수정하므로 함께 처리해도 된다. 제한 공개 전 권장 수정이며 프런트 가드를 서버 남용 방지로 간주하지 않는다.

### F013. 로그아웃으로 Chat가 제거되어도 스트림이 취소되지 않음

- 분류는 자원 관리·예외 처리, 확인된 결함, P2, 확신도 높음이다.
- 위치는 `services/chatbot/frontend/src/useChatStream.ts:9-12,18-50`, `App.tsx:19-24,59-62,143`, `api.ts:42-51`이다.
- hook은 controller를 만들지만 unmount cleanup을 등록하지 않는다. 로그아웃/세션 해제로 Chat가 사라져도 fetch가 계속 살아 있다. 서버가 이미 인증된 스트림을 계속 수행하는 구조라면 답변 생성도 남을 수 있다. 서버 측 취소 전파 자체는 F020의 모델 서버 취소 경계와 연결된다.
- fake pending stream을 시작하고 renderHook의 unmount를 호출했다. 전달한 AbortSignal.aborted는 false였고, 기대값 true assertion이 실패했다. 이후 합성 스트림을 닫아 임시 작업을 정리했다.
- 최소 수정은 effect cleanup에서 active controller.abort를 호출하는 것이다. api.chat도 reader read/parsing 실패 경로에서 cancel/releaseLock을 finally로 수행하도록 자원 수명을 분명히 한다. F012 가드로 요청을 하나로 제한해야 하나의 ref cleanup이 모든 허용 요청을 대표한다.
- 유지할 동작은 관리자 채팅/관리 화면 전환에서 숨겨진 Chat를 계속 mount하는 기존 선택이다. 단순 탭 전환과 로그아웃 unmount를 혼동해 취소 동작을 바꾸지 않는다. stop, 성공, network failure, parse failure, unmount 각각 reader cleanup과 controller 종료를 검증한다.
- 영향 환경은 현재·제한 공개·불특정 공개 모두이며 로그아웃 등 SPA 내 Chat unmount의 정상 경로다. 브라우저 탭 종료 자체를 이번 표적 검증에서 실행하지 않았다. 공개 전 수정 권장이다. 미인증 공격자가 이 결함만으로 모델 API를 실행한다고 판정하지 않았다.

### F014. 로그아웃 요청 실패를 숨겨 서버 세션 종료와 화면 상태가 달라짐

- 분류는 인증·오류 처리, 확인된 결함, P2, 확신도 높음이다.
- 위치는 `services/chatbot/frontend/src/App.tsx:72-73`의 AdminSession와138-143의 Chat logout handler, `api.ts:24-27,37`이다.
- 관리 화면은 `then(onSignedOut, onSignedOut)`, 채팅 화면은 `finally(onSignedOut)`를 사용한다. 두 경로 모두 HTTP 오류나 네트워크 오류 뒤 로그인 화면으로 이동한다. 채팅 경로에서는 거부된 Promise도 처리하지 않는다. 서버가 세션을 삭제하기 전에 실패한 경우 새로고침하면 기존 세션으로 돌아갈 수 있다.
- 합성 admin 세션에서 `/api/logout`만503을 반환하도록 했다. 클릭 후 비밀번호 입력 화면이 나타났고 가짜 `/api/me`는 여전히200이었다. 실패 alert 기대값에서 실패했다. 실제 운영 쿠키를 확인하거나 변경하지 않았다.
- 위험 주체는 같은 브라우저를 이후 사용하는 사람이다. 인증정보가 남은 상태에서 새로고침하면 재인증 없이 기존 계정으로 복귀할 수 있다. 현재 로컬/내부와 외부 공개 모두 해당하며, 피해 크기는 남은 사용자/관리자 세션 권한에 달린다. 현재 실제 세션 유출을 관찰한 것은 아니다. 해결 위치는 SPA의 로그아웃 상태와 API 세션 종료 계약이다.
- 최소 수정은 성공 또는 명백한 이미 만료된 세션만 완료로 처리하고, 실패 시 민감 화면을 비우되 서버 로그아웃이 미완료임을 알려 재시도할 수 있게 한다. HttpOnly cookie 삭제는 서버 성공 응답이 담당한다. 화면만 닫는 것을 세션 무효화 완료로 표현하지 않는다. 두 핸들러를 작은 공통 함수로 모으면 동작 차이와 unhandled rejection을 함께 없앨 수 있다.
- 회귀 테스트는 성공 로그아웃 후 me401, 503/네트워크 오류 후 세션 상태 미확정 안내, 재시도 성공, 스트림 취소(F013), 관리자/일반 사용자 경로를 확인한다. 서버 endpoint 보호와 실제 쿠키 만료 검증은 별도 integration 계층이다. 세션 종료 완료를 보장하는 공개 전 필수 해결 항목이다.

### F015. 모델 Markdown이 임의 외부 이미지를 자동 로딩할 수 있음

- 분류는 보안·출력 경계, 확인된 동작에 근거한 보안 결함, P2, 확신도 높음이다. 실제 공격 성공이나 모델의 악성 출력 생성 가능성은 검증하지 않았다.
- 위치는 `services/chatbot/frontend/src/components/TurnView.tsx:13-14`의 AnswerMarkdown, `admin/CachePanel.tsx:244-255`의 같은 렌더러 사용 경로다. 서버 web.py에는 CSP를 추가하는 코드가 없다. 운영 프록시가 CSP를 추가하는지는 외부 확인 대상이다.
- `![synthetic](https://example.invalid/pixel?data=SYNTHETIC_ONLY)`를 표시했더니 해당 src의 img가 생성됐다. React 기본 escaping과 react-markdown의 URL scheme 필터는 정상 동작하지만 http(s) 이미지까지 차단하지 않는다. 모델 응답이나 저장된 캐시 답변에 질문 일부가 이미지 URL로 들어가면 브라우저가 그 값을 외부 서버로 전송할 수 있다. 관리자 cache preview에도 같은 renderer가 적용된다.
- 공격에 필요한 조건은 사용자에게 표시되는 모델 출력/캐시 답변을 해당 형태로 유도하거나 공급할 수 있는 것이다. 프롬프트 인젝션이 실제로 이를 만들어 낼지는 추가 검증 대상이다. 서버 파일 읽기·쿠키 추출·코드 실행 경로를 가정하지 않았다. 자동 요청에 따른 IP/접속시각 노출과 URL에 포함된 데이터 유출 위험을 설명한 항목이다.
- 영향 환경은 로컬·내부와 양쪽 공개 방식 모두다. 해결 위치는 SPA이며, 외부 공개 전 출력을 제한하거나 운영 CSP의 동일 보호를 확인해야 한다. 최소 방향은 답변에서 이미지가 필요하지 않으면 img를 제거하거나 alt 텍스트로 표현한다. 실제 필요한 이미지가 있으면 같은 origin 또는 명시된 정적 호스트만 허용한다. 일반 링크 클릭·표·굵은 글씨·한글 Markdown은 유지한다. 추가 보안 라이브러리를 일괄 도입할 필요는 없다.
- 회귀 테스트는 합성 외부 URL·상대 URL·javascript URL·raw HTML을 렌더링한다. jsdom에서는 img/src DOM을 검사하고, 실제 이미지 자동 요청은 브라우저 request interception으로 검사한다. 일반 fetch mock은 img의 브라우저 요청을 관찰하지 못한다. parser/브라우저 테스트와 외부 CSP/캐시 설정 확인을 구분한다.
- 공식 근거는 react-markdown defaultUrlTransform이 http/https 등 URL을 허용하며 기본 raw HTML 실행 경계를 갖는다는 문서다. 아래 참고 자료에 링크를 적었다.

### F016. Clipboard API가 없을 때도 복사 성공으로 표시

- 분류는 버그·예외 처리, 확인된 결함, P3, 확신도 높음이다.
- 위치는 `services/chatbot/frontend/src/components/TurnView.tsx:80-92`의 CopyButton이다.
- `await navigator.clipboard?.writeText(text)`는 clipboard가 없으면 undefined를 await하고 성공 상태로 진행한다. 지원되지 않는 브라우저 또는 보안 context 제약에서는 복사가 일어나지 않아도 버튼이 복사됨으로 바뀐다. API가 reject할 때는 catch가 없어 실패 설명도 없다.
- jsdom navigator.clipboard를 undefined로 설정하고 복사 버튼을 클릭했다. 실제 복사 함수 없이 복사됨 버튼이 나타나 기대 assertion이 실패했다.
- 최소 수정은 API 가용성·성공을 확인한 뒤 성공 표시하고 실패 시 사용자가 알 수 있게 한다. 타이머 cleanup도 작은 수정에 포함할 수 있다. 정확한 답변 원문 복사와 정상 성공 표시를 보존한다. 테스트는 undefined, promise reject, 성공 세 가지를 fake clipboard로 검증한다. 공개 후 개선 가능하다.

### F017. SSE의 CRLF가 chunk 경계에서 나뉘면 이벤트가 합쳐지고 이름에 CR이 남음

- 분류는 parser 버그·테스트 경계, 확인된 결함, P3, 확신도 높음이다. 현재 web.py SSE writer는 LF를 보내므로 현재 서버 정상 출력에서 발생했다고 주장하지 않는다.
- 위치는 `services/chatbot/frontend/src/sse.ts:16-21`, `services/chatbot/frontend/src/sse.test.ts:21-25`이다. chunk마다 replace를 적용하므로 전 chunk 마지막 CR과 다음 chunk 첫 LF가 결합되지 않는다.
- 재현 입력은 `event: answer\r`, `\ndata: {"text":"one"}\r`, `\n\r`, `\nevent: done\r`, `\ndata: {"text":"two"}\r`, `\n\r`, `\n`을 순차 feed하고 end하는 것이다. 기대는 answer와done 두 이벤트이며 실제는 `done\r` 한 이벤트뿐이었다.
- 최소 수정은 줄 경계를 누적 상태에서 해석해 CRLF 분할을 지원한다. 현 서버 JSON 한 줄 data 계약을 유지한다. 범용 SSE 라이브러리로 전환할 필요까지는 없다. 현재 LF 정상 경로와 whole-CRLF 테스트를 유지하고 CR/LF split 위치를 각각 추가한다. 공개 차단 항목은 아니며 프록시가 내용을 바꾼다고 가정하지 않는다.

### F018. 모델 서버의 본문 상한이 chunked 입력의 메모리 소비를 제한하지 못함

- 분류는 보안·자원 관리, 판정은 확인된 결함, P2, 확신 높음이다. 현재 우선순위와 공개 차단은 별개다. 모델 API를 외부에 전달하는 구성에서는 공개 전 필수 해결이고, 모델 API를 루프백에 유지할 때는 내부 토큰 보유 클라이언트의 오용 경로다.
- 위치는 `services/model-server/src/lawbot_model_server/api.py` 70–73 `parse`, 284–298 `Guard.__call__`이다.
- Guard는 Content-Length가 없으면 0으로 취급한다. 이후 `await request.body()`로 끝까지 읽은 뒤 크기를 검사한다. 인증은 먼저 수행되지만 인증된 chunked 요청이 상한보다 많은 메모리를 소비하는 것을 막지 못한다.
- 합성 ASGI 검증에서 max_body=100, 80바이트×3 chunk를 보냈다. 미인증 기준은 body read 0회·401이었다. 인증된 실행은 240바이트/3 chunk 전부 읽은 뒤 413이었다. 운영 서비스 호출과 실제 대용량 입력은 없었다.
- 최소 수정은 인증 후 ASGI receive 또는 bounded request.stream 소비에서 누적 크기를 넘으면 즉시 중단하는 것이다. Content-Length 조기 거부와 strict schema를 유지한다. body를 두 번 읽거나 검증 후 모델 호출하는 순서를 바꾸지 않는다.
- `test_body_too_large`는 유지·보강 대상이다. 73–75행의 기존 테스트를 그대로 실행한 정상 기준이 PASS였고, 메모리에서 `api.parse`의 실제 body 길이 검사를 제거한 변형도 PASS였다. 이 테스트가 Content-Length 조기 거부를 보호하는 가치는 유지된다. 추가 테스트는 길이 헤더가 없는 chunk stream, limit와 동일한 길이, 거부 후 registry/scheduler 호출 0회, 초과 chunk 뒤 receive 0회를 확인한다.
- 해결 위치는 모델 API다. 초과 입력이 모델 서버의 RAM과 연결 자원을 사용한다. 이 경로는 모델 서버에 직접 접근할 수 있고 유효한 토큰을 가진 클라이언트에 해당한다. 챗봇은 요청에서 필요한 필드를 읽어 새 모델 API 요청을 구성한다.
- F019 및 F020과 자원 제한 회귀를 함께 실행할 수 있다.

### F019. 임베딩·리랭크 큐에는 대기 수와 대기 시간 상한이 없음

- 분류는 보안·자원 관리, 판정은 확인된 제한 누락, P2, 확신 높음이다. 외부 chat 공개에서는 백엔드의 전체 admission limit와 함께 공개 전 확인·보강할 항목이다. model API의 외부 직접 공개는 별도 차단해야 한다.
- 위치는 `services/model-server/src/lawbot_model_server/scheduler.py` 50–67 `Scheduler.__init__/call`, 69–77 `chat`, 103–125 `_receive/_run_calls`, `api.py` 87–101 `run_call`이다.
- `max_queued`와 `_queued`는 chat에만 적용된다. embedding/rerank는 무제한 queue.Queue/deque에 들어가며 `_run_calls`가 모든 one-shot 작업을 처리한 뒤 생성 step으로 넘어간다. 대량 one-shot 요청은 메모리 증가와 chat 지연을 유발할 수 있다. 호출당 문서 수 상한은 전체 대기 요청 수를 제한하지 않는다.
- 네트워크·스레드 실행 없이 실제 `Scheduler.call`을 사용하고 `_submit`만 합성 수신함에 대체했다. max_queued=1에서 3개의 call이 모두 접수됐고 chat queued=0이었다. 이는 admission 코드 검증이며 실측 부하·처리량 결과가 아니다.
- 최소 수정은 one-shot 대기에도 명시 상한·deadline·취소를 적용하고, 생성 step 사이에 처리할 one-shot 개수를 제한해 굶주림을 줄이는 것이다. 수치는 장비의 정상 처리 특성으로 정한다. GPU 연산 단일 스레드 보장은 유지한다.
- 기존 `test_queue_limit_rejects_new_chat_and_frees_after`, `test_calls_run_between_generation_steps`를 유지한다. 가짜 blocked call로 queue를 채워 추가 요청의 503/server_busy, 실행 함수 미호출, 취소 후 자리 회복, chat 진척을 확인한다.
- 침해 자원은 공유 모델의 RAM/GPU/응답성이다. 현재 루프백 + Bearer, 향후 API 백엔드를 통한 다수 허용 사용자의 누적 입력을 구분해야 한다. 통합 검토의 백엔드 동시성 발견사항과 연결한다.

### F020. 첫 토큰을 기다릴 때와 비스트리밍 생성 중에 클라이언트 연결 종료를 감지하지 않는다

- 분류는 버그·자원 관리·보안, 판정은 확인된 결함, P2, 확신 높음이다. 제한 공개에서 다수 사용자가 취소/재전송할 때의 자원 보호와 관련되어 공개 전 보강을 권고한다.
- 위치는 `services/model-server/src/lawbot_model_server/api.py` 144–154의 첫 `job.next`와 162–170의 비스트리밍 반복이다. 비스트리밍 반복이 끝나거나 태스크가 취소되면 `job.cancel()`이 실행된다. 스트리밍 응답을 시작한 뒤에는 `events`의 `finally`가 정리를 담당한다.
- 첫 토큰을 기다리는 동안과 비스트리밍 답변이 완성되기 전에는 ASGI `http.disconnect`를 읽는 경로가 없다. 대기 중인 태스크 자체가 계속 실행되면 이미 큐에 들어간 요청이 클라이언트 연결 종료 뒤에도 진행될 수 있다. 임베딩·리랭크의 `run_call`도 종료 신호를 직접 확인하지 않고 결과를 기다린다. 수집기의 비스트리밍 HTTP 클라이언트는 전송 오류를 재시도하므로 앞 요청이 남아 있으면 추론 비용이 중복될 수 있다.
- 최초 리뷰에서는 합성 ASGI에서 본문 뒤에 disconnect를 제공하고 가짜 `Ticket.next`가 첫 토큰을 기다리게 했다. 연결 종료 신호를 읽거나 취소하지 않았고, 정리 단계에서 태스크를 직접 취소했을 때 `Ticket.cancel()`이 실행됐다. 비스트리밍 답변의 첫 토큰 이후 동작은 코드 경로로 확인했다.
- 최소 수정은 첫 토큰/비스트리밍 완료 대기와 disconnect 감지를 함께 관리하고, 종료가 확인되면 Ticket 취소를 설정하는 것이다. 이미 실행 중인 동기 embedding/rerank를 즉시 중단할 수 있는 것처럼 주장해서는 안 된다. 큐 미시작 작업 취소와 실행 중 연산의 종료 경계를 구분한다.
- 기존 scheduler 직접 cancel 테스트를 유지한다. 가짜 ASGI 또는 로컬 가짜 모델 프로세스로 첫 토큰 전 종료, queued 종료, stream 중 종료, nonstream 종료를 각각 검증하고 model start/step 카운트와 queued=0을 확인한다. 외부 배포에서 SSE 중단 전달도 따로 검증한다.
- 영향 환경은 현재 내부 사용과 두 공개 방식 모두이며 주체는 정상 취소하는 사용자 또는 반복 취소하는 허용 사용자다. 인증 우회 문제는 확인되지 않았다.

### F021. 수집기의 내용 기반 ID가 과거 내용으로 되돌아가는 실제 변경을 누락함

- 분류는 버그·데이터 정합성, 판정은 확인된 결함, P2, 확신 높음이다. 공개 전 보안 차단 항목은 아니다.
- 위치는 `services/monitor/src/law_monitor/collectors/legalize_git.py` 263–264, 305, 312–323 `_Builder.emit/_track`, `services/monitor/src/law_monitor/app.py` 226–233 `Monitor._run`이다.
- event_id에 source, item, 새 blob만 들어간다. 이미 관측했던 A 원문이 X→A→B→A 순서로 돌아오면 마지막 B→A는 처음 X→A와 같은 ID다. `_Builder`는 변경을 계산하지만 Monitor는 DB에 ID가 있다는 이유로 이벤트를 버리고 item/cursor를 새 상태로 갱신한다. 요약/캘린더 소비자는 마지막 복구·정정을 받지 못한다. 한 번의 collect 안에서도 dict가 같은 ID를 덮어쓴다.
- 운영 Git을 쓰지 않은 합성 `_Builder` + `Monitor` 검증에서 각 run 사건 수는 [1,1,0], 모든 failed={}, latest_tracked_blob='A'였다. 기본 기준 X→A 및 A→B는 각각 1개 사건이었다.
- 최소 수정은 동일 수집의 재실행 중복 억제와 다른 변경 발생의 identity를 분리하는 것이다. old/new transition, 실제 commit identity 또는 source revision을 고려하되 force-push로 같은 snapshot을 다시 읽을 때의 중복 억제 계약을 정한다. 기존 저장 ID를 일괄 재작성하기 전에 호환 전략이 필요하다.
- 회귀는 X→A→B→A와 A→삭제→A→삭제, 같은 collect의 반복 실행, 여러 commit을 한꺼번에 읽기, 강제 이력 재작성 후 동일 내용 유지다. 기존 `test_amendment_makes_one_event_then_zero`, `test_two_amendments_in_one_range_make_two_events`, `test_force_push_falls_back_to_content_compare`는 유지하고 보강한다.

### F022. 누락된 이벤트 파일을 정상 무변경 요약으로 보고함

- 분류는 버그·예외 처리·관찰 가능성, 판정은 확인된 결함, P2, 확신 높음이다.
- 위치는 `services/monitor/src/law_monitor/summarize.py` 322–331 `load_events`, 255 pending 계산 및 274–275 새 변경 없음 처리, `app.py` 171–203 `Monitor.summarize`다.
- state DB에 pending event가 있지만 JSON 파일이 없으면 warning 후 continue한다. 반환된 events만으로 pending과 성공 상태를 계산하므로 result.pending=0, failed=None, run status=ok 및 '새 변경 없음'이 가능하다. DB의 pending은 그대로 남아 있고 매번 같은 잘못된 성공 메시지가 생성된다.
- 합성 DB에 event 1개와 존재하지 않는 합성 JSON 경로를 등록해 summarize를 실행했다. 모델 호출은 0회였고 events=0, reported_pending=0, DB pending=1, status=ok, claims_no_change=True를 확인했다.
- 최소 수정은 누락·불일치 파일 목록을 명시 오류로 올리거나 부분 성공 결과에 반영하는 것이다. 누락된 event를 processed 처리하지 않고 정상 이벤트의 요약 성공은 유지한다. 동일 원칙을 `_diff_file`의 누락 원문 처리에도 검토하되 이번 검증은 이벤트 JSON에 한정한다.
- 기존 정상 무변경 테스트와 `test_failure_keeps_events_pending`을 유지한다. 누락 JSON 1개, 정상+누락 혼합, 복구 후 재실행의 reported pending/DB pending/exit status/보고 본문을 함께 검사한다.

### F023. 법제처 비정상 응답이 인증키를 오류 로그로 반사할 수 있음

- 분류는 보안·예외 처리, 판정은 확인된 노출 경로, P2, 확신 높음이다. 실제 키가 유출됐다는 증거는 조사하지 않았다. 현재 내부 운영에도 해당하고 공개 전 로그 접근권한과 함께 해결해야 한다.
- 위치는 `services/monitor/src/law_monitor/lawgo.py` 34–47 `LawGoClient._service`, `app.py` 221–224 `Monitor._run`, 270 이후 `_failing_event`다.
- HTTP 200이지만 구조가 예상과 다르면 `str(data)[:120]`를 예외 메시지에 삽입한다. 이 범위에 반사된 OC가 포함되면 예외에 남으며, 수집기 오류 처리에서 로그와 실행 상태 DB에 기록될 수 있다. 연속 실패가 경고 기준에 도달하면 실패 이벤트와 요약에도 들어갈 수 있다. URL 쿼리 마스킹은 이 응답 본문에 적용되지 않는다. 국회 API의 `AssemblyClient._get`에는 인증키가 본문에 포함됐는지 검사하는 경로가 있다.
- `httpx.MockTransport`로 HTTP 200의 비정상 JSON을 돌려주면 합성 표식이 `LawGoError`에 남는 것을 재확인했다. 이후 로그·DB·이벤트·요약으로 전달되는 경로는 코드에서 확인했다.
- 최소 수정은 응답 원문·값을 예외에 넣지 않고 고정된 유형과 안전한 구조 정보만 사용한다. 실패 원인 식별과 재시도·cursor 보존은 유지한다. 이미 저장된 로그의 실제 노출 여부는 운영자가 안전한 마스킹 검사로 확인하며 유효성 검증 요청을 보내지 않는다.
- 기존 `test_oc_never_leaks`는 유지·보강한다. HTTP 오류/URL 외에 HTTP200 malformed object, 배열, error string에 합성 키를 넣고 예외·captured log·summary.failed·DB·실패 이벤트·최종 Markdown 모두에 값이 없는지 확인한다.
- 공격/오용 주체는 응답을 제어하거나 키를 반사하는 upstream 서버다. 침해 대상은 LAW_OC 비밀값이며 웹 익명 사용자가 바로 유발할 수 있다고 확인한 것은 아니다. 해결은 collector 오류 경계와 로그 운영에 있다.

### F024. Git 네트워크 작업에 종료 시간 상한이 없어 daily 전체가 무기한 대기할 수 있음

- 분류는 예외 처리·자원 관리, 판정은 확인된 timeout 누락과 조건부 운영 위험, P2, 확신 높음이다. 실제 운영 hang은 재현하지 않았다. 공개 후 개선 가능하다.
- 위치는 `services/monitor/src/law_monitor/gitrepo.py:39-56`의 `GitRepo.run`과 `ensure`, `services/monitor/src/law_monitor/cli.py:76-92,103-104`의 `_daily`와 `run_lock`이다.
- clone/fetch와 partial clone의 lazy blob 조회가 `subprocess.run(...capture_output=True)`에 timeout 없이 들어간다. Git이 완료하지 못하면 collector 예외 격리까지 도달하지 못하고 daily lock을 유지해 이후 calendar/check/summarize와 다음 실행을 막을 수 있다. GIT_TERMINAL_PROMPT=0은 모든 네트워크 지연 상한을 보장하지 않는다.
- 정적 검증만 수행했다. 운영 네트워크를 끊거나 대기시키지 않았다.
- 최소 수정은 Git I/O에 작업별 상한을 두고 TimeoutExpired를 GitError로 변환하는 것이다. 필요하면 직접 시작한 담당 검토 process group 정리를 검증한다. partial clone·cursor 원자성을 유지한다.
- 가짜 subprocess timeout과 작은 임시 sleep 프로세스로 오류 분류·잠금 해제·다른 collector 진행·cursor 불변을 검증한다.

### F025. 같은 초에 요약 파일명이 충돌하면 이전 파일을 덮어쓴다

- 분류는 버그·파일 보존, 판정은 확인된 결함, P3, 확신 높음이다. 외부 공개 차단과 무관하다.
- 위치는 `services/monitor/src/law_monitor/app.py` 174–177 `Monitor.summarize`, `summarize.py` 309–310 `out_path.write_text`이다.
- 날짜 파일이 있으면 시각을 초 단위로 붙인 이름을 사용하며, 새 경로의 존재는 확인하지 않는다. 날짜 파일이 이미 있는 상태에서 같은 초에 두 번 실행하면 먼저 기록한 요약을 덮어쓴다.
- 합성 임시 폴더·고정 clock·빈 event로 3회 실행해 second.path==third.path, 기존 합성 표식 보존 False를 확인했다. 모델 호출 0회.
- 최소 수정은 이미 고유한 run_id를 파일명에 쓰거나 exclusive creation으로 충돌 시 새 이름을 선택하는 것이다. 첫 날짜 파일명과 별도 replay 출력 계약은 필요에 맞게 유지한다.
- 기존 `test_one_call_per_amendment_and_md`를 유지하고 동일 now의 3회 이상 호출에서 모든 이전 내용 보존을 assertion한다.

### F026. Vite 8.3.4가 개발 서버 RCE 권고의 영향 버전이다

- 분류는 의존성 보안, 판정은 추가 검증 필요(영향 버전은 확인, 실제 노출 미확인), 현재 P2, 확신 높음이다. 영향 버전이라는 사실과 외부에서 악용 가능한 배포라는 판단을 구분한다.
- 위치는 `services/chatbot/frontend/package-lock.json:3764-3765`, `services/law-calendar/frontend/package-lock.json:2150-2151`, 두 `package.json`의 `devDependencies.vite`, `services/chatbot/frontend/vite.config.ts:25`와 `services/law-calendar/frontend/vite.config.ts:13`의 `server.host`다.
- 두 잠금 파일은 Vite 8.3.4와 Rolldown 1.2.16을 지정하며, 개발 트리의 Vite 설치본도 8.3.4다. 공식 GHSA-p4f5-76q4-ffq7은 8.3.0–8.3.6이 영향 범위이며, Vite 8.3.7은 Rolldown 1.2.17의 수정에 의존한다고 설명한다. 권고의 노출 조건은 네트워크에서 Vite 개발 서버로 접근할 수 있는 구성이다. 저장소의 개발 서버 설정은 루프백이며 운영용 화면은 FastAPI가 빌드된 dist에서 제공한다. 실제 개발 서버 실행·포워딩 상태는 미확인이다. [공식 권고](https://github.com/vitejs/vite/security/advisories/GHSA-p4f5-76q4-ffq7)
- 오용 주체는 해당 개발 서버에 요청을 보낼 수 있는 사람이다. 실행 계정의 파일·코드 실행 권한에 영향이 있다. 로컬 접근·잘못 연결된 Tunnel/프록시로의 공개를 구분하며 운영 RCE나 실제 비밀 탈취를 재현하지 않았다.
- 최소 수정은 다음 수정 세션에서 두 manifest/lock을 호환되는 수정 버전으로 갱신하고 타입·테스트·빌드를 확인하는 것이다. 이번 리뷰에서 의존성 변경은 수행하지 않았다. 외부 공개 전 Vite/HMR을 공개 경로에서 제외해야 하며 임시 dev 공개를 계획하면 패치가 선행 조건이다. statically built frontend의 동작과 fingerprint 검사를 유지한다.
- 안전한 회귀는 lock/installed version 일치, 양쪽 build/test/typecheck, 공개 route에 dev/HMR endpoint가 없다는 배포 검사다. 실서비스 exploit 실행은 필요하지 않다. F012/F013 수정의 frontend 검증과 함께 실행 가능하다.

### F027. 민감 챗봇 API 응답에 저장 금지 정책이 일관되게 적용되지 않는다

- 분류는 보안·배포 준비, 판정은 유지보수 개선 제안(헤더 누락 확인, 외부 캐시 설정 추가 확인), P2다. 현재 공유 캐시 유출은 확인하지 않았다.
- 위치는 `services/chatbot/src/lawbot/web.py:514-530`의 me/config/chat, `services/chatbot/src/lawbot/admin.py:263-364`의 cache/feedback 조회다. `admin.py:393-401`의 캘린더 프록시는 캘린더 원본의 캐시 헤더를 전달하지 않고 내부용 응답에만 `no-store`를 붙인다. 독립 캘린더는 `services/law-calendar/src/law_calendar/server.py:105-110`의 미들웨어에서 `no-store`를 설정한다.
- `chat`은 no-cache이며 admin DB 조회와 me에는 저장 금지가 명시되지 않는다. no-cache는 재검증을 요구하고 저장 자체를 금지하지 않는다. Cloudflare 기본 동작만으로 유출을 단정할 수 없지만 향후 과도한 Cache Rules가 인증 응답의 저장 여부를 바꿀 수 있으므로 공개 전 보호 계약을 명시해야 한다. [캐시 제어 문서](https://developers.cloudflare.com/cache/concepts/cache-control/)
- 위험 주체는 캐시 구성 오류에 의해 다른 사용자의 응답을 받는 사용자다. 사용자명·피드백·검수 데이터·대화가 대상이다. 현재 로컬/내부의 실증된 유출과 향후 edge 위험은 구분한다. 해결 위치는 API 헤더와 Cloudflare rules 두 곳이다.
- 최소 변경은 민감 API·인증·오류 응답에 no-store를 일관되게 부여하고 edge에서 해당 API를 cache bypass하는 것이다. index와 해시 assets의 캐시 정책은 별도로 유지한다. CSP나 전역 no-store로 모든 정적 자산 정책까지 묶을 필요가 없다.
- 정적 분석과 합성 정적 파일 응답만 확인했다. 실제 edge cache는 미실행이다. 다음 회귀는 로그인/관리 GET·chat·401/403에서 no-store, 두 합성 세션의 응답 재사용0, 배포 edge HIT 없음이다. 공개 전 필수 코드 보완 또는 동등한 배포 보호 입증 항목이다. 원본 접근 통제가 선행되어야 edge 정책의 보호 범위를 주장할 수 있다.

### F028. README가 v4 평가를 현재 웹앱 구성의 결과로 소개한다

- 분류는 문서·아키텍처 정합성, 판정은 확인된 결함(문서 불일치), P2, 확신 높음이다.
- `README.md:38-43,142-147`은 v4 결과를 양쪽 언어에서 현재 구성의 결과로 소개한다. v4는 여전히 지원하며 환경 변수가 비어 있을 때 선택하는 프로필이다. 다만 `.env.example:26`은 v5를 선택하고, `docs/evals/2026-11-11-deploy-v5.5.0.md`에는 확장 코퍼스·계산 라우터·정확 일치 캐시를 사용하는 배포가 기록돼 있다. `docs/architecture.md:3-14`의 흐름도는 v4라고 범위를 명시하며, v5와 계산·캐시 분기 설명을 보완할 수 있다.
- `AGENTS.md`의 질문당 세 호출은 리랭커가 켜진 기본 검색·생성 흐름에 해당한다. 정확 일치 캐시 적중은 추론을 건너뛴다. 계산 질문의 분류와 하위법령 검색은 모델 호출을 추가할 수 있고, 리랭커 설정에 따라 호출이 생략된다.
- README 지표를 평가 당시 v4 구성으로 표시하고 v5 배포 기록과 연결한다. 기존 평가 결과와 실패 사례는 보존한다. 실행 중인 운영 프로세스의 상태는 별도 검증 범위다.
- 검증은 문서와 구현의 정적 대조다. 품질 평가 재실행은 하지 않았다. 다음 세션에서는 README·architecture·AGENTS의 실행 흐름을 정상 RAG/계산/cache hit별로 대조하고, 문서 수정 diff만 검사한다. 평가 metadata F007과 함께 처리할 수 있으나 새 품질 수치를 만들어 채우지 않는다. 외부 공개의 기술 보안 차단과 별개이며 공개 사용자에게 기능·한계를 설명하기 전에 정리한다.

### F029. import 부작용 테스트가 실제 import를 실행하지 않는다

테스트 실효성 결함이며 우선순위는 P2다.

- 위치: `services/chatbot/tests/test_pipeline.py:296-305`, `test_import_does_not_check_auth_or_load_models`.
- 현재 동작: `FakeRetriever.created = FakeLLM.created = 0`으로 초기화하고 service의 클래스 둘을 대체한 뒤 `sys.modules.pop('lawbot.web', None)`을 수행한다. `try` 본문에는 방금 초기화한 값이 0인지 확인하는 assertion만 있으며 `import lawbot.web`, `importlib.import_module`, `reload`, `web.main` 등 검증 대상 실행이 없다. `finally`도 모듈 제거만 한다.
- 기대와 차이: 이름/주석은 import만으로 인증 검사를 하거나 모델을 로딩하지 않음을 보장한다고 주장한다. 실제로는 어떤 import 부작용도 유발하지 않기 때문에, 그 테스트 자체가 해당 회귀를 감지할 수 없다. auth 검사 호출 여부를 나타내는 sentinel도 없다.
- 정적 재확인: 위 함수의 setup→pop→assert→pop 제어 흐름을 확인하면 재현된다. mutation 실행은 이 보완 단계에서 하지 않았고 실행 실패를 감지 성공으로 보고하지 않는다.
- 현재 제품 코드와 구분: `services/chatbot/src/lawbot/web.py:609-636`의 `main` 안에 `.env` 로딩·설정 검증·앱 로딩·서버 실행이 있으며, `639-643`의 main guard도 확인했다. 현재 import에서 모델 로딩하는 제품 결함을 발견했다는 뜻은 아니다.
- 최소 수정: **유지·보강**. 독립 subprocess 안에서 실제 `lawbot.web` import를 수행하고, import 전 안전한 가짜 `service.load_app`/모델 생성·설정 인증·uvicorn 실행 경계에 호출 시 실패하는 sentinel을 둔다. `.env` 및 네트워크는 차단하고 PYTHONDONTWRITEBYTECODE 및 임시 cwd로 부작용을 격리한다. 이미 import된 모듈을 부모 pytest 프로세스에서 지우는 방식은 피하면 테스트 간 모듈 identity 오염도 줄어든다.
- 회귀 합격 기준: 정상 import가 성공하고 어떤 sentinel도 호출되지 않음. 저장소 밖 복사본에서 module top-level에 `load_app()` 또는 인증 검사를 넣은 변형은 그 sentinel 때문에 실패해야 한다. import 경로/의존성 오류로 실패한 것은 감지 성공이 아니다.
- 보존해야 할 보호: `test_citation.py:148-166`의 subprocess 테스트는 실제 import 후 heavyweight model library가 import되지 않는지 검사한다. `test_layers.py:14-22`는 실제 import 후 tools 계층 의존성을 검사한다. 이들은 유효한 별도 계약이므로 삭제·통합 대상으로 잡지 않는다. 특히 현재 F029 하나가 빈 테스트라고 전체 import 보호가 없다고 표현하면 안 된다.
- 의존 관계: F009(빈 결과로 통과하는 scoring assertion)와 같은 테스트 정리 작업에서 처리할 수 있으나 근본 원인은 별개다. 프런트엔드 Answer 이유/turnId guard 누락과도 독립이다.

### F030. eval compare의 strict 모드가 통째로 누락된 주요 지표를 놓친다

- 분류/판정/우선순위: 평가·테스트 보장, **확인된 결함 P2**, 확신 높음. F008의 빈 파일 diff와 목적이 관련되지만 다른 실행 도구/분기다.
- 위치: `tools/lawbot-tools/src/lawbot_tools/eval_v4.py:740-776` `compare`, 특히755-759; `1063-1066` main strict 처리.
- 원인: outer merge로 한쪽에 없는 metric이 NaN이 됨 → NaN이 하나라도 있으면 continue → warning 없음. 문항 수 차이도 이 continue 아래이므로 빠진 지표에는 검사하지 않는다. `--strict`는 warning만 보고 실패하므로 불완전한 새 평가가 성공할 수 있다.
- 재현: /tmp의 baseline summary에 `(답변,전체,조문 인용 정확도(법령명 포함),0.9,200)` 및 `(검색,전체,리랭킹 Recall@3,0.94,1110)` 두 행. new summary에는 두번째 검색행만 둔다. `ev.OUT`만 해당 temp Path로 바꿔 실제 `compare('base','new')` 호출 → `[]`, 출력 `경고 없음`. 운영 모델 불필요. strict exit0은 main의 명확한 분기에서 도출했으며 subprocess main까지 실행했다고 쓰지 않는다.
- 영향: 답변 평가가 안 되었거나 요약 생성이 빠진 새 결과를 이전 결과보다 나빠진 것이 없는 것으로 해석하게 한다. CLI가 의도하는 부분 단계 비교는 유지할 수 있지만 비교 불가능을 통과로 표현하면 안 된다.
- 최소 수정: baseline에 있는 비교대상 지표가 new에 없거나 value/n이 유효하지 않으면 별도의 '누락/비교 불가' warning; strict는 실패. 서로 다른 의도된 단계 비교는 명시 옵션/출력으로 구분. 새로운 평가 프레임워크 필요 없음.
- 테스트: `services/chatbot/tests/test_eval_v4.py:450-473 test_compare_warns` 보강. 현재 score 하락/개수 차이/summary 파일 부재를 보호한다. metric 행 누락·NaN value·중복 metric key·두 값 모두 없음 경계를 추가, 정상 비교 허용. 기존 test 유지.
- 다음 세션: F008/F009와 '평가 결과 완전성' 작은 변경 묶음 가능하나 서로의 회귀를 각각 확인. 외부 공개 인증과 무관, 현재 평가 결과 신뢰성 문제.

### F031. 타법개정 부칙의 유효기간을 현재 법률의 동번호 조문에 적용한다

- 분류/판정/우선순위: 조문 생성·데이터 정확성, **확인된 결함 P2**, 확신 높음(합성 입력에 실제 parser 실행). 현행 저장 코퍼스에 이미 잘못 삭제된 조문이 있는지는 미확인.
- 위치: `tools/lawbot-tools/src/lawbot_tools/build_articles.py:565-571` `_compose_history`, `779-803` expire, `1040-1065` build_rows의 expired→deleted 변환. 비교 경계: `schedule:469`와 `addendum_overrides:665`는 owner가 있으면 다른 법 부칙으로 보고 현재 법 적용을 건너뛴다.
- 원인: `addendum, owner = addendum_of(...)`로 타법 여부를 알고도 바로 `expiry_clauses(addendum)`를 호출. 반환된 `제1조`를 현행법 `arts['제1조']`에 붙이므로 타법 조문명을 잃는다. 후속 expire에서 title 비교도 현재법 자료끼리 비교하여 보호가 되지 않는다.
- 안전한 재현 데이터: Markdown frontmatter 제목 `시험법`, 공포번호300, 시행일2020-01-01, 본칙 `##### 제1조(시험 규정)`/합성 본문. 부칙은 `부칙(다른법) <제300호,2020.1.1>`, `제1조(시행일) 이 법은 공포한 날부터 시행한다.`, `제2조(유효기간) 제1조의 개정규정은 2020년 12월 31일까지 효력을 가진다.`. `_compose_history('kr/시험법/법률.md','2026-01-01',[('synthetic','2020-01-01','시험법(타법개정)')],[md])` 실행.
- 관찰: `addendum_of(...)[1]='다른법'`인데 현재 시험법 제1조 `expired=True`, `expiry_unhandled=[]`. shell/git/파일 수집/모델 호출 없이 메모리 입력만 사용. 현행 법조문에 대한 법률적 판단이 아니라 함수가 이미 정의한 타법 owner 분리 계약의 위반이다.
- 영향: 이후 build_rows가 deleted1로 내보내어 검색 대상에서 제외될 수 있다. parser 지원 입력의 결함이지 운영 데이터 오염을 입증한 것은 아니다.
- 최소 수정: expiry 해석 전에 owner를 구분하여 다른 법 부칙을 현재 법에 적용하지 않는다. 외부 대상까지 지원해야 하면 문서 소유자를 보존한 명시 규칙과 판정 보류를 사용하되 일괄 전체 parser 재작성 불필요.
- 회귀: `test_build_articles.py`의 `test_schedule_uses_other_law...` 계열/`test_expiry_*` 유지, 타법 owner+동번호+만료된 날짜에서 현재법 expired/deleted가 바뀌지 않는 test, 동일 내용의 자기법 부칙에서는 만료되는 양성대조 추가. build_rows까지 이어가는 합성 fixture로 실제 CSV deleted와 reports를 확인.
- 공개 전: 조문 갱신 경로의 수정·회귀 먼저. 기존 코퍼스 영향 여부는 읽기전용 생성 비교나 trusted source 샘플로 후속 확인(실제 자동업데이트/재시작은 별도 승인된 세션). F001 수식 입력 결함과 원인은 독립.

### F032. 알려지지 않은 명시 법령명을 무시해 앞 법령의 조문으로 잘못 인정한다

- 분류/판정/우선순위: 인용 파싱·평가 정확성, **확인된 결함 P2**, 확신 높음. 과거 v1 scorer known_limit이 아니라 현재 웹·v4/v5 평가가 공유하는 코드다.
- 위치: `services/chatbot/src/lawbot/citation.py:97-101` `_TOKEN`, `118-134` parse; `tools/lawbot-tools/src/lawbot_tools/eval_v4.py:60-63` cite_gold; `services/chatbot/src/lawbot/answer_format.py:25-39` cited_hits/`72-80` answer_citations.
- 원인: parser가 알려진 LAW_ALIASES와 조문번호만 토큰화한다. 사이에 명시적으로 등장한 비등록 법령명은 소비되지 않으므로 `law`가 이전 값으로 유지된다. 생략된 법령명을 상속하는 정상 기능과 실제 다른법 이름을 무시하는 동작을 구분하지 않는다.
- 재현: `raw='【결론】합성 답변입니다.\n【근거 조문】근로기준법 제1조, 민법 제26조'`. `citations(raw)` → `[('근로기준법','제1조'),('근로기준법','제26조')]`. `cite_gold(raw,'근로기준법 제26조')` → **True**. 실제 `check`에 `law='근로기준법',article_id='제26조'` Hit를 주면 두번째 인용 verdict **ok**. 메모리 함수 호출만, 모델/외부법률 서비스 없음.
- 영향: 모델이 명백히 다른법을 인용했는데 검색근거 카드가 일치로 표시되고 인용 정확도 채점도 정답 처리한다. 정확한 '민법 제26조' 내용과는 무관하다. 모델이 이런 답변을 실제로 생성하는 빈도는 측정하지 않았다.
- 최소 수정은 명시된 비등록 법령을 보존하거나 그 경계를 확인되지 않은 인용으로 판정하는 것이다. `check`는 법령 값이 `None`일 때 같은 번호의 검색 결과가 하나면 인정하므로, 비등록 법령을 이름 없는 인용으로 바꾸면 오인정이 남을 수 있다. 기존 `근로기준법 제26조, 제27조`의 생략 목록, 등록 약칭, 부칙·시행령 구분은 유지한다.
- 회귀: `test_citation.py`에 등록법→미등록법+같은 번호, 인용부호 `「민법」`, 미등록법 뒤 생략 인용, 정상 등록법 목록을 추가. 평가 cite_gold false와 UI cited_hits가 현재법 hit를 채택하지 않는지를 함께 확인. 기존 `test_citation.py:143-145`의 최저임금법 오인용 검사는 인식 가능한 법령만 다뤄 이 결함을 놓친다. 해당 기존 테스트는 삭제하지 않는다.
- 공개 전: 인용 정확성을 내세우는 사용자 공개 이전 수정 권장(법률 품질 차단 조건). 보안 인증 우회/코드실행 취약점으로 분류하지 않는다.

### F033. 빈 system 파일이 배포 점검을 예외로 중단시킨다

- 분류는 예외 처리·운영 점검, 판정은 확인된 결함, 우선순위 P3, 확신 높음이다. 런타임 인증 우회와는 무관하다.
- 위치는 `services/chatbot/src/lawbot/check_deploy.py:158-168`의 `check_system_file`, 호출자는 `check_system:171-175`다.
- 파일이 존재하면 내용을 strip한 후 `text.splitlines()[0]`를 첫 줄 표시용으로 읽는다. 빈 파일 또는 공백뿐인 파일은 splitlines가 빈 배열이어서 IndexError가 발생한다. FAIL 결과를 기록하지 못하고 뒤의 배포 검사도 멈출 수 있다.
- 재검토에서 공백만 있는 임시 파일을 `check_system_file`에 직접 넘기자 `IndexError`가 발생했고 결과 목록은 비어 있었다.
- 최소 수정은 빈 내용에 명시적 FAIL을 추가하고 반환하는 것이다. 정상 파일의 길이/hash/첫 줄 점검과 없는 파일의 FAIL 계약은 보존한다. 프롬프트 전체를 추가로 로그에 출력하지 않는다.
- 먼저 `services/chatbot/tests/test_webapp.py`의 check_deploy 시험에 빈 파일·공백 파일·정상 파일 양성 대조를 추가한다. 빈 경우 예외가 없고 FAIL이 기록되며 후속 점검이 계속되는 것이 합격 기준이다. 원인과 수정은 F010의 환경 설정 로딩과 독립이며 공개 후 개선 가능하다.

## D. 테스트 정리 제안

### 테스트 실효성과 정리

| 대상 | 현재 보호 | 빠진 동작 | 조치와 보호 범위 |
|---|---|---|---|
| `useChatStream.test.tsx` | 실제 hook→API client→fetch/stream parser, stop·401·오류·EOF | F012/F013 재진입과 unmount, parse error reader cleanup | 유지·보강. fetch만 fake여서 핵심 로직을 건너뛰지 않음 |
| `App.test.tsx` | 로그인 화면, 모델/형식, IME, 대화 선택, 관리자 목록·교정·proxy | F014 로그아웃 성공/실패, busy 중 과거 답변 retry | 유지·보강. UI mock 서버에 `/api/logout` handler도 없음. 브라우저 cookie·서버 auth 보장을 뜻하지 않음 |
| `components/Answer.test.tsx:20-25` | reasons 없음/있음에 따른 feedback 표시 | 이름에 있는 turnId 없음 조건 | 유지·보강. 임시 소스에서 `&& turn.turnId` 제거해도 기존5tests통과. 현재 UI 코드가 틀린 것은 아니며 이 한 변형으로 테스트 전체 무의미 판정하지 않음. reasons가 있고 turnId null/undefined인 input 추가 |
| `sse.test.ts` | LF chunk 분할, 합쳐진 이벤트, whole CRLF, EOF flush | F017 경계 분할 CRLF와 invalid JSON 이후 정리 | 유지·보강. 타입오류/import 실패로 인한 검증 성공 없음 |
| `turns.test.ts`와 `Answer.test.tsx`의 계산 테스트 | 이벤트 상태 반영과 화면의 금액·근거·경고·순서 | 계산 자체의 법적 정답은 검증 안 함 | 유지. 계산기 backend 테스트와 다른 경계 |
| `LawCalendarPage.test.tsx`, data/HoldDialog tests | API pagination, 로딩/오류/재시도, 공개 credential omit, 선택/keyboard | 취소된 상세가 후속 UI를 덮지 않음, malformed item runtime schema | 유지·선택 보강. snapshot-only 아님 |
| `ChangeView.test.tsx`, dates tests | 원문 보존, literal HTML escaping, 날짜 경계 | 실제브라우저 modal focus trapping은 jsdom 한계 | 유지. 기존 Python browser E2E와 함께 판단 |

표적 변형의 기존 기준 실행은 전체42통과에 포함된다. 변형 실행5통과는 기대한 방어 조건 제거를 검출하지 못한 결과다. 검토한 프런트엔드 테스트에서는 assertion을 우회해 항상 성공하는 추가 사례를 확인하지 못했다. assertion 실행 횟수 instrumentation을 수행한 것은 아니다. Python의 실행 없는 import 테스트는 별도 F029로 확인했다. 유사한 계산/UI tests가 보호하는 층이 달라 삭제를 권고하지 않는다.

### 테스트 정리와 실행 기록

| 대상 | 보호 범위 | 놓친 동작 | 방향 |
|---|---|---|---|
| test_ms_api.test_body_too_large | Content-Length 사전 413 | 없는 header의 incremental limit | 유지·보강. 이 한 변형을 놓쳤다는 결과는 전체 테스트 무효를 뜻하지 않음 |
| test_ms_api.test_post_requires_token / test_token_required_everywhere | 실제 Guard의 401 | 모든 추론 route에서 보호 함수 미실행 | 유지·보강. spy registry/scheduler 및 receive 호출 0회 |
| test_ms_scheduler.test_cancel_frees_slot_and_stops_generation / test_cancelled_waiting_request_never_starts | Ticket.cancel 이후 scheduler 정리 | ASGI disconnect가 cancel로 연결되는지 | 유지 + API/프로세스 층 추가 |
| test_queue_limit_rejects_new_chat_and_frees_after | chat queue overflow/회복 | embedding/rerank queue | 유지 + one-shot admission 회귀 |
| test_mon_summary.test_failure_keeps_events_pending | 모델 실패 후 event 보존/재시도 | JSON 파일 누락 후 가짜 성공 | 유지·보강 |
| test_mon_lawgo.test_oc_never_leaks | HTTP 오류 URL masking | 200 response raw body echo | 유지·보강 |
| test_mon_legalize의 정상·재실행·강제이력 테스트 | 정상 변경 수집, 동일 실행 dedup | 원문이 과거 version으로 되돌아감 | 유지·보강 |
| test_calendar_review_api | 실제 service token, snapshot, publication, idempotency | token reject 시 DB function 호출이 전혀 없는지 | 유지. invalid post {}만 대신 valid payload와 no side effect 추가 |
| test_ms_models / test_ms_scheduler | fake loader + 실제 MLX 작은 tensor/BatchGenerator | OS GPU 미지원 환경의 수집 | 순수 Python와 MLX 파일 분리 후보 |

원본을 외부 임시 디렉터리에 복사한 다음 기존 Python 실행 파일을 사용했다. 설치·lock 변경 없음.

```sh
PYTHONDONTWRITEBYTECODE=1 LAWBOT_ENV_FILE='' .venv/bin/python -m pytest -q -c pyproject.toml /tmp/lawbot_ms_review_tests/test_ms_api.py /tmp/lawbot_ms_review_tests/test_ms_revision.py -k 'not test_lru_switch' -o cache_dir=/tmp/lawbot-ms-review-cache --basetemp=/tmp/lawbot-ms-review-run
```

결과는 47 passed, 1 deselected, exit 0, 0.63초다. skip/xfail/xpass 없음. 제외한 test_lru_switch는 실제 GPU 메모리 정리 import가 통합 검토의 다른 실행에서 abort된 경로다. 최초 `/tmp/lawbot-ms-review-tests` 하이픈 폴더 복사에서는 relative import 수집 오류 exit 4가 있었고 Python package 폴더명 수정으로 해소했다. 이것은 코드 결함이나 결함 감지 성공이 아니다.

표적 검증은 `/tmp/lawbot-services-probes.py`, `/tmp/lawbot-services-extra-probes.py`, `/tmp/lawbot-model-disconnect-probe.py`, `/tmp/lawbot-monitor-error-probe.py`, `/tmp/lawbot-model-mutation-probe.py`에서 각각 exit 0이었다. 실행 중 synthetic 값과 boolean만 출력했다. body/queue/disconnect/summary/reversion/echo/filename을 검증했으며 실모델 동작·실인터넷 도달성은 검증하지 않았다. 통합 검토의 전체 suite 결과와 중복 집계하지 않도록 개별 실행으로 보고한다.


### 백엔드·평가 회귀의 우선 보강

| 테스트 | 현재 보호·놓치는 조건 | 방향·삭제 시 손실 |
| --- | --- | --- |
| test_calc_amount_spans / test_calc_router_ambiguity | 같은 문자열 재사용·모호성은 보호, 서로 다른 문자열의 겹치는 span은 누락 F001 | 유지·보강. 독립 위치의 같은 금액 정상 케이스 유지 |
| test_feedback / test_web feedback | 저장 정상·중복·세션 소유권, 실패시 API 거짓성공 F002 누락 | 유지·보강. 실패 후 재시도 저장 확인 |
| test_web 로그인·입력 | admin fixture의 제한·일반적인 invalid data, F003–F005 누락 | admin 설정 유무 parameterize, 거부시 inference0 |
| test_api_client | JSON 구조·timeout·일부 누락, index 의미 F006 누락 | 순열·음수·초과·중복 회귀 유지 |
| test_eval_v4 metadata | commit 정보 존재, monorepo dirty 범위 F007 누락 | 현 경로 변경시 dirty 검증 |
| test_diff_answers | 정상 CSV/self 양성 대조와 수치 비교, 빈 비교 F008 누락 | 유지·보강. 이 자기비교 테스트는 행수 assertion도 있어 삭제 후보 아님 |
| test_scoring 5개 | 기록별 결과 재현, 빈 DataFrame F009 누락 | 유지·보강. 기준5pass/빈 변형5pass, ID집합·행수 assertion 추가 |

실제 인증 함수 대신 UI 상태만 mock한 frontend 검사는 UI의 책임만 보장한다. 서버 보안 회귀에서는 Guard/current/require_admin을 실제로 통과하고 모델·외부 HTTP·저장만 가짜 의존성으로 격리한다. test request status만으로 보호 대상 함수가 실행되지 않았다고 결론내리지 않도록 spy를 추가한다. 삭제 확정 대상으로 판단한 테스트는 없다.

### 캐시·RAG·평가 테스트의 추가 정독 결과

이 단계는 정적 정독이며 새 테스트 통과 수를 추가하지 않았다. 실제 인증·DB 경계를 검증하는 테스트와 외부 모델 경계만 대체한 테스트를 구별했다.

| 테스트/범위 | 실제 보호하는 동작 | 놓치는 범위와 판단 | 방향 |
|---|---|---|---|
| `test_pipeline.py:296-305` 위 함수 | 방금 0으로 만든 fake count만 확인 | 실제 import, auth 검사, 서버 시작 모두 미호출 | F029 보강 |
| `test_exact_cache.py:116-120` 계산 질문 lookup | 실제 ExactCache guard를 실행하고 저장소 호출 시 pytest.fail | guard 자체를 mock하지 않음. 유효한 조기 거절 테스트 | 유지 |
| `test_exact_cache.py:123-126` stage skip | actual store.stage의 등록 개수 1 | 잘못된 항목 하나를 등록해도 이 함수만으론 통과할 수 있음. 같은 파일 legacy/split 테스트 및 carry tests가 관련 정책을 별도로 검사하므로 suite 전체 무효 아님 | 보강: DB에 ordinary만 있고 calculation 없음을 ID로 assertion. 삭제 시 direct stage 진입 보호 손실 |
| `test_exact_cache.py:129-164`, `test_cache_carry.py:145-177,327-377` | legacy 데이터 준비 동안만 계산 guard를 껐다가 context 종료 후 실제 제외/승인 금지·lookup을 검사 | guard mock이 검증 단계까지 이어지지 않음 | 유지, mock 자체를 문제 삼지 않음 |
| `test_exact_cache.py:316-400` | 실제 SQLite 트랜잭션 rollback, 철회, TTL, citation 불일치, concurrent revoke, exclusive lock fallback | 실제 운영 DB/권한/다중 프로세스 부하 아님 | 유지 |
| `test_cache_carry.py:294-324` | 실제 login/session role 및 Origin guard로 401/403/409, actual persistent 승인·idempotency·audit | 반환코드만 보지 않고 마지막 승인·audit도 확인. 다만 각 거부 직후 storage/preview 호출이 0인지 직접 확인하진 않음 | 유지+거부 직후 generation/audit unchanged, protected handler spy 추가 |
| `test_cache_carry.py:535-576` | actual batch 승인·재전송 receipt·mode·DB audit | limiter.allowed만 false로 patch하는 부분은 route429 매핑 검사이며 limiter 알고리즘 검증 아님 | 유지; rate algorithm 시험은 auth 단위 테스트와 분리해 명명 |
| `test_cache_carry.py:588-670` | actual batch rollback, stale release/generation, revoked IDs, audit0 | verified_inputs는 synthetic corpus 경계, auth는 actual. 안전성 핵심 처리를 fake로 바꾸지 않음 | 유지 |
| `test_pipeline.py:195-201,244-266` | 실제 LLM wrapper generator close, FakeStream.closed, timeout/error 변환 | Python generator→SDK close까지. 운영 모델 엔진의 실제 취소 완료는 보장하지 않음 | 유지, 서버 취소는 별도 model-server/배포 경계 |
| `test_reranker.py:61-121` | 실제 Reranker 정렬·tie·metadata·잘못된 input에서 upstream 미호출, 점수 범위 | ScoreClient가 float list를 반환하므로 actual HTTP 응답 index 정합성(F006)을 검증할 수 없음 | 유지. F006은 api_client 경계에 negative/duplicate/missing/out-of-range index 추가 |
| `test_decree.py:84-104` | 저장 fp16 점수의 attach 필터 수치·문맥 순서 재현, 입력 수444/1110 및 표본200 명시 | 실시간 모델 품질 아님 | 유지; 저장 결과 회귀라고 보고 |
| `test_eval_v4.py:176-222,628-733` | 실제 eval stage/resume/count/CSV/요약 및 no-extra-call/fresh-tag 사전 거절 | fake ret/model이라 실모델 추론 품질 아님. stage routing만 검사하는 다른 함수의 mocks는 적절함 | 유지 |
| `test_eval_data.py:88-93` scope_controls | ID uniqueness/참조·조문 일치 | 이 함수만 보면 header-only 빈 표에서 all/빈집합이 참이 될 수 있음. 그러나 `test_eval_v4.py:206`이 actual oos end-to-end에서 in_control=20을 기대하므로 suite의 무검출 결함으로 확정하지 않음 | 저비용 보강: len(c)==문서화 기대수; 기존 F009와 구별 |
| `test_layers.py:8-22` | 명시된 runtime 모듈의 tools 비의존 | 수동 목록 밖 신규 runtime 파일의 지연 import는 미포함. SQLite 제한 테스트46-63는 rglob 전수 | P3 유지보수: 대상 목록 자동 검증/AST import 검사 보완, 실제 위반 발견 아님 |
| `test_embed_cache.py:30-105` | 실제 npz/cache, probe mismatch, broken/unwritable, dual index bit equality | Enc는 외부 모델 경계만 대체. 모델 변경 probe에 관한 제품 설계 판단은 별도 | 유지 |
| `test_eval_reasoning.py:24-115` | 실제 run_questions 출력·집계, load-stage에서 중단, 실패 원인/민감정보 redaction | service.ask는 평가 runner의 외부 의존. 추론 pipeline 자체를 검사한다고 주장하면 안 됨 | 유지 |
| `test_request_log.py` | actual service와 log_summary의 성공/실패/취소 metadata, 질문 비기록, 월별 로그, timing 범위 | 생성기 close 로그만으로 실제 모델 취소 보장 불가 | 유지 |
| `test_webapp.py`, `test_max_seq.py` | actual config/format/source grouping/env parsing, fake token count 기반 크기·rounding 및 실제 local tokenizer synthetic 파일 | `check_deploy`가 fake server 성공한다고 운영 배포 정상 증거가 되지 않음 | 유지 |

`test_cache_carry.py:123-124`의 동일한 active_token monkeypatch 중복 1줄은 삭제 가능한 사소한 중복이다. 별도 높은 우선순위 결함으로 늘리지 않는다. `test_eval_v4.py:156` fake_app의 settings 매개변수가 미사용인 것은 호출 signature 호환을 위한 fixture이며 제품 미사용 API로 분류하지 않는다.

## E. 미사용 코드 및 단순화 제안

### 미사용과 호출 계약의 단순화

- `services/chatbot/src/lawbot/calc_numbers.py:88-98`의 `parse_quantity`: 저장소 전체 reference 검색에서 정의와 `test_calc_routing.py` 테스트만 확인. `%/개월` parser는 현재 calculator runtime이 사용하지 않는다. repo 내부 미사용으로 확인, 공개 Python import 외부 사용은 확인 불가이므로 삭제 후보(확정 삭제 아님). 제거시 해당 미래 단위 parser 보호가 없어지지만 현재 금액 계산 계약에 영향 없음. CLI/entry point/동적등록 검색상 연결 없음.
- `services/chatbot/src/lawbot/service.py:54-77`의 `ChatModels.get`의 `(llm,None)` 반환과 service/eval/smoke에서의 load-time unpack는 과거 로컬 로딩 지표 잔재 후보. 모델 API 경계 자체는 유용하므로 없애지 말고 호출자 반환 계약을 정리하는 작은 변경만 제안. log schema 외부 소비 확인 전 삭제 금지.
- `feedback_store`의 로컬 write error swallow와 Feedback layer 반환값을 F002에서 정리하면 설명하기 어려운 중첩 성공 계약이 줄어든다.
- exact cache의 release/namespace/generation/protection fence는 승인 변경·코퍼스 변경·검증셋 누출 방어 책임이 있어 단지 모듈 수가 많다는 이유로 통합/삭제 권고하지 않는다. 전체 세부 SQL 경합 검증은 일부만 확인했다.

### 미사용·단순화·규칙

- 확인된 작은 미사용 selector는 `packages/lawbot-ui/src/calendar.css:42`의 `.lc-key-help`이다. 실제 `MonthCalendar.tsx:83`는 id가 lc-key-help이고 class는 lc-caption이다. 두 프런트와 shared 전체에서 class 생성이나 동적 등록을 찾지 못했다. selector를 id로 맞출지 규칙을 지울지 선택할 수 있다. 화면 여백만 달라질 수 있으므로 시각 확인이 필요하다. 동작상 중대 결함으로 올리지 않는다.
- 프런트 source의 Gradio import·의존성·route는 발견하지 않았다. 두 package.json의 runtime deps는 실제 사용한다. 동적 import되는 `demo.ts`는 개발 preview 경로가 있어 삭제 대상이 아니다. shared source와 icons도 실제 참조된다. TypeScript noUnused 검사를 통과했다는 사실만으로 export나 CSS까지 전부 사용됨을 증명하지 않는다.
- `services/law-calendar/frontend/src/law-calendar/HoldDialog.tsx`는 shared Modal과 showModal/close/focus 복원/Tab wrapping을 중복한다. 기존 shared Modal로 합치면 같은 접근성 결함을 두 곳에서 고칠 필요가 줄어든다. header·hold state·pagination은 HoldDialog에 남긴다. focus 첫 위치와 Escape·페이지 변경·닫기 복원 browser 테스트를 보존해야 한다. 합치는 과정에서 focusable element selector가 달라질 수 있는 단점이 있다. 현재 기능 장애를 확정한 항목은 아니다.
- `api.ts`와 `adminApi.ts`는 정상 호출과 관리자 오류(code/answerId/idempotency) 책임이 다르다. request를 무조건 하나의 범용 client로 합칠 이유가 부족하다. F014에 해당하는 logout 완료 처리는 App의 중복 handler를 통합하는 작은 변경으로 해결할 수 있다.
- Chat/turns/applyEvent의 API casting은 작은 shape 검증과 명확한 오류 전환 후보다. 현재 matching backend 출력에서 재현한 오류가 없어 추가검증/유지보수 항목이다. 대형 validation framework나 전역 상태 관리 도입을 제안하지 않는다.
- CachePanel은 상태/선택/승인/감사/미리보기를 약330줄에 담는다. 명확한 독립 하위화면을 추출할 수 있지만 현재 복잡도의 직접적인 결함은 찾지 못했다. 신규 abstraction·registry로 나눌 근거가 없다.
- 대화는 page memory에서 계속 누적되고 매 이벤트마다 모든 turns를 map한다. 긴 대화에서 old Markdown도 재렌더될 가능성이 있으나 계측하지 않았다. 현재는 성능 측정 후보로 두고, 필요할 때 완료 turn memoization/표시 범위 제한을 검토한다. 임의의 메시지 최대 수를 요구사항으로 만들지 않는다.

### 추가 검증 필요와 작은 정리 후보

1. LawGoClient.download는 공식 API의 첨부 링크를 그대로 허용하고 공통 HttpClient가 redirect를 따른다. `services/monitor/src/law_monitor/lawgo.py:51-59` → `services/monitor/src/law_monitor/utils/http.py:44-46,69-82` 흐름이다. upstream 응답/redirect가 공격자 제어일 때 arbitrary HTTP target 및 본문 무제한 수신이 가능할 수 있다. 현재 웹 사용자가 이 URL을 직접 지정하는 API는 없다. 외부 공개 자체가 이 권한을 새로 부여하지 않는다. 정적 확인된 경계 부족으로 기록하고 SSRF가 운영에서 가능하다고 단정하지 않는다. 공식 첨부 host/path 정책을 실제 응답 표본에 맞추고 redirects·byte limit를 합성 transport로 검증하는 작은 작업이 적절하다.
2. `services/monitor/src/law_monitor/state.py`의 `seen_posts` 테이블은 현재 제품 소스·CLI의 데이터 읽기/쓰기 참조가 없고 테스트 `test_mon_basics.py:196`는 테이블 존재만 확인하며 문서에는 구 계획 설명이 있다. 현재 Assembly 수집은 items/collector_baselines를 사용한다. 새 DB schema에서 정리 후보이며 기존 DB DROP은 필요성·외부 사용자 확인 후 별도 migration 판단이 필요하다. 보안 방어 코드는 아니다.
3. Scheduler와 config의 MAX_CONCURRENT_LIMIT=16 중복은 규칙 단일 위치로 옮길 수 있다. 다른 보안 경계의 인증을 합치는 제안과 다르다. 현재 불일치는 없어 P3 유지보수 제안이다.
4. Monitor가 State/sqlite 및 HTTP 클라이언트를 명시적으로 close하지 않지만 CLI는 단발 프로세스다. 긴 수명의 재사용 API/테스트에서는 누적될 수 있다. 새 factory/DI framework보다 Monitor.close/context manager 및 소유권 명시 정도가 적절하다. 현재 장기 운영 daemon leak으로 확인한 것은 아니다.
5. `services/model-server/src/lawbot_model_server/api.py`의 `exception_body`는 내부 경로를 포함한 원래 예외 메시지를 authenticated caller에게 돌려준다. 루프백 service token 경계 안의 진단 목적은 존재한다. `services/chatbot/src/lawbot/web.py:299-303` 및 `services/chatbot/src/lawbot/view.py:119-137`에서 일반 실패는 유형별 사용자 안내로 변환됨을 확인했다. raw exception은 서버 로그에 남으므로 모델 API 비공개와 로그 파일 접근·보관 정책은 유지한다. 이 후보를 현재 웹 UI의 raw traceback 유출로 확정하지 않았다.
6. 가짜 엔진을 쓰는 Scheduler 테스트와 MLX tensor·batch 테스트가 `test_ms_scheduler.py`에 함께 있다. 모듈을 수집하는 단계에서 MLX·MLX-LM을 import하며 최초 리뷰 환경에서는 이 과정에서 중단됐다. 테스트 파일을 분리하면 해당 import 없이 검증할 수 있는 Scheduler 동작을 별도로 실행할 수 있다.
7. `revision.fingerprint`는 각 파일의 앞뒤 1 MiB만 해시한다. `services/model-server/src/lawbot_model_server/revision.py:1-4`의 '가중치 변경은 이것으로 잡힌다' 문구는 파일 중간의 동일 크기 변경까지 보장하지 않는다. 이 의도적 비용 절충을 partial fingerprint로 정확히 문서화하고 immutable 배포 artifact/hash와 혼동하지 않게 한다. 테스트는 edge/size/name 변경만 보장한다.


위 후보는 내부 참조가 없는 selector·테이블·parser와 외부 API 사용 여부를 모르는 함수를 구분한다. 삭제 전에 entry point·문서 수동 실행·동적 등록·공개 import 소비자를 다시 확인한다. 정확 일치 캐시의 승인·코퍼스 generation fence, API 인증과 관리자 Origin, 모델 서버 Bearer는 서로 다른 책임이므로 줄 수를 줄이기 위해 합치거나 제거하지 않는다. 영어 docstring과 좁은 try는 다음 기능 수정 범위에서 적용하며 기존 한글 주석 일괄 교체 작업을 만들지 않는다.

## F. 다음 세션의 수정 작업 계획

각 작업은 회귀 실패 확인 → 최소 수정 → 관련 계층 통과 → 동작 보존 리팩터링 순서다. 명령은 수정 세션의 허용된 개발 환경에서 실행하며 기본 pytest의 모델·network·live 제외를 유지한다. 이번 리뷰에서 환경 때문에 미실행한 테스트는 그 환경을 확보한 다음 수행한다.

| 작업·발견사항 | 최소 대상·목적 | 먼저 실패시킬 회귀 | 수정 후 명령·완료 조건·선행 |
| --- | --- | --- | --- |
| 1 F001 | calc_router/complete_amount_span, 겹친 금액 할당 거부 | 부분문자열/같은금액 독립위치 | `pytest services/chatbot/tests/test_calc_amount_spans.py services/chatbot/tests/test_calc_router_ambiguity.py services/chatbot/tests/test_calculators.py` 후 계산 대역 E2E. guidance와 정상 계산 모두 유지 |
| 2 F002 | Feedback.save_row/write, feedback_store.write, web.feedback_ | 파일·DB 실패 후 동일 요청 재시도 | `pytest services/chatbot/tests/test_feedback.py services/chatbot/tests/test_web.py` 저장0이면 성공표시0, 복구 후1회 저장 |
| 3 F003–F005 | web read_json/valid_chat/login/stream | byte상한, model타입, admin없음 login window, queue포화 | `pytest services/chatbot/tests/test_web.py services/chatbot/tests/test_auth.py services/chatbot/tests/test_web_integration.py` 모델호출 전 거부·queue회복. 정상 batch/UX 제한값 결정 필요 |
| 4 F012–F014 | useChatStream.send/stop, App logout 두 경로, api reader | pending중 retry, unmount, logout503 | frontend `npm run typecheck && npm test && npm run build`, `pytest -m e2e_stub` 진행요청1·취소·실패안내·logout 후 me401 |
| 5 F015/F027 | AnswerMarkdown, web/admin API 헤더 | 외부이미지·민감응답 header | frontend tests + test_web/test_admin_api, 외부자동요청0/no-store. 배포 CSP·Cache Rules 별도 |
| 6 F018–F020 | model api.parse/run_call/chat_completions, Scheduler.call | 본문 상한·임베딩과 리랭크 대기열·첫 토큰 전과 비스트리밍 생성 중 연결 종료 | `pytest services/model-server/tests/test_ms_api.py services/model-server/tests/test_ms_scheduler.py` queued회복·호환 API·GPU단일스레드 유지. 웹 admission과 연계 |
| 7 F023 | LawGoClient._service와 collector error 전파 | HTTP200 synthetic key echo | `pytest services/monitor/tests/test_mon_lawgo.py services/monitor/tests/test_mon_runner.py services/monitor/tests/test_mon_summary.py` 예외/log/DB/report canary0 |
| 8 F021 | legalize_git _Builder/Monitor event identity | X→A→B→A, 삭제반복, 같은run재실행 | `pytest services/monitor/tests/test_mon_legalize.py services/monitor/tests/test_mon_runner.py` 실제변경3개·재실행0, 기존state 호환 정책 먼저 |
| 9 F022/F024/F025 | summarize.load_events, GitRepo.run, summary filename | 이벤트 JSON 누락·Git 시간 초과·요약 파일명 충돌 | monitor 해당 tests, partial/fail 표시·pending보존·lock해제·파일보존 |
| 10 F006 | ModelServerClient.rerank/embed 응답 경계 | 음수·누락·초과·정상역순 | `pytest services/chatbot/tests/test_api_client.py services/chatbot/tests/test_reranker.py services/chatbot/tests/test_pipeline.py` 정확 index 연결·형식오류 통일 |
| 11 F007–F009 | eval_v4.git_state, diff_answers.main, scoring tests | 서비스파일 dirty·empty/no-common·empty fixture | `pytest services/chatbot/tests/test_eval_v4.py services/chatbot/tests/test_diff_answers.py services/chatbot/tests/test_scoring.py` 정상기준 보존·변형실패. 실모델 불필요 |
| 12 F026 | 두 package.json/lock | 영향버전 점검 | 호환 패치 후 양쪽 typecheck/test/build 및 stub E2E, 기존 코드검증 먼저. 이번 세션 설치 없음 |
| 13 F016/F017 | CopyButton, SSEParser | clipboard없음/reject, splitCRLF | 해당 Vitest와 전체 frontend, LF·기존 UX 보존 |
| 14 F028 | README/architecture/AGENTS 실행·평가 설명 | 문서/현재 지원 profile 대조 | diff와 링크 검사, 과거 결과 보존·현재 평가로 오인하지 않음 |
| 15 F031 | build_articles._compose_history의 expiry owner 경계 | 타법 동번호 부칙+자기법 양성대조, build_rows deleted 확인 | `pytest services/chatbot/tests/test_build_articles.py services/chatbot/tests/test_articles_check.py services/chatbot/tests/test_articles_update.py` 뒤 기존 코퍼스 영향 읽기 비교. 다음 조문 생성 전에 처리 |
| 16 F032 | citation.parse, answer_format, eval_v4.cite_gold의 공유 계약 | 등록법 다음 미등록법, 약칭·생략 정상 목록 | `pytest services/chatbot/tests/test_citation.py services/chatbot/tests/test_eval_v4.py services/chatbot/tests/test_webapp.py` 오인용 false·잘못된 근거 카드 제외. F031과 독립 |
| 17 F030/F029 | eval compare 누락 처리와 실제 import 회귀 | metric 누락/NaN strict 실패, 실제 import 및 부작용 변형 | `pytest services/chatbot/tests/test_eval_v4.py services/chatbot/tests/test_pipeline.py services/chatbot/tests/test_citation.py services/chatbot/tests/test_layers.py` 정상 통과·각 변형은 의도된 assertion/sentinel로 실패. F007–F009 작업과 함께 가능 |
| 18 F033 | check_deploy.check_system_file | 빈/공백/정상 system 파일 | `pytest services/chatbot/tests/test_webapp.py`에서 FAIL 보고·후속 점검 지속, 정상 요약 보존. 실모델 불필요 |

작업 번호는 참조용이다. 정확성 영향이 큰 1·15·16과 안전성 작업 3–7을 먼저 하고, 각 결함의 회귀를 같은 변경에 포함한다. 이후 평가·실효성 보강과 미사용 정리·동작 보존 단순화를 진행한다.

F010은 문서의 실행 명령과 환경 파일 로딩을 맞춘 뒤 합성 설정과 가짜 클라이언트로 확인한다. F011은 결과 금액을 항목별로 대조해야 하는지 결정하고, 계산 카드가 계산기 결과를 표시하는 계약을 유지한다. E절의 미사용·중복 정리는 위 결함과 회귀 테스트 이후 진행한다.

챗봇 외부 공개의 필수 코드/보호 계약은 F003/F004/F014/F015/F027이며 F012/F013/F020은 정상 취소·추론 수명 검증에 함께 포함한다. F018/F019는 내부 모델 API를 외부에 전달하지 않는 것을 우선 전제로 삼고 내부 클라이언트 오용에도 상한을 보완한다. F026은 개발 서버 접근 가능 조건을 확인하고 공개 경로에서 Vite를 제외한다. F001/F031/F032는 법률·계산 정확성을 내세우는 기능의 공개 전 품질 작업이다. 나머지 P2가 모두 동일한 보안 공개 차단 항목인 것은 아니다.

Cloudflare·호스트 작업은 코드 작업과 별도다. 대상 사용자/host/path·Tunnel 또는 DNS proxy·Access 선택, origin 우회 차단·proxy 신뢰·TLS·cache·로그 보관·token 회수 절차를 먼저 문서화한다. 기존 Tailscale 관리 경로의 허용 사용자와 인터넷 공개 경로를 함께 확인한다. 공개 직전에는 J절의 합성 계정 검증과 실제 edge SSE/cache 검사를 수행하고 준비도를 재판정한다. 계획을 만들었다는 이유로 I절의 상태를 확인 완료로 바꾸지 않는다.
## G. 한계와 최종 상태

재검토에서 아래 동작을 다시 재현했다. 나머지 발견사항은 해당 코드 경로와 테스트를 대조했다. F026은 두 잠금 파일·설치본과 Vite 공식 권고의 버전 범위를 대조했다.

| 재확인 대상 | 실행 방법과 관찰 결과 |
| --- | --- |
| F001, F011 | Python에서 가짜 분류 결과로 계산 라우터를 호출했다. 단일 700만원 구간을 두 항목에 배정해 2,925,000원이 반환됐다. 결론 검사에서는 항목명을 바꾸거나 허용된 입력 금액을 덧붙여도 통과했다. |
| F002, F004, F005 | 인프로세스 TestClient와 임시 경로를 사용했다. 피드백 저장 실패 뒤 첫 응답은 `saved: true`, 재시도는 `saved: false`였다. 관리자 미설정에서 로그인 실패 6회 모두 401이었고, 배열 모델 값은 500을 반환했다. |
| F008, F030 | 임시 CSV로 빈 답변 비교가 종료 코드 0을, 비교 지표 누락이 빈 경고 목록을 반환함을 확인했다. |
| F017 | Node에서 실제 SSE 파서에 CRLF를 청크 경계로 나눠 전달하자 앞 이벤트가 사라지고 마지막 이름에 CR이 남았다. |
| F023 | HTTPX MockTransport로 비정상 응답을 반환하자 합성 표식이 예외 메시지에 포함됐다. |
| F031, F032, F033 | 합성 타법 부칙으로 현재 법률의 조문이 만료됐다. 미등록 법령의 조문을 앞 법령의 인용으로 해석해 채점 함수와 화면용 인용 분류 함수가 모두 인정했다. 공백 시스템 파일 점검은 `IndexError`로 중단됐다. |

재검토에서는 제품 테스트 스위트, 실제 모델 E2E, 모델 스모크, 품질 평가를 실행하지 않았다. 최초 리뷰의 일부 MLX 단위 검사와 HTTP 통합·대역 E2E는 미완료 상태다. F031이 기존 코퍼스에 미친 영향, 운영 프로세스·데이터·로그, Cloudflare와 Tailscale의 실제 공개 설정은 미확인이다. 보안 권고 대조는 K절에 적은 범위다.

## H. 보안 구조와 접근 경로 표

### 현재 확인 구조

```text
브라우저
  → 문서상 Tailscale HTTPS 또는 로컬 브라우저
  → loopback FastAPI chatbot ── 같은 origin dist + /api/*
       ├─ 세션/관리자 인가 → 피드백·코퍼스·정확일치 캐시 SQLite
       ├─ Bearer → loopback model-server → MLX 모델
       └─ 관리자 세션 → 허용 경로 프록시 → loopback calendar + 서비스 토큰

별도 브라우저 → calendar FastAPI → 공개 projection + 독립 SPA
monitor CLI → 고정 수집 출처·관리자 설정 URL → 파일/상태 DB → calendar import
            └─ Bearer → 내부 model-server 요약
```

SPA가 사용자 입력과 화면 상태를 관리하고 서버가 실제 인증·인가를 수행한다. 모델 서버는 웹앱과 수집기의 서버 토큰을 신뢰하며 사용자 신원을 판정하지 않는다. 정확 일치 캐시는 승인된 공통 답변이고 사용자 대화의 자동 공유 캐시와 구별된다. 일반 사용자의 대화 전체를 서버에서 다시 조회하는 API는 없다. feedback turn 접근은 서버 세션별로 분리되며 관리자는 의도적으로 전체 피드백을 검수한다.

| 경로 또는 기능 | 현재 접근 범위와 근거 | 인증·인가/자원 접근 | 제한·격리 | 향후 공개·미확인  관련 ID |
| --- | --- | --- | --- | ---  --- |
| chatbot `/`, `/assets/*`, `/{path:path}` | 코드·설정에서 127.0.0.1 고정, 안전한 합성 실행에서 dist 한정 확인 | 로그인 전 SPA 정적 파일, 모델/DB 호출 없음 | dist realpath 경계, index no-cache, API fallback 제외 | 웹 공개 후보. reverse proxy root, 기존 별도 서버 확인  — |
| `POST /api/login` | loopback 코드, Tailscale은 문서에만 기재 | 비밀번호 확인→메모리 세션, admin이면 감사 DB | 최대 세션 200, admin 설정 때 로그인 window. 일반 body 상한 없음 | 제한 공개에 보호 포함. 로그인 제한 항목 참조  F003/F004 |
| `POST /api/logout`, `GET /api/me`, `GET /api/config` | 동일 웹앱 | 서버 세션 필수, logout 세션 삭제 | 7일 TTL, 사용자별 세션, 캐시 헤더 보완 대상 | 인증 만료·브라우저 cookie·프록시 HTTPS 확인  F014/F027 |
| `POST /api/chat` | 동일 웹앱 | 세션→입력 검증→검색/계산/리랭크/생성, 요청 로그 | 질문 2000자, 설정 모델 allowlist, 동시성 semaphore, 무제한 waiter. fetch SSE | 로그인된 사용자까지 공개 후보. 본문/큐 제한·취소 항목 참조  F001/F003/F005/F012/F013/F020/F032 |
| `POST /api/feedback` | 동일 웹앱 | 세션→turn 소유권→파일 또는 SQLite | turn 세션 귀속, 저장 실패 처리 보완 | 공개 사용자 데이터 처리·보관 기간 결정  F002 |
| `/api/admin/feedbacks`, `/feedbacks/{feedback_id}`, `/articles` | admin 설정 시 등록 | 서버 admin role, DB 읽기 | 페이지·filter 제한, 일반 사용자 거부 | 관리자 전용. HTTP cache no-store 보완  F027 |
| `/api/admin/feedbacks/{feedback_id}/review`, `/feedbacks/promote-up`, `/feedbacks/{feedback_id}/promote-down`, `/promotions/{promotion_id}/revoke` | 위와 동일 | admin+Origin+JSON+rate+멱등키, DB transaction | 64KiB body, POST 30/60초/세션, 승격 재검사 | 관리자 전용, edge 예외·직접 원본 확인  F027 |
| `/api/admin/cache`, `/cache/answers/{answer_id}`, `/cache/actions`, `/cache/approvals` | 위와 동일 | cache 검수·승인·중지, admin role | POST 공통 guards·멱등 처리 | 관리자 전용, preview 이미지 정책·cache 헤더  F015/F027 |
| `/api/admin/calendar/{path:path}`, `POST /api/admin/calendar/law-holds/{kind}/{hold_id}` | 관리자 전용 프록시, localhost 설정만 허용 | 경로 allowlist, 고정 origin, redirect off, private API에 서비스 토큰 | timeout·관리 POST guards, 서버 토큰은 브라우저에 없음 | 공개 SPA에서 직접 내부 calendar admin 토큰 전달 금지  F027 |
| model `GET /health`, `GET /v1/models` | 코드 127.0.0.1 고정 | Guard Bearer 필수, revision/model metadata | 토큰 미설정 fail-closed, API docs 비활성 | 외부 비공개 유지, 모든 별도 포워딩 확인  — |
| model `POST /v1/chat/completions`, `/v1/embeddings`, `/v1/rerank` | 위와 동일 | Bearer→Pydantic→scheduler→모델 | body 8MiB 판정 시점 보완, chat queue 제한, call queue 보완, 공통 schema | 외부 비공개 유지. 백엔드 서버만 허용  F006/F018/F019/F020 |
| calendar `GET /api/law-calendar`, `/api/law-events`, `/api/law-changes/{change_id}`, `/api/law-changes/{change_id}/notice-comparison/{page}`, `/api/law-calendar/status`, `/api/law-holds` | 코드 loopback, 문서상 독립 공개 읽기 | 공개 이벤트·갱신 상태·보류 요약을 조회한다. 입법예고 비교 페이지는 공개 상세에서 연결된 PNG를 반환한다. | 쿼리 검증, 목록 페이지 크기 상한, `no-store`·`nosniff` | 공개 데이터 범위와 철회 동작을 유지 |
| calendar `/api/admin/law-holds/{kind}/{hold_id}`, `/api/admin/import-failures` | admin router 코드 | 서비스 Bearer, 미설정 거부, DB 검수/읽기 | 토큰 검증·body·decision 검증 | 외부 공개 제외 권장. 사용자별 인증은 chatbot proxy 담당  — |
| calendar `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` | FastAPI 기본 등록을 create_app 코드/로컬 route 조사로 확인 | schema 문서, endpoint 인증 자체를 우회하지 않음 | 별도 인증 없음 | 공개 필요성 결정, 실제 네트워크 노출 미확인  — |
| monitor·tools CLI, DB backup/import, 데이터 갱신 | 호스트 실행 권한 필요 | 외부 수집·요약·파일/DB 쓰기·일부 배포 재시작 기능 | CLI 입력과 신뢰된 호스트 계정 경계 | 웹 공개 대상 아님, 실행 계정·파일권한·보관 확인  F007–F011/F021–F025/F030/F031 |

표의 근거 표시는 **코드 또는 설정에서 확인**, **안전한 로컬 실행으로 확인**, **문서에만 기재**, **운영 환경 또는 외부 관리 설정 확인 필요**를 구분한다. 실제 운영 노출을 안전한 로컬 실행으로 대신 확인한 것으로 해석하지 않는다.

각 `/api/admin` 축약 행은 chatbot의 prefix를 포함한다. 캘린더 admin 행은 별도 프로세스다. 저장소에서 Dockerfile/Compose/publish 설정을 확인하지 못했으므로 컨테이너 배포를 현 구조로 그리지 않았다. 운영 listen 포트·호스트명·접속 URL·토큰값은 기록하지 않았다.

### 향후 공개 방식별 전제

제한 공개의 초기 제안은 허용된 사용자만 chatbot origin에 도달하도록 보호하는 방식이다. Cloudflare Tunnel을 선택하면 같은 호스트의 loopback 웹앱과 캘린더의 필요한 공개 읽기 호스트만 연결한다. Access를 선택하면 SPA와 보호 API·스트림·관리 경로 전체에 일관된 정책을 적용하고 cloudflared의 Protect with Access 또는 애플리케이션 중 책임 계층 한 곳에서 유효한 토큰을 검증한다. cloudflared 검증을 신뢰하려면 해당 경계 우회 차단을 확인한다. 헤더 존재나 JWT decode만으로 신원을 믿지 않는다. 서명·issuer·audience·만료와 검증 실패 시 거부가 합격 기준이다.

불특정 사용자 공개는 캘린더의 공개 읽기와 챗봇의 GPU 사용 권한을 따로 결정해야 한다. Access를 전체 공개 사이트의 필수 조건으로 두지 않는다. 애플리케이션 인증을 유지할지 공개 demo로 바꿀지, 허용 기능·개인정보 보관·생성 비용·동시 요청·큐 대기를 결정해야 한다. 현재 단일 일반 계정 구성을 다중 사용자 계정 체계로 간주하지 않는다.

DNS 프록시 방식을 선택하면 origin에 필요한 ingress가 생기므로 IPv4·IPv6·별도 호스트명·포트 포워딩의 직접 접근을 통제하고 origin TLS 검증을 확인해야 한다. Tunnel 방식의 outbound 연결 특성과 혼합하지 않는다. 사용자↔Cloudflare는 HTTPS, Cloudflare↔connector/origin은 선택 방식의 암호화·인증, 같은 호스트 connector↔app와 app↔model은 loopback HTTP라는 구간별 판단이 필요하다. 원격 호스트·컨테이너로 옮기면 그 구간의 TLS·접근 제어를 새로 검증한다.

## I. Cloudflare 외부 공개 전 점검표

| 점검 항목 | 상태 | 근거와 합격 기준 |
| --- | --- | --- |
| 공개 대상 사용자·host/path 결정 | 외부 설정 확인 필요 | chatbot 제한/공개 demo 미정. calendar 공개 읽기는 문서 의도. 관리자·모델은 별도 비공개 |
| SPA/API/model 경계 | 확인 완료 | 코드상 SPA+API 같은 origin, 모델 별도 Bearer loopback. 실제 배포 매핑은 별도 검증 |
| 원본 직접 접근·IPv6·별도 dev server | 외부 설정 확인 필요 | NAT, firewall, Serve/Funnel, 다른 reverse proxy/hostnames를 확인. 의도된 tailnet 관리 접근은 정책으로 구분 |
| 선택 인증·보호 경로 누락 | 외부 설정 확인 필요 | `/api/*`, admin, SSE까지 테스트. 모든 API 권한은 서버가 확인 |
| Access 선택 시 토큰 검증 | 외부 설정 확인 필요 | cloudflared 또는 origin의 서명·issuer·aud·expiry 검증 책임과 bypass 차단 확인 |
| SPA logout·서버 세션 일치 | 수정 필요 | 로그아웃 실패·unmount 취소 발견사항 수정, 새로고침 후 재로그인 여부 검증 |
| API·파일 접근 | 확인 완료 | 코드 세션/role·dist 경계와 합성 symlink/상위 경로 확인. 실제 프록시 root는 미확인 |
| 사용자·turn·stream 격리 | 확인 완료 | 서버 세션에 turn 귀속, 스트림 작업 ID 구독 API 없음. 다중 계정 설계 변경 시 재검증 |
| 본문·생성량·동시성·큐 | 수정 필요 | 웹 본문·waiter, 모델 call queue/body 누적과 disconnect 보완. 숫자는 장비 측정 후 확정 |
| 로그인 요청 남용 | 수정 필요 | admin 설정 유무와 무관한 제한. edge rate limit은 보조, 앱에서 명확히 거부 |
| 프록시 헤더 신뢰 | 외부 설정 확인 필요 | Uvicorn 기본/환경의 trusted proxy를 실제 확인. 무제한 trust 설정 금지, local connector만 신뢰 |
| TLS·Secure cookie·Origin | 배포 후 검증 필요 | 외부 HTTPS가 서버 scheme에 반영되고 admin same-origin 요청 성공. 인증서 검증 해제 없음 확인 |
| index와 hashed assets 캐시 | 배포 후 검증 필요 | index revalidate, assets 버전 일치. `/index.html` 직접 제공 경로도 같은 정책 확인 |
| 민감 API shared cache | 수정 필요 | admin·me·chat 등에 no-store 명시, Cloudflare API bypass 규칙 확인. no-cache는 저장 금지가 아님 |
| SSE 연결·취소·인증만료 | 배포 후 검증 필요 | POST fetch streaming, 재연결 자동없음. 실제 idle·first-token·완료·cancel·Access 만료 처리 확인 |
| Quick Tunnel 적합성 | 확인 완료 | 공식 문서는 SSE 미지원 명시. 이 앱 chat 공개 수단으로 선택하지 않는 판단 근거 확인 |
| Vite 개발 서버·HMR 비공개 | 외부 설정 확인 필요 | repo는 loopback이며 GHSA 영향8.3.4. 수정 버전 갱신·dev 비공개 보장 필요 |
| 프런트 번들 비밀정보 | 외부 설정 확인 필요 | 소스의 VITE 비밀 참조 없음, 임시 build의 sourcemap 0개·demo marker 부재는 확인. 전체 dist 비밀 스캔·서버 env canary·운영 build 주입값 검증은 미실행 |
| 모델 Markdown 안전성 | 수정 필요 | raw HTML/script 차단 확인, 외부 이미지 자동요청 제한 또는 검증된 CSP 적용 |
| CSP·클릭재킹·nosniff | 외부 설정 확인 필요 | 챗봇 서버 CSP/frame 정책 없음. 필요 헤더 최소 적용 후 local fonts·Markdown·fetch 스트림 확인 |
| 비밀정보·오류 로그 | 수정 필요 | monitor upstream body 마스킹 보완. 로그/DB 파일 읽기 권한·보관·rotation 검증 |
| 의존성 보안 권고 | 수정 필요 | Vite 영향 권고 확인. Python 검토 권고 범위는 K절, 전체 transitive audit 미실행 |
| WAF/rate/challenge | 외부 설정 확인 필요 | 플랜과 path별 실제 기능 확인. API/SSE에 browser challenge가 들어오는 오류 UI 테스트 |
| 공개 중단·자격증명 회수 | 외부 설정 확인 필요 | 선택한 host route/Access 정책을 회수하는 담당자·절차와 토큰 교체 후 확인을 문서화 |
| 공개 후 안전 재검증 | 배포 후 검증 필요 | 합성 계정·짧은 요청으로 인증·origin 우회·cache·SSE·정적 파일 확인. 부하 시험과 분리 |
| 파일 upload·WebSocket·LLM 도구 실행 | 해당 없음 | 현재 자체 작성 제품 경로에서 해당 기능 없음. Vite HMR은 dev 전용 |

점검표의 코드 항목은 저장소 근거로 판정했다. 실제 공개 준비도를 평가하려면 선택한 접근 정책, 원본으로의 접근 경로, 캐시 규칙과 스트리밍 동작을 해당 배포에서 확인해야 한다.

## J. 보안 회귀 테스트 제안

아래는 다음 세션·배포 시 수행할 제안이다. C절과 B절에 실행 결과가 명시된 경우만 이번 실행에 해당한다.

| 시나리오 | 계층·환경·가짜 의존성 | 기대 결과 |
| --- | --- | --- |
| 무인증 직접 chat/admin/feedback | ASGI 실제 app+fake model/counters | 401/403, handler·DB 쓰기·추론 호출 0 |
| 잘못된/만료 쿠키·logout 후 요청 | 실제 Sessions + TestClient, clock 제어 | 세션 실패와 재인증, 동일 쿠키 재사용 거부 |
| A/B 세션 turn 접근 | 서로 다른 TestClient cookie jar + fake 답변 | B가 A의 turn feedback 요청 시 거부, 저장/추론 0. 일반 계정명 동일이어도 세션 분리 |
| admin 권한·Origin·멱등키 | 실제 guards+합성 SQLite | user403, 잘못된 Origin403, 인증정보 없이 guard mock 금지, 재시도 중복 상태변경 없음 |
| 합성 비밀 파일·symlink·경로 이동 | ASGI dist 임시 폴더 | 외부 표식 반환 없음, docs report 미제공, 없는 API404 |
| oversized JSON·chunked·type 오류 | ASGI request generator + fake model | 상한 초과 chunk에서413, 소비량 제한, model=[]400, 모델 호출0 |
| 생성 파라미터·embedding/rerank 배치 | 모델 실제 schema/Guard+fake registry | 허용범위 밖 모델·tokens·batch 거부. 웹 allowlist UI를 우회한 직접 요청 포함 |
| 전체/사용자 큐 초과 | fake blocking model + 제한된 소수 async 요청 | 정한 상한 이후429/503, 대기 deadline, count 복구, 무제한 task 누적 없음 |
| first-token 전·중간 연결 종료 | ASGI disconnect + fake job gate, 브라우저 fake stream | job.cancel, generator close, slot release. 프런트 stop/unmount signal.abort |
| 프록시 헤더 위조 | 로컬 격리 Uvicorn 두 source 경계 | 신뢰되지 않는 source의 XFF/XFP/CF 헤더가 신원·role·rate key를 바꾸지 않음 |
| Markdown 출력 | 실제 AnswerMarkdown+합성 URL/HTML, 브라우저 request interception | script/js URL 비실행, 외부 이미지 자동 요청0, 링크/표/한글 표시 유지 |
| 번들 비밀 포함 여부 | 임시 build, 서버 env에 합성 canary | dist 어디에도 canary없음, DEV demo·sourcemap 정책 일치 |
| SPA direct URL·refresh | 빌드 + isolated server/browser | 챗봇 fallback 정상, api404, 로그인/관리 서버 권한 독립. 캘린더 루트 계약 유지 |
| cache 유출 | 두 합성 세션 + 실제 배포 edge | 인증·관리·대화 응답 no-store, 다른 사용자 body 재사용0, edge HIT 없음 확인 |
| Cloudflare bypass | 배포 담당자가 허용된 합성 요청 | origin/다른 hostname/IPv6에서 요구한 보호 우회 불가. 모델 API 공개 경로 없음 |
| Access 선택 시 JWT 실패 | fake issuer keys/토큰, 검증 담당 계층 | 누락·잘못된 서명·issuer/aud·expiry·키조회 실패 모두 거부 |
| 로그 유출 | 합성 upstream error에 canary key 삽입 | 예외·logger·summary·state·failure event에 canary0 |
| 공개 중단·회수 | 배포 변경 권한을 별도 부여한 운영 검증 | 공개 route 회수 후 접근 종료, 기존 정책·token 회수의 효과 확인 |

사용자별 데이터 인가는 Access 로그인 성공과 별개다. 현재 구현에 없는 사용자 파일·작업 ID 기능은 테스트 대상으로 만들어 내지 않았다. 미래에 추가하면 소유권 검증을 해당 API 경계에 추가한다. 세션 만료를 요청 시점에 검사하는 현재 동작과 이미 열린 스트림을 즉시 종료해야 하는 정책은 별도 결정 사항이다.

## K. 참고 근거

아래 공식 문서와 권고를 2026-11-12 KST에 저장소 설정 및 잠금 버전과 대조했다. 비교 범위는 표에 기재한 권고다.

| 자료·직접 링크 | 적용 제품·버전과 근거 | 한계 |
| --- | --- | --- |
| [Vite RCE GHSA-p4f5-76q4-ffq7](https://github.com/vitejs/vite/security/advisories/GHSA-p4f5-76q4-ffq7) | 사용8.3.4, 영향8.3.0–8.3.6, 해당 branch 수정8.3.7. 개발 서버 도달 조건 | 정적 dist 서비스의 RCE로 확대하지 않음. 실제 노출·공격 미검증 |
| [Vite 경로 권고 GHSA-rq7h-c2jc-7f22](https://github.com/vitejs/vite/security/advisories/GHSA-rq7h-c2jc-7f22), [Wasm 권고 GHSA-vfpm-58rq-9qcg](https://github.com/vitejs/vite/security/advisories/GHSA-vfpm-58rq-9qcg) | 8.3 branch 영향≤8.3.2, 수정8.3.3. 저장소8.3.4 범위 밖 | 신규 RCE 권고와 별개 |
| [Vite 환경변수](https://vite.dev/guide/env-and-mode), [서버 설정](https://vite.dev/config/server-options) | Vite8 설정과 VITE_ build 노출·host/allowedHosts 의미 | 운영 CLI override와 번들 주입값 미확인 |
| [React Effects](https://react.dev/learn/synchronizing-with-effects) | React19.3/설치19.3.1 cleanup 및 dev StrictMode | frontend 재진입은 이벤트 경로로 따로 재현 |
| [react-markdown 보안·API](https://github.com/remarkjs/react-markdown#security) | 설치10.1.0, 기본 URL transform·raw HTML 경계, 실제 DOM 검증 | 모델이 악성 이미지를 생성하는 빈도 미측정 |
| [Cloudflare self-hosted 공개 앱](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) | Access 선택 시 token 검증 책임 | 계정에 적용된 정책 확인 아님 |
| [Cloudflare 원본 보호](https://developers.cloudflare.com/fundamentals/security/protect-your-origin-server/) | Tunnel outbound 구조와 DNS proxy origin 보호 검토 | 방화벽·IP allowlist 실제 검증 없음 |
| [Tunnel origin parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/) | Protect with Access·TLS 검증·origin 설정 | cloudflared 설치 버전·원격 관리 ingress 미확인 |
| [Cloudflare 캐시 제어](https://developers.cloudflare.com/cache/concepts/cache-control/) | no-store 저장 금지, no-cache 재검증 차이 | Cache Rules/Edge TTL 실제 설정 미확인 |
| [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) | 검토 시 공식 문서의 SSE 미지원 | 실제 Tunnel 실행 없음 |
| [Starlette URL 권고](https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3) | 영향<1.3.0, 수정1.3.0. lock1.8.0/1.10.0 범위 밖 | 전체 Starlette 취약점 부재 보증 아님 |
| [Starlette form 제한 권고](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq) | 영향≥0.4.1,<1.3.1, 수정1.3.1. 두 lock 범위 밖 | JSON body 자체 제한 문제는 별도 |
| [Starlette Host 권고](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr) | 영향≤1.0.0, 수정1.0.1. 두 lock 범위 밖 | 신뢰 proxy 배포 설정 보증 아님 |
| [Starlette Range DoS](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8) | 영향0.39.0–0.49.0, 수정0.49.1. 두 lock 범위 밖 | FileResponse 사용은 있음. 공격 실행 없음 |
| [FastAPI CSRF 권고](https://github.com/fastapi/fastapi/security/advisories/GHSA-8h2j-cgx8-6xv7) | 영향<0.65.2, 수정0.65.2. 사용0.143.3 범위 밖, 앱 JSON guard 별도 확인 | 모든 CSRF/Origin 정책 충분성 보증 아님 |

Uvicorn 공식 settings 페이지는 fetch timeout으로 내용을 확인하지 못했다. 대신 로컬 설치본 0.54.0의 Config 기본값과 ProxyHeadersMiddleware 연결을 확인했으며 B절에 파일·라인을 남겼다. 운영 환경의 override 값은 별도 확인이 필요하다. 전체 npm/Python transitive advisory 자동 audit, 원격 Actions 실행 결과 조회, Cloudflare 플랜별 WAF entitlement 확인은 하지 않았다. 소스·프롬프트·비공개 설정·리뷰 본문을 외부 분석 서비스에 업로드하지 않았다.

### 파일 목록과 검토 깊이 상세

루트 `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `pyproject.toml`, `uv.lock`, `.gitignore`, `.env.example`, `.env.dev.example`, `.github/workflows/ci.yml`, `services/chatbot/pyproject.toml`, `tools/lawbot-tools/pyproject.toml`도 실행·검증 경계에 포함했다. `.env.*.example`은 변수 이름과 기본 동작만 확인했고 실제 `.env` 값을 출력하지 않았다.

### 범위와 남은 읽기 제약

전체 파일 목록은 아래 inventory로 인계한다. '검토'는 정독/선택구간/목록·참조검색을 구분한다. 대규모 평가 CSV/학습 원시데이터/모델 가중치 전수 정독 안 함; schemas/loaders/splits/recorded fixtures 경로를 확인. 평가 실행 도구는 운영 부작용(모델호출,결과쓰기,git/재시작) 때문에 실행하지 않고 분석만 했다. 모든 테스트 본문을 한 줄씩 전수 정독한 것은 아니다. 선택 검토 파일은 전수 정독 파일과 구분해 인계한다.

- 깊게 검토: web/auth/security/api_client/llm/settings/service; calc_router/calculators/calc_numbers/calc_gate/calc_corpus; retriever/reranker/decree/feedback/exact_cache/cache_runtime/cache_carry/cache_eval/embed_cache/corpus_store; 주요 admin path; envfile/common API/law_scope/logs.
- 추가 전체 정독 완료: feedback_store, cache_store, cache_admin, cache_release_store, cache_review_store, cache_seed, audit_store, dataset_store, qa_rules, manifest, db/*.py, answer_format, citation, hits. 추가 전체 정독 완료: deploy/check_deploy/view. 캐시 generation fence, DB transaction/idempotency, CSV byte 복원·hash 검증은 필요한 안전성 경계로 판단했다.
- tools 깊게 검토: score_answers/eval_reasoning/diff_answers/log_summary/reindex/max_seq/feedback_review/feedback_export/assign_splits/qa_split/export_dataset_snapshot/migrate_csv_to_sqlite/versions/smoke_decree, articles_update의 주요 갱신/롤백 경로와 최근 변경.
- tools 추가 전체 정독 완료: eval_v4 1141줄, eval_calc_router 215줄, articles_check 303줄, build_articles 1185줄. refusal도 전체 정독했다. 정독과 별개로 운영 업데이트·전체 평가를 실행하지 않았다.
- tests 전체 정독: conftest, chat_support, test_scoring, test_calc_conclusion, test_auth, test_diff_answers, test_reindex, test_pipeline, test_exact_cache, test_cache_carry, test_reranker, test_decree, test_layers, test_embed_cache, test_eval_reasoning, test_eval_v4, test_eval_data, test_request_log, test_webapp, test_max_seq. test_web/계산 span/ambiguity/test_api_client/test_feedback/test_citation은 주요 assertion·fixture·관련 오류 경로 정독이며 모든 본문 전수 정독으로 분류하지 않았다.
- 나머지 tests: 파일목록, skip/xfail/early-return/mocks/assert 패턴, 대상모듈 연결을 검색. 이름이 같은 입력이라는 이유로 중복으로 지적하지 않음. E2E fixtures는 운영 호출/서버 프로세스 부작용 분류를 통합 검토에서 추가 검토.
- 최근 변경 범위: `0eac5af^..HEAD`에서 이 담당 검토 소유 경로 stat 및 articles_update/build_articles/common diff 확인; calc/cache 최신 구현 직접 검토. 과거 모든 commit diff 전수 확인 아님. 통합 검토에서 실제20개 commit 목록은 B절에 기재했다.
- 임시 검증 프로세스는 모두 종료되었다. 최종 임시 파일 정리와 작업 트리 대조는 문서 끝의 확인 기록을 따른다.

#### Inventory: Backend authored source/SQL/JSON (57개)

목록화 완료; 파일별 검토 깊이는 위 그룹 분류 기준.

```text
services/chatbot/src/lawbot/__init__.py
services/chatbot/src/lawbot/admin.py
services/chatbot/src/lawbot/answer_format.py
services/chatbot/src/lawbot/api_client.py
services/chatbot/src/lawbot/audit_store.py
services/chatbot/src/lawbot/auth.py
services/chatbot/src/lawbot/cache_admin.py
services/chatbot/src/lawbot/cache_carry.py
services/chatbot/src/lawbot/cache_eval.py
services/chatbot/src/lawbot/cache_release_store.py
services/chatbot/src/lawbot/cache_review_store.py
services/chatbot/src/lawbot/cache_runtime.py
services/chatbot/src/lawbot/cache_seed.py
services/chatbot/src/lawbot/cache_store.py
services/chatbot/src/lawbot/calc_corpus.py
services/chatbot/src/lawbot/calc_gate.py
services/chatbot/src/lawbot/calc_numbers.py
services/chatbot/src/lawbot/calc_router.py
services/chatbot/src/lawbot/calc_statutes.json
services/chatbot/src/lawbot/calculators.py
services/chatbot/src/lawbot/check_deploy.py
services/chatbot/src/lawbot/citation.py
services/chatbot/src/lawbot/corpus_store.py
services/chatbot/src/lawbot/dataset_store.py
services/chatbot/src/lawbot/db/__init__.py
services/chatbot/src/lawbot/db/backup.py
services/chatbot/src/lawbot/db/cache_migrations/0001_exact_cache.sql
services/chatbot/src/lawbot/db/cache_migrations/0002_release_review.sql
services/chatbot/src/lawbot/db/cli.py
services/chatbot/src/lawbot/db/connection.py
services/chatbot/src/lawbot/db/corpus_import.py
services/chatbot/src/lawbot/db/csv_import.py
services/chatbot/src/lawbot/db/dataset_import.py
services/chatbot/src/lawbot/db/feedback_import.py
services/chatbot/src/lawbot/db/importers.py
services/chatbot/src/lawbot/db/migrate.py
services/chatbot/src/lawbot/db/migrations/0001_init.sql
services/chatbot/src/lawbot/db/migrations/0002_corpus_csv_format.sql
services/chatbot/src/lawbot/decree.py
services/chatbot/src/lawbot/deploy.py
services/chatbot/src/lawbot/embed_cache.py
services/chatbot/src/lawbot/envfile.py
services/chatbot/src/lawbot/exact_cache.py
services/chatbot/src/lawbot/feedback.py
services/chatbot/src/lawbot/feedback_store.py
services/chatbot/src/lawbot/hits.py
services/chatbot/src/lawbot/llm.py
services/chatbot/src/lawbot/manifest.py
services/chatbot/src/lawbot/paths.py
services/chatbot/src/lawbot/qa_rules.py
services/chatbot/src/lawbot/reranker.py
services/chatbot/src/lawbot/retriever.py
services/chatbot/src/lawbot/security.py
services/chatbot/src/lawbot/service.py
services/chatbot/src/lawbot/settings.py
services/chatbot/src/lawbot/view.py
services/chatbot/src/lawbot/web.py
```

#### Inventory: Backend tests/fixtures (62개)

목록화 완료; 파일별 검토 깊이는 위 그룹 분류 기준.

```text
services/chatbot/tests/admin_test_data.py
services/chatbot/tests/cache_review_test_server.py
services/chatbot/tests/chat_support.py
services/chatbot/tests/conftest.py
services/chatbot/tests/e2e_model_server.py
services/chatbot/tests/e2e_support.py
services/chatbot/tests/fakes.py
services/chatbot/tests/fixtures/calc_conclusion_replay.json
services/chatbot/tests/fixtures/calc_router_val.json
services/chatbot/tests/test_admin_api.py
services/chatbot/tests/test_api_client.py
services/chatbot/tests/test_articles_check.py
services/chatbot/tests/test_articles_update.py
services/chatbot/tests/test_articles_update_db.py
services/chatbot/tests/test_assign_splits.py
services/chatbot/tests/test_auth.py
services/chatbot/tests/test_build_articles.py
services/chatbot/tests/test_cache_carry.py
services/chatbot/tests/test_cache_review_e2e.py
services/chatbot/tests/test_calc_amount_spans.py
services/chatbot/tests/test_calc_conclusion.py
services/chatbot/tests/test_calc_corpus.py
services/chatbot/tests/test_calc_router_ambiguity.py
services/chatbot/tests/test_calc_router_latency.py
services/chatbot/tests/test_calc_routing.py
services/chatbot/tests/test_calculation_e2e.py
services/chatbot/tests/test_calculators.py
services/chatbot/tests/test_citation.py
services/chatbot/tests/test_corpus_from_db.py
services/chatbot/tests/test_db.py
services/chatbot/tests/test_decree.py
services/chatbot/tests/test_diff_answers.py
services/chatbot/tests/test_e2e_calendar_review.py
services/chatbot/tests/test_e2e_live.py
services/chatbot/tests/test_e2e_live_preflight.py
services/chatbot/tests/test_e2e_stub.py
services/chatbot/tests/test_embed_cache.py
services/chatbot/tests/test_eval_data.py
services/chatbot/tests/test_eval_reasoning.py
services/chatbot/tests/test_eval_v4.py
services/chatbot/tests/test_exact_cache.py
services/chatbot/tests/test_exact_cache_e2e.py
services/chatbot/tests/test_export_dataset_snapshot.py
services/chatbot/tests/test_feedback.py
services/chatbot/tests/test_layers.py
services/chatbot/tests/test_manifest.py
services/chatbot/tests/test_max_seq.py
services/chatbot/tests/test_migrate_csv_to_sqlite.py
services/chatbot/tests/test_pipeline.py
services/chatbot/tests/test_qa_rules.py
services/chatbot/tests/test_qa_split.py
services/chatbot/tests/test_reindex.py
services/chatbot/tests/test_request_log.py
services/chatbot/tests/test_reranker.py
services/chatbot/tests/test_scoring.py
services/chatbot/tests/test_security.py
services/chatbot/tests/test_settings.py
services/chatbot/tests/test_v5_data.py
services/chatbot/tests/test_web.py
services/chatbot/tests/test_web_integration.py
services/chatbot/tests/test_webapp.py
services/chatbot/tests/web_server.py
```

#### Inventory: Maintenance/evaluation tools (21개)

목록화 완료; 파일별 검토 깊이는 위 그룹 분류 기준.

```text
tools/lawbot-tools/src/lawbot_tools/__init__.py
tools/lawbot-tools/src/lawbot_tools/articles_check.py
tools/lawbot-tools/src/lawbot_tools/articles_update.py
tools/lawbot-tools/src/lawbot_tools/assign_splits.py
tools/lawbot-tools/src/lawbot_tools/build_articles.py
tools/lawbot-tools/src/lawbot_tools/diff_answers.py
tools/lawbot-tools/src/lawbot_tools/eval_calc_router.py
tools/lawbot-tools/src/lawbot_tools/eval_reasoning.py
tools/lawbot-tools/src/lawbot_tools/eval_v4.py
tools/lawbot-tools/src/lawbot_tools/export_dataset_snapshot.py
tools/lawbot-tools/src/lawbot_tools/feedback_export.py
tools/lawbot-tools/src/lawbot_tools/feedback_review.py
tools/lawbot-tools/src/lawbot_tools/log_summary.py
tools/lawbot-tools/src/lawbot_tools/max_seq.py
tools/lawbot-tools/src/lawbot_tools/migrate_csv_to_sqlite.py
tools/lawbot-tools/src/lawbot_tools/qa_split.py
tools/lawbot-tools/src/lawbot_tools/refusal.py
tools/lawbot-tools/src/lawbot_tools/reindex.py
tools/lawbot-tools/src/lawbot_tools/score_answers.py
tools/lawbot-tools/src/lawbot_tools/smoke_decree.py
tools/lawbot-tools/src/lawbot_tools/versions.py
```

#### Inventory: Common source/tests/package config (10개)

목록화 완료; 파일별 검토 깊이는 위 그룹 분류 기준.

```text
packages/lawbot-common/pyproject.toml
packages/lawbot-common/src/lawbot_common/__init__.py
packages/lawbot-common/src/lawbot_common/api.py
packages/lawbot-common/src/lawbot_common/envfile.py
packages/lawbot-common/src/lawbot_common/law_scope.json
packages/lawbot-common/src/lawbot_common/law_scope.py
packages/lawbot-common/src/lawbot_common/logs.py
packages/lawbot-common/src/lawbot_common/ports.py
packages/lawbot-common/tests/test_common_api.py
packages/lawbot-common/tests/test_common_envfile.py
```
### 범위와 깊이

- `services/model-server/src/lawbot_model_server`의 Python 12개를 전체 읽었다. API 인증·입력·실패·취소, scheduler 수명, registry LRU, ChatModel/BatchEngine, 임베딩·리랭크, 설정·CLI·revision을 추적했다.
- `services/monitor/src/law_monitor`의 Python 25개를 전체 읽었다. CLI → Monitor → collector → event/state → check → summarize, HTTP와 Git 부작용, 공식 자료와 파일 출처를 추적했다.
- `services/law-calendar/src/law_calendar`의 Python 21개를 전체 읽었다. 공개 조회와 관리 API, SQLite migration·읽기·쓰기, import·snapshot·승인·취소·재시도, 시행일 파싱, 공식 PDF 제한을 검토했다.
- 모델 서버 API·revision 테스트는 전체 본문과 fixture를 검토하고 실행했다. scheduler/models/server/real_models는 import 경계·테스트 함수·대표 assertion과 관련 취소·큐 테스트를 검토했다. 이 중 MLX 환경이 필요한 테스트의 수학적 적정성 전체는 실행 확인하지 못했다.
- monitor/calendar 테스트는 전체 파일을 목록화하고 테스트 함수·assertion·fixture·표적 실패 경로를 확인했다. 대표 상세 정독은 monitor summary/runner/lawgo, calendar review API/workflow/automation/scoped changes/notice content다. 법률 원문 해석을 포함하는 모든 합성 케이스의 독립 법률 정확성까지 확인한 범위는 아니다.
- `.github/workflows/ci.yml`, `deploy/launchd` 2개, monitor launchd 예시, 각 Python 패키지 설정, monitor targets YAML을 읽었다. monitor·calendar README 중 실행·권한·데이터·점검 경로를 확인했다. 로컬 launchd 활성화 상태를 조회하지 않았다.
- 최근 20개 커밋 각각의 이 경로 diff-tree를 확인했다. 해당 20개 커밋의 일반 부모 diff에서 이 담당 경로 변경은 없었다. 공통 law_scope 변경은 백엔드·공통 패키지 범위에서 함께 검토했다. 추가로 경로별 과거 이력의 커밋 ID 목록을 조회했으나 그 전체 patch를 검토했다고 주장하지 않는다.

### 파일 inventory

- `.github/workflows/ci.yml`
- `deploy/launchd/com.lawbot.model-server.plist.example`
- `deploy/launchd/com.lawbot.web.plist.example`
- `services/law-calendar/README.md`
- `services/law-calendar/pyproject.toml`
- `services/law-calendar/src/law_calendar/__init__.py`
- `services/law-calendar/src/law_calendar/admin.py`
- `services/law-calendar/src/law_calendar/article_fragments.py`
- `services/law-calendar/src/law_calendar/assembly_ingest.py`
- `services/law-calendar/src/law_calendar/automation.py`
- `services/law-calendar/src/law_calendar/db.py`
- `services/law-calendar/src/law_calendar/effective_clause.py`
- `services/law-calendar/src/law_calendar/effective_scope.py`
- `services/law-calendar/src/law_calendar/import_failures.py`
- `services/law-calendar/src/law_calendar/ingest.py`
- `services/law-calendar/src/law_calendar/manage.py`
- `services/law-calendar/src/law_calendar/models.py`
- `services/law-calendar/src/law_calendar/notice_comparison.py`
- `services/law-calendar/src/law_calendar/notice_content.py`
- `services/law-calendar/src/law_calendar/notice_source.py`
- `services/law-calendar/src/law_calendar/review.py`
- `services/law-calendar/src/law_calendar/schema.py`
- `services/law-calendar/src/law_calendar/server.py`
- `services/law-calendar/src/law_calendar/source_addenda.py`
- `services/law-calendar/src/law_calendar/sync.py`
- `services/law-calendar/src/law_calendar/validation.py`
- `services/law-calendar/tests/calendar_test_fixtures.py`
- `services/law-calendar/tests/calendar_ui_support.py`
- `services/law-calendar/tests/test_calendar_api.py`
- `services/law-calendar/tests/test_calendar_assembly.py`
- `services/law-calendar/tests/test_calendar_automation.py`
- `services/law-calendar/tests/test_calendar_notice_content.py`
- `services/law-calendar/tests/test_calendar_review_api.py`
- `services/law-calendar/tests/test_calendar_scoped_changes.py`
- `services/law-calendar/tests/test_calendar_server_envfile.py`
- `services/law-calendar/tests/test_calendar_sync.py`
- `services/law-calendar/tests/test_calendar_ui.py`
- `services/law-calendar/tests/test_calendar_workflow.py`
- `services/model-server/pyproject.toml`
- `services/model-server/src/lawbot_model_server/__init__.py`
- `services/model-server/src/lawbot_model_server/api.py`
- `services/model-server/src/lawbot_model_server/chat.py`
- `services/model-server/src/lawbot_model_server/check.py`
- `services/model-server/src/lawbot_model_server/config.py`
- `services/model-server/src/lawbot_model_server/embeddings.py`
- `services/model-server/src/lawbot_model_server/envfile.py`
- `services/model-server/src/lawbot_model_server/registry.py`
- `services/model-server/src/lawbot_model_server/rerank.py`
- `services/model-server/src/lawbot_model_server/revision.py`
- `services/model-server/src/lawbot_model_server/scheduler.py`
- `services/model-server/src/lawbot_model_server/server.py`
- `services/model-server/tests/__init__.py`
- `services/model-server/tests/conftest.py`
- `services/model-server/tests/ms_fakes.py`
- `services/model-server/tests/test_ms_api.py`
- `services/model-server/tests/test_ms_models.py`
- `services/model-server/tests/test_ms_real_models.py`
- `services/model-server/tests/test_ms_revision.py`
- `services/model-server/tests/test_ms_scheduler.py`
- `services/model-server/tests/test_ms_server.py`
- `services/monitor/.env.example`
- `services/monitor/README.md`
- `services/monitor/config/targets.yaml`
- `services/monitor/deploy/com.lawbot.monitor.plist.example`
- `services/monitor/pyproject.toml`
- `services/monitor/src/law_monitor/__init__.py`
- `services/monitor/src/law_monitor/app.py`
- `services/monitor/src/law_monitor/articles.py`
- `services/monitor/src/law_monitor/assembly.py`
- `services/monitor/src/law_monitor/attachments.py`
- `services/monitor/src/law_monitor/checks.py`
- `services/monitor/src/law_monitor/cli.py`
- `services/monitor/src/law_monitor/collectors/__init__.py`
- `services/monitor/src/law_monitor/collectors/assembly.py`
- `services/monitor/src/law_monitor/collectors/base.py`
- `services/monitor/src/law_monitor/collectors/legalize_git.py`
- `services/monitor/src/law_monitor/config.py`
- `services/monitor/src/law_monitor/enrich.py`
- `services/monitor/src/law_monitor/events.py`
- `services/monitor/src/law_monitor/frontmatter.py`
- `services/monitor/src/law_monitor/gitrepo.py`
- `services/monitor/src/law_monitor/lawgo.py`
- `services/monitor/src/law_monitor/llm.py`
- `services/monitor/src/law_monitor/lock.py`
- `services/monitor/src/law_monitor/paths.py`
- `services/monitor/src/law_monitor/secrets.py`
- `services/monitor/src/law_monitor/state.py`
- `services/monitor/src/law_monitor/summarize.py`
- `services/monitor/src/law_monitor/utils/__init__.py`
- `services/monitor/src/law_monitor/utils/http.py`
- `services/monitor/tests/fixtures/lawgo/admrul_2100000232308.json`
- `services/monitor/tests/fixtures/lawgo/law_284987.json`
- `services/monitor/tests/fixtures/lawgo/law_286379.json`
- `services/monitor/tests/mon_helpers.py`
- `services/monitor/tests/test_mon_assembly.py`
- `services/monitor/tests/test_mon_assembly_network.py`
- `services/monitor/tests/test_mon_basics.py`
- `services/monitor/tests/test_mon_checks.py`
- `services/monitor/tests/test_mon_lawgo.py`
- `services/monitor/tests/test_mon_legalize.py`
- `services/monitor/tests/test_mon_runner.py`
- `services/monitor/tests/test_mon_scope.py`
- `services/monitor/tests/test_mon_summary.py`
### 파일별 목록

다음은 검토 범위에 포함한 추적 파일 전체다. source/tests/config/CSS/HTML은 정적 정독, lock은 버전·의존성·lifecycle 메타데이터 확인을 했다.

- `packages/lawbot-ui/src/ChangeView.test.tsx`
- `packages/lawbot-ui/src/ChangeView.tsx`
- `packages/lawbot-ui/src/EventList.tsx`
- `packages/lawbot-ui/src/Modal.tsx`
- `packages/lawbot-ui/src/MonthCalendar.tsx`
- `packages/lawbot-ui/src/calendar.css`
- `packages/lawbot-ui/src/dates.test.ts`
- `packages/lawbot-ui/src/dates.ts`
- `packages/lawbot-ui/src/index.ts`
- `packages/lawbot-ui/src/types.ts`
- `services/chatbot/frontend/.gitignore`
- `services/chatbot/frontend/.nvmrc`
- `services/chatbot/frontend/fingerprint.ts`
- `services/chatbot/frontend/index.html`
- `services/chatbot/frontend/package-lock.json`
- `services/chatbot/frontend/package.json`
- `services/chatbot/frontend/src/App.test.tsx`
- `services/chatbot/frontend/src/App.tsx`
- `services/chatbot/frontend/src/admin/AdminPage.tsx`
- `services/chatbot/frontend/src/admin/CachePanel.tsx`
- `services/chatbot/frontend/src/admin/CalendarHoldReview.tsx`
- `services/chatbot/frontend/src/admin/CalendarPanel.tsx`
- `services/chatbot/frontend/src/admin/FeedbackCorrectionDialog.tsx`
- `services/chatbot/frontend/src/admin/FeedbackDetail.tsx`
- `services/chatbot/frontend/src/admin/FeedbackList.tsx`
- `services/chatbot/frontend/src/admin/admin.css`
- `services/chatbot/frontend/src/admin/adminApi.ts`
- `services/chatbot/frontend/src/admin/types.ts`
- `services/chatbot/frontend/src/api.ts`
- `services/chatbot/frontend/src/components/Answer.test.tsx`
- `services/chatbot/frontend/src/components/CalculationCard.tsx`
- `services/chatbot/frontend/src/components/Composer.tsx`
- `services/chatbot/frontend/src/components/Login.tsx`
- `services/chatbot/frontend/src/components/Segmented.tsx`
- `services/chatbot/frontend/src/components/SourcesView.tsx`
- `services/chatbot/frontend/src/components/TurnView.tsx`
- `services/chatbot/frontend/src/components/icons.tsx`
- `services/chatbot/frontend/src/main.tsx`
- `services/chatbot/frontend/src/sse.test.ts`
- `services/chatbot/frontend/src/sse.ts`
- `services/chatbot/frontend/src/styles.css`
- `services/chatbot/frontend/src/test-setup.ts`
- `services/chatbot/frontend/src/turns.test.ts`
- `services/chatbot/frontend/src/turns.ts`
- `services/chatbot/frontend/src/types.ts`
- `services/chatbot/frontend/src/useChatStream.test.tsx`
- `services/chatbot/frontend/src/useChatStream.ts`
- `services/chatbot/frontend/tsconfig.json`
- `services/chatbot/frontend/vite.config.ts`
- `services/law-calendar/frontend/.gitignore`
- `services/law-calendar/frontend/.nvmrc`
- `services/law-calendar/frontend/index.html`
- `services/law-calendar/frontend/package-lock.json`
- `services/law-calendar/frontend/package.json`
- `services/law-calendar/frontend/src/law-calendar/ChangeDialog.tsx`
- `services/law-calendar/frontend/src/law-calendar/HoldDialog.test.tsx`
- `services/law-calendar/frontend/src/law-calendar/HoldDialog.tsx`
- `services/law-calendar/frontend/src/law-calendar/LawCalendarPage.test.tsx`
- `services/law-calendar/frontend/src/law-calendar/LawCalendarPage.tsx`
- `services/law-calendar/frontend/src/law-calendar/api-types.ts`
- `services/law-calendar/frontend/src/law-calendar/calendar.css`
- `services/law-calendar/frontend/src/law-calendar/data.test.ts`
- `services/law-calendar/frontend/src/law-calendar/data.ts`
- `services/law-calendar/frontend/src/law-calendar/demo.ts`
- `services/law-calendar/frontend/src/law-calendar/types.ts`
- `services/law-calendar/frontend/src/main.tsx`
- `services/law-calendar/frontend/src/styles.css`
- `services/law-calendar/frontend/src/test-setup.ts`
- `services/law-calendar/frontend/tsconfig.json`
- `services/law-calendar/frontend/vite.config.ts`


### 최초 리뷰의 최종 확인 기록

- 최종 확인 시각: 2026-11-12T00:41:30+09:00 (Asia/Seoul).
- 시작 기준선의 추적 파일 613개 SHA-256을 다시 비교했고 변경 0개였다. main 및 HEAD `193c8d9f789fc7a24a7a4a0ebcdfc3b6910cbf9a`가 그대로다.
- staged/unstaged 변경은 없고 untracked 항목은 이번 보고서 `docs/code_review_20261112_001052.md` 하나다. 시작 당시 기존 사용자 변경사항은 없었다. 코드·테스트·설정·의존성·잠금 파일·데이터·기존 문서를 수정하지 않았다.
- 외부 임시 복사본, 검사 로그·캐시·합성 데이터·표적 검증 스크립트와 병합용 메모를 정리했다. 최종 전송용 임시 Markdown 및 해시 기준선도 저장 검증 후 제거했다. 직접 실행한 명령은 모두 종료되었고 남은 자체 프로세스는 없다. 운영 프로세스는 종료·재시작하지 않았다.
- 재현 설명의 `/tmp` 경로는 이번 실행 위치다. 파일 자체는 정리했으므로 다음 세션은 적힌 입력·가짜 의존성·함수·명령을 자체 임시 디렉터리에 구성해야 한다. 원본 소스 변형이 필요한 시험은 계속 격리 복사본만 사용한다.
- 운영 모델 호출 0회. Cloudflare·DNS·Access·Tunnel·방화벽·공개 설정 변경 0건.
