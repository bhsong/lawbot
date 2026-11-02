# 계획: 웹앱 화면을 Gradio에서 Vite + React SPA로

- 작성일: 2026-11-02
- 범위: `services/chatbot`의 화면(`app.py`, `feedback_ui.py`)과 그 테스트. 검색·리랭크·생성(`service.ask`), 답변 형식(`answer_format`), 인용 판정(`citation`), 요청 로그, 피드백 저장 형식, 모델 서버는 바꾸지 않는다.

## 1. 목표와 결정

| 항목 | 결정 (2026-11-02 사용자) |
| --- | --- |
| 화면 | Vite + React + TypeScript SPA. ChatGPT처럼 깔끔한 단일 대화 칼럼, 채팅방 목록 없음 (단일 턴) |
| 상단 | 제목·법령 기준일 한 줄 + 모델 선택 버튼(Pro \| Fast) + 형식 선택 버튼(간결 \| 전체). 지금의 Radio 두 개 대신 |
| 대화 영역 | 화면 높이에 맞춰 늘어남 (지금 `height=480` 고정) |
| 오른쪽 조문 | 지금처럼 오른쪽 패널. 인용한 조문은 펼치고 검색만 된 조문은 접음. 지난 답변을 누르면 그 답변의 조문으로 바뀜 |
| 휴대폰 | 상단 바 두 줄, 조문은 답변 아래 "근거 조문 n개" 버튼 → 아래에서 올라오는 시트 |
| 기록 | 화면에만 쌓고 저장하지 않음. 새로고침하면 비움 |
| 스트리밍 | Vercel AI SDK 대신 직접 만든 훅(`fetch` + SSE 읽기). 이벤트 형식은 우리가 정함 (2절) |
| 스타일 | 일반 CSS(CSS 변수) + Pretendard(npm으로 묶음, 외부 CDN 없음). Tailwind·UI 키트 없음. 시스템 설정에 따른 다크 모드 |
| 배포 | 운영 반영 때 `npm ci && npm run build`. FastAPI가 같은 포트에서 `dist`를 내보냄. 빌드 결과물은 git에 넣지 않음 |
| 주소 | `127.0.0.1:<LAWBOT_PORT>` 그대로, 로그인 필수 그대로 |

AI SDK를 쓰지 않는 이유: 단일 턴이라 대화 상태 관리가 거의 필요 없고, 화면의 핵심(조문 카드·측정 줄)은 어차피 사용자 정의 데이터다. SDK 스트림 형식은 메이저 버전마다 바뀌어 Python 쪽이 따라가야 한다. 다중 턴·도구 호출이 생기면 다시 본다.

## 2. 서버 API (FastAPI, `lawbot/web.py`)

Gradio가 하던 일을 옮긴다. `service.ask`와 `app.py`의 문구·조립 함수(`source_title`, `fmt_stats`, `STAGE_ERRORS` 등)는 화면과 무관한 모듈(`lawbot/view.py`)로 옮겨 그대로 쓴다.

| 경로 | 인증 | 내용 |
| --- | --- | --- |
| `POST /api/login` | - | `{user, password}`. `hmac.compare_digest`로 비교, 실패 시 1초 지연 후 401. 성공 시 세션 쿠키 (`HttpOnly`, `SameSite=Strict`, 서버 메모리, 재시작하면 다시 로그인) |
| `POST /api/logout` | 필요 | 세션 삭제 (본문 `{}`) |
| `GET /api/me` | 필요 | `{user}`. 화면이 로그인 여부를 확인 |
| `GET /api/config` | 필요 | 제목·인스턴스, 안내 문구, 법령 기준일 줄, 모델 목록(key·label·info·기본 형식·비활성), 형식, 예시 질문, 조문 패널 제목, 피드백 사용 여부와 이유 목록 |
| `POST /api/chat` | 필요 | `{question, model, format}` → `text/event-stream` (아래 이벤트) |
| `POST /api/feedback` | 필요 | `{turn_id, label: like\|flag\|cleared, reason?}` |
| `GET /`, `/assets/*` | - | SPA 파일. 로그인 전에는 SPA가 로그인 화면을 그림. `/api/*` 외 경로는 `index.html` |

- POST는 `Content-Type: application/json`만 받는다 (`SameSite=Strict`와 함께 CSRF 방지).
- 인증 없는 `/api/*`는 401. 로그인 화면에는 제목 외 정보(모델·데이터)를 보이지 않는다 (지금 `test_auth_integration`과 같은 기준).

### 채팅 이벤트 (SSE `event:` 이름 → `data:` JSON)

| 이벤트 | 데이터 | 시점 |
| --- | --- | --- |
| `status` | `{text}` | 대기열·검색 중·모델 전환 중·쓰는 중 (`searching_stats`, 불러오는 중, `WRITING`) |
| `sources` | `{groups: [{label, open, items: [{title, scores, body, decree}]}]}` | 검색 직후(모두 펼침), 끝날 때(인용/검색만 구분) |
| `answer` | `{text}` | 생성 중 화면 글 전체 (간결형은 끝까지 다시 그리므로 조각이 아니라 전체) |
| `stats` | `{text}` | 측정 줄 (`fmt_stats` 그대로, 바뀔 때만) |
| `done` | `{text, finish, turn_id}` | 끝. 빈 답이면 `EMPTY` 문구. `turn_id`는 피드백이 켜져 있을 때만 |
| `error` | `{kind, message}` | `AskError` 종류별 지금 문구 그대로. 설정 오류(`config`)는 자세한 값 없이 안내 + 서버 로그 |

- 동시 처리: `LAWBOT_CHAT_CONCURRENCY` 크기의 세마포어. 자리가 없으면 `status`로 "앞 질문을 기다리는 중" 후 대기 (Gradio 대기열과 같은 동작). `service.ask`(동기 생성기)는 스레드에서 돈다.
- 브라우저가 연결을 끊으면(중지 버튼) 생성기를 닫는다. 끊김은 토큰마다 확인한다: 간결형은 결론 뒤 토큰(근거·설명)이 화면 글을 바꾸지 않아 보낼 조각이 없으므로, 그 단계에도 보내지 않는 `tick`을 내고 확인한다 (PR 2 대역 E2E에서 발견). 모델 서버의 생성이 즉시 멈춘다고 가정하지 않는다 (지침).
- 입력 검사: 질문은 비어 있지 않고 2,000자 이하, 모델은 쓰는 모델, 형식은 `concise`·`full`. 틀리면 400 (모델 서버를 부르지 않음).
- 피드백: 지금 `Feedback`의 저장 형식(JSONL 줄)은 그대로. 서버가 세션별 순번을 매겨 `turn_id`로 찾는 메서드(`remember_turn`·`save`)를 더하고, Gradio 전용(`history`, `LikeData`, 'Like' 문자열, 화면 글 비교)은 PR 3에서 지운다.

## 3. 화면 (`services/chatbot/frontend/`)

- Vite + React + TypeScript. 의존성: `react`, `react-dom`, `react-markdown`, `pretendard`. 개발: `vite`, `typescript`, `vitest`, `@testing-library/react`. 버전은 `package-lock.json`으로 고정, Node 버전은 `.nvmrc`.
- 구성: `useChatStream`(SSE 읽기·중지·다시 묻기) / `TopBar` / `MessageList` / `Composer` / `SourcesPanel`(데스크톱) · `SourcesSheet`(휴대폰) / `Login`.
- 디자인: 최대 폭 약 760px 가운데 칼럼, 질문만 오른쪽 둥근 말풍선, 답변은 말풍선 없이 본문. 답변 아래 아이콘 줄(복사·👍·깃발 4종·다시 묻기). 하단 고정 둥근 입력창(자동 높이, Enter 전송·Shift+Enter 줄바꿈, 생성 중 중지 버튼). 빈 화면은 안내 한 줄 + 예시 질문 카드 3개. 회색 몇 단계 + 강조색 하나. ChatGPT의 로고·이름·고유 아이콘은 쓰지 않는다.
- E2E가 찾는 자리: 역할·라벨 우선, 필요한 곳만 `data-testid` (`lawbot-sources`, `lawbot-stats`는 지금 이름 유지).
- 개발 실행: `npm run dev`(Vite, `/api`를 개발 웹앱 포트로 프록시) 또는 빌드 후 `uv run lawbot-web`.

## 4. PR 나누기

최종 실행 명령은 `uv run lawbot-app` 그대로다. `lawbot-web`은 PR 1·2 동안 개발 트리에서 새 화면을 Gradio와 나란히 확인하는 임시 명령이고 PR 3에서 지운다 (운영 launchd plist·명령은 바뀌지 않음).

| PR | 내용 | Gradio |
| --- | --- | --- |
| **PR 1** | 이 계획서. `view.py`로 화면 문구 함수 이동(동작 같음), `web.py`(로그인·config·chat SSE·feedback·정적 파일), 명령 `lawbot-web`(개발용, `dist`가 없으면 API만), 테스트 | 그대로 운영 화면 |
| **PR 2** | `frontend/` SPA. **가짜 데이터로 화면을 먼저 만들어 스크린샷(데스크톱·휴대폰·다크·생성 중·오류)을 사용자에게 보이고 승인 뒤 연결**. CI에 Node(설치·`tsc`·Vitest·빌드) 추가. SPA 대역 E2E | 그대로 |
| **PR 3** | `lawbot-app`이 FastAPI+SPA를 띄움(launchd plist 그대로), Gradio·`feedback_ui.py`·Gradio 테스트 제거, `gradio` 의존성 제거, `docs/operations.md` 운영 반영 절차에 빌드 추가, `e2e_live`를 새 화면으로 (실행은 승인 후). 휴대폰 접속 경로(Tailscale) 확인 후 https일 때 쿠키 `Secure` | 제거 |

## 5. 테스트 (구현보다 먼저)

### PR 1
| 층 | 파일 | 내용 |
| --- | --- | --- |
| 단위 | `test_view.py` | 옮긴 함수가 지금과 같은 문자열을 만듦 (기존 `test_webapp`·`test_pipeline`의 화면 문구 테스트를 옮김) |
| 단위 | `test_web_events.py` | SSE 줄 형식(한글·줄바꿈 포함 JSON), 이벤트 순서: `start → status → sources → answer… → stats → sources(구분) → done`, 오류 종류별 `error` 문구 |
| 통합 | `test_web_integration.py` | 실제 `lawbot-web` 프로세스 + 가짜 모델: 로그인 없이 `/api/*` 401, 틀린 비밀번호 401(지연), JSON 아닌 POST 거부, 로그인 후 config·chat 스트림 끝까지, 비활성·모르는 모델 400, 피드백 저장 줄이 지금 형식과 같음, 동시 1일 때 두 번째 질문이 기다렸다가 처리 |
| 계약 | 기존 `e2e_model_server` 재사용 | 모델 서버 타임아웃·바쁨·연결 실패가 `error` 이벤트 종류로 |

### PR 2
- Vitest: SSE 파서(조각이 줄 중간에서 끊김, 여러 이벤트 한 덩어리), `useChatStream`(중지·오류·다시 묻기), 조문 패널 펼침/접힘, 지난 답변 선택 시 조문 전환.
- 대역 E2E (`e2e_stub`, Playwright, 지금 `test_e2e_stub.py`의 시나리오를 SPA로): 로그인 → 질문 → 스트리밍 → 조문 카드 3개 → 측정 줄 → 예시 클릭 → 오류 4종(타임아웃·생성 오류·서버 없음·바쁨). 휴대폰 크기에서 시트 열기. 실패 때만 `results/`에 스크린샷.

### PR 3
- 기본 테스트 전체 + 대역 E2E가 새 `lawbot-app`으로. `e2e_live` 3문항은 사용자 승인 후 실행하고 `docs/evals/`에 기록.

## 6. 완료 기준

- PR마다: `uv sync --locked`, ruff check·format, pyright, pytest(기본) 통과. PR 2부터 `npm ci`, `tsc --noEmit`, Vitest, `npm run build` 통과.
- PR 3: 운영과 같은 구성의 대역 E2E 통과, `e2e_live` 3문항 통과(승인 후), 운영 반영 절차 문서화. 운영 반영 자체는 병합 뒤 별도 요청.
- 요청 로그 줄 형식과 피드백 JSONL 형식이 바뀌지 않음 (테스트로 고정).

## 7. 위험과 대응

| 위험 | 대응 |
| --- | --- |
| 로그인을 직접 구현 | 비교는 `hmac.compare_digest`, 쿠키 `HttpOnly`·`SameSite=Strict`, 실패 지연. 통합 테스트로 보호 경로 전부 확인 |
| 운영 반영에 Node 빌드 단계 추가 | 운영 절차에 명령을 적고, `dist`가 없거나 오래되면 `lawbot-app`이 시작을 거부(빌드 지문 확인) |
| CI 시간 증가 | `npm ci` 캐시. 늘어난 시간은 PR에 기록 |
| 휴대폰 화면 | PR 2 스크린샷 승인 단계에서 확인 |
