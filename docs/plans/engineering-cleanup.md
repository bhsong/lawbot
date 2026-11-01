# 계획: 레포 엔지니어링 정리 (README 최소화 · v1 코드 삭제 · 안정화 · 구조 정리)

- 작성일: 2026-11-02
- 출발점: 2026-11-02 코드 리뷰 (소프트웨어 엔지니어링 관점, `data/`·`data_gd/`·`results/`·`.env`·모델 학습·평가 로직 제외)
- 이 계획에서 하지 않는 것: **monitor 3단계(전송·리마인드·입법예고·국회 수집)**, Docker·vLLM·클라우드 배포 ("질문이 맥 밖으로 나가지 않는다"는 전제를 바꿀 때 다시 검토)

## 0. 정해진 것 (사용자 결정, 2026-11-02)

| 항목 | 결정 |
| --- | --- |
| `LAWBOT_CHAT_CONCURRENCY` | 기본 1 유지 (테스트로 정한 값). 바꾸지 않는다 |
| v1 코드 | 더 이상 필요 없음 → 삭제. **테스트·평가 결과 기록(`docs/evals/2026-10-18-v1.md` 등)은 히스토리로 남긴다** |
| README | 최소화: 소개, 주요 기능, 주요 지표(최종만), 사용법 + 꼭 필요한 것만 |
| README 영어 절 | 남긴다 (한국어와 같은 구성으로 짧게) |
| monitor 3단계 | 이번 계획에서 하지 않음 |
| 검색 결과 타입 (2026-11-02) | 이번에는 `TypedDict`. dataclass는 다음에 따로 시도 |
| 조문 CSV 자동 생성 (2026-11-02) | 법령 원문 → 조문 CSV, monitor 이벤트와 연결은 **이 계획이 끝난 다음 차례**로 따로 한다 (monitor 3단계와 별개). monitor 3단계는 그 뒤 |

## 1. 순서와 PR

PR 하나는 한 가지 일만 한다. 동작이 바뀌는 PR은 테스트를 먼저 쓴다.

| 순서 | PR | 한 문장 | 동작 변화 |
| --- | --- | --- | --- |
| 1 | `docs/readme-minimal` | README를 최소 구성으로 줄이고, 빠진 내용은 `docs/`로 옮긴다 | 없음 |
| 2 | `chore/remove-v1` | v1 프로필·프롬프트·검색 형식·v1 전용 도구를 지우고 테스트를 v4 기준으로 옮긴다 | v1 실행 불가 (의도) |
| 3 | `refactor/common-envfile` | 세 서비스가 같은 `.env` 파서를 쓰도록 공통 패키지로 합친다 | monitor의 `.env` 해석이 웹앱과 같아짐 |
| 4 | `fix/generation-timeout` | 질문 처리 중 모델 서버 호출에 시간 상한을 두고, 모델 서버 대기열 길이에 상한을 둔다 | 멈춘 요청이 오류로 끝남 |
| 5 | `fix/monitor-lock` | monitor에 실행 잠금 파일을 둬서 launchd 실행과 수동 실행이 겹치지 않게 한다 | 겹치면 두 번째 실행이 거부됨 |
| 6 | `refactor/split-eval-tools` | 평가·리포트·피드백 분석 스크립트를 웹앱 패키지에서 별도 도구 패키지로 분리한다 | 없음 (명령 이름 유지) |
| 7 | `refactor/api-schema` | 웹앱과 모델 서버가 주고받는 요청·응답 형식과 오류 종류를 공통 스키마로 한곳에 정의한다 | 없음 |
| 8 | `refactor/settings` | 흩어진 환경 변수 읽기를 시작할 때 한 번 검증하는 설정 객체로 모은다 | 없음 (오류 문구만 정리) |
| 9 | `refactor/typed-hits` | 검색 결과와 측정값을 dict 대신 dataclass로 바꾸고 CI에 타입 검사를 추가한다 | 없음 |
| 10 | `refactor/logging` | 세 서비스의 로그를 `logging`으로 통일하고, 요청 id를 모델 서버까지 넘겨 두 로그를 이어 볼 수 있게 한다 | 로그 형식 |
| 11 | `feat/embedding-cache` | 조문 임베딩을 매니페스트 해시 기준으로 저장해 두고, 웹앱을 재시작할 때 전체를 다시 임베딩하지 않게 한다 | 재시작 시간 |
| 12 | `feat/reindex` | 법 개정이 반영된 조문 CSV로 매니페스트와 웹앱 인덱스를 다시 만드는 재인덱싱 명령을 만든다 | 새 명령 |

PR 1~5는 이 계획에서 자세히 정한다. PR 6~12는 시작하기 전에 각 절에 설계를 덧붙이고 확인을 받는다 (이 문서를 고쳐서).

**진행 (2026-11-02 사용자 지시)**: 하나씩 PR로 하지 않고 커밋 단위로 고친 뒤 PR로 묶는다. PR을 올리기 전마다 개발 웹앱에서 운영 모델 서버에 질문 3개로 확인하고, 모델 서버가 바뀌면 운영에 반영한 뒤 확인한다.
- 1~5: **PR #43**으로 병합·운영 반영 (2026-11-02, 커밋 5개)
- 6~12: 두 번째 PR (아래 7절 설계)

## 2. PR 1: README 최소화 (문서만)

### 2.1 README 구성 (한국어·영어 같은 구성)

| 절 | 내용 |
| --- | --- |
| 소개 | 무엇을 하는 챗봇인지 2~3문장 + "법률 자문 아님" |
| 주요 기능 | 조문 근거 답변, 근거 부족 시 거절, 시행령·시행규칙 첨부(Pro), 법령 변경 수집·요약(monitor), 맥 한 대에서 로컬 실행 |
| 구성 | 서비스 세 개(웹앱 → 모델 서버, monitor → 모델 서버) 그림 하나 |
| 주요 지표 | 현재 구성의 최종 결과만: v4 웹앱 test(Recall@3 0.940, 인용 정확도 0.905, 범위 밖 거절 0.96, 질문당 15.1초) + 하위법령 첨부 웹앱 검증(정답 첨부 0.91, 인용 0.885). 나머지는 `docs/evals/` 링크 한 줄 |
| 사용법 | 요구 사항, 설치(`uv sync`), `.env` 최소 예시, 실행 명령 세 개(모델 서버, 웹앱, monitor), 자세한 운영은 `docs/operations.md` 링크 |
| 문서 | `docs/` 안내 (운영·구조·평가·계획·기록) |
| 고지 | 모델 라이선스, 법령 저작권, 상담처 |

추가한 절의 이유: **구성**은 서비스가 셋이라 그림이 없으면 사용법을 따라가기 어렵다. **문서**는 README에서 뺀 내용이 어디로 갔는지 알려 준다. **고지**의 모델 라이선스는 남겨야 한다.

### 2.2 옮길 곳

| 지금 README | 옮길 곳 |
| --- | --- |
| 빠른 시작 상세(설치·`.env` 전체 변수 표·상시 운영·Tailscale·개발 동시 실행·배포 확인) | `docs/operations.md` |
| 구조 그림 상세, 프로젝트 구조 | `docs/architecture.md` |
| 성능 절의 평가별 한 줄, 데이터와 평가 방법, 실패한 실험 | `docs/evals/README.md` (기록 목록 + 평가 방법 + 실패한 실험) |
| 한계와 향후 계획 | 삭제 (낡음. 앞으로 할 일은 `docs/plans/`) |
| v1 실행 방법 (`LAWBOT_PROFILE=v1`) | 삭제 (PR 2에서 v1 코드가 없어짐). v1 결과는 `docs/evals/2026-10-18-v1.md`에 그대로 |

- 영어 절도 같은 구성으로 짧게 쓴다. `docs/` 문서는 한국어만 (지금 `docs/`와 같음).
- 메모리의 "README에는 평가마다 결과 한 줄" 규칙은 이번 결정으로 "최종 결과만"으로 바뀐다.

### 2.3 확인

- 링크 확인: README·`docs/`의 상대 링크가 모두 있는 파일을 가리키는지 스크립트로 검사 (커밋하지 않는 일회성 확인).
- `services/monitor/README.md`는 그대로 둔다 (서비스 README).

## 3. PR 2: v1 코드 삭제

### 3.1 지우는 것

| 위치 | 지우는 것 |
| --- | --- |
| `deploy.py` | `_profile('v1')` 분기, v1 관련 오류 문구. 프로필은 v4 하나만 남음 |
| `deploy.py` · `LAWBOT_PROFILE` | 변수 자체를 없앤다. 단, 운영 `.env`에 `LAWBOT_PROFILE=v4`가 남아 있을 수 있으므로 **없거나 `v4`면 통과, 다른 값이면 시작 거부**(조용히 무시하지 않음) |
| `llm.py` | import 시 CSV 읽기(`_law`), `ART`, `build_prompt(ids, …)`, `prompt_style` 분기. v4 프롬프트(`build_prompt_v4`)만 남기고 이름을 `build_prompt`로 |
| `prompts.py` | v1 학습 system(`SYSTEM`). v4는 항상 프로필 system 파일을 씀 → `LLM`의 기본 system 인자, `service.full_system`·`feedback`·`check_deploy`의 `SYSTEM` 대체 경로도 같이 정리 |
| `retriever.py` | `DOC_STYLES['v1']`, 기본 인자 `law_csv=ARTICLES`·`doc_style='v1'`, `laws is None` 분기 |
| `citation.py` | `DEFAULT_LAW`(v1 hit에 `law`가 없던 경우). 모든 hit에 `law`가 있으므로 대체값 없이 씀 |
| `check_deploy.py` | `v1` 인자, `EXPECTED_ARTICLES`·`COLUMNS`의 v1 값 |
| `paths.py` | `ARTICLES`, `SYSTEM_V1_CONCISE` (`EVAL`은 v4 평가가 쓰므로 남김) |
| `feedback_export.py` | 기본 조문 파일 `ARTICLES` → `V4_ARTICLES` / `V4_DECREE_ARTICLES` |
| `report.py` | v1 평가 결과(`results/`) 통합 리포트 CLI. `eval_v4`가 쓰는 `METRICS`는 `eval_v4` 쪽으로 옮김 |
| `manifest.py` 주석 | v1 매니페스트 경로 설명 |

### 3.2 남기는 것

- `refusal.py`, `score_answers.py`, `paths.EVAL`(`labor_eval_keywords.csv`): v4 평가(`eval_v4`)가 키워드 채점과 거절 판정에 쓴다. v1 전용 함수가 있으면 그것만 지운다.
- `data/`의 v1 파일: 손대지 않는다 (`data/`는 이번 작업 범위 밖). 지울지는 따로 정한다.
- `docs/evals/2026-10-18-v1.md`와 그 밖의 v1 기록: 그대로. 지운 코드는 git 기록에 있다고 기록 파일 머리에 한 줄 추가.

### 3.3 테스트 (먼저)

- 지금 테스트는 `conftest.py`가 `LAWBOT_PROFILE=v1`을 기본으로 두고, 가짜 조문(`fakes.HITS`)도 v1 형식(`law` 없음)이다. → **가짜 데이터를 v4 형식(`law` 있음, `근로기준법 제26조(해고의 예고)` 문서 형식)으로 바꾸고 기본 프로필 설정을 지운다.**
- 새로 쓰는 테스트:
  - `LAWBOT_PROFILE`이 없거나 `v4`면 통과, `v1`이면 시작 거부
  - `llm` 모듈 import가 파일을 읽지 않음 (조문 CSV가 없어도 import됨)
  - 프롬프트가 지금 v4 프롬프트와 글자 단위로 같음 (기존 v4 프롬프트 테스트 유지)
- v1만 검사하던 테스트는 지운다. 대상 파일: `test_webapp`, `test_pipeline`, `test_api_client`, `test_manifest`, `test_decree`, `test_citation`, `test_feedback`, `test_reranker`, `test_report`, `test_eval_data` 등 (grep으로 v1 참조가 있는 파일).

### 3.4 확인

- 기본 테스트 전부 통과, ruff 통과.
- v4 웹앱 동작이 그대로인지: 맥 미니에서 `eval_v4` val 일부로 답변을 만들어 `diff_answers`로 기준과 글자 단위 비교. **실제 모델 실행이므로 이유·방법을 설명하고 승인받은 뒤 실행**, 결과는 `docs/evals/`에 기록.
- 운영 `.env`(사용자 확인): `LAWBOT_PROFILE`이 `v4`가 아니거나 v1 모델 id(`lawbot-emb-v1` 등)가 있으면 정리. 모델 서버 `.env`의 v1 모델 등록(`LAWBOT_SERVE_*`)도 필요 없으면 정리.

## 4. PR 3: `.env` 파서 공통화

### 4.1 문제 (2026-11-02 확인)

- 같은 레포 최상위 `.env`를 웹앱(`lawbot.envfile`)과 monitor(`law_monitor.secrets`)가 다르게 읽는다.
  - `export LAWBOT_MODEL_TOKEN=…` → monitor는 `None`
  - `LAWBOT_MODEL_TOKEN=…  # 주석` → monitor는 주석까지 토큰으로 읽음 (401)
- `LAW_MONITOR_SUMMARY_MODEL`을 최상위 `.env`에 적으면 웹앱이 `LAWBOT_*`가 아닌 변수라며 시작을 거부한다. monitor는 최상위 `.env`에서도 이 값을 찾는다.
- 모델 서버 `envfile.py`는 웹앱 것의 복사본이다.

### 4.2 바꾸는 것

- workspace 멤버 `packages/lawbot-common` (모듈 `lawbot_common`)을 만들고 `envfile.parse`(형식 규칙 하나)와 `load(path, allowed_prefixes)`를 둔다.
- 각 서비스는 허용 접두어만 다르게 쓴다:
  - 웹앱·모델 서버: 최상위 `.env`, `LAWBOT_*`
  - monitor: `services/monitor/.env`는 `LAW_*`·`LAW_MONITOR_*`·`DATA_GO_KR_*`, 최상위 `.env`는 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`만 (최상위 `.env`에서 `LAW_MONITOR_SUMMARY_MODEL`을 찾지 않음)
- 포트 검증(`parse_port`)도 같은 패키지로 옮긴다 (웹앱·모델 서버 중복).
- `lawbot_model_server/envfile.py` 복사본과 `law_monitor/secrets.py`의 자체 파서는 지운다.

### 4.3 테스트 (먼저)

- 같은 `.env` 문자열을 세 서비스 로더가 같게 읽음 (`export`, 따옴표, 줄 끝 주석, 빈 값)
- monitor가 최상위 `.env`에서 모델 서버 주소·토큰만 읽고 다른 변수는 읽지 않음
- 기존 envfile 테스트(웹앱·모델 서버)는 공통 패키지 테스트로 옮김

## 5. PR 4: 질문 처리 중 모델 서버 호출의 시간 상한, 대기열 상한

### 5.1 문제

- 웹앱의 모델 서버 클라이언트는 읽기 시간 제한이 없다 (`api_client.TIMEOUT = Timeout(None, connect=5)`). 생성 호출(openai SDK)도 같다.
- 동시 처리 수가 1(정해진 값)이라, 요청 하나가 멈추면 뒤의 질문이 모두 기다린다.
- 모델 서버 대기열(`scheduler.inbox`·`waiting`)에는 길이 제한이 없다.

### 5.2 바꾸는 것

- 시작할 때의 조문 인덱스 임베딩(415 + 397개, 오래 걸림)은 지금처럼 제한 없이 둔다.
- 질문마다의 호출(질문 임베딩, 리랭크, 생성 스트림)에는 **읽기 간격 상한**을 둔다: 응답 조각 사이가 이 시간을 넘으면 오류. 기본값 후보 300초 (27B 교체 로딩 + monitor 요약 뒤 대기를 넘는 값), `LAWBOT_MODEL_READ_TIMEOUT`으로 변경.
- 시간 초과는 요청 로그 `error_type`에 `model_server_timeout`으로 남기고, 화면에는 "모델 서버 응답이 없습니다" 안내.
- 모델 서버: 대기 중인 chat 요청이 상한(기본 후보 32, `LAWBOT_MAX_QUEUED`)을 넘으면 503 `server_busy`.

### 5.3 정할 것 (구현 전에 확인)

- 읽기 간격 상한 기본값 (300초 제안). 실측 근거: 운영 기록의 모델 교체 시간·요약 처리 시간을 보고 정한다.
- 대기열 상한 기본값 (32 제안).

### 5.4 테스트 (먼저)

- 가짜 서버가 응답을 멈추면 상한 뒤 오류로 끝나고, 요청 로그에 `model_server_timeout`
- 시작 인덱스 임베딩에는 상한이 적용되지 않음
- 모델 서버 대기열이 상한을 넘으면 503 `server_busy`, 상한 안이면 그대로 처리

## 6. PR 5: monitor 실행 잠금

- `data/.lock`에 `fcntl.flock(LOCK_EX | LOCK_NB)`를 건다. 이미 잡혀 있으면 "다른 law-monitor 실행 중"으로 종료 코드 2.
- 대상 명령: `bootstrap`, `collect`, `replay`, `summarize`, `daily` (모두 같은 `state.db`·저장소 클론을 씀).
- 테스트 (먼저): 잠금을 잡은 상태에서 두 번째 실행이 종료 코드 2, 첫 실행이 끝나면 다시 실행 가능, 프로세스가 죽으면 잠금이 풀림 (flock 특성).

## 7. 6~12 설계 (두 번째 PR, 커밋 단위)

순서는 의존 관계대로: 6 → 7 → 8 → 9 → 10 → 11 → 12. 커밋마다 테스트를 먼저 쓰고, 기본 테스트·ruff가 통과해야 다음으로 간다.

### 7.1 평가·분석 도구 분리 (6)

- 새 workspace 멤버 `tools/lawbot-tools` (패키지 `lawbot_tools`). 옮기는 모듈: `eval_v4`, `score_answers`, `diff_answers`, `smoke_decree`, `feedback_review`, `feedback_export`, `log_summary`, `versions`, `refusal`.
- 웹앱 런타임(`lawbot`)에 남는 것: `app`, `service`, `api_client`, `llm`, `retriever`, `reranker`, `decree`, `deploy`, `security`, `citation`, `answer_format`, `feedback`, `feedback_ui`, `manifest`, `envfile`, `paths`, `check_deploy`.
- 막히는 곳 하나: 런타임 `citation`이 `score_answers`의 `norm`·`conclusion_part`를 import한다 → 이 두 함수를 `citation`으로 옮기고 `score_answers`가 `citation`에서 가져온다 (도구 → 런타임 방향만 허용).
- 방향 규칙: `lawbot_tools`는 `lawbot`을 import해도 되고, `lawbot`은 `lawbot_tools`를 import하지 않는다. 테스트로 고정 (런타임 모듈을 import한 뒤 `sys.modules`에 `lawbot_tools`가 없음).
- 명령: `lawbot-eval-v4` 이름 유지 (엔트리포인트만 `lawbot_tools.eval_v4:main`으로). `python -m lawbot.log_summary` 같은 모듈 명령은 `python -m lawbot_tools.log_summary`로 바뀐다 → `docs/`·주석 일괄 수정.
- 테스트 파일은 `services/chatbot/tests`에 그대로 둔다 (공통 `conftest`·`fakes`를 같이 쓰므로). import 경로만 바꾼다.
- 웹앱 의존성: pandas는 `retriever`·`manifest`가 쓰므로 남는다. 줄어드는 의존성은 없고, 목적은 경계를 분명히 하는 것.

### 7.2 API 계약 공통화 (7)

- `lawbot_common.api` (pydantic 의존 추가, 웹앱·모델 서버 모두 이미 pydantic 사용):
  - 요청 모델: `EmbeddingRequest`, `RerankRequest` (지금 `api.py`에 있는 것을 옮김). `ChatRequest`는 웹앱이 openai SDK로 보내므로 서버 쪽 검증용으로만 옮김.
  - 응답 모델: `EmbeddingResponse`, `RerankResponse`, `ModelsResponse`(확장 필드 `type`·`loaded`), `HealthResponse`.
  - 오류 type 상수: `invalid_request_error`, `authentication_error`, `model_load_error`, `server_not_ready`, `server_busy`, `server_error`.
- 모델 서버는 요청을 이 모델로 검증하고 응답을 이 모델로 만든다. 웹앱 `api_client`는 요청 본문을 이 모델로 만들고(`model_dump(exclude_none=True)`) 응답을 이 모델로 검증한다.
- 바뀌지 않는 것: HTTP 경로·필드 이름·값. 기존 계약 테스트(openai SDK ↔ 실제 서버 앱)를 그대로 통과해야 한다.
- 새 테스트: 웹앱이 만드는 요청 본문이 서버 모델 검증을 통과, 서버 응답이 웹앱 응답 모델 검증을 통과 (가짜 모델로 왕복).

### 7.3 설정 객체 (8)

- `lawbot.settings.Settings` (frozen dataclass)와 `load(environ=os.environ)`: 시작할 때 한 번 읽어 모두 검증하고, 틀린 값은 **한꺼번에** 알린다 (지금은 첫 오류에서 멈춤).
- 묶는 값: 인증·포트·인스턴스(`security`), 모델 서버 주소·토큰·생성 API(`security`), 프로필·모델 선택·리랭커·하위법령(`deploy`), 서버 대기·읽기 상한(`service`), 동시 처리 수(`app`), 피드백·요청 로그 경로(`feedback`·`service`).
- 지금 함수들(`security.server_port()` 등)은 값을 받아 검증하는 순수 함수로 바꾸고, `os.environ`은 `settings.load`에서만 읽는다. `app.main` → `settings.load()` → `load_app(settings)` → `build_demo(app, settings)`.
- 평가·점검 명령(`eval_v4`, `check_deploy`, `smoke_decree`)도 같은 `settings.load()`를 쓴다.
- 테스트: 지금 환경 변수 테스트는 `settings.load({...})`에 dict를 넘기는 형태로 바꾼다. 효과 확인: `conftest.ENV_KEYS`(환경 변수 지우기 목록)가 없어지거나 크게 준다.
- 동작 변화: 오류 문구 모음 방식만 (한 번에 여러 개). 기본값·허용 범위는 그대로 (테스트로 고정).

### 7.4 타입과 타입 검사 (9)

- 검색 결과는 `TypedDict`로 정의한다: `Hit`(`law`·`article_id`·`title`·`doc`·`score`, 선택 `rerank_score`·`emb_rank`·`kind`·`parent_law`), 측정값 `Timing`. dict 그대로라 피드백 JSON·평가 CSV·화면 코드가 바뀌지 않고, 타입 검사만 붙는다 (dataclass로 바꾸면 dict 접근 수백 곳과 저장 형식이 바뀌어 위험만 큼).
- 오류: 예외 객체에 `lawbot_error` 속성을 붙이는 방식 대신 `service.AskError(kind, stage)`로 감싸 `raise … from e`. 화면은 `AskError.kind`로 안내하고, 안내가 없는 종류는 원래 예외를 보여 준다. 요청 로그 `error_type`은 그대로.
- 타입 검사: pyright `basic` 모드를 CI에 추가. 범위는 `packages/lawbot-common`, `services/model-server`, `services/monitor`, 웹앱 런타임 모듈. 평가 도구(`tools/`)는 pandas 위주라 제외.
- 2026-11-02 측정 (pyright 1.1.408, 고치기 전): lawbot-common 3, 모델 서버 20, monitor 18, 웹앱 런타임 71개. 평가 도구까지 넣으면 웹앱 943개라 제외한다.

### 7.5 로깅과 요청 id (10)

- 세 서비스 모두 `logging` (logger `lawbot`·`lawbot_model_server`·`law_monitor`). 형식은 지금 launchd 로그와 같은 `[lawbot] …` 한 줄로 두어 운영 로그 보는 법이 바뀌지 않게 한다. 각 `main`에서만 설정.
- 요청 id: `service.ask`가 만든 `request_id`를 `contextvars`에 두고, `api_client`(임베딩·리랭크)와 `llm`(생성, `extra_headers`)이 `X-Request-Id` 헤더로 보낸다. 모델 서버 `Guard`는 접근 로그 줄에 그 id를 남긴다 (질문 내용은 여전히 남기지 않음). 헤더 값은 영숫자 32자 이하만 받아 로그에 넣는다.
- 요청 로그 파일: 기본 경로를 `logs/requests-YYYY-MM.jsonl`(월별)로. `LAWBOT_REQUEST_LOG`로 파일을 직접 정하면 지금처럼 그 파일 하나. `log_summary`는 기본 폴더의 월별 파일과 옛 `requests.jsonl`을 함께 읽는다.
- 테스트: 질문 하나의 임베딩·리랭크·생성 요청이 모두 같은 `X-Request-Id`를 보냄 (실제 서버 앱 + 가짜 모델), 서버 로그 줄에 id, 월이 바뀌면 새 파일.

### 7.6 조문 임베딩 캐시 (11)

- 지금 웹앱은 시작할 때마다 812조문을 다시 임베딩한다 (운영 35~42초).
- 캐시 키: 조문 문서 문자열 전체의 sha256 + 임베딩 모델 id + max_seq (법률·하위법령 각각). 파일: `cache/embeddings/<키>.npy` (레포 최상위, gitignore).
- 같은 id로 모델 폴더가 바뀌는 경우를 막기 위해, 시작할 때 **조문 1개만 임베딩해 캐시의 같은 행과 비트 단위로 비교**하고 다르면 전체를 다시 임베딩해 캐시를 바꾼다.
- 캐시 쓰기 실패는 경고만 하고 계속한다 (지금처럼 매번 임베딩). `LAWBOT_EMBED_CACHE=0`으로 끌 수 있다.
- 테스트: 캐시를 쓴 벡터가 다시 임베딩한 벡터와 비트 단위로 같음, 문서가 한 글자 바뀌면 새 키, 확인용 1개가 다르면 다시 임베딩, 캐시 폴더에 못 쓰면 경고만.
- 운영 확인: 재시작 시간 (조문 인덱스 줄) 전후 비교.

### 7.7 재인덱싱 명령 (12)

- `uv run lawbot-reindex <새 조문 CSV> [--decree]`: ① `check_deploy`의 조문 CSV 검사(열·중복·번호 형식·법령별 수) ② 매니페스트 새로 쓰기(데이터 버전·기준일·조문 수·sha256, 이전 매니페스트는 `manifest.prev.json`으로) ③ 모델 서버로 임베딩 캐시 미리 만들기 ④ 웹앱 재시작 안내 출력. 웹앱을 직접 재시작하지는 않는다.
- 이번 범위 밖: 법령 원문(legalize-kr)에서 조문 CSV를 만드는 것과 monitor 이벤트 연결. 이 계획이 끝난 다음 차례에 따로 한다 (monitor 3단계와 별개). 이번 명령은 그때 만든 CSV를 받는 입구가 된다.
- 테스트: 검사 실패면 아무것도 쓰지 않음, 성공하면 매니페스트가 `manifest.check`를 통과하고 캐시가 생김, 이전 매니페스트 보관.

### 7.8 두 번째 PR 확인

- 기본 테스트·ruff·pyright 통과.
- 모델 서버가 바뀌므로(7·10) 운영 모델 서버에 반영한 뒤, 개발 웹앱에서 운영 모델 서버로 질문 3개. 서버 로그에 요청 id가 남는지, 웹앱 재시작 시간이 줄었는지 함께 본다.
- 운영 웹앱 재시작은 권한 확인에 막힐 수 있음 → 막히면 사용자에게 요청.

## 8. 정리할 문서

- 시작 전 (작업 순서 0단계): `docs/plans/monitor-collector.md`·`monitor-collector-spec.md`는 PR 3(입법예고·국회)과 3단계가 남아 있어 `docs/plans/`에 둔다.
- 이 계획의 PR이 모두 끝나면 이 문서를 `docs/archives/`로 옮긴다.
