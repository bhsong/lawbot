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

## 7. PR 6~12 (시작 전에 이 문서에 설계를 덧붙임)

- **PR 6, 평가 도구 분리:** `eval_v4`, `report`(남는 부분), `score_answers`, `diff_answers`, `smoke_decree`, `feedback_review`, `feedback_export`, `log_summary`, `versions`를 `tools/lawbot-tools`로. 웹앱 런타임 의존성에서 분석용 라이브러리를 뺄 수 있는지 함께 본다. `uv run` 명령 이름은 유지.
- **PR 7, API 스키마 공통화:** 확장 필드(`input_type`, `max_seq_length`, `timings`, `/v1/rerank`)와 오류 type(`model_load_error`, `server_not_ready`, `server_busy`)을 `lawbot_common`의 pydantic 모델·상수로. 모델 서버는 그것으로 검증하고 웹앱은 그것으로 요청을 만든다. OpenAI SDK 계약 테스트는 유지.
- **PR 8, 설정 객체:** `security`·`deploy`·`service`·`app`·`feedback`이 각자 읽는 `os.environ`을 시작할 때 한 번 읽어 검증하는 `Settings`로. 테스트의 환경 변수 지우기 목록(`conftest.ENV_KEYS`)이 줄어드는지로 효과를 본다.
- **PR 9, 타입:** `Hit`(법률·하위법령), `Timing`, 오류 클래스 계층(예외에 `lawbot_error` 속성을 붙이는 방식 대신). CI에 pyright 추가.
- **PR 10, 로깅:** `print` → `logging`, 웹앱 `request_id`를 `X-Request-Id` 헤더로 모델 서버에 전달해 모델 서버 로그에 남김, `logs/requests.jsonl` 월별 파일.
- **PR 11, 임베딩 캐시:** 조문 CSV의 매니페스트 sha256 + 임베딩 모델 id + max_seq를 키로 벡터를 저장(gitignore 경로). 키가 같으면 재사용, 다르면 다시 임베딩. 캐시를 쓴 결과가 다시 임베딩한 결과와 비트 단위로 같은지 테스트.
- **PR 12, 재인덱싱 명령:** 새 조문 CSV → 매니페스트 갱신 → 임베딩 캐시 갱신 → 웹앱 재시작 안내. monitor 이벤트를 자동으로 연결하는 것은 3단계와 함께 다시 정한다 (이번에는 수동 명령까지).

## 8. 정리할 문서

- 시작 전 (작업 순서 0단계): `docs/plans/monitor-collector.md`·`monitor-collector-spec.md`는 PR 3(입법예고·국회)과 3단계가 남아 있어 `docs/plans/`에 둔다.
- 이 계획의 PR이 모두 끝나면 이 문서를 `docs/archives/`로 옮긴다.
