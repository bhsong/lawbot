# 계획: 브라우저 E2E 테스트 (Playwright)

- 작성일: 2026-11-02
- 작업 브랜치: `feat/e2e-playwright`
- 작업자: Claude Code 에이전트 (계획 → 테스트 → 구현 → PR). **병합은 사용자가 한다.**
- 근거: 사용자가 정한 "AI 웹앱 테스트 구성" 원칙을 Lawbot에 맞춘 초안 (2026-11-02 대화. 최종본은 모델 revision 작업 뒤 레포 md로 들어감)

## 0. 목표와 범위

지금 테스트는 단위·계약(챗봇 클라이언트 ↔ 실제 모델 서버 앱 + 가짜 모델)·`integration`(Gradio 서버 + 가짜 모델, 인증만)·`models`(모델 서버 단독, 실제 모델)까지 있다. **브라우저에서 질문하고 답이 화면에 나오는 흐름**은 어느 층에서도 검증하지 않는다.

두 층을 추가한다.

| 이름 | 진입점 → 모델 | 실행 |
| --- | --- | --- |
| `e2e_stub` | Playwright(Chromium) → 실제 웹앱 프로세스 → **실제 모델 서버 앱 + 가짜 모델** 프로세스 | 기본 `uv run pytest`, CI |
| `e2e_live` | Playwright(Chromium) → 개발 웹앱 프로세스 → **공유 모델 서버(맥 미니, 실제 모델)** | `-m e2e_live`, 사용자 승인 후 맥 미니에서만 |

이번 계획에 넣지 않는 것:
- (같은 PR의 3단계로 옮김) 모델 가중치 revision 노출은 5절 3단계에서 설계를 이 문서에 추가한 뒤 한다.
- 품질 평가(인용 정확도·거절률): 기존 `docs/evals`·`tools/` 그대로.

## 1. 결정

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 브라우저 도구 | Playwright Python, Chromium만, headless | 사용자 결정 (2026-11-02). pytest와 같은 프로세스에서 돈다 |
| pytest 연동 | `pytest-playwright` 플러그인 (`page` 픽스처). pytest 9와 맞지 않으면 `playwright`만 쓰고 픽스처를 직접 둔다 | 0단계에서 확인 |
| 가짜 모델의 위치 | 모델 서버 **앱 뒤**(Registry에 `ms_fakes`의 가짜 모델). `ModelServerClient`는 진짜 | HTTP·SSE·토큰 인증·오류 본문을 진짜 코드가 지난다. 계약 테스트와 같은 경계 |
| 세 추론 호출 | 임베딩·리랭크·chat 모두 가짜(`e2e_stub`) 또는 모두 실제(`e2e_live`). 섞지 않음 | 일부만 실제로 부른 결과를 live로 보고하지 않기 위해 |
| 웹앱 코드 | 그대로 `lawbot.app.main()` 실행. 바꾸는 것은 선택자용 `elem_id`(조문 카드·측정 줄)만 | Gradio DOM 구조에 기대지 않기 위해. 화면 동작은 바뀌지 않음 |
| CI | `e2e_stub`를 CI 기본 실행에 넣음 | 사용자 결정 (2026-11-02) |
| live 실행 조건 | 요청할 chat 모델이 상주 중이 아니면 질문 없이 실패로 끝냄 | 사용자 승인 (2026-11-02). 모델 교체로 운영 응답이 느려지지 않게 |
| 테스트 산출물 | 요청 로그·피드백·프로세스 출력은 tmp. **성공하면 테스트가 바로 지움**. 실패하면 `results/e2e_live/<시각>/`(stub은 `results/e2e_stub/<시각>/`)에 요청 로그·프로세스 출력·스크린샷·trace를 남김(`.gitignore`), 진단 후 지움. CI 실패 시 같은 폴더를 아티팩트로 업로드 | 사용자 결정 (2026-11-02). 남길 정보는 evals 기록·구성 JSON에 따로 있음 |
| 마커 | `e2e_stub`는 기본 실행 포함, `e2e_live`는 `addopts`에서 제외 (`-m 'not models and not network and not e2e_live'`) | 기존 `models`·`network`와 같은 방식 |

## 2. `e2e_stub` 구성

```
pytest (Playwright) ──▶ 127.0.0.1:<빈 포트 A>  웹앱 프로세스 (lawbot.app.main, 실제 코드)
                                │  HTTP + 테스트 토큰
                                ▼
                        127.0.0.1:<빈 포트 B>  가짜 모델 서버 프로세스
                        api.create_app(실제) + Registry(FakeEmbedder·FakeReranker·FakeChat)
```

- 새 파일 `services/chatbot/tests/e2e/fake_model_server.py`: `api.create_app(TOKEN, …)` + 가짜 모델 Registry를 uvicorn으로 띄운다. 모델 id는 v4 프로필이 요구하는 것(`lawbot-emb-v4`, 리랭커 id, `qwen3.8-27b-4bit`)과 같게 한다. 동작은 시작 인자(모드)로 고른다: `normal`, `slow`(토큰 간 지연), `chat_error`(생성 중 예외).
- `ms_fakes.py`는 `services/model-server/tests/`에 있다. 챗봇 테스트에서 쓰려면 `sys.path`에 그 폴더를 넣는다(테스트 전용 스크립트 안에서만). 공통 패키지로 옮기지는 않는다.
- 웹앱 프로세스 환경: `LAWBOT_ENV_FILE=''`(실제 `.env`를 읽지 않음), `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`은 가짜 서버, `LAWBOT_MODELS=pro`, `LAWBOT_EMBED_CACHE=0`, `LAWBOT_REQUEST_LOG`·피드백 폴더는 tmp, `LAWBOT_MODEL_READ_TIMEOUT`은 짧게(타임아웃 시나리오용), `GRADIO_ANALYTICS_ENABLED=False`. 조문 CSV는 레포의 `data/v4`를 읽기만 한다.
- 시작 시 조문 전체 임베딩은 가짜 임베더(8차원)라 빠르다. 시작부터 화면이 뜨기까지 30초를 넘으면 실패로 하고 두 프로세스의 출력을 보여 준다(`test_auth_integration.py`와 같은 방식, 비밀번호는 가림).
- 프로세스는 모드별로 모듈 범위 픽스처가 띄우고 끝나면 정리한다. 접속은 `127.0.0.1`만 한다(기존 conftest의 외부 접속 차단 유지).
- 브라우저는 시스템 Chromium 업데이트·동기화 기능을 끄는 옵션으로 띄운다(외부 접속 없게).

### 시나리오 (테스트 목록)

`services/chatbot/tests/e2e/test_e2e_stub.py`

| 테스트 | 확인 |
| --- | --- |
| `test_login_required` | 로그인 전에는 질문 입력창이 없다. 틀린 비밀번호는 거부, 맞으면 챗봇 화면 |
| `test_question_streams_answer` | 질문 입력 → 전송 → "검색 중" 표시 → 답이 조각으로 늘어나 끝까지 표시(가짜 chunks 전체). 빈 답이 아님 |
| `test_sources_and_stats_shown` | 끝난 뒤 조문 카드에 법률 조문 3개, 인용한 조문과 검색만 된 조문 구분, 측정 줄(검색·리랭킹·첫 토큰·완료) |
| `test_example_click` | 예시 질문을 누르면 같은 흐름으로 답이 나온다 |
| `test_request_log_api_mode` | 요청 로그 한 줄: `mode=api`, 오류 유형 없음, 질문 원문은 남지 않음(기존 로그 규칙) |
| `test_error_timeout` | `slow` 모드 + 짧은 read timeout → "모델 서버 응답이 없습니다" 안내 |
| `test_error_generation` | `chat_error` 모드 → 화면에 오류 표시, 임베딩 순위로 되돌린 답을 보이지 않음 |
| `test_error_server_down` | 웹앱이 뜬 뒤 가짜 모델 서버를 종료 → "모델 서버가 준비되지 않았습니다" 안내 |
| `test_error_busy` | 가짜 서버 `LAWBOT_MAX_QUEUED=1`·`slow`, 테스트가 직접 chat 요청 2개로 자리를 채운 뒤 브라우저 질문 → "모델 서버가 바쁩니다" |

정확한 문장 일치는 가짜 답변 문자열에 대해서만 한다(가짜이므로 고정). 실행 시간 목표: `e2e_stub` 전체 60초 이내(맥 기준).

## 3. `e2e_live` 구성

- 같은 Playwright 픽스처를 쓰되 웹앱 프로세스를 **이 worktree의 코드**로 새 빈 포트에 띄운다. 모델 서버 주소·토큰만 레포 `.env`에서 읽고, 요청 로그·피드백은 tmp로 보낸다(운영 기록에 섞이지 않게). 임베딩 캐시는 기본 위치를 그대로 쓴다(맞으면 공유 서버에 조문 전체 임베딩을 다시 보내지 않음).
- **실행 전 확인(preflight)**. 하나라도 실패하면 질문을 보내지 않고 "실패: 서버 미준비/모델 없음"으로 끝낸다. 건너뛰기(skip)로 처리하지 않는다.
  - `/health`가 ready
  - `/v1/models`에 필요한 id가 모두 있음
  - 요청할 chat 모델이 이미 상주(`loaded`) 중. 아니면 모델 교체로 운영이 느려지므로 중단
  - 시각이 06:20–06:50(조문 자동 갱신)이 아님
- 시나리오 3개, 순차, 재시도 없음(재실행 플러그인을 넣지 않음, 웹앱 `max_retries=0` 그대로).
  1. 일반 노동법 질문(예시 질문 1번)
  2. 범위 밖 질문(`data/oos_eval.csv`에서 1개 고정)
  3. 시행령이 첨부되는 질문(Pro, 첨부 val에서 1개 고정)
- 계약(문장 일치 없음): 스트리밍 종료, 비어 있지 않은 답, 근거 조문 형식(`법령명 제N조`) 존재(범위 밖 질문은 제외), 조문 카드 3개(+ 시나리오 3은 하위법령 카드 1개 이상), 측정 줄, 요청 로그 `mode=api`·오류 없음. 503 `server_busy`·타임아웃은 실패로 기록.
- **실행 구성 기록**: 시작 전과 끝난 뒤에 읽어 JSON으로 남긴다(`results/e2e_live/<시각>.json`, 커밋하지 않음, 성공 시 evals 기록에 옮긴 뒤 지움). 둘이 다르면 결과를 무효로 표시.
  - 웹앱 git 커밋, 모델 서버 `/v1/models` 목록(id·type·loaded), 모델 revision(지금은 "없음")
  - 조문 매니페스트 `dataset_version`·sha256, system 파일 sha256
  - temperature·max_tokens·후보 수·리랭커 사용 여부, `LAWBOT_MAX_CONCURRENT`(서버 `.env` 값)
- 실행 구성 JSON은 결과와 함께 `docs/evals` 기록에 옮겨 적는다. 결과는 `docs/evals/YYYY-MM-DD-e2e-live.md`에 기록하고, README에는 넣지 않는다(지표가 아님).

## 4. 의존성과 CI

- dev 그룹에 `pytest-playwright`(또는 `playwright`) 추가, `uv lock`.
- 브라우저 설치: `uv run playwright install chromium` (로컬은 한 번, CI는 매 실행. `~/Library/Caches/ms-playwright`를 캐시).
- CI(`.github/workflows/ci.yml`): "테스트" 단계 앞에 Chromium 설치 단계 추가. `e2e_live`는 수집하지 않음. 실패 시 `results/e2e_stub/`를 `pytest-report`와 함께 업로드.
- `docs/operations.md` 테스트 절: `e2e_stub`·`e2e_live` 실행법과 "live는 승인 후" 한 줄.

## 5. 작업 순서 (PR 하나, 단계마다 커밋·중간 보고)

사용자 결정 (2026-11-02): PR은 `feat/e2e-playwright` 하나. 단계마다 커밋하고 중간 보고한다.

| 단계 | 내용 | 끝나면 |
| --- | --- | --- |
| 1 | 이 계획 커밋, 0단계 확인 | 중간 보고 ① |
| 2 | 마커·의존성 → 가짜 모델 서버 스크립트 → `e2e_stub` 테스트(먼저 실패 확인) → `elem_id` → 통과 → CI → 문서 | 중간 보고 ② |
| 3 | 모델 revision 노출 (공통 API 계약 변경. 시작 전에 이 문서에 설계 절을 추가) | 중간 보고 ③ |
| 4 | `e2e_live` 픽스처·preflight·구성 기록 | 중간 보고 ④ + 맥 미니 실행 승인 요청 |
| 5 | 승인 후 1회 실행 → `docs/evals` 기록 → PR 생성 | 최종 보고, 테스트 구성 문구 최종본 전달 |

0단계 확인 (2026-11-02, 결과):
- `pytest-playwright` 0.10.0 (playwright 1.64.1)이 pytest 9.1.1·Python 3.14에서 설치·동작. 기존 기본 테스트 643개 그대로 통과. Chromium headless shell 설치 약 99MB
- Gradio 6.30 화면을 기존 `integration_server.py`(가짜 모델)로 띄워 확인: 로그인은 `input[type=password]`·`Login` 버튼, 질문 입력창은 `data-testid=textbox`(Enter로 전송), 답변은 `data-testid=bot`, 예시 질문은 버튼 `aria-label="Select example N: …"`. 측정 줄·조문 카드는 Markdown이라 `elem_id`를 붙여 찾는다
- `ms_fakes.Registry(entries, loaders)`에 v4 id(`lawbot-emb-v4`, `qwen3-reranker-4b`, `qwen3.8-27b-4bit`)로 `ModelEntry`를 넣으면 가짜 모델로 등록 가능

## 6. 위험

| 위험 | 대응 |
| --- | --- |
| Gradio 버전이 바뀌면 DOM이 바뀜 | 역할·라벨·`elem_id`로만 찾고 CSS 클래스에 기대지 않음 |
| CI 시간 증가(Chromium 설치 ~1분 + 테스트) | 브라우저 캐시. 현재 제한 20분 안 |
| 테스트가 실제 `.env`·운영 로그를 건드림 | `LAWBOT_ENV_FILE=''`, 로그·피드백 tmp. `e2e_live`도 로그는 tmp |
| `e2e_live`가 운영 질문을 지연시킴 | 3문항·순차·재시도 없음·상주 모델만·승인 후 실행 |
