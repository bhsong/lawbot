# 계획: 기능 정리와 답변 모델(qwen3.8-27b) 분리

- 작성: 2026-11-18 KST. 기준 `main` `dc5bf32`, 운영 `706026f`(v6.2.1).
- 통합 브랜치: `refactor/slim-and-split-model`. 단계별 작업 브랜치를 이 브랜치에서 만들고, PR의 대상도 이 브랜치로 해서 병합한다. 모든 단계가 끝나면 통합 브랜치를 main에 병합하고 운영에 반영한다.
- 문서만 바꾸는 단계는 PR 없이, 사용자 승인 뒤 통합 브랜치에 바로 푸시한다.
- 구현은 `omp -p`(기본 모델 `openai-codex/gpt-6.1-sol:medium`)에 프롬프트를 전달해 수행한다. Claude는 프롬프트 작성, 판단, 테스트 실행, git·PR 작업, 운영 반영을 맡는다.

## 1. 사용자 결정 (2026-11-18)

1. 정확 일치 캐시와 검토된 비슷한 질문 추천의 코드·문서·데이터를 모두 제거한다. 남은 할 일(캐시 초안 2개 재승인, τ 0.80 결정)도 함께 없앤다.
2. 국회 입법예고와 국회 법률안의 수집·표시·검토 코드와 데이터를 모두 제거한다. 캘린더 DB의 해당 테이블도 삭제한다. 고용노동부·재정경제부의 '입법·행정예고' 게시판은 유지한다.
3. 기관 소식
   - 국세청은 지금처럼 모든 게시판을 수집한다.
   - 고용노동부·재정경제부는 아래 표의 게시판만 남긴다.
   - 홈택스 링크와 국가법령정보센터는 그대로 둔다.
4. qwen3.8-27b를 lawbot에서 분리해 단독 서비스로 운영한다.
   - 맥 미니 안의 다른 앱도 호출할 수 있게 한다. 무선망에는 열지 않는다.
   - git 없는 `~/Services/qwen27b/`에서 lawbot의 검증된 chat 서버 코드를 옮긴 범용 서버로 실행한다(B안, `mlx_lm.server`는 쓰지 않음). 요청은 먼저 온 순서대로 처리한다.
   - lawbot 모델 서버는 임베딩과 리랭커만 맡는다.
5. 삭제 후보는 Claude가 판단한다(§6).
6. `docs/`의 과거 기록(`docs/evals`, `docs/archives`)은 남긴다. 운영 문서(`docs/operations.md`, 서비스 README, 루트 README)의 설명만 지운다.

## 2. 단계와 PR

| 단계 | 작업 브랜치 | 내용 | 버전 영향 |
| --- | --- | --- | --- |
| 0 | (통합 브랜치에 직접) | 이 계획서. 끝난 계획 `reviewed-question-suggestions.md`를 보관 폴더로 옮긴다 | 없음 |
| 1 | `refactor/remove-exact-cache` | 정확 일치 캐시와 추천 질문 제거(§3) | Y |
| 2 | `refactor/remove-assembly` | 국회 법률안·입법예고 제거, 캘린더 DB 마이그레이션(§4) | Y(스키마) |
| 3 | `refactor/trim-agency-boards` | 고용노동부·재정경제부 게시판 정리(§5) | Z |
| 4 | `refactor/remove-unused-tools` | 삭제 후보 정리(§6) | Z |
| 5 | `refactor/split-chat-model` | 답변 모델 분리(§7) | Y |
| 6 | `refactor/review-followups` | 전체 코드 리뷰와 리팩터링(§8) | Z |
| 7 | 통합 → main | 최종 PR, 병합, 운영 반영 v6.3.0(§9) | 가장 높은 자리 Y 하나 → v6.3.0 |

단계 1~4는 서로 독립이라 순서를 바꿔도 된다. 단계 5는 단계 1 이후에 한다(추천 질문의 질의 벡터 코드가 사라진 상태에서 모델 연결을 바꿔야 충돌이 적다).

각 단계는 다음 순서로 진행한다.

1. 통합 브랜치에서 작업 브랜치를 만든다.
2. Claude가 omp 프롬프트를 작성해 전달한다. 프롬프트에는 범위, 금지 사항, TDD 순서, 완료 기준을 넣는다.
3. 결과 diff를 Claude가 검토한다.
4. 기본 테스트 전체와 린트·타입 검사를 실행한다.
5. 통합 브랜치를 대상으로 PR을 만들고, CI가 통과하면 병합한다.

## 3. 단계 1: 정확 일치 캐시·추천 질문 제거

**지울 것**

- 챗봇 모듈
  - `exact_cache.py`, `cache_runtime.py`, `cache_store.py`, `cache_question_store.py`
  - `cache_admin.py`, `cache_review_store.py`, `cache_release_store.py`, `cache_carry.py`
  - `cache_seed.py`, `cache_seed_store.py`, `cache_seed_pass.py`, `cache_seed_db_store.py`, `cache_eval.py`
  - `suggestions.py`, `question_vectors.py`
- 평가 도구 `eval_suggestions.py`
- 관리자 캐시 탭(`CachePanel.tsx`)과 채팅 화면의 캐시 표시·추천 칩
- 관련 테스트와 테스트 데이터
- 설정 `LAWBOT_EXACT_CACHE_DB`, `LAWBOT_SUGGESTIONS_TAU`와 `.env.example`·`.env.dev.example`의 해당 줄

**함께 고칠 곳**

| 위치 | 지금 | 바꿀 것 |
| --- | --- | --- |
| `tools/.../articles_update.py` | 06:30 조문 갱신 때 `cache_carry.transition()` 호출 | 호출과 요약 줄 제거. 06:30 실행 결과가 같은지 테스트로 확인 |
| `calc_corpus.py` | `exact_cache.digest` import | 함수를 사용하는 곳으로 옮김 |
| `calc_gate.py` | 계산 판정과 캐시 대상 판정을 같이 함 | 계산 판정만 남김 |
| `feedback_store.py` | 캐시 답변 피드백의 승격 거부 | 분기 제거 |
| `check_deploy.py`, `admin.py`, `web.py`, `service.py`, `settings.py` | 캐시·추천 연결 | 제거 |
| `log_summary.py`, `feedback_export.py` | `cache_*`·`suggestion_*` 필드 집계 | 집계 제거. 지난 로그에 필드가 있어도 오류 없이 읽음 |
| `docs/operations.md`, README | 캐시·추천 절 | 삭제 |

**완료 기준**

- 코드에 `exact_cache`, `cache_`(임베딩 캐시 `embed_cache` 제외), `suggestion`의 참조가 남지 않는다.
- 웹앱은 캐시 설정 없이 시작한다. 운영 `.env`에 옛 설정 줄이 남아 있어도 시작한다(반영 순서 안전).
- 계산 질문, 일반 질문, 피드백, 관리자 피드백 화면의 단위·통합·대역 E2E가 통과한다.

## 4. 단계 2: 국회 법률안·입법예고 제거

**지울 것**

- monitor
  - `collectors/assembly.py`, `assembly.py`
  - `config/targets.yaml`의 국회 대상
  - 요약(`summarize.py`)의 국회 절
  - CLI·설정의 국회 항목
- 캘린더
  - `assembly_ingest.py`, `notice_content.py`, `notice_source.py`
  - `sync.py`의 국회 가져오기
  - `manage.py`의 `assembly-*`, `notice-content` 명령
  - `server.py`·`models.py`·`admin.py`·`import_failures.py`의 국회 응답·검토
- 공통
  - `lawbot_common/assembly.py`
  - `erp_rules.py`의 `proposalLinks`
  - `lawbot-ui`(`ChangeView`, `EventList`, `types`)의 국회 표시
- 화면
  - 캘린더(`ChangeDialog`, `HoldDialog`, `api-types`)와 챗봇 관리자 `CalendarPanel`의 국회 검토
- source-hub
  - `sources.yaml`의 `assembly_bill`, `assembly_notice`
  - `daily.py`·`config.py`의 국회 처리
- 관련 테스트와 fixture
- 운영 문서와 README의 국회 절
- 국회 입법예고 전용이던 캘린더 '입법예고(notice)' 개념(PR #167 리뷰 중 추가 결정)
  - `change_events`의 `kind`·`published_on`·`comment_deadline` 컬럼, API의 notice 필터·건수·의견 마감 필드
  - 화면·공유 UI·관리자의 입법예고 범례·목록·문구, monitor 요약의 category 4(`입법예고·법안`)
  - 입법예고 근거 출처 `opinion.lawmaking.go.kr` 허용
- 고용노동부·재정경제부의 '입법·행정예고' 게시판(ERP 기관 소식)은 다른 기능이라 유지한다.

**원칙:** 옛 운영 데이터를 코드로 봐주는 호환 처리(폐기 필드 무시, seed에 없는 설정 무시, 폐기 출처 이벤트 건너뛰기 등)를 넣지 않는다. 옛 데이터는 운영 반영 때 정리한다(§9).

**캘린더 DB 마이그레이션 (스키마 v9)**

- 지울 테이블
  - `assembly_snapshots`, `assembly_entries`, `assembly_dates`, `assembly_date_index`
  - `assembly_review_audit`, `assembly_collection_status`
  - `assembly_notice_content`, `assembly_notice_attempts`
  - 뷰 `public_assembly`
- 국회 이벤트를 가리키는 다른 테이블의 행이 있으면 함께 정리한다(구현 때 외래 키를 확인한다).
- 기존 `law_calendar.db` 마이그레이션 방식을 따른다. 시작 전 백업 `.pre-v9.backup`을 만들고, 마이그레이션 뒤 `quick_check`와 `foreign_key_check`를 실행한다.
- 확인 기준: 법령 변경 공개 수(현재 70)와 보류·감사 기록은 그대로다.

**운영 데이터 (반영 때, §9)**

운영 사본 리허설(2026-11-18)에서 옛 데이터 때문에 새 코드가 실패하는 두 곳을 확인했다.
- source-hub 등록부에 국회·지운 게시판의 설정 행이 있으면 `Registry` 설정 검증이 실패해 `daily` 전체가 멈춘다.
- 오늘 작업 폴더(`collector.db.daily/<날짜>/rules.json`)와 `erp_daily.json`의 규칙에 옛 `proposalLinks`가 있어 `export-rules`와 캘린더 규칙 검증이 실패한다.

사본에 아래 정리를 적용한 뒤 새 코드의 `daily --republish`가 status ok로 끝났다(규칙 검증 통과, `proposalLinks`·국회 흔적 없음).

| DB | 지운 행 (리허설) |
| --- | --- |
| source-hub `collector.db` | `settings` 11, `runs` 45, `audit` 11 |
| `materials.db` | `revisions` 295, `documents` 295, `board_posts` 120, `board_runs` 6, `latest_success` 9 |
| monitor `state.db` | `events` 164(처리 안 된 것 0), `items` 4,202, `collector_baselines` 2 |

대상은 국회 2개와 §5의 지운 게시판 12개, 모두 14개 출처다. 국회 원문 파일(`monitor/data/files/assembly-*.json` 4,301개)과 국회 이벤트 JSON 164개는 확인 뒤 지운다.

**완료 기준**

- 코드에 `assembly` 참조가 남지 않는다.
- 06:30 실행 순서(수집 → 캘린더 sync → 조문 점검 → 요약 → ERP 발행)가 국회 없이 끝난다.
- v8 DB 사본에서 마이그레이션 시험이 통과한다(공개 법령 변경 수 불변).
- 캘린더 화면의 대역 E2E가 통과한다.

## 5. 단계 3: 고용노동부·재정경제부 게시판 정리

| 기관 | 남김 | 등록 삭제 |
| --- | --- | --- |
| 고용노동부 | `moel-rss-lawinfo` 입법·행정예고 RSS, `moel-rules` 고시·훈령·예규, `moel-interpretations` 행정해석·질의회시 | `moel-rss-notice` 공지사항 RSS, `moel-rss-policy` 정책자료 RSS, `moel-policy` 정책자료(비활성), `moel-publications` 주요 발간물·업무편람, `moel-press` 보도자료 |
| 재정경제부 | `mofe-tax-reform` 입법·행정예고, `mofe-laws` 법령자료실, `mofe-notice` 고시·공고·지침, `mofe-publications` 간추린 개정세법, `mofe-qa` 세제 문답자료(비활성, 후보 아님) | `mofe-rss` 공식 RSS 채널(비활성), `mofe-rss-press` 보도·참고자료, `mofe-rss-explanation` 설명·반박자료, `mofe-rss-notice` 공지, `mofe-rss-policy` 정책게시판, `mofe-rss-economy` 최근경제동향, `mofe-press` 보도·설명자료(비활성) |
| 국세청 | 모든 게시판 | 없음 |

- 쓰는 곳이 없어지는 수집 코드를 지운다. 예상 대상은 재정경제부 RSS 처리이고, 보도자료 전용 파싱이 있으면 그것도 지운다.
- 고용노동부 RSS는 입법·행정예고에서 계속 쓴다.
- `sources.yaml`의 YAML 앵커(`&moel`이 공지사항 RSS 행에 걸려 있음)는 남는 행으로 옮긴다.
- 게시판 순서 규칙(고시·법령·행정규칙 → 기타 → 공지·보도자료)은 그대로 둔다.
- 등록부 DB에 지운 출처의 설정 행이 있으면 설정 검증이 실패한다. 코드로 봐주지 않고, 반영 단계에서 설정 행을 지운다(§4 운영 데이터).
- 지운 게시판의 운영 기록(수집 자료 DB `materials.db`의 행, 등록부의 설정·실행 기록)은 반영 단계에서 삭제한다(사용자 결정 2026-11-18). 대상 행 수는 §4의 리허설 표에 있다.

**완료 기준**

- source-hub `daily`가 남은 출처만 수집하고, ERP 파일에 지운 게시판이 나오지 않는다.
- 남은 게시판의 순서와 10개 상한이 유지된다.

## 6. 단계 4: 삭제 후보

| 후보 | 결정 | 이유 |
| --- | --- | --- |
| `cache_seed*` | 단계 1에서 삭제 | 캐시 일부 |
| `services/monitor/deploy/com.lawbot.monitor.plist.example` | 삭제. `docs/plans/monitor-collector.md`의 언급을 고친다 | 운영 launchd는 source-hub 구성(`services/source-hub/deploy`) |
| `law-calendar/demo.ts` | **유지** | 캘린더 화면 테스트가 `?demo=true` 데이터로 돈다. 지우면 테스트 데이터를 새로 만들어야 하고 얻는 게 적다 |
| `smoke_decree.py`, `diff_answers.py`와 각 테스트 | 삭제 | 다른 코드가 쓰지 않는 수동 도구 |
| `score_answers.py`, `refusal.py` | 유지 | 기본 평가 `eval_v4.py`가 씀 |

## 7. 단계 5: 답변 모델 분리

### 구성

| 구분 | 지금 | 바꾼 뒤 |
| --- | --- | --- |
| 답변 생성(qwen3.8-27b) | lawbot 모델 서버(`chat.py`, BatchGenerator) | 단독 생성 서버(lawbot 밖, lawbot chat 서버 코드를 옮긴 범용 서버) |
| 임베딩·리랭커 | lawbot 모델 서버 | lawbot 모델 서버(그대로) |
| 가중치 | `lawbot-prod/models/v4/qwen_27b_mlx_4bit` | `~/Models/qwen3.8-27b-4bit` |
| 호출하는 곳 | 웹앱 답변, 계산 라우터, 하위법령 판단, monitor 아침 요약 | 같은 곳 + 맥 미니 안의 다른 앱 |

### 단독 생성 서버 (git 밖 `~/Services/qwen27b/`, B안 — 사용자 결정 2026-11-18)

처음에는 `mlx_lm.server`(mlx-lm에 들어 있는 예시용 서버)를 실행 스크립트로 감싸려 했다. 리뷰에서 그 서버에 운영용 마감이 없어 생기는 결함(인증·CORS·Host 없음, 잘못된 `stop` 값이나 프롬프트 캐시 완전 일치로 생성 스레드가 죽음, 이미지 URL 다운로드, 문자열 오류 형식 등)이 계속 나와 방향을 바꿨다. mlx-lm은 생성 라이브러리로만 쓰고, 서버는 lawbot 모델 서버에서 운영 검증된 chat 서버 코드(단계 5 직전 `e4c5595`의 `services/model-server`)를 옮겨 범용으로 만든다.

- 가져오는 것: 토큰 인증(Bearer), 127.0.0.1 고정, `mlx_lm` BatchGenerator 생성 엔진, 생각 숨김, 동시 생성 수·대기열 상한(가득 차면 503 busy), OpenAI 오류 형식, 가중치 지문(revision)
- 걷어내는 것: 임베딩·리랭커, 여러 chat 모델 상주 교체(27B 하나 고정), `lawbot_common` 의존(필요한 작은 부분만 복사), `LAWBOT_` 설정 이름
- 함께 넣는 것: 보안 리뷰 S05(chunked 본문 누적 상한)
- 계약(lawbot이 쓰는 그대로): `POST /v1/chat/completions`(스트리밍·usage·`chat_template_kwargs`), `GET /v1/models` → `id`, `revision`, `max_concurrent`. 허용하지 않은 모델 id는 404. 토큰은 lawbot `LAWBOT_LLM_API_KEY`와 같은 값
- 실행 폴더: uv 프로젝트, `mlx-lm`은 `uv.lock`으로 고정(0.32.2), `.env`(포트·토큰·가중치 경로·모델 id)와 `.env`를 읽어 실행하는 시작 스크립트. `HF_HUB_OFFLINE=1`
- 구현과 테스트는 omp가 가짜 생성 엔진으로 한다(실제 27B는 운영 반영 때만 올린다). 끝나면 Astra(high)로 소프트웨어·보안 리뷰를 한다.
- lawbot 설정: 생성 서버 주소는 기존 `LAWBOT_LLM_BASE_URL`을 필수로 바꿔 쓰고 토큰은 `LAWBOT_LLM_API_KEY`로 받는다(`LAWBOT_CHAT_SERVER`는 만들지 않는다). 대기열 상한 때문에 503 busy가 다시 생기므로 lawbot의 "모델 서버가 바쁩니다" 안내를 되살린다.
- 상시 실행
  - `~/Library/LaunchAgents/` 아래 plist. 레이블은 lawbot과 겹치지 않게 `local.qwen27b`로 한다. 비밀값·포트는 plist에 넣지 않는다.
  - 로그는 `~/Library/Logs/qwen27b/`에 둔다.
- 운영 문서: `docs/operations.md`에 외부 의존 서비스로 주소 설정, 상태 확인, 재시작, 가중치 위치를 적는다. 포트와 토큰은 적지 않는다.

### lawbot 변경 (omp 구현)

- 모델 서버
  - `chat.py`와 chat 등록, 대기열·동시 실행 수, chat 경로를 제거한다.
  - 임베딩·리랭크 API와 토큰 인증, revision 보고는 유지한다.
- 웹앱·계산 라우터·하위법령 판단
  - chat 호출을 `LAWBOT_LLM_BASE_URL`(필수)과 `LAWBOT_LLM_API_KEY`로 보낸다.
  - 임베딩·리랭크는 기존 `LAWBOT_MODEL_SERVER`로 보낸다.
  - 모델 id는 `.env.example`에서 읽는다.
- 생성 서버 계약에 맞출 것: 스트리밍 SSE 형식, usage, 오류 응답, 첫 토큰·완료 측정.
  - 연결 실패는 `model_server`로 안내한다.
  - 응답 조각 사이 시간 초과는 `model_server_timeout`으로 안내한다.
  - 대기열이 가득 찬 503 `server_busy`는 지금처럼 혼잡 안내(`model_server_busy`)로 보여 준다.
  - 생각 블록이 나오면 지금처럼 숨긴다.
- monitor 아침 요약: chat 주소를 새 설정으로 바꾼다.
- `check_deploy`와 시작 시 점검: chat 서버의 `/v1/models`에 필요한 모델 id가 있는지 확인한다.
- 실행 구성 기록(AGENTS.md 실제 모델 검증): chat 모델 revision은 `lawbot_common.model_revision`으로 가중치 폴더의 지문을 만들어 남기고, `mlx-lm` 버전을 함께 적는다.
- 테스트
  - 계약 테스트와 대역 E2E에 생성 서버 계약을 따르는 가짜 chat 서버를 둔다(CI에서 실제 MLX는 쓰지 않는다).
  - 임베딩·리랭크는 지금처럼 실제 모델 서버 앱과 가짜 모델로 시험한다.
- 문서: AGENTS.md의 공유 모델 서버·테스트 구성 절, `docs/operations.md`, `.env.example`, `.env.dev.example`

### 검증

- 기본 테스트 전체
- 실제 모델 검증(승인 후, 맥 미니): 반영 직후에 실행한다.
  - 모델 스모크: chat 1회, 임베딩 1회, 리랭크 1회
  - `e2e_live` 3시나리오
  - 실행 전에 요청 수와 시간을 다시 알리고 승인받는다.

**완료 기준**

- lawbot 저장소에 chat 생성 코드가 남지 않는다.
- 웹앱·계산 라우터·아침 요약이 새 chat 주소로 동작한다.
- 대역 E2E와 계약 테스트가 통과한다. 실제 모델 E2E는 반영 단계에서 통과한다.

## 8. 단계 6: 전체 코드 리뷰와 리팩터링

1. 단계 1~5가 통합 브랜치에 병합된 뒤 리뷰를 실행한다.
   - 명령: `omp -p --model openai-codex/gpt-6-astra --thinking high`
   - 범위: 코드베이스 전체. 특히 이번에 지운 기능의 잔재, 죽은 코드, 단순화 여지, 오류 처리, 테스트 공백을 본다.
   - 결과는 `results/` 아래 임시 파일로 받는다.
2. Claude가 항목별로 근거를 확인해 타당한 것만 고른다. 고른 것과 버린 것(이유 포함)을 사용자에게 보고한다.
3. 타당한 항목을 omp 기본 모델로 리팩터링한다. PR은 통합 브랜치 대상으로 만든다.

리뷰 문서는 `docs/reviews`에 저장하지 않는다(요청 시에만).

## 9. 단계 7: main 병합과 운영 반영 (v6.3.0)

1. 통합 브랜치 → main PR을 만든다.
   - 검증 표: 단위, 계약, 통합, 대역 E2E, 실제 모델 E2E(반영 후 채움), 모델 스모크, 품질 평가(실행 안 함).
   - CI 통과와 리뷰 확인 뒤 병합한다(merge commit).
2. 운영 반영은 06:20–06:50을 피한다. 5–7번(모델 서버 재시작부터 웹앱 재시작까지, 27B 로드 포함 몇 분) 동안 휴대폰 답변이 멈춘다.
   1. 운영 트리 HEAD와 미커밋 변경을 확인하고, `706026f` → 병합 커밋의 diff를 확인한다.
   2. 백업(git 밖, 권한 600)
      - 캘린더 DB `.pre-v6.3.0.backup`
      - source-hub `collector.db`·`collector.db.daily/`·`materials.db`, monitor `state.db`
      - 운영 `.env`와 개발 `.env` 사본
   3. 운영 트리를 병합 커밋으로 옮기고(detached) `.env`를 고친다. 값은 출력하지 않는다.
      - 운영 `.env` 추가: `LAWBOT_LLM_BASE_URL`, `LAWBOT_LLM_API_KEY`(생성 서버 토큰은 새로 만든다)
      - 운영 `.env` 삭제: `LAWBOT_SERVE_CHAT`, `LAWBOT_SERVE_PRELOAD`, `LAWBOT_MAX_RESIDENT`, `LAWBOT_MAX_CONCURRENT`, `LAWBOT_MAX_QUEUED`, `LAWBOT_EXACT_CACHE_DB`, `LAWBOT_SUGGESTIONS_TAU`
      - 개발 `.env`: 같은 두 키를 추가한다.
   4. 데이터 정리(재시작 전, 새 코드가 옛 데이터로 실패하지 않게). 명령과 대상 행 수를 보여 주고 승인받은 뒤 실행한다.
      1. 지운 출처 14개의 행 삭제(§4 운영 데이터 표의 DB들, 한 DB마다 한 트랜잭션, 무결성 검사)
      2. 캘린더 DB 마이그레이션 v9(`.pre-v9.backup` 생성, 무결성 검사)
      3. 오늘 작업 폴더의 옛 `rules.json` 삭제
      4. 새 코드로 `source-hub daily --republish`(launchd와 같은 인자, 외부 수집 없음)
   5. 모델 서버를 새 코드로 재시작해 27B를 내린다(임베딩·리랭커만 남는다). 27B가 두 번 올라가지 않도록 생성 서버보다 먼저 한다.
   6. 생성 서버를 띄운다.
      - 가중치를 `~/Models/qwen3.8-27b-4bit`로 이동(같은 디스크라 즉시)
      - `~/Services/qwen27b/.env`(포트·토큰·가중치 경로, 권한 600)를 쓰고, `run.sh`를 실행하는 launchd `local.qwen27b`를 등록한다(로그 `~/Library/Logs/qwen27b/`).
      - `/v1/models`가 200을 돌려줄 때까지 기다린다.
   7. 웹앱과 캘린더를 재시작한다. source-hub는 다음 06:30에 새 코드로 돈다.
   8. 상태 확인: `check_deploy`, 웹 `/`, 캘린더 `/`·상태 API, ERP API(`X-ERP-Snapshot`과 파일 해시), `holds-check`, 규칙 검증
   9. 실제 모델 검증(승인 후): 모델 스모크, `e2e_live` 3시나리오
   10. 남은 운영 파일 삭제. 대상 목록을 보여 주고 승인받은 뒤 지운다.
      - 캐시 DB
      - 국회 원문 파일 4,301개와 국회 이벤트 JSON 164개
      - 2번의 `.env` 백업 사본(반영 확인 뒤)
   11. 태그 `v6.3.0`을 붙이고 `docs/evals/2026-11-18-deploy-v6.3.0.md`에 기록한다. 이 계획서를 `docs/archives/`로 옮긴다. 다음 날 06:41 점검 cron을 예약한다.
3. 되돌리기
   - 운영 트리를 `706026f`로 되돌리고 `.env` 사본과 2번의 DB 백업을 복구한다.
   - 생성 서버를 launchd에서 내리고, 가중치를 원래 경로로 옮긴다.
   - 이전 모델 서버(27B 포함)로 재시작하고 웹앱·캘린더를 재시작한다.
   - 파일 삭제(10번)는 확인이 끝난 뒤에만 하므로, 되돌리기 전에는 원본 파일과 백업이 남아 있다.

## 10. 위험

| 위험 | 대응 |
| --- | --- |
| 06:30 실행이 지운 기능을 부름 | 단계마다 `daily` 순서 테스트. 반영 다음 날 06:41 점검 |
| 생성 서버의 생각 끄기·스트리밍이 실제 모델에서 다르게 동작 | 개발 중에는 27B를 한 번 더 올릴 메모리가 없으므로 생성 서버 테스트와 가짜 서버로 계약을 맞춘다. 실제 확인은 반영 단계의 모델 스모크에서 한다(실패하면 되돌리기) |
| 메모리 | 총량은 같다(27B 한 번만 로드). 반영 때 모델 서버를 새 코드로 재시작해 27B를 내린 뒤 생성 서버를 띄워 두 번 올라가지 않게 한다. 반영 뒤 메모리와 첫 토큰 시간을 기록한다 |
| 다른 앱이 생성을 오래 점유 | 먼저 온 순서대로 처리하기로 결정함. 웹앱의 응답 대기 상한 안내가 나올 수 있음 |
| 캘린더 스키마 v9 | 사본 시험, 백업, 무결성 검사 |
