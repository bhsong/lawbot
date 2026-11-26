# 설치·실행·운영

README의 "사용법"을 자세히 적은 문서입니다. 구조는 [architecture.md](architecture.md), 평가는 [evals/README.md](evals/README.md)를 보세요.

## 기관 소식 수집 범위

고용노동부는 입법·행정예고 RSS(`moel-rss-lawinfo`), 고시·훈령·예규(`moel-rules`), 행정해석·질의회시(`moel-interpretations`)만 등록합니다. 재정경제부는 입법·행정예고(`mofe-tax-reform`), 법령자료실(`mofe-laws`), 고시·공고·지침(`mofe-notice`), 간추린 개정세법(`mofe-publications`)과 비활성 세제 문답자료(`mofe-qa`)를 등록합니다. 국세청의 전체 게시판, 국가법령정보센터와 홈택스 링크는 유지합니다. [수동 수집·일일 발행](../services/source-hub/README.md)

ERP 기관 소식은 고시·법령·행정규칙 → 기타 → 공지·보도자료 순서이며 게시판별 최신 10개까지 제공합니다. 국세청 공지사항은 마지막에 표시합니다.

축소 전 데이터는 코드로 호환하지 않습니다. 운영 반영 시 별도 승인·백업 후 제거 대상 출처의 등록부 `settings`·`audit`(`kind='source' AND target IN (...)`)와 `runs`(`source_id IN (...)`), 수집 자료 DB의 `news_reviews`·`board_posts`·`revisions`·`documents`·`board_runs`·`latest_success`(`source_id IN (...)`)를 외래 키의 자식부터 정리합니다. 대상은 이번 등록 축소에서 제거한 12개 출처 ID로 한정하고 유지 출처의 행을 보존합니다. 당일 작업 스냅샷과 공개 ERP 파일에도 이전 등록 목록이 남을 수 있으므로 등록부 정리 후 새 계획으로 재발행하고 게시판 목록을 확인합니다.

## 요구 사항

- Apple Silicon 맥(M 시리즈), 메모리 8GB 이상
- [uv](https://docs.astral.sh/uv/) (Python 3.14는 `uv sync` 때 자동 설치)
- Node.js (`services/chatbot/frontend/.nvmrc`의 버전, 화면 빌드용)
- 학습된 모델 파일 (아래 "모델 준비" 참고)

## 설치

```bash
git clone https://github.com/bhsong/lawbot.git
cd lawbot
uv sync
(cd services/chatbot/frontend && npm ci && npm run build)   # 화면 파일 (dist/, Git 제외)
```

화면 코드(`services/chatbot/frontend`)가 바뀌면 다시 빌드합니다. 빌드가 없거나 지금 소스와 다르면 `lawbot-app`이 시작을 거부합니다(`dist/build.json`의 소스 지문 비교).

## 캘린더 DB v9 반영

운영 반영 승인 후 등록된 캘린더 DB 경로와 쓰기 작업을 확인합니다. 같은 DB를 사용하는 sync와 검토 쓰기가 없는 배포 창을 잡고, 먼저 SQLite 사본에서 `uv run --project services/law-calendar --locked python -m law_calendar.db "$CALENDAR_DB"`를 실행합니다.

v8에서 올리면 `.pre-v9.backup`이 생성됩니다. 마이그레이션은 제거된 수집원의 테이블과 공개 뷰를 정리하고, 법령 변경 이벤트의 종류·게시일·의견 마감 컬럼과 전용 인덱스를 삭제한 뒤 시행 조문 기준의 공개 뷰를 다시 만듭니다. 모두 한 트랜잭션에서 처리하며 새 DB도 같은 v9 스키마로 초기화합니다. `PRAGMA user_version`, `PRAGMA quick_check`, `PRAGMA foreign_key_check`와 공개 법령 변경 수, 보류·감사 기록의 보존을 확인합니다. 양쪽 화면을 빌드한 뒤 공개 캘린더와 관리자 보류 검토를 확인합니다.

백업에는 제거 전 자료가 포함되므로 접근 권한과 보존 기간을 정합니다. 복구가 필요하면 새 쓰기를 멈추고 현재 DB와 WAL을 보존한 뒤, 백업 사본의 정합성과 버전을 확인해 승인된 이전 코드와 DB 경로로 되돌립니다.

## 캘린더 매일 점검

06:41 KST에 캘린더 수집 결과를 확인할 때 등록된 캘린더 DB 경로를 `CALENDAR_DB`에 지정하고 다음 읽기 전용 점검을 실행합니다.

```sh
uv run --project services/law-calendar --locked python -m law_calendar.manage --database "$CALENDAR_DB" holds-check
```

`pending_outside_snapshot`은 현재 수집본 밖에 있는 `pending` 조문 수이고, `hidden_pending_holds`는 보류 행이 있으나 관리자 보류 목록에 나오지 않는 `pending` 조문 수입니다. 한쪽이라도 0보다 크면 종료 코드는 1입니다.

## 법령 캘린더 정적 사이트 게시

운영 반영 승인 후 Pages에 Direct Upload 프로젝트를 만들고 운영 브랜치를 `main`으로 설정합니다. 계정 2단계 인증과 미리보기 접근 제한을 확인합니다. 계정 범위를 제한하고 **Pages Edit 권한만** 가진 전용 API 토큰을 발급합니다. 처음 게시하기 전에 대시보드에서 계정과 프로젝트 이름이 설정과 일치하는지 확인합니다. 같은 계정에 있는 다른 유효 프로젝트 이름을 지정하면 게시기가 의도와 다른 이름인지 구분할 수 없습니다.

고정 게시 입력은 `services/law-calendar/.env`에 둡니다. 게시기는 셸이나 `LAWBOT_ENV_FILE`의 값을 사용하지 않습니다. 운영에서 사용하는 DB·일일 스냅숏·화면 경로는 절대 경로로 설정합니다.

| 변수 이름 | 용도 |
| --- | --- |
| `LAWBOT_CALENDAR_PAGES_PROJECT` | Pages 프로젝트 이름 |
| `LAW_CALENDAR_DB` | 캘린더 DB 파일 |
| `LAWBOT_CALENDAR_ERP_DAILY_FEED` | `source-hub daily`가 만드는 ERP 일일 공개 스냅숏 파일 |
| `LAWBOT_CALENDAR_NODE` | 필수. 신뢰된 Node 실행 파일의 절대 경로. 해석한 경로의 파일과 조상은 현재 사용자 또는 root 소유여야 함. 다른 사용자 쓰기와 실행 파일의 그룹 쓰기는 허용하지 않음. 조상 디렉터리의 그룹 쓰기는 macOS에서만 `wheel`(gid 0) 또는 `admin`에 허용하며, 그 외 OS에서는 모두 거부함. ACL은 확인하지 않으므로 Node의 조상에 다른 사용자에게 쓰기를 허용하는 ACL이 없어야 함(Homebrew 기본 설치에는 없음) |
| `LAWBOT_CALENDAR_FRONTEND_DIST` | 화면 빌드 폴더. 미설정 시 서비스의 `frontend/dist` |
| `LAWBOT_CALENDAR_SITE` | 공개 사이트 폴더. 미설정 시 `services/law-calendar/data/site` |

`CLOUDFLARE_ACCOUNT_ID`와 `CLOUDFLARE_API_TOKEN`은 git에서 제외된 `services/law-calendar/pages.env`에만 둡니다. 현재 사용자 소유 `0600` 일반 파일이 필요하며 심볼릭 링크와 하드 링크를 거부합니다. 두 변수는 기존 서비스 `.env`, 셸·공통 launchd 환경에서 제거합니다. 서버는 서비스 `.env`의 예전 키를 거부합니다. 전환 뒤 캘린더 서버는 루프백 전용이지만, **같은 OS 사용자로 실행하므로 서버가 침해되면 `0600` 파일도 읽힐 수 있습니다.** 이 절차는 프로세스 환경을 분리하며 OS 사용자를 분리하지는 않습니다.

운영 트리 루트에서 화면을 빌드하고 처음에는 수동으로 게시합니다. `publish`의 `--database`, `--erp-daily-feed`, `--frontend-dist`, `--out`은 덮어쓰기가 아니라 일치 검사입니다. daily에 설정한 DB·스냅숏도 `.env`와 같아야 합니다. Wrangler는 서비스의 `frontend/node_modules`에서만 실행하며, 작업·홈·캐시는 비공개 디렉터리에 격리하고 디스크 로그는 끕니다.

```sh
uv sync --project services/law-calendar --locked
(cd services/law-calendar/frontend && npm ci && npm run build)
uv run --project services/law-calendar --frozen --no-sync --no-env-file law-calendar publish
```

게시기는 업로드 전에 설정한 계정에서 프로젝트 이름과 운영 브랜치(`main`)를 확인합니다. 운영 주소의 `data/site-build.json`이 이번 내보내기와 일치할 때만 이력을 정리합니다. 완전한 목록을 검증한 뒤 각 삭제 직전 canonical 운영 ID를 재확인하고, 삭제 뒤 현재 운영 배포 하나만 남았는지 확인합니다. 운영 확인이 실패하면 삭제하지 않습니다. 게시 도중 대시보드나 다른 자동화에서 배포·롤백하지 않습니다. API 조회와 삭제 사이의 외부 변경까지 원자적으로 막을 수는 없습니다.

캘린더 공개 화면은 Pages에서만 제공합니다. 운영 반영 시 `com.lawbot.calendar`의 launchd 실행 인자에서 `--frontend`와 그 경로를 제거하고 루프백 API만 유지합니다. 터널에서 캘린더 ingress 규칙을 제거하는 절차는 [전환 순서](archives/calendar-pages-static.md#6-전환-순서-운영-반영-승인-뒤-각-단계-확인-후-다음-단계)를 따릅니다.

daily는 정상·부분 실패·스냅숏 재사용 뒤 게시를 한 번 요청하고 `calendarPublish`를 남깁니다. 수집 결과와 종료 코드는 게시 결과와 별개입니다. 서버·로컬 검토 CLI는 프로젝트가 설정돼 있고 검토 DB가 설정 DB와 같은 경우에만 예약합니다. 요청 잠금 대기 실패는 저장 결과를 바꾸지 않습니다. 큐·잠금·작업자 로그는 업로드 밖의 `services/law-calendar/.pages-publish/`(`0700`)에 둡니다. 서비스·CLI 로그와 이 폴더의 `worker.log`에서 `calendar_publish_*`를 확인합니다.

수동 실행은 한 줄 JSON으로 보고합니다. `ok`는 운영 확인·정리 완료, `pages_not_configured`를 동반한 `skipped`는 프로젝트를 설정하지 않은 상태입니다(고정 입력이 구성된 경우 로컬 사이트는 갱신). 프로젝트가 활성화됐는데 자격 증명 파일이 누락되거나 불완전하거나 권한이 잘못됐으면 `invalid_pages_credentials`로 실패합니다. 게시 전체 기한은 480초, 워커·daily의 상한은 540초이며 초과 시 프로세스 그룹을 종료합니다. 검증 실패 시 자동 롤백하지 않습니다. 정리 도중 실패했다면 이미 지운 배포는 돌아오지 않습니다.

철회는 [로컬 검토 명령](../services/law-calendar/README.md#저장된-모니터링-결과와-공개-검토)에서 `decision`을 `withdraw`로 저장합니다. 수집 중 자동 철회와 수동 철회는 모두 다음 게시에 반영됩니다. 정리에 실패하면 게시 결과도 실패로 보고하고, 다음 게시에서 남은 이력을 다시 정리합니다. 배포 이력 삭제는 CDN·브라우저 캐시 무효화가 아닙니다.

**Pages 이력으로 롤백할 수 없습니다.** 내용을 되돌려야 하면 캘린더 DB의 검토 상태를 수정하거나 승인된 DB 사본을 복원하고, 대응하는 일일 스냅숏을 확인한 뒤 위와 같은 명령으로 다시 게시합니다.

## 법령 개정 재생성 미리보기

작업 트리의 루트에서 `MONITOR_HOME`에 monitor home, `LAW_PATH`에 로컬 Legalize 저장소 안의 파일 경로, `COMMIT`에 개정 커밋을 지정합니다. monitor는 기존 법제처 보강을 적용하고 미리보기 원천을 실행별 임시 폴더에 저장합니다. 다른 로컬 클론을 쓸 때는 `--repo-path`를 지정합니다.

```sh
PREVIEW_DIR="$(mktemp -d)"
uv run law-monitor --home "$MONITOR_HOME" replay-amendment --path "$LAW_PATH" --commit "$COMMIT" \
  --dry-run --output-dir "$PREVIEW_DIR"
```

출력 JSON의 `event_path`를 `EVENT_JSON`에 지정합니다. `lawgo_requests`에서 실제 법제처 요청 수를 확인합니다. 캘린더는 별도 환경에서 이벤트 JSON과 그 폴더의 `data/files/` 원문을 읽어 조문별 시행일과 공개 판정을 출력하므로 미리보기에 DB 경로가 필요 없습니다.

```sh
uv run --project services/law-calendar --locked python -m law_calendar.manage preview "$EVENT_JSON"
```

운영 코드 반영과 운영 이벤트 재생성·import는 각각 사용자 승인 후 진행합니다. [실제 재생성 및 import](../services/monitor/README.md#감시-기준점과-단일-개정-재생성)

## 모델 준비

학습된 모델은 용량이 커서 저장소에 포함하지 않습니다. `.env.example`은 v5 조문과 v6 임베딩, 기존 리랭커와 Pro 27B를 선택합니다. 조문·매니페스트와 프롬프트는 저장소에 있으며 임베딩·리랭크 모델 폴더는 `LAWBOT_SERVE_*`로 지정합니다. Pro 생성 모델은 저장소 밖 단독 생성 서버에서 관리합니다.

```
data/v5/
├── articles_v5.csv                    # 비삭제 법률 조문 812개
├── law_articles_v5_decree.csv          # 법률·하위법령 비삭제 1,859개
├── manifest.json
├── manifest_law_articles_v5_decree.json
└── system_v5_concise.txt               # Pro 간결형 system (답변 형식은 간결형 하나)
models/v6/
└── emb_v6_fp16/                        # v6 Qwen3-Embedding-0.6B (v5b에서 이어 학습), sentence-transformers fp16
models/v5/
└── emb_v5b_fp16/                       # v5b (v6.0.0부터 모델 서버에 올리지 않음, 되돌리기용)
models/v4/
└── reranker_4b_mlx_4bit/               # Qwen3-Reranker-4B, MLX 4비트
```

`article_id`에 법령명이 들어 있으면(`근로기준법 제26조`) 번호만 담은 `article_no` 열(`제26조`)을 함께 둡니다. 앱은 `article_no`가 있으면 그 값을 조문 번호로 씁니다.

리랭커는 원본을 MLX 4비트로 변환합니다.

```bash
uv run mlx_lm.convert --hf-path Qwen/Qwen3-Reranker-4B --mlx-path models/v4/reranker_4b_mlx_4bit -q --q-bits 4 --q-group-size 64 --dtype float16
```

파일이 설정과 맞는지는 모델을 올리지 않고 확인할 수 있습니다. 문제가 있는 항목은 `FAIL`로 표시됩니다.

```bash
uv run python -m lawbot_model_server.check  # 모델 서버: .env의 LAWBOT_SERVE_* 모델 폴더 (양자화·어댑터·토크나이저·query 프롬프트)
uv run python -m lawbot.check_deploy        # 웹앱: 조문 CSV·system·검색 설정 + 모델 서버 접속·필요 모델 확인
uv run python -m lawbot_tools.versions            # 설치된 패키지 버전·맥 사양
```

## 실행 (임베딩·리랭크 모델 서버 + 단독 생성 서버 + 웹앱)

임베딩·리랭크는 모델 서버(`services/model-server`), 답변·계산 분류·하위법령 판단·monitor 요약은 저장소 밖 단독 생성 서버가 맡습니다. 웹앱은 조문 검색·프롬프트 조립·화면을 담당합니다. `.env.example`을 복사해서 로그인, 모델 서버 주소·토큰과 생성 서버 주소·전용 토큰을 채웁니다. 생성 서버는 먼저 별도 관리 위치에서 준비합니다.

```zsh
cp .env.example .env
chmod 600 .env            # 나만 읽고 쓰기 (비밀번호·토큰이 들어가므로)
openssl rand -hex 32      # LAWBOT_MODEL_TOKEN과 LAWBOT_LLM_API_KEY는 각각 별도로 생성
open -e .env              # 로그인 및 두 서버의 주소·전용 토큰 입력
uv run lawbot-model-server   # 터미널 1: 포트를 먼저 열고 모델을 올린 뒤 'ready'
uv run lawbot-app            # 터미널 2: 모델 서버가 준비될 때까지 기다린 뒤 조문 인덱스를 만들고 시작
```

- `.env`는 `.gitignore`에 들어 있어 Git에 올라가지 않습니다. `.env.example`에는 비밀번호를 적지 마세요.
- 셸에서 `export`한 변수가 있으면 그 값이 `.env`보다 우선합니다. 한 번만 다르게 실행할 때는 `LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app`처럼 앞에 붙입니다.
- 다른 파일을 쓰려면 `LAWBOT_ENV_FILE=<설정 파일 경로> uv run lawbot-app`, 파일을 읽지 않으려면 `LAWBOT_ENV_FILE=`.
- 화면을 고치는 중에는 `lawbot-app`을 띄운 채 `services/chatbot/frontend`에서 `LAWBOT_WEB_URL=http://127.0.0.1:<웹앱 포트> npm run dev`로 Vite 개발 서버를 띄웁니다. `/api`는 웹앱으로 넘어가고 화면은 고치는 즉시 바뀝니다. 다 고친 뒤 `npm run build`를 해야 `lawbot-app`이 새 화면을 냅니다.
- `.env`에는 `LAWBOT_*`만 적을 수 있습니다. 서버 주소는 바꿀 수 없고, 웹앱 포트는 `LAWBOT_PORT`로 정합니다(아래 참고).
- 값에 공백이나 ` #`이 들어가면 작은따옴표로 감쌉니다: `LAWBOT_PW='a b#c'`.

비밀번호를 파일에 두지 않으려면 `.env`의 `LAWBOT_PW=` 줄을 지우고 실행할 때 입력하거나 macOS 키체인에서 꺼내 씁니다(zsh).

```zsh
read -rs 'LAWBOT_PW?비밀번호: ' && export LAWBOT_PW && uv run lawbot-app
security add-generic-password -a lawbot -s lawbot -w   # 키체인에 저장 (최초 1회)
LAWBOT_PW="$(security find-generic-password -a lawbot -s lawbot -w)" uv run lawbot-app
```

조문 CSV나 system 파일이 없으면 모델 서버에 접속하기 전에 실행을 거부하고 안내합니다.

v4·v5의 답변 모델은 Lawbot Pro 하나입니다. v4는 `data/v4/system_v4_concise.txt`, v5는 `data/v5/system_v5_concise.txt`를 기본 프롬프트로 씁니다. 화면은 결론과 실제 인용 조문을 표시합니다. [형식 비교](evals/2026-11-19-reasoning-format-definitions.md)

화면 아래 측정 줄에 검색·첫 토큰·첫 글자·생성 속도·완료 시간이 표시됩니다. 시작 시 로컬 생성 서버의 연결 실패·503 `server_not_ready`는 `LAWBOT_MODEL_SERVER_WAIT` 한도 안에서 1초 간격으로 다시 확인합니다. 토큰 오류나 `/v1/models`의 필요한 모델 id 누락은 즉시 시작을 거부합니다. 외부 생성 API는 준비 확인을 생략하고, `check_deploy`는 한 번만 조회합니다. 생성 서버는 모델 교체 요청을 받지 않습니다.

웹앱은 모델을 폴더가 아니라 모델 id로 고릅니다(`LAWBOT_*_MODEL`). 임베딩·리랭크 폴더는 모델 서버의 `LAWBOT_SERVE_*`, 생성 모델은 저장소 밖 생성 서버가 관리합니다. 아래 표가 전체 변수입니다(상대 경로는 저장소 최상위 기준).

웹앱은 이 맥에서만 쓰는 로컬 전용입니다. 주소는 `127.0.0.1` 고정이고, 포트는 `.env`의 `LAWBOT_PORT`(없으면 기존 기본값)입니다. 브라우저에서 `http://127.0.0.1:<포트>`로 접속해 로그인합니다. 같은 네트워크의 다른 기기에서는 접속할 수 없습니다. 주소는 바꿀 수 없습니다(`services/chatbot/src/lawbot/security.py`). 로그인 세션은 웹앱 메모리에만 있어 웹앱을 다시 띄우면 다시 로그인합니다. https(Tailscale)로 접속하면 로그인 쿠키에 `Secure`가 붙습니다. 상시 운영할 때는 `.env`에 `LAWBOT_PORT`를 따로 정하세요.

웹앱(`lawbot-app`, `check_deploy`, `eval_v4`):

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `LAWBOT_USER` | 예 | 로그인 사용자명 (앞뒤 공백 제거 후 비어 있으면 안 됨) |
| `LAWBOT_PW` | 예 | 로그인 비밀번호 (빈 값·공백만 있는 값은 안 됨) |
| `LAWBOT_ADMIN_USER` | 관리자 사용 시 | 관리자 사용자명. `LAWBOT_USER`와 달라야 함 |
| `LAWBOT_ADMIN_PW_HASH` | 관리자 사용 시 | `python -m lawbot.auth hash`가 출력한 scrypt 해시. `.env`에서 작은따옴표로 감쌈 |
| `LAWBOT_MODEL_SERVER` | 예 | 모델 서버 주소 `http://127.0.0.1:<모델 서버 포트>` (이 맥만 허용) |
| `LAWBOT_MODEL_TOKEN` | 예 | 모델 서버 토큰 (16자 이상, `openssl rand -hex 32`). 웹앱과 모델 서버가 같은 값 |
| `LAWBOT_MODEL_SERVER_WAIT` | 아니요 | 시작할 때 임베딩·리랭크 모델 서버와 로컬 생성 서버 각각의 준비를 기다리는 최대 초 (기본 `300`) |
| `LAWBOT_MODEL_READ_TIMEOUT` | 아니요 | 질문 처리 중(질문 임베딩·리랭크·생성) 응답 조각 사이 최대 대기 초 (기본 `300`). 넘으면 "모델 서버 응답이 없습니다", 요청 로그 `model_server_timeout`. 시작할 때 조문 인덱스 인코딩에는 걸지 않음 |
| `LAWBOT_PROFILE` | 아니요 | `v4` (생략 시 기본값) 또는 `v5` (노동·소득세법 전체와 세금·4대보험 선택 범위) |
| `LAWBOT_EMB_MODEL` | 아니요 | 검색 임베딩 모델 id (v4 기본 `lawbot-emb-v4`, v5 기본 `lawbot-emb-v6`. v6.0.0 전에는 `lawbot-emb-v5b`) |
| `LAWBOT_PRO_MODEL` | 아니요 | 답변 모델 id (기본 `qwen3.8-27b-4bit`). 외부 API면 그쪽 모델 이름 |
| `LAWBOT_ENV_FILE` | 아니요 | 읽을 설정 파일 (기본 레포 최상위 `.env`, 없으면 안 읽음). 빈 값이면 읽지 않음. 셸에서만 지정 |
| `LAWBOT_PRO_SYSTEM` | 아니요 | Pro 간결형 system 파일. v4 기본 `data/v4/system_v4_concise.txt`, v5 기본 `data/v5/system_v5_concise.txt` |
| `LAWBOT_V4_ARTICLES` | 아니요 | v4 조문 CSV (기본 `data/v4/articles_v4.csv`) |
| `LAWBOT_V5_ARTICLES` / `LAWBOT_V5_DECREE_ARTICLES` | 아니요 | v5 법률 / 법률·하위법령 CSV (기본 `data/v5/articles_v5.csv`, `data/v5/law_articles_v5_decree.csv`). 각 매니페스트 필수 |
| `LAWBOT_MAX_SEQ` / `LAWBOT_DECREE_MAX_SEQ` | 아니요 | 법률 / 하위법령의 인코딩 최대 토큰 (v4 2,304 / 3,072, v5 4,352 / 5,120) |
| `LAWBOT_RERANKER_ENABLED` | 아니요 | `1`이면 임베딩 상위 후보를 Qwen3-Reranker-4B로 다시 정렬해 상위 3개를 씀 (기본 `0`: 기존 임베딩 상위 3개). |
| `LAWBOT_RERANK_MODEL` | 아니요 | 리랭커 모델 id (기본 `qwen3-reranker-4b`) |
| `LAWBOT_RERANKER_CANDIDATES` | 아니요 | 리랭커에 넘길 임베딩 후보 수 (기본 `10`, 실험값. 3 이상) |
| `LAWBOT_DECREE_ENABLED` | 아니요 | `1`이면 시행령·시행규칙을 따로 찾아 법률 3개 뒤에 붙임 (기본 `0`: 법률 3개만). 리랭커 필요 |
| `LAWBOT_DECREE_TAU` | 아니요 | 법률 1위와 다른 법의 하위법령이 붙을 리랭커 점수 (기본 `0.9`, val로 고른 값) |
| `LAWBOT_DECREE_K` | 아니요 | 붙일 하위법령 최대 수 (기본 `2`) |
| `LAWBOT_V4_DECREE_ARTICLES` | 아니요 | 하위법령을 켰을 때 쓰는 812조문 CSV (기본 `data/v4/law_articles_v4_decree.csv`). 켜면 `LAWBOT_V4_ARTICLES` 대신 이 파일 |
| `LAWBOT_LLM_BASE_URL` | 예 | 생성 API 주소 `http://127.0.0.1:<생성 서버 포트>/v1`. 모델 서버로 되돌아가지 않음 |
| `LAWBOT_LLM_API_KEY` | 예 | 생성 서버 전용 키(로컬도 16자 이상). 모델 서버 토큰으로 대신하지 않음 |
| `LAWBOT_LLM_ALLOW_EXTERNAL` | 외부 API면 예 | `1`: 질문이 이 맥 밖으로 나가는 것을 허용 (https만) |
| `LAWBOT_PORT` | 아니요 | 웹앱 포트 (1024–65535). 없으면 기존 기본값. 운영·개발을 함께 띄울 때는 서로 다른 값 |
| `LAWBOT_INSTANCE` | 아니요 | 화면·탭 제목에 붙일 이름 (예: `운영`, `개발`, 40자 이하) |
| `LAWBOT_CHAT_CONCURRENCY` | 아니요 | 웹앱이 동시에 처리하는 질문 수 (1–16, 기본 `1`). 처리 슬롯당 질문 한 개가 앞 질문 완료까지 대기하며 대기열이 차면 503으로 응답합니다. |
| `LAWBOT_EMBED_CACHE` | 아니요 | `1`(기본)이면 조문 임베딩을 `cache/embeddings/`(Git 제외)에 저장해 다음 시작 때 다시 쓴다. 시작할 때 조문 1개만 다시 임베딩해 확인하고, 다르면(모델 폴더가 바뀜) 전체를 다시 임베딩. `0`이면 매번 임베딩 |
| `LAWBOT_REQUEST_LOG` | 아니요 | 요청 로그 파일 하나로 정함 (기본: 레포 최상위 `logs/requests-YYYY-MM.jsonl`, 달마다 새 파일). 빈 값이면 기록하지 않음 |
| `LAWBOT_FEEDBACK` | 아니요 | `1`이면 답변마다 👍·깃발 버튼을 달고 누른 답을 저장 (기본 `0`, `0`·`1`만) |
| `LAWBOT_DB` | 관리자 또는 DB 코퍼스 사용 시 | 미리 초기화한 SQLite 경로. 지정하면 피드백과 관리자 로그인 감사 기록을 이 DB에 저장. 레포 안에서는 Git 제외 경로만 허용 |
| `LAWBOT_CORPUS` | 아니요 | `csv`(기본) 또는 `db`. DB 모드에서는 시작 시 프로필의 활성 릴리스에서 조문을 읽음 |
| `LAWBOT_FEEDBACK_DIR` | 아니요 | `LAWBOT_DB`가 비어 있을 때 쓰는 JSONL 폴더. 기본 레포 최상위 `feedback/`. 레포 안에서는 Git 제외 경로만 허용 |
| `LAWBOT_CALENDAR_API_URL` | 캘린더 프록시 사용 시 | 포트가 명시된 루프백 HTTP 주소. 비워 두면 관리자 캘린더 API는 503 `calendar_not_configured` 반환 |
| `LAWBOT_CALENDAR_ADMIN_TOKEN` | 캘린더 검토와 가져오기 실패 조회 사용 시 | 레포 최상위 `.env`와 `services/law-calendar/.env`에 같은 서비스 토큰을 입력 |

모델 서버(`lawbot-model-server`, `lawbot_model_server.check`):

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `LAWBOT_MODEL_SERVER_PORT` | 예 | 모델 서버 포트 (1024–65535, 기본값 없음). 주소는 127.0.0.1 고정 |
| `LAWBOT_MODEL_TOKEN` | 예 | 위와 같은 토큰 (16자 이상) |
| `LAWBOT_SERVE_EMBEDDING` / `LAWBOT_SERVE_RERANK` | 하나 이상 | 제공할 임베딩·리랭크 모델 `id=폴더`를 쉼표로 지정 |

웹앱은 시작할 때 위 설정을 한 번에 모두 검사하고(`lawbot.settings`), 틀린 값이 여러 개면 "설정 오류 N개"로 한꺼번에 알린 뒤 모델 서버에 접속하기 전에 끝납니다(값은 출력하지 않음). 요청 로그 경로 같은 설정은 시작할 때 읽은 값을 끝까지 씁니다.

`LAWBOT_USER`와 `LAWBOT_PW`가 하나라도 없거나 비어 있으면 모델을 불러오기 전에 `RuntimeError`로 실행을 거부합니다. 인증 없이 서버를 띄우는 방법은 없습니다.

## 리랭커 (선택)

켜면 검색 흐름이 `임베딩 상위 10개 → Qwen3-Reranker-4B(튜닝 없음) → 리랭킹 상위 3개 → 답변 모델`이 됩니다. 리랭커는 모델 서버가 시작할 때 올려 상주합니다. 끄면(기본) 웹앱이 리랭커를 부르지 않고 이전과 같은 임베딩 상위 3개를 씁니다. 채점 방식(프롬프트, 최대 2,560토큰, yes/no 로짓)은 v4 리랭커 실험 노트북과 같고(`services/model-server/src/lawbot_model_server/rerank.py`), 작업 설명(instruction)과 정렬(동점은 임베딩 순위)은 웹앱이 정합니다(`services/chatbot/src/lawbot/reranker.py`). 모델 서버는 리랭크 요청이 끝날 때마다 MLX 버퍼 캐시를 비워 캐시로 인한 메모리 증가를 막습니다.

`.env.example`에는 켜져 있습니다(`LAWBOT_RERANKER_ENABLED=1`). 모델 준비는 위 "모델 준비"의 변환 명령을 씁니다.

```zsh
uv run python -m lawbot_model_server.check     # 리랭커 폴더·양자화·yes/no 토큰 검사
uv run pytest -m models services/model-server   # 실제 로딩·채점 스모크 테스트 (임베딩·답변 모델 포함)
LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app    # 한 번만 끄고 실행
```

리랭커 오류(점수가 NaN 등)는 임베딩 순위로 몰래 되돌리지 않고 "조문 순위를 매기지 못했습니다"로 안내합니다. 모델 서버에 리랭커 모델 id가 없으면 웹앱이 시작을 거부합니다. 화면의 조문 카드에는 리랭커 점수와 원래 임베딩 순위가, 측정 줄에는 리랭킹 시간이 나오고, 터미널에는 질문마다 검색·리랭킹·첫 토큰·완료 시간이 한 줄씩 남습니다.

## 시행령·시행규칙 첨부 (선택)

켜면(`LAWBOT_DECREE_ENABLED=1`, `.env.example`은 켜짐) 법률 검색과 별도로 하위법령을 검색해 붙입니다. v5 기본 데이터는 법률 812개와 하위법령 1,047개입니다.

```
법률: 임베딩 상위 10개 → 리랭커 → 상위 3개 (꺼짐과 같은 순위·점수·프롬프트)
하위법령: 임베딩 상위 10개 → 같은 리랭커 → 필터 → 최대 2개 첨부 ([4]·[5])
  필터: 법률 1위와 같은 법의 하위법령은 그대로, 다른 법은 리랭커 점수 ≥ 0.9일 때만 (lawbot.decree)
```

- 통과한 하위법령이 없으면 붙이지 않고, 프롬프트는 꺼짐과 글자 단위로 같습니다. 참고 조문 형식은 법률과 같은 `[n] 법령명 제N조(제목)`입니다(예: `[4] 근로기준법 시행령 제2조(…)`).
- 법률과 하위법령은 별도로 인코딩합니다. 최대 길이는 v5에서 4,352·5,120토큰, v4에서 2,304·3,072토큰입니다.
- 리랭커가 꺼져 있으면 모델 서버에 접속하기 전에 실행을 거부합니다. 하위법령 리랭킹 오류도 법률과 같이 화면에 오류로 표시합니다(되돌리지 않음).
- 화면: 하위법령 카드는 `〔시행령〕`·`〔시행규칙〕` 표시와 함께 법률 카드 뒤에 놓이고, 인용한 조문은 펼치고 나머지는 접습니다. 간결형 근거 줄·경고는 법률과 같은 규칙입니다. 측정 줄에 하위법령 시간과 첨부 수, 요청 로그에 `decree_search_ms`·`decree_rerank_ms`·`decree`·`n_decree`가 남습니다.
- 812조문 매니페스트는 `data/v4/manifest_decree.json`(데이터 `v4-decree`)입니다. 기존 `data/v4/manifest.json`은 415조문 그대로입니다.

Colab 실험(transformers fp16)과의 순위 일치는 리랭커 도입 때 val로 대조했습니다(`results/reranker_verify_val_mlx4bit.csv`).

## 모델 서버 (OpenAI 호환 API)

모델 서버(`services/model-server`, FastAPI)는 임베딩·리랭크만 제공합니다. chat 생성 경로·모델 로더·생성 대기열은 없습니다.

| 엔드포인트 | 형식 | 비고 |
| --- | --- | --- |
| `POST /v1/embeddings` | OpenAI | `encoding_format` `float`·`base64`. 확장: `input_type`(`query`·`document`), `max_seq_length`. 목록 하나를 encode 한 번으로 처리 |
| `POST /v1/rerank` | Cohere/Jina | `results: [{index, relevance_score}]`. 확장: `instruction`(작업 설명) |
| `GET /v1/models` | OpenAI | 확장: `type`(embedding·rerank), `loaded`, `revision`(모델 폴더 지문 `sha256:<16자>`) |
| `GET /health` | | `{ready, commit}` (모델을 불러오는 중에도 응답. `commit`은 모델 서버를 띄운 레포의 git 커밋, 변경이 있으면 `+dirty`) |

- 127.0.0.1에만 열고, 모든 요청(`/health` 포함)에 `LAWBOT_MODEL_TOKEN`이 필요합니다. 모르는 요청 필드는 400으로 거절합니다. 질문·문서 내용은 로그에 남기지 않습니다.
- 임베딩·리랭크 연산은 GPU 작업 스레드 하나에서 순차 실행합니다.

### 단독 생성 서버 (외부 의존 서비스)

- 관리·재시작 위치는 저장소 밖 `~/Services/qwen27b`입니다. FastAPI·uvicorn과 `mlx-lm` 라이브러리로 구현하며 `mlx_lm.server` 래퍼가 아닙니다. `run.sh`로 실행하고 launchd 서비스 이름은 `local.qwen27b`입니다. 이 저장소의 모델 서버 실행·재시작으로 생성 서버를 관리하지 않습니다.
- 바인딩은 `127.0.0.1`로 고정합니다. 모든 HTTP 경로(상태·존재하지 않는 경로 포함)에 전용 Bearer 토큰이 필요하며, 인증 실패는 OpenAI 오류 형식의 401입니다. `HF_HUB_OFFLINE=1`로 실행하고 설정한 단일 모델 id만 제공합니다.
- 웹앱과 monitor는 `LAWBOT_LLM_BASE_URL`·`LAWBOT_LLM_API_KEY`로 접속합니다. 인증된 `GET <LAWBOT_LLM_BASE_URL>/models`는 `object: list`와 `data`의 `id`, `object: model`, `revision`, `max_concurrent`, `mlx_lm_version`을 제공합니다(`type`·`loaded` 없음). `mlx_lm_version`은 생성 서버가 시작할 때 한 번 확인한 설치 배포판 버전이며 요청마다 조회하지 않습니다.
- 모델·엔진 준비 전이나 작업 스레드 종료 후에는 모델 목록·생성 요청에 503 `server_not_ready`를 반환합니다. `GET /health`는 인증 후 `ready`를 보고합니다. 실제 실행은 모델·엔진 초기화 후 리스너를 엽니다.
- `POST <LAWBOT_LLM_BASE_URL>/chat/completions`는 스트리밍·usage·`chat_template_kwargs`·`max_tokens`·`temperature` 등을 지원합니다. 생각은 기본적으로 끄고 출력 맨 앞 생각 블록을 숨깁니다. `enable_thinking=true`이면 생각 텍스트를 그대로 내보내며 별도 `reasoning` 필드는 만들지 않습니다.
- GPU 작업 스레드 하나가 생성·취소·종료를 담당합니다. 대기 요청은 FIFO로 배치에 투입하며, 제한된 대기열이 차면 503 `server_busy`를 반환합니다. 생성 중인 요청은 대기열 수에 포함하지 않습니다.
- 로컬 생성은 요청 로그 `mode=api`, 명시적으로 허용한 외부 https 생성 API는 `mode=external`입니다. 외부 생성 API는 chat 모델 존재 확인과 revision 조회를 생략합니다. 실제 모델 검증 기록에는 로컬 생성 서버 `/v1/models`의 `revision`·`mlx_lm_version`을 적고 lawbot에 설치된 버전으로 대신하지 않습니다.

## 상시 운영과 개발 동시 실행

운영·개발 웹앱은 임베딩·리랭크 모델 서버 하나와 단독 생성 서버 하나를 함께 씁니다. 웹앱마다 모델을 별도로 올리지 않습니다.

```
휴대폰 ──(Tailscale HTTPS, tailscale serve)──▶ 127.0.0.1:<운영 포트>  운영 웹앱 (FastAPI + React 화면, 모델 없음)
맥 브라우저 ─────────────────────────────────▶ 127.0.0.1:<개발 포트>  개발 웹앱 (FastAPI + React 화면, 모델 없음)
                                                   │  둘 다 OpenAI 호환 HTTP + 토큰
                                                   ▼
                                     임베딩·리랭크 모델 서버 + 단독 생성 서버
```

| 프로세스 | 포트 | 실행 위치 | 외부 노출 |
| --- | --- | --- | --- |
| 운영 웹앱 | `<운영 포트>` | 운영 worktree | `tailscale serve`, 선택 시 Cloudflare Tunnel + Access(관리자 경로 제외) |
| 개발 웹앱 | `<개발 포트>` | 개발 레포 | 없음 |
| 모델 서버 | `<모델 서버 포트>` | 운영 worktree | 없음 (127.0.0.1 고정) |
| 단독 생성 서버 | `<생성 서버 포트>` | `~/Services/qwen27b` | 없음 (127.0.0.1 고정) |

포트 번호는 직접 정해 각 `.env`에만 적습니다(레포에 올리지 않음). 운영·개발 포트는 서로 떨어뜨리고, 모델 서버 포트도 따로 둡니다. 모델 서버는 토큰이나 포트가 없으면 실행을 거부합니다.

1. 토큰을 한 번 만들어 운영·개발 `.env`의 `LAWBOT_MODEL_TOKEN`에 같은 값으로 적습니다: `openssl rand -hex 32`
2. 운영 worktree를 만듭니다(배포할 태그 기준, 1회).

   ```zsh
   cd ~/Work/lawbot
   git worktree add --detach ~/Work/lawbot-prod <태그>
   cd ~/Work/lawbot-prod
   ln -s ~/Work/lawbot/models models            # 모델 파일 공유
   cp ~/Work/lawbot/.env .env && chmod 600 .env # .env.example대로 (모델 서버 블록 + 맨 아래 운영 블록)
   uv sync --frozen
   (cd services/chatbot/frontend && npm ci && npm run build)   # 화면 파일
   ```

   운영 `.env`는 `.env.example`에 `LAWBOT_INSTANCE=운영`, `LAWBOT_PORT`를 더하고, 개발 `.env`는 `.env.dev.example`대로 적습니다. 두 웹앱은 같은 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`과 같은 `LAWBOT_LLM_BASE_URL`·`LAWBOT_LLM_API_KEY`를 씁니다. 임베딩·리랭크 id는 모델 서버 목록, chat id는 생성 서버 목록에 있어야 합니다.
3. 직접 실행해 확인합니다.

   ```zsh
   uv run python -m lawbot_model_server.check   # 운영 worktree: 모델 폴더
   uv run lawbot-model-server                   # 포트를 먼저 열고 모델을 올린 뒤 'ready'
   uv run python -m lawbot.check_deploy         # 조문·설정 + 모델 서버 접속·토큰·필요 모델
   uv run lawbot-app                            # 모델 서버가 준비될 때까지 최대 LAWBOT_MODEL_SERVER_WAIT초 대기
   ```

4. launchd로 상시 실행합니다(1회). 템플릿의 자리표시를 채워 복사합니다. 비밀 값과 포트는 plist에 넣지 않습니다(`.env`).

   ```zsh
   mkdir -p ~/Library/Logs/lawbot
   for k in model-server web; do
     sed -e "s|__UV__|$(which uv)|g" -e "s|__PROD_DIR__|$HOME/Work/lawbot-prod|g" -e "s|__LOG_DIR__|$HOME/Library/Logs/lawbot|g" \
       deploy/launchd/com.lawbot.$k.plist.example > ~/Library/LaunchAgents/com.lawbot.$k.plist
     launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.$k.plist   # 등록·시작
   done
   launchctl print gui/$(id -u)/com.lawbot.web | grep state
   tail -f ~/Library/Logs/lawbot/model-server.log
   launchctl kickstart -k gui/$(id -u)/com.lawbot.web          # 재시작 (모델 서버 코드가 바뀌면 com.lawbot.model-server도)
   launchctl bootout gui/$(id -u)/com.lawbot.web               # 해제 (모델 서버도 같은 방식)
   ```

   새 커밋을 운영에 반영할 때 화면 코드(`services/chatbot/frontend`)가 바뀌었으면 웹앱 재시작 전에 운영 worktree에서 `npm ci && npm run build`를 다시 합니다. 빠뜨리면 웹앱이 시작을 거부하므로 `~/Library/Logs/lawbot/web.log`에서 확인합니다.

   종료되면 launchd가 다시 띄웁니다(`KeepAlive`). 모델 로딩에 실패하면 모델 서버는 종료 코드 1로 끝나고 30초 간격(`ThrottleInterval`)으로 다시 시도합니다. 맥이 잠자기에 들어가지 않게 설정합니다.
5. 휴대폰에서는 Tailscale로 운영 웹앱 포트만 tailnet 안에 HTTPS로 노출합니다. 공개 인터넷에 여는 `tailscale funnel`은 쓰지 않습니다. 인자 형식은 Tailscale 버전마다 다를 수 있으니 `tailscale serve --help`로 확인합니다.

   ```zsh
   tailscale serve --bg <운영 포트>
   tailscale serve status
   ```

6. (선택) Tailscale을 설치할 수 없는 브라우저에서는 Cloudflare Tunnel + Access로 **운영 챗봇에만** 접속합니다. 캘린더 공개 화면은 [Cloudflare Pages](#법령-캘린더-정적-사이트-게시)로 제공하며 터널에 연결하지 않습니다. 맥 미니는 Cloudflare로 나가는 연결만 맺습니다. Access는 허용한 계정만 통과시키고 챗봇 앱 로그인은 유지합니다. **관리자 기능은 Tailscale(또는 맥 미니 로컬)에서만 씁니다.** 챗봇은 Cloudflare를 거친 요청(`CF-Connecting-IP` 헤더)의 `/api/admin`을 404로 막고 관리자 사용자명을 없는 사용자명으로 취급합니다(`lawbot_common.edge`). 모델 서버·생성 서버·Source Hub는 터널에 연결하지 않습니다.

   순서가 중요합니다. **Access 앱을 먼저 만든 뒤** DNS를 연결해 Access 없이 열리는 순간이 없게 합니다.

   1. Access(Zero Trust 대시보드). 새 조직은 One-time PIN이 기본으로 켜져 있지 않습니다. 대시보드 메뉴 이름은 바뀔 수 있습니다.
      - **Integrations → Identity providers → Add new identity provider → One-time PIN**을 추가합니다. Cloudflare 계정 로그인(기본 방식)은 회사 PC에 계정 세션을 남기므로 쓰지 않습니다.
      - **Access controls → Applications → Create new application → Self-hosted and private**에서 public hostname에 챗봇만 지정합니다. Login methods는 One-time PIN만 고르고 "Accept all available identity providers"는 끕니다. Session duration은 24시간입니다.
      - 정책은 Action **Allow**, Include **Emails** = 본인 이메일 하나입니다. Everyone·이메일 도메인 전체·Bypass는 쓰지 않습니다.
   2. 터널을 만들고 챗봇 호스트의 DNS만 연결합니다. `tunnel login`은 브라우저에서 도메인을 골라 Authorize하면 `~/.cloudflared/cert.pem`을 만듭니다.

      ```zsh
      brew install cloudflared
      cloudflared tunnel login
      cloudflared tunnel create <터널 이름>
      cloudflared tunnel route dns <터널 이름> <챗봇 호스트>
      ```

   3. 레포 밖 `~/.cloudflared/config.yml`(권한 600)에 다음을 적습니다. 관리자 경로는 터널에서도 404로 보냅니다(앱 차단과 이중). 마지막 규칙이 나머지 호스트를 모두 404로 막습니다. 터널 인증 파일 `<터널 ID>.json`은 권한 400이며 커밋하지 않습니다.

      ```yaml
      tunnel: <터널 ID>
      credentials-file: <홈>/.cloudflared/<터널 ID>.json
      ingress:
        - {hostname: <챗봇 호스트>, path: ^/api/admin, service: http_status:404}
        - {hostname: <챗봇 호스트>, service: <챗봇 루프백 HTTP 주소>}
        - service: http_status:404
      ```

   4. `cloudflared tunnel ingress validate`와 `cloudflared tunnel ingress rule https://<호스트>/<경로>`로 규칙을 확인하고, `cloudflared tunnel run <터널 이름>`으로 직접 띄워 공개 주소가 Access 로그인으로 302 이동하는지 봅니다. 그다음 `cloudflared service install`로 사용자 LaunchAgent(`~/Library/LaunchAgents/com.cloudflare.cloudflared.plist`)를 만듭니다. 이 명령이 만든 plist에는 실행 인자가 `cloudflared` 하나뿐이라 터널이 뜨지 않으므로, 인자를 고치고 다시 등록합니다. 로그는 `~/Library/Logs/com.cloudflare.cloudflared.err.log`입니다.

      ```zsh
      P=~/Library/LaunchAgents/com.cloudflare.cloudflared.plist
      plutil -replace ProgramArguments -json '["/opt/homebrew/bin/cloudflared","tunnel","--config","<홈>/.cloudflared/config.yml","run"]' $P
      launchctl bootout gui/$(id -u) $P; launchctl bootstrap gui/$(id -u) $P
      launchctl print gui/$(id -u)/com.cloudflare.cloudflared | grep state
      ```

   5. 외부 브라우저(회사 PC, 와이파이를 끈 휴대폰)에서 확인합니다: Access OTP → 챗봇 로그인과 질문 1건, `/api/admin` 404, 관리자 계정 로그인 거부. `/admin`처럼 `api`가 없는 경로는 챗봇 화면 앱이 같은 `index.html`을 돌려줄 뿐 관리자 화면이 아닙니다. tailnet에서는 관리자 기능이 그대로 동작해야 합니다. 캘린더의 공개 화면과 예전 API 경로는 Pages 전환 절차에서 따로 확인합니다.

   외부 접속을 끄려면 `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.cloudflare.cloudflared.plist`로 터널 서비스만 내립니다. Tailscale 접속은 영향을 받지 않습니다. 이 서비스도 다른 LaunchAgent처럼 맥 미니에 로그인된 동안 실행됩니다. 호스트명·이메일·터널 ID·포트는 문서와 커밋에 적지 않습니다.

이 구조의 제약:

- 웹앱 쪽 코드(`services/chatbot`: 검색·리랭킹 정렬·하위법령 필터·프롬프트 조립·화면, system 파일, 조문 데이터)는 개발 웹앱만 다시 띄워 시험합니다.
- 모델 서버 코드(`services/model-server`)는 개발용 모델 서버로 시험합니다. 운영 모델 서버를 멈추고(`launchctl bootout …/com.lawbot.model-server`), 개발 레포에서 다른 포트로 띄운 뒤(`LAWBOT_MODEL_SERVER_PORT=<시험 포트> uv run lawbot-model-server`) 개발 웹앱을 그 포트로 연결합니다(`LAWBOT_MODEL_SERVER=http://127.0.0.1:<시험 포트> uv run lawbot-app`). 메모리 때문에 모델 서버는 한 번에 하나만 띄웁니다.
- 단독 생성 서버는 기본 동시 생성 1개로 운영합니다. 개발 요청도 운영과 같은 생성 순서를 기다립니다. 클라이언트 연결 종료가 서버 추론 취소를 보장한다고 가정하지 않습니다.
- 웹앱은 시작할 때 조문 인덱스를 모델 서버의 임베딩으로 만듭니다(812조문 기준 수십 초). 같은 조문·모델이면 다음 시작부터는 `cache/embeddings/`의 캐시를 씁니다(`LAWBOT_EMBED_CACHE`). 운영·개발 웹앱은 각자 레포 폴더에 캐시를 둡니다.
- 같은 브라우저에서 운영·개발 화면에 번갈아 로그인할 때 로그인 쿠키가 겹치면, 개발 화면은 `http://localhost:<개발 포트>`로 접속합니다(호스트가 다르면 쿠키가 분리됨).
- `eval_v4`도 같은 두 서버를 씁니다. 생성 서버가 제공하지 않는 메모리 측정값을 모델 서버 값으로 대신하지 않습니다.

## 운영 버전과 태그

운영 worktree에 반영하고 확인을 마친 커밋에 annotated 태그 `vX.Y.Z`를 붙입니다. 한 번에 여러 PR을 반영하면 해당하는 가장 높은 자리 하나만 올리고 아래 자리는 0으로 돌립니다.

| 자리 | 올리는 경우 |
| --- | --- |
| X | 임베딩 모델 교체. 법령 범위를 넓히며 임베딩을 다시 학습한 경우를 포함하고, 범위가 같아도 임베딩 모델이 바뀌면 올립니다. |
| Y | 임베딩 재학습 없는 법령 범위 확장, 기능 추가, 답변 모델·리랭커·system 프롬프트 변경, DB 스키마 마이그레이션 |
| Z | 버그 수정, 기능 개선, UI 개선 |

06:30 조문 자동 갱신은 코퍼스 릴리스 ID(`v5-live-…`)로 관리하므로 버전을 올리지 않습니다. 문서만 바꾼 PR도 올리지 않습니다.

```zsh
git tag -a vX.Y.Z <운영 커밋> -m "vX.Y.Z: <요약>"
git push origin vX.Y.Z
```

## 법령 데이터 버전 (매니페스트)

조문 CSV 옆의 `manifest.json`(`data/v4/manifest.json`)에 데이터 버전·기준일·수집일·출처·조문 수(법령별·종류별)·sha256과, 임베딩·4B LoRA가 학습한 데이터 버전을 적어 둡니다. 검색 인덱스는 파일이 아니라 웹앱이 시작할 때 만들므로, 대신 이 값으로 데이터와 모델이 맞는지 확인합니다. 조문 데이터는 웹앱에만 있습니다(모델 서버는 모름).

- 웹앱은 시작할 때 CSV를 검사해, 조문 수나 sha256이 다르면 모델 서버에 접속하기 전에 실행을 거부합니다. 매니페스트가 없으면 v5는 시작을 거부하고 v4는 경고합니다.
- `uv run python -m lawbot.check_deploy`가 같은 검사를 OK/FAIL로 보여 줍니다. 시행일이 수집일보다 늦은 조문은 WARN입니다.
- 조문 CSV는 `lawbot-build-articles`가 Legalize KR 원문과 기준일로 만듭니다. 같은 원문 커밋과 기준일이면 바이트 단위로 같은 파일이 나옵니다(규칙: [data-provenance.md](data-provenance.md) 5절).

  ```zsh
  git clone --filter=blob:none --no-checkout https://github.com/legalize-kr/legalize-kr ../legalize-kr
  uv run lawbot-build-articles --repo ../legalize-kr --asof 2026-10-20 --out-dir data/v4 --check   # 레포 CSV와 비교만
  uv run lawbot-build-articles --repo ../legalize-kr --asof 2026-12-08 --out-dir data/v4.1         # 새 기준일로 만들기
  ```

- **CSV 모드 자동 갱신**은 git 밖 `data/live/`에서 수행합니다. 웹앱은 `.env`의 `LAWBOT_V4_ARTICLES=data/live/articles_v4.csv`와 `LAWBOT_V4_DECREE_ARTICLES=data/live/law_articles_v4_decree.csv`를 읽습니다. `law-monitor daily`(06:30)는 `lawbot-articles-update --restart`로 생성·임베딩·검색 확인·아카이브·전환·재시작을 수행합니다. 결과는 monitor 요약의 '점검' 절과 `services/monitor/data/articles/<날짜>.md`에서 확인합니다. [DB 모드 자동 갱신](#db-코퍼스-자동-갱신) · [데이터 출처와 재현](data-provenance.md)

  ```zsh
  uv run lawbot-articles-update --init                      # 처음 한 번: data/v4 → data/live (그다음 .env를 data/live로)
  uv run lawbot-articles-update --repo services/monitor/data/repos/legalize-kr --report-dir /tmp/articles   # 손으로 갱신 (재시작 없이)
  uv run lawbot-articles-update --rollback --restart        # 바로 전 데이터로 되돌리고 재시작
  uv run lawbot-articles-check --repo services/monitor/data/repos/legalize-kr --asof 2026-12-08   # 미리 보기 (아무것도 안 바꿈)
  ```

  재시작은 launchd `com.lawbot.web`의 작업 폴더가 그 명령을 실행한 레포일 때만 합니다 (개발 worktree에서 실행하면 `restart=skipped`). 종료 코드 0 바뀐 것 없음 · 1 갱신함 · 2 실패(지금 데이터 그대로).

- 조문 CSV를 바꾸면 `lawbot-reindex`로 매니페스트를 새 데이터 버전으로 바꾸고 임베딩 캐시를 미리 만든 뒤 웹앱을 재시작합니다. 운영에 넣을 때는 운영 worktree에서 실행합니다(캐시가 그 폴더의 `cache/`에 생김).

  ```zsh
  uv run lawbot-reindex data/v4/articles_v4.csv --dataset-version v4.1 --reference-date 2026-12-08 --check   # 검사만
  uv run lawbot-reindex data/v4/articles_v4.csv --dataset-version v4.1 --reference-date 2026-12-08 --source-ref <legalize-kr HEAD>   # 매니페스트 + 캐시
  uv run python -m lawbot.check_deploy && launchctl kickstart -k gui/$(id -u)/com.lawbot.web
  ```

  검사(열·중복·조문 번호 형식·빈 본문·종류)에 실패하면 아무것도 쓰지 않습니다. 이전 매니페스트는 `<이름>.json.prev`로 남깁니다. 조문 수는 바뀌어도 되고(`check_deploy`는 매니페스트의 법률 조문 수를 기준으로 봄), 임베딩·4B가 학습한 데이터 버전(`trained_on`)과 달라지면 경고합니다. 조문 CSV를 만드는 일은 위 `lawbot-build-articles`가 합니다.
- 한 폴더에 조문 CSV가 여럿이면 `manifest_<이름>.json`을 더 둡니다. `articles_file`이 그 CSV인 매니페스트를 고릅니다(812조문: `data/v4/manifest_decree.json`).

## v5 프로필 전환

개발에서는 [.env.dev.example](../.env.dev.example)의 v5 설정을 적용합니다. 기본 조문은 `data/v5`의 두 CSV와 각 매니페스트입니다. 기존 설정에서 `LAWBOT_EMB_MODEL`은 `lawbot-emb-v5b`로 바꾸고, 이전 `LAWBOT_PRO_SYSTEM`과 인코딩 길이 재지정을 비우면 v5의 기본 간결형과 인코딩 설정을 씁니다.

운영 반영 승인 후 운영 worktree에서 기존 `.env`와 HEAD를 보관합니다. 임베딩·리랭크 모델 서버의 모델 목록에서 `lawbot-emb-v5b`, `qwen3-reranker-4b`의 id와 revision을 확인하고, 별도 생성 서버의 `/v1/models`에서 `qwen3.8-27b-4bit`의 id와 revision을 확인합니다. 조문 전환과 별개로 현재 운영 HEAD에서 반영할 커밋까지의 변경과 필요한 배포 단계를 확인합니다.

```zsh
LAWBOT_PROFILE=v5 uv run lawbot-articles-update --init
```

운영 `.env`에는 `LAWBOT_PROFILE=v5`, `LAWBOT_V5_ARTICLES=data/live/v5/articles_v5.csv`, `LAWBOT_V5_DECREE_ARTICLES=data/live/v5/law_articles_v5_decree.csv`를 설정합니다. 웹앱은 Pro와 기존 리랭커를 선택하고, 간결형은 v5 기본 파일을 사용합니다. `uv run python -m lawbot.check_deploy`에서 매니페스트와 모델 설정을 확인한 뒤 운영 웹앱을 재시작합니다. 첫 시작에서 법률·하위법령 인덱스 캐시가 생성됩니다.

06:30 `law-monitor daily`는 같은 웹앱 프로필을 읽습니다. CSV 모드의 v5에서는 `data/v5/scope_tax.csv`와 기존 생성기로 `data/live/v5/`를 갱신합니다. 다음 실행 뒤 조문 보고서의 데이터 경로, 갱신 상태와 웹앱 응답을 확인합니다. v5 내에서 직전 조문 세트로 되돌리려면 `uv run lawbot-articles-update --rollback --restart`를 실행합니다. v4로 전환을 되돌리는 경우에는 보관한 운영 설정과 이전 운영 커밋을 복구하고 `data/live/`의 v4 조문을 사용합니다.

## 요청 로그와 오류 안내

웹앱은 질문마다 `logs/requests-YYYY-MM.jsonl`(달마다 새 파일, Git 제외)에 한 줄을 남깁니다. `log_summary`는 이 폴더의 월별 파일과 예전 `requests.jsonl`을 함께 읽습니다. 질문마다의 `request_id`는 서버 요청에 `X-Request-Id` 헤더로 함께 갑니다. 단계별 시간(검색·리랭킹·첫 토큰·완료), 토큰 수, 제공되는 경우 `peak_gb`, 상태(`ok`/`error`/`cancelled`), 오류 유형이 들어가고, 질문 원문·답변·조문 내용은 남기지 않습니다. 평가 스크립트도 같은 경로를 쓰므로 `caller`로 구분합니다.

```zsh
uv run python -m lawbot_tools.log_summary                      # 상태별 건수, 단계별 중앙값·p95, 오류 유형별 건수
uv run python -m lawbot_tools.log_summary --caller lawbot-app  # 평가 실행 제외
```

오류 유형은 화면 안내와 같습니다. `model_server`(생성 서버 또는 임베딩·리랭크 서버 연결 실패·토큰 불일치·미준비)는 "모델 서버가 준비되지 않았습니다", `model_load`(모델 폴더·system 파일 오류)는 "모델을 불러오지 못했습니다"로 안내합니다. `model_server_timeout`(질문 처리 중 응답 조각 사이가 `LAWBOT_MODEL_READ_TIMEOUT`을 넘음)은 "모델 서버 응답이 없습니다"로 안내합니다. `model_server_busy`(생성 서버 대기열 가득 참: HTTP `503`, `error.type=server_busy`)는 "모델 서버가 바쁩니다 (대기 중인 질문이 많음). 잠시 뒤 다시 질문하세요."로 안내합니다. 계산 라우터에서도 busy가 발생하면 추가 생성을 하지 않고 같은 오류로 안내하며, 생성 요청은 자동 재시도하지 않습니다. monitor 아침 요약은 실패와 대기 이벤트를 기록하고, ERP 메일 보강은 HTTP 실패를 경고 로그에 남긴 뒤 기존 원문 표시를 유지합니다. 요청 로그의 `mode`는 `api`(로컬 단독 생성 서버) 또는 `external`(외부 생성 API)입니다. `search`·`rerank`·`generation` 오류는 각각 "조문을 검색하지 못했습니다"·"조문 순위를 매기지 못했습니다"·"답변을 생성하지 못했습니다"로 안내하고, 임베딩 순위로 되돌리지 않습니다.

생성 스트림의 종료 `finish_reason`이 없으면 `generation` 오류로 처리하며, 부분 답변을 정상 턴이나 피드백 대상으로 저장하지 않습니다.

## 관리자 계정 (선택)

관리자 계정을 쓰려면 먼저 `lawbot-db init`으로 DB를 준비합니다. `LAWBOT_DB`를 그 파일로 지정하고, 일반 계정과 다른 `LAWBOT_ADMIN_USER`를 설정합니다. 관리자 비밀번호 해시는 터미널에서 만듭니다.

```sh
uv run python -m lawbot.auth hash
```

비밀번호를 두 번 입력한 뒤 출력된 한 줄을 `.env`의 `LAWBOT_ADMIN_PW_HASH`에 작은따옴표로 감싸 붙여 넣습니다. 해시에는 `$`가 포함됩니다. `.env`는 Git에서 제외하고 권한을 600으로 제한합니다. 관리자도 기존 로그인 화면을 사용합니다. 두 관리자 변수를 모두 비우면 기존 일반 로그인만 사용합니다.

관리자 로그인과 관리자 API는 Tailscale·로컬 접속에서만 동작합니다. Cloudflare Tunnel을 거친 요청에서는 관리자 사용자명을 없는 사용자명과 똑같이 처리하고(같은 검증 시간과 잠금, 감사 기록 없음) 관리자 API는 404를 반환합니다. 로그인 실패 횟수는 터널 경유와 Tailscale·로컬을 따로 세므로 외부 시도가 tailnet 로그인을 잠그지 않습니다.

관리자 계정을 사용하면 사용자명별로 15분 안에 로그인에 5번 실패했을 때 15분 동안 추가 로그인이 제한됩니다. 관리자 로그인 결과는 `audit_events`에 기록되며, 비밀번호·해시·쿠키·세션 ID는 기록하지 않습니다.

### 관리자 API 준비

관리자 계정을 설정하면 시작할 때 `LAWBOT_DB`의 QA 이관 상태를 확인합니다. `migrate_csv_to_sqlite qa --set <세트>`로 `v4`, `v5`, `decree_v4`, `reasoning_v5_2`, `oos`, `scope`, `refusal_hard`, `labor_eval`을 각각 이관하고 `verify`로 확인합니다. 빠진 세트가 있으면 웹앱이 시작을 거부합니다.

| 작업 | `/api/admin` 기준 경로 | 확인할 응답 |
| --- | --- | --- |
| 목록·상세 조회 | `GET /feedbacks`, `GET /feedbacks/{id}` | 200, 비로그인 401, 일반 계정 403 |
| 검수·승격 | `POST /feedbacks/{id}/review`, `POST /feedbacks/promote-up`, `POST /feedbacks/{id}/promote-down` | 검수·문제 신고 승격 201, 좋아요 배치 200, 다른 Origin 403 |
| 승격 취소 | `POST /promotions/{id}/revoke` | 200, 이전 QA와 분할 이력 보존 |
| 조문 조회 | `GET /articles?q=…` | 200, 현재 검색기가 읽은 조문 반환 |
| 캘린더 조회 | `GET /calendar/{path}` | 주소 미설정 503, 캘린더 연결 실패 502, 10초 초과 504 |
| 캘린더 보류 검토 | `GET /calendar/law-holds/{kind}/{id}`, `POST /calendar/law-holds/{kind}/{id}` | 토큰 미설정 503, 오래된 스냅숏 409, 검증 실패 422, 저장 200 |
| 캘린더 가져오기 실패 | `GET /calendar/import-failures` | 소스·이벤트별 최근 실패를 페이지 단위로 조회 |
| 상담 기록 목록·상세 | `GET /consults`, `GET /consults/{id}` | 200, 비로그인 401, 일반 계정 403, 터널 경유 404 |
| 상담 기록 삭제 | `POST /consults/delete-preview`, `POST /consults/delete` | 200, 미리 보기 이후 대상 변경 409, 조건 누락 422 |

POST는 JSON과 멱등 키가 필요합니다. 같은 요청을 재시도할 때는 본문 바이트와 키를 유지합니다. Tailscale로 접속하는 경우 브라우저가 보낸 `Origin`이 프록시를 거친 요청의 공개 주소와 일치하는지 확인합니다. 캘린더 프록시를 쓸 때는 `LAWBOT_CALENDAR_API_URL`을 설정하고 관리자 세션으로 조회합니다. 허용 경로와 요청 본문 형식은 [관리자 API 명세](archives/sqlite-admin-pipeline.md#37-관리자-api)에 있습니다.

관리자 캘린더 쓰기를 켤 때는 사용자가 두 환경 파일에 같은 토큰을 직접 입력하고 권한을 `600`으로 설정합니다. 캘린더는 `com.lawbot.calendar`에서 실행 파일을 직접 호출하는 경우에도 서비스 폴더의 `.env`를 읽습니다. 프로세스 환경에 설정한 값이 파일보다 우선합니다. 파일을 바꾼 뒤 캘린더 서버와 웹앱을 다시 시작합니다.

### 상담 기록

`LAWBOT_DB`가 있으면 `LAWBOT_CONSULT_TRACE` 기본값(`1`)으로 상담 원문을 기한 없이 보관합니다(`consult-trace-v1`). DB가 없거나 `0`이면 저장과 입력창 고지를 모두 끕니다. 기존 기록은 설정만으로 삭제되지 않습니다. 질문·최종 글·단계별 결과와 모델 본문을 상담 종료 후 한 트랜잭션에 저장합니다. 시작 시 조문 임베딩·준비 확인·모델 목록 조회와 대기열 포화로 접수 전에 거부한 503 요청은 상담에 포함하지 않습니다. 모델 revision은 시작 시 확인한 값이며, 조회하지 않는 외부 생성 API는 비어 있습니다.

요청·응답 헤더는 보관하지 않습니다. 모델 토큰·API 키·모델 주소·웹앱 포트·세션 쿠키는 저장 직전에 가립니다. JSON의 이스케이프를 해석하고, 웹앱 포트는 주소 문맥에서만 가립니다. gzip은 모든 멤버와 헤더를 검사하며, 확인할 수 없는 압축은 `unreadable_encoding` 표식만 저장합니다. 질문에 포함된 개인 정보는 별도로 가리지 않습니다. 요청 로그에는 `trace_status`(`saved`/`failed`)와 실패 시 `trace_error`(예외 종류)만 추가합니다. 요청 로그를 꺼도 저장 실패는 콘솔에 요청 id와 예외 종류만 경고합니다.

`delivery_status`는 최종 SSE 미생성(`not_prepared`), 준비 후 미전송(`interrupted`), ASGI 전송 완료(`sent`)를 구분합니다. 완료는 브라우저가 읽었다는 뜻이 아닙니다. 모델 호출의 `send_status`는 응답을 받은 `sent`, 연결·연결 할당 단계 실패 `failed_before_response`, 실제 전송량을 모르는 `unknown`입니다. 응답을 받지 못한 호출의 본문은 송신이 확인되지 않은 시도 본문입니다.

관리자 화면의 **상담 기록**에서 기간·사용자 해시·상태로 찾습니다. 목록의 `start`(포함)·`end`(제외)는 시간대 포함 ISO 8601이며, `user_hash`는 로그인 이름의 SHA-256 앞 16자입니다. `status`(`ok`/`error`/`cancelled`), `limit`, `cursor`를 지원합니다. 같은 조건으로 `next_cursor`를 전달하세요.

`GET /consults/{id}`는 메타데이터만 돌려줍니다. 질문과 최종 글은 `/text`, 단계 결과는 `/steps/{seq}`, 모델 원문은 `/calls/{seq}/body?part=request|response|chunks`를 사용합니다(모두 상세 경로 뒤에 추가). 모델 본문은 페이지당 최대 32 KiB, 스트림은 페이지당 최대 64개 조각 또는 조각 일부입니다. 본문은 `{encoding, data}`(`utf-8` 또는 `base64`)로, 스트림은 조각 순번·수신 오프셋(`offset_ns`)·조각 내 바이트 오프셋을 돌려줍니다.

삭제는 사용자 해시 또는 기간 중 하나 이상을 지정하고, 읽기 스냅숏에서 집계한 건수를 확인한 뒤 실행합니다. API는 같은 조건과 미리 보기 응답의 `confirmation`을 요구합니다. 관리자 세션이나 대상이 바뀌면 새 멱등 키로 다시 미리 봐야 합니다. 삭제는 상담·단계·모델 호출과 그 조각에 적용되며, 감사에는 조건과 건수만 남깁니다. 기존 피드백·백업은 별도이며, SQLite WAL에도 체크포인트 전 기록이 남을 수 있습니다.

## 답변 피드백 (선택, `LAWBOT_FEEDBACK=1`)

화면에 확정 표시한 답변에는 👍과 깃발(이유: 검색 오류·답변 오류·불필요한 거절·기타)이 붙습니다. 버튼을 누르면 질문, 참고 조문과 첨부 설정, system, 답변 원문·화면 글, 인용 판정, 측정값, 데이터 버전, 커밋, `request_id`를 저장합니다. 실제로 읽은 조문 CSV와 사용한 system의 SHA-256, 임베딩·리랭킹·생성 모델 ID도 기록합니다. 리랭커가 꺼져 있으면 `rerank_model`은 `null`이고, CSV 코퍼스의 `corpus_release_id`도 `null`입니다. 앱을 다시 시작하기 전의 답이나 다른 로그인 세션의 답에는 저장하지 않습니다.

일반 답변은 생성 중에 초안으로 표시합니다. 인용이 없거나 검색된 조문과 일치하지 않거나 출력 상한에 닿으면 안내와 검색 조문을 보여 줍니다. 계산 카드는 계산 결과를 바로 표시하고, 모델의 결론에서 카드와 다른 숫자나 결과 누락이 확인되면 모델 설명을 보류합니다. 일반 생성 경로의 공개 판정은 요청 로그 `answer_verdict`에 남습니다. `status=ok`는 모델 요청이 끝났음을 뜻합니다.

`LAWBOT_DB`를 지정하면 피드백을 SQLite에만 씁니다. 이 설정을 켜기 전에 `lawbot-db init`으로 DB를 준비하고 `LAWBOT_FEEDBACK_DIR`를 비우세요. 기존 JSONL은 `migrate_csv_to_sqlite feedback`으로 이관할 수 있습니다. `LAWBOT_DB`가 비어 있으면 기존 JSONL(`feedback/feedback_YYYY-MM.jsonl`, 권한 600)을 사용합니다. `/api/feedback`의 `saved`는 2초 안에 같은 선택을 반복했는지 구분합니다. 저장 오류는 경고 로그에 기록합니다.

저장한 줄은 바로 학습에 쓰지 않고 검수를 거칩니다.

```zsh
uv run python -m lawbot_tools.feedback_review --db data/db/lawbot.db --out feedback_review_<날짜>.csv
uv run python -m lawbot_tools.feedback_export feedback_review_<날짜>.csv --out data/feedback/
```

- `feedback_review`는 `--db` 또는 기존 JSONL 폴더·파일을 받습니다. 같은 답의 마지막 선택만 남기고 해제는 뺍니다. 평가 문항과 공백·문장부호를 제외하고 같거나 유사도가 0.9 이상인 질문은 빼고 ID를 출력합니다. `emb`·`sllm`의 합집합을 대조하며, 어느 쪽에서든 test면 test로 분류합니다. 대상은 v4 test 2,075·val 1,554문항, v5 test 3,420·val 2,907문항, v5 추론 검증 30문항, 하위법령 test 1,880·val 625문항과 `oos_eval`, `scope_controls`, `refusal_hard_cases`, `labor_eval_keywords`입니다. 엑셀·Numbers에서 `h_ok`(맞음/틀림/모름), `h_gold`(여러 `법령명 제N조`는 세미콜론으로 구분), `h_scope`(범위 안/범위 밖), `h_note`를 채웁니다. `--keep 이전 CSV`로 채운 칸을 이어받습니다.
- `feedback_export`는 v4·v5 기록을 함께 처리합니다. 정답 조문과 저장된 참고 조문을 각 기록의 프로필에 맞는 법률·하위법령 CSV로 대조합니다. 셸의 `LAWBOT_V4_*`·`LAWBOT_V5_*` 조문 경로 설정을 따르며, `--articles`로 지정한 CSV는 모든 기록에 적용합니다. 지원 범위를 벗어난 프로필은 제외하고 건수를 표시합니다.
- 출력은 `fb_questions.csv`(검수 질문), `fb_emb_pairs.csv`(정답 조문 positive, 검색 오류는 검색 1위를 hard negative), `fb_sft.csv`(맞음 + 👍), `fb_kto.csv`(맞음/틀림 라벨)입니다. 프롬프트는 저장된 참고 조문으로 `llm.build_prompt`를 호출해 재구성합니다. 피드백에서 `random_state=42`로 val을 떼고, 평가는 기존 test로만 합니다.

> 주의: 비밀번호를 소스 코드, 셸 설정 파일, 스크립트, `.env.example` 등 Git에 올라가는 파일에 적지 마세요. 파일에 둘 때는 Git에서 제외된 `.env`(권한 600)에만 두고, 더 안전하게는 실행할 때 입력하거나 macOS 키체인을 쓰세요.

## 데이터 DB

`LAWBOT_DB`는 챗봇 데이터 DB의 선택 설정입니다. 환경 변수와 `lawbot-db` 옵션(`--db`·`--out`·`--from`·`--to`)의 상대 경로는 모두 레포 최상위 기준입니다. 관리 명령에서 `--db`를 생략하면 셸·`.env`의 `LAWBOT_DB`를 읽고, 값이 비어 있으면 `data/db/lawbot.db`를 사용합니다. 웹앱은 지정한 DB의 경로와 마이그레이션 이력을 시작할 때 검사하며, 파일 생성과 마이그레이션은 관리 명령에서 수행합니다.

```bash
uv run lawbot-db init --db data/db/lawbot.db
uv run lawbot-db status --db data/db/lawbot.db
backup="data/db/backups/lawbot-$(date +%Y%m%d-%H%M%S).db"
uv run lawbot-db backup --db data/db/lawbot.db --out "$backup"
uv run lawbot-db migrate --db data/db/lawbot.db
uv run lawbot-db restore --from "$backup" --to data/db/restore/lawbot.db
```

`init`은 새 DB를 만들고 `migrate`는 기존 DB에 미적용 마이그레이션을 적용합니다. `status`는 스키마 버전과 적용된 마이그레이션의 체크섬을 검사합니다. 마이그레이션 전에 온라인 백업을 남기세요. 백업은 커밋된 데이터를 복사한 뒤 무결성을 검사합니다.

상담 기록 배포 시 승인된 운영 커밋에서 웹앱 화면을 다시 빌드하고, 운영 DB 온라인 백업 → `lawbot-db migrate` → `lawbot-db status`로 0003 적용·체크섬 확인 → `com.lawbot.web`만 재시작합니다. 서비스 상태와 실제 질문 없는 로그인·고지·관리자 목록 진입을 확인합니다. 공유 모델 서버를 재시작하지 않습니다. DB 스키마 변경이므로 운영 버전 Y를 올립니다.

DB와 백업에는 질문 원문이 들어갈 수 있습니다. 레포 밖의 절대 경로나 Git에서 제외되는 `data/db/` 아래에 보관하세요. DB 폴더 권한은 700, DB·WAL·SHM·백업 파일 권한은 600입니다. 권한이 넓으면 프로세스에서 경로별 한 번 경고합니다. 초기화·백업·복원 대상은 하드 링크를 지원하는 파일시스템에 두세요. 외장 디스크가 하드 링크를 지원하지 않으면 로컬 파일시스템에 백업한 뒤 복사하세요.

복구할 때는 `restore`로 새 경로에 복원한 뒤 `status`로 확인하세요. 무결성과 스키마를 확인한 복원본은 담당자가 DB를 쓰는 프로세스를 중지한 상태에서 교체합니다. 초기화·백업·복원은 이미 존재하는 대상 경로를 거부합니다.

### DB 코퍼스와 CSV 내보내기

`LAWBOT_DB`에 현재 스키마의 DB를 지정하고 `LAWBOT_CORPUS=db`를 설정합니다. 웹앱은 시작 때 `LAWBOT_PROFILE`의 활성 릴리스를 읽습니다. `LAWBOT_DECREE_ENABLED=1`이면 조합 CSV를, 그 외에는 법률 CSV를 사용합니다. `uv run python -m lawbot.check_deploy`로 릴리스 ID·데이터 버전·해시·조문 수를 확인합니다. 새 활성 릴리스는 웹앱을 다시 시작할 때 적용됩니다. [코퍼스 전환과 복구 순서](archives/sqlite-admin-pipeline.md#73-코퍼스-db-전환-pr-1012-병합-후)

```sh
uv run lawbot-db export-release --db PATH --release ID --out DIR
```

조문 CSV 두 개와 매니페스트 두 개를 원본 이름과 바이트로 내보냅니다. 출력이 모두 같으면 `같음`으로 끝납니다. 기존 파일의 내용이 다른 경우에는 `--overwrite`로 교체를 명시합니다. 교체 결과에는 이전과 새 SHA-256 앞 12자리가 표시됩니다.

### DB 코퍼스 자동 갱신

웹앱과 monitor의 `lawbot-articles-update`가 같은 `.env`에서 `LAWBOT_CORPUS=db`, `LAWBOT_DB`, `LAWBOT_PROFILE`을 읽도록 설정합니다. 운영의 첫 릴리스는 백업과 기존 CSV 이관 순서에 따라 준비합니다. 빈 프로필에서 기본 조문으로 시작할 때는 `uv run lawbot-articles-update --init`이 `data/<프로필>`을 이관하고 활성화합니다. 프로필에 이미 활성 릴리스가 있으면 초기화를 거부합니다. [코퍼스 전환과 복구 순서](archives/sqlite-admin-pipeline.md#73-코퍼스-db-전환-pr-1012-병합-후)

06:30 monitor는 같은 명령을 사용합니다. DB 폴더의 `.articles-update.lock`을 잡고 활성 릴리스의 해시를 확인한 뒤, 새 조문을 검사하고 임베딩·검색 검증을 거쳐 활성화합니다. `--restart`로 재시작한 웹앱이 응답하지 않으면 이전 릴리스를 다시 활성화하고 재시작합니다. `restart=skipped(...)`이면 새 릴리스가 활성으로 남으며 웹앱에는 다음 시작 때 적용됩니다. 갱신 결과의 `archive=`와 보고서의 보관 항목은 이전 릴리스 ID입니다. [자동 갱신 설계](archives/articles-auto-update.md#211-db-모드)

수동 복구는 `uv run lawbot-articles-update --rollback --restart`로 실행합니다. 현재 활성 릴리스를 마지막으로 활성화한 이벤트의 `previous_release_id`를 복구합니다. DB를 CSV 모드로 전환할 때는 [전환과 복구 순서](archives/sqlite-admin-pipeline.md#73-코퍼스-db-전환-pr-1012-병합-후)에 따라 활성 릴리스를 먼저 내보냅니다.

DB 갱신 뒤에도 `data/live/v5`는 이전 CSV를 담고 있습니다. 평가 도구는 CSV를 읽으므로 평가 전에 `lawbot-db export-release`로 평가할 릴리스를 별도 폴더에 내보내고 그 CSV 경로를 지정합니다. 자동 생성 릴리스는 비교할 원본 CSV 폴더를 보관하지 않으므로 저장 바이트의 해시는 `export-release`로 검증합니다. [CSV 내보내기](#db-코퍼스와-csv-내보내기)

`uv run python -m lawbot.check_deploy`와 `lawbot-articles-update` 갱신 보고서에서 계산기별 근거 조문 본문 일치 여부를 확인합니다. 적용 데이터와 본문 해시가 다르거나 조문이 누락된 계산기는 자동으로 꺼집니다.

### CSV·JSONL 이관과 대조

개발 확인은 임시 DB에서 실행합니다. 이관 명령의 `--db`는 필수이며 상대 경로는 레포 최상위 기준입니다. 기존 스키마 버전 1의 DB에는 백업 후 `lawbot-db migrate --db PATH`로 CSV 복원 형식 열을 추가합니다. 이관 전에 `--dry-run`으로 원본을 검사할 수 있습니다. `verify --dry-run`은 경로만 검사합니다. 실제 대조는 `verify`가 DB를 읽기 전용으로 열어 수행합니다.

```bash
migration_dir="$(mktemp -d)"
migration_db="$migration_dir/lawbot.db"
uv run lawbot-db init --db "$migration_db"
uv run python -m lawbot_tools.migrate_csv_to_sqlite corpus --db "$migration_db" --dir data/v5 --profile v5 --release-id v5 --dry-run
uv run python -m lawbot_tools.migrate_csv_to_sqlite corpus --db "$migration_db" --dir data/v5 --profile v5 --release-id v5
uv run python -m lawbot_tools.migrate_csv_to_sqlite corpus --db "$migration_db" --dir data/v4 --profile v4 --release-id v4
for dataset in v4 v5 decree_v4 reasoning_v5_2 oos scope refusal_hard labor_eval; do
  uv run python -m lawbot_tools.migrate_csv_to_sqlite qa --db "$migration_db" --set "$dataset" || break
done
uv run python -m lawbot_tools.migrate_csv_to_sqlite verify --db "$migration_db" --report "$migration_dir/verify.md"
```

`corpus`는 두 CSV와 매니페스트를 대조합니다. `--activate`를 지정하면 검증한 릴리스를 DB의 해당 프로필 활성 대상으로 기록합니다. `qa --file PATH`는 해당 세트의 질문 CSV를 바꿉니다. 분할과 정답 키는 세트에 정의된 원본을 사용합니다. `decree_v4`의 정답 키 문자열은 원본 행에 보존하고, 연결된 정답 키 CSV가 있는 세트에는 외래 키를 채웁니다.

피드백은 `uv run python -m lawbot_tools.migrate_csv_to_sqlite feedback --db "$migration_db" <JSONL 파일 또는 폴더>`로 이관합니다. 깨진 JSON과 유효하지 않은 레코드는 파일명과 줄 번호로 보고합니다. 건너뛴 줄이 있으면 유효한 줄은 저장하고 종료 코드 1을 반환합니다. 같은 ID에 서로 다른 내용이 들어오면 배치를 롤백하고 2를 반환합니다.

`verify`는 DB에서 조문·매니페스트와 QA 원본을 복원하고 저장된 해시를 확인한 뒤 기록된 원본 경로와 대조합니다. 전체 확인에서는 조문 릴리스 2개와 QA 세트 8개가 모두 `OK`이고 종료 코드가 0이어야 합니다. DB가 정상이면 원본 파일의 변경·누락·읽기 실패는 `SOURCE CHANGED`·`SOURCE MISSING`·`SOURCE UNREADABLE`로 표시하고 1을 반환합니다. DB 검증 실패는 `FAIL`과 2로 표시합니다. 보고서는 새 파일로 쓰며 대조 결과와 파일별 해시를 포함합니다. 바이트 복원이 가능한 UTF-8 CSV만 이관하며, 지원하는 줄바꿈은 LF와 CRLF입니다.

## 테스트 실행

기본 테스트는 모델 파일, 네트워크, 실제 비밀번호 없이 돌아갑니다. `uv sync`, 화면 빌드(`npm ci && npm run build`, 브라우저 E2E가 씀), 브라우저 E2E용 Chromium 설치(`uv run playwright install chromium`, 한 번)만 하면 됩니다. 두 서비스의 테스트(`services/*/tests`)를 함께 돌리고, 실제 모델 대신 가짜 모델·가짜 모델 서버를 넣으며, 테스트 중 외부 네트워크 접속은 막습니다. 챗봇 클라이언트와 실제 모델 서버 앱(가짜 모델)이 OpenAI SDK로 통하는지 보는 계약 테스트도 들어 있습니다. `data/`·`results/`는 읽기만 합니다.

```bash
uv run pytest              # 기본 테스트 전체 (모델 불필요)
(set -a; . ./.env; set +a; uv run pytest -m models services/model-server)   # 실제 모델 스모크 (models/ 필요, 기본 실행 제외). 생성 시험은 셸 환경의 LAWBOT_LLM_*·LAWBOT_PRO_MODEL을 씀
uv run pytest -m e2e_stub  # 브라우저 E2E (대역)만: Playwright → 실제 웹앱 → 실제 모델 서버 앱 + 가짜 모델 (약 30초)
uv run ruff check          # 린트 (규칙은 pyproject.toml의 [tool.ruff])
uv run ruff format         # 포맷 (작은따옴표, 120자)
uv run pyright             # 타입 검사 (웹앱 런타임·모델 서버·monitor·공통 패키지, 평가 도구 제외)
(cd services/chatbot/frontend && npm run typecheck && npm test)   # 화면: tsc, Vitest
```

브라우저 E2E는 두 층입니다. `e2e_stub`(기본 실행·CI 포함)는 임베딩·리랭크·답변 생성 세 호출을 모두 가짜 모델로 바꾼 것이라 **실제 모델 연결 확인이 아닙니다**. 설정은 `.env.example`(운영 구성)과 같게 띄우고, 실패한 테스트만 `results/e2e_stub/<시각>-<테스트>/`에 스크린샷·화면 HTML·프로세스 출력·요청 로그를 남깁니다(성공하면 남기지 않음, 진단 후 지움). 실제 공유 임베딩·리랭크 모델 서버와 별도 생성 서버로 하는 `e2e_live`는 승인 후 맥 미니의 개발 worktree에서만 실행합니다([계획](archives/e2e-playwright.md)).

브라우저 E2E를 새로 쓰거나 고칠 때:

- 화면 요소는 역할·라벨·테스트 id로 찾습니다(`e2e_support`: 입력창 `textbox` 이름 '질문', 답변 `answer`, 측정 줄 `lawbot-stats`, 조문 패널 `lawbot-sources`). CSS 클래스에 기대지 않습니다.
- 브라우저는 `services/chatbot/tests/conftest.py`의 모듈 범위 픽스처를 씁니다. `pytest-playwright`는 쓰지 않습니다(세션 내내 이벤트 루프를 열어 두어 뒤 테스트의 `asyncio.run()`을 깨뜨림).

```bash
(set -a; . ./.env; set +a; uv run pytest -m e2e_live services/chatbot/tests/test_e2e_live.py)   # 질문 3개 (순서대로, 재시도 없음)
```

- 시험은 레포 `.env`를 직접 읽지 않고 셸 환경의 `LAWBOT_*`만 씁니다. 위처럼 서브셸에서 개발 `.env`를 내보내 실행합니다.
- 실행 전에 확인합니다. 06:20–06:50(조문 자동 갱신)이 아닌지, 모델 서버가 ready인지, 임베딩·리랭크 모델이 모델 서버에 있는지, chat 모델이 생성 서버 `/v1/models`에 있고 `revision`·`mlx_lm_version`·`max_concurrent`를 읽을 수 있는지 봅니다. 확인 항목 하나라도 실패하면 질문을 보내기 전에 중단합니다(모델 교체로 운영 응답이 느려지지 않게).
- 웹앱은 이 worktree 코드로 빈 포트에 띄우고, 요청 로그·피드백은 임시 폴더로 보냅니다(운영 기록에 섞이지 않음).
- 실행 구성(웹앱·모델 서버 커밋, 모델 `revision`, 생성 서버 mlx-lm 버전, 조문 `dataset_version`·sha256, system sha256, temperature·max_tokens, 검색 설정, 서버가 보고한 동시 생성 상한. 비밀값·모델 서버 주소·포트 제외)을 시작 전·후에 읽어 `results/e2e_live/<시각>.json`에 남깁니다. 둘이 다르면 결과는 무효입니다. 결과는 `docs/evals/`에 옮겨 적고 JSON은 지웁니다.

GitHub Actions(`.github/workflows/ci.yml`)가 `main` 푸시와 모든 pull request에서 macOS(Apple Silicon) 러너로 `uv sync --locked`, 문법 검사(`compileall`), 린트·포맷 검사(`ruff check`·`ruff format --check`), 타입 검사(`pyright`), Chromium 설치, `uv run pytest`를 실행합니다. 비밀정보는 쓰지 않으며, `-m models` 테스트와 평가 스크립트는 CI에서 실행하지 않습니다. 결과는 PR의 체크와 Actions 로그, `pytest-report` 아티팩트(e2e_stub가 실패하면 `results/e2e_stub/` 포함)에서 볼 수 있습니다.

### M5 A/A′ 평가

`run`은 공유 추론 서비스를 사용합니다. 질문 수·운영 영향에 대한 **별도 승인 후** 개발 트리에서 실행합니다. 고정 val 262문항을 추론 → 사실 부족 → 범위 밖 → 일반 표본 순서로 한 번에 하나씩 처리합니다. 명령의 출력 폴더 부모는 미리 준비합니다.

```bash
uv run lawbot-eval-m5 run --condition A --output results/m5-a --stop 06:00:00
uv run lawbot-eval-m5 run --condition Aprime --output results/m5-aprime --stop 06:00:00
# 중단된 같은 조건을 이어서 실행할 때 위 명령에 --resume 추가
uv run lawbot-eval-m5 score results/m5-aprime
uv run lawbot-eval-m5 compare results/m5-a results/m5-aprime
```

- 06:00–07:00 KST에는 시작하지 않습니다. 기본 06:00:00, 지정한 `--stop`, 또는 SIGINT에 도달하면 실행 중인 추론만 기다리고 다음 추론은 보내지 않습니다. 검색·writer 실패는 중단합니다. 판정 호출 오류 뒤 정상 생성된 답은 채점하되 판정 호출 오류가 3회 연속이면 중단합니다.
- 시작 전에 조문 캐시의 모델 revision·문서 해시·입력 한도가 일치해야 합니다. 평가에서는 유효성 확인용 임베딩이나 인덱스 재생성을 하지 않습니다.
- `run.json`에 실행 전후 구성과 무효 구간, `answers.jsonl`에 문항별 모델 입출력·화면 판정과 표시 글, `steps.jsonl`에 각 추론 송신 경계·종료, `requests.jsonl`에 원문 없는 요청 로그를 기록합니다. 오류 진단은 단계·요청 ID·예외 타입·상태 코드만 남깁니다.
- `--resume`은 시작 구성이 같을 때만 허용합니다. 강제 종료 또는 종료 구성 미확인 시 그 실행 구간은 무효이며, 완료 기록이나 송신 경계 기록이 있는 문항은 다시 보내지 않습니다.
- A′의 사실 부족 문항은 미리 작성한 가상 답으로 재개합니다. 나머지 세트에서 되물으면 주 채점에서는 실패이며, 동일 조문으로 만든 별도 writer 답변은 진단에만 씁니다.
- `score`·`compare`는 네트워크 없이 동작합니다. `score`는 무효 기록을 제외하고 그 건수를 별도로 표시합니다. `compare`는 같은 구성으로 고정 세트 전체의 유효 기록을 갖춘 A와 A′만 비교하며, 양쪽 검색에서 모두 놓친 일반 문항은 `total` 짝 비교 분모에서 제외합니다.
- `missing_citation`으로 답변 대신 안내만 표시된 문항은 미응답으로 채점하고, 검색 적중 집계는 유지합니다.
