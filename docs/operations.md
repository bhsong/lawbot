# 설치·실행·운영

README의 "사용법"을 자세히 적은 문서입니다. 구조는 [architecture.md](architecture.md), 평가는 [evals/README.md](evals/README.md)를 보세요.

## 요구 사항

- Apple Silicon 맥(M 시리즈), 메모리 8GB 이상
- [uv](https://docs.astral.sh/uv/) (Python 3.14는 `uv sync` 때 자동 설치)
- 학습된 모델 파일 (아래 "모델 준비" 참고)

## 설치

```bash
git clone https://github.com/bhsong/lawbot.git
cd lawbot
uv sync
```

## 모델 준비

학습된 모델은 용량이 커서 저장소에 포함하지 않습니다. 웹앱 기본 프로필은 **v4**(7개 노동 관련 법률 415조문)이고, 아래 경로에 파일을 두면 됩니다. 이름은 아직 확정 전이므로, 다르면 아래 환경 변수 표의 변수로 지정합니다.

```
data/v4/
├── articles_v4.csv          # 7개 법률 비삭제 415조문 (열: law, article_id, title, deleted, text, 선택 article_no)
├── system_v4.txt            # 4B v4 학습 때 쓴 system 원문 (4항목 형식)
└── system_v4_concise.txt    # Pro 간결형 system (튜닝 전 27B용 초안, 저장소에 포함)
models/v4/
├── emb_v4_fp16/             # v4 튜닝 Qwen3-Embedding-0.6B (sentence-transformers 형식, fp16)
├── reranker_4b_mlx_4bit/    # Qwen3-Reranker-4B (튜닝 없음, MLX 4비트) = 리랭커 (선택)
├── sllm_4b_lora_mlx_4bit/   # Qwen3-4B + v4 LoRA (MLX 4비트) = 웹앱 Lawbot Fast (소형 맥용)
└── qwen3.8_27b_mlx_4bit/    # mlx-community/Qwen3.8-27B-4bit (튜닝 전) = 웹앱 Lawbot Pro
```

`article_id`에 법령명이 들어 있으면(`근로기준법 제26조`) 번호만 담은 `article_no` 열(`제26조`)을 함께 둡니다. 앱은 `article_no`가 있으면 그 값을 조문 번호로 씁니다.

sLLM은 병합한 Hugging Face 모델을 MLX 4비트로 변환해서 만듭니다.

```bash
uv run mlx_lm.convert --hf-path <병합한 4B 폴더> --mlx-path models/v4/sllm_4b_lora_mlx_4bit -q --q-bits 4 --q-group-size 64
```

Pro(27B)는 이미 4비트로 변환된 MLX 모델을 그대로 내려받고, 리랭커는 원본을 4비트로 변환합니다.

```bash
uv run hf download mlx-community/Qwen3.8-27B-4bit --local-dir models/v4/qwen3.8_27b_mlx_4bit
uv run mlx_lm.convert --hf-path Qwen/Qwen3-Reranker-4B --mlx-path models/v4/reranker_4b_mlx_4bit -q --q-bits 4 --q-group-size 64 --dtype float16
```

파일이 설정과 맞는지는 모델을 올리지 않고 확인할 수 있습니다. 문제가 있는 항목은 `FAIL`로 표시됩니다.

```bash
uv run python -m lawbot_model_server.check  # 모델 서버: .env의 LAWBOT_SERVE_* 모델 폴더 (양자화·어댑터·토크나이저·query 프롬프트)
uv run python -m lawbot.check_deploy        # 웹앱: 조문 CSV·system·검색 설정 + 모델 서버 접속·필요 모델 (기본 v4)
uv run python -m lawbot.versions            # 설치된 패키지 버전·맥 사양
```

## 실행 (모델 서버 + 웹앱, 설정 파일 .env)

모델은 모두 모델 서버(`services/model-server`, OpenAI 호환 API)가 올리고, 웹앱(`services/chatbot`)은 조문 검색·프롬프트 조립·화면만 맡습니다. 두 프로세스가 레포 최상위의 같은 `.env`를 읽습니다. `.env.example`(맥 미니 32GB 기본값: v4, 임베딩 0.6B, 후보 10개 → 리랭커 4비트 → 3개, Pro 27B만 사용)을 복사해서 사용자명·비밀번호와 모델 서버의 포트·주소·토큰을 채웁니다.

```zsh
cp .env.example .env
chmod 600 .env            # 나만 읽고 쓰기 (비밀번호·토큰이 들어가므로)
openssl rand -hex 32      # 토큰: LAWBOT_MODEL_TOKEN에 붙여 넣기
open -e .env              # LAWBOT_USER·LAWBOT_PW, LAWBOT_MODEL_SERVER_PORT·LAWBOT_MODEL_SERVER 입력 후 저장
uv run lawbot-model-server   # 터미널 1: 포트를 먼저 열고 모델을 올린 뒤 'ready'
uv run lawbot-app            # 터미널 2: 모델 서버가 준비될 때까지 기다린 뒤 조문 인덱스를 만들고 시작
```

- `.env`는 `.gitignore`에 들어 있어 Git에 올라가지 않습니다. `.env.example`에는 비밀번호를 적지 마세요.
- 셸에서 `export`한 변수가 있으면 그 값이 `.env`보다 우선합니다. 한 번만 다르게 실행할 때는 `LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app`처럼 앞에 붙입니다.
- 다른 파일을 쓰려면 `LAWBOT_ENV_FILE=.env.small uv run lawbot-app`(예: 소형 맥의 Fast 4B 설정), 파일을 읽지 않으려면 `LAWBOT_ENV_FILE=`.
- `.env`에는 `LAWBOT_*`만 적을 수 있습니다. 서버 주소는 바꿀 수 없고, 웹앱 포트는 `LAWBOT_PORT`로 정합니다(아래 참고).
- 값에 공백이나 ` #`이 들어가면 작은따옴표로 감쌉니다: `LAWBOT_PW='a b#c'`.

비밀번호를 파일에 두지 않으려면 `.env`의 `LAWBOT_PW=` 줄을 지우고 실행할 때 입력하거나 macOS 키체인에서 꺼내 씁니다(zsh).

```zsh
read -rs 'LAWBOT_PW?비밀번호: ' && export LAWBOT_PW && uv run lawbot-app
security add-generic-password -a lawbot -s lawbot -w   # 키체인에 저장 (최초 1회)
LAWBOT_PW="$(security find-generic-password -a lawbot -s lawbot -w)" uv run lawbot-app
```

조문 CSV나 system 파일이 없으면 모델 서버에 접속하기 전에 실행을 거부하고 안내합니다.

`LAWBOT_MODELS`로 이 맥에서 쓸 답변 모델을 고릅니다(`.env.example`은 `pro`만). 빠진 모델은 화면에 "이 기기에서 사용 안 함"으로 비활성 표시합니다(고르면 기본 모델로 되돌림). 둘 다 쓰면(`fast,pro`) 화면에서 고르고, 두 모델은 같은 검색 결과(상위 3개 조문)를 참고합니다.

답변 형식에서 Fast(4항목으로 학습)는 항상 4항목으로 생성하고 "간결"은 화면에서 결론·근거만 보여 줍니다. 간결형이 기본인 Pro는 "전체 (4항목)"를 고르면 4항목 system(`data/v4/system_v4.txt`)으로 다시 생성합니다.

| 모델 | 구성 (모델 id · v4 기본 폴더) | 기본 답변 형식 |
| --- | --- | --- |
| Lawbot Fast | Qwen3-4B + LoRA (`lawbot-fast-v4` · `models/v4/sllm_4b_lora_mlx_4bit`) | 전체: `【결론】【근거 조문】【설명】【참고】` 4항목 |
| Lawbot Pro | Qwen3.8-27B 4비트, 튜닝 전 (`qwen3.8-27b-4bit` · `models/v4/qwen3.8_27b_mlx_4bit`), 간결형 system(`data/v4/system_v4_concise.txt`, 초안) | 간결: 결론 + `근거: 실제 인용 조문`. 답변이 더 느림 |

모델을 바꾸면 답변 형식도 그 모델의 기본값으로 바뀌고, 형식은 직접 다시 고를 수 있습니다. 화면 아래 측정 줄에 검색·첫 토큰·첫 글자·생성 속도·완료 시간이 표시됩니다. 모델 서버에 아직 올라 있지 않은 모델을 고르면 "불러오는 중"을 먼저 보여 주고, 불러오는 시간은 첫 토큰 시간에 들어갑니다. 웹앱이 고른 모델 id가 모델 서버에 없으면 시작을 거부합니다.

웹앱은 모델을 폴더가 아니라 모델 id로 고르고(`LAWBOT_*_MODEL`), 모델 폴더와 상주 수는 모델 서버 설정(`LAWBOT_SERVE_*`, `LAWBOT_MAX_RESIDENT`)입니다. 아래 표가 전체 변수입니다(상대 경로는 저장소 최상위 기준).

웹앱은 이 맥에서만 쓰는 로컬 전용입니다. 주소는 `127.0.0.1` 고정이고, 포트는 `.env`의 `LAWBOT_PORT`(없으면 기존 기본값)입니다. 브라우저에서 `http://127.0.0.1:<포트>`로 접속해 로그인합니다. 같은 네트워크의 다른 기기에서는 접속할 수 없으며, Gradio 공개 공유 링크(`share`)도 만들지 않습니다. 주소·공유 설정은 바꿀 수 없고, `GRADIO_SERVER_NAME`·`GRADIO_SERVER_PORT` 같은 Gradio 환경 변수는 무시합니다(`services/chatbot/src/lawbot/security.py`). 상시 운영할 때는 `.env`에 `LAWBOT_PORT`를 따로 정하세요.

웹앱(`lawbot-app`, `check_deploy`, `eval_v4`):

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `LAWBOT_USER` | 예 | 로그인 사용자명 (앞뒤 공백 제거 후 비어 있으면 안 됨) |
| `LAWBOT_PW` | 예 | 로그인 비밀번호 (빈 값·공백만 있는 값은 안 됨) |
| `LAWBOT_MODEL_SERVER` | 예 | 모델 서버 주소 `http://127.0.0.1:<모델 서버 포트>` (이 맥만 허용) |
| `LAWBOT_MODEL_TOKEN` | 예 | 모델 서버 토큰 (16자 이상, `openssl rand -hex 32`). 웹앱과 모델 서버가 같은 값 |
| `LAWBOT_MODEL_SERVER_WAIT` | 아니요 | 시작할 때 모델 서버 준비를 기다리는 최대 초 (기본 `300`) |
| `LAWBOT_PROFILE` | 아니요 | 적지 않음. 예전 `.env`의 `v4`는 그대로 통과하고, 다른 값(`v1` 등)이면 시작을 거부 (v1 구성은 삭제됨, 기록은 [evals/2026-10-18-v1.md](evals/2026-10-18-v1.md)) |
| `LAWBOT_EMB_MODEL` | 아니요 | 검색 임베딩 모델 id (기본 `lawbot-emb-v4`) |
| `LAWBOT_FAST_MODEL` / `LAWBOT_PRO_MODEL` | 아니요 | 답변 모델 id (기본 `lawbot-fast-v4` / `qwen3.8-27b-4bit`). 외부 API면 그쪽 모델 이름 |
| `LAWBOT_MODELS` | 아니요 | 쓸 답변 모델 key를 쉼표로 (`pro`, `fast`, `fast,pro`). 첫 번째가 기본 선택. 비우면 프로필의 Fast·Pro 모두 |
| `LAWBOT_ENV_FILE` | 아니요 | 읽을 설정 파일 (기본 레포 최상위 `.env`, 없으면 안 읽음). 빈 값이면 읽지 않음. 셸에서만 지정 |
| `LAWBOT_PRO_SYSTEM` | 아니요 | Pro 간결형 system 파일. 기본 `data/v4/system_v4_concise.txt` |
| `LAWBOT_V4_ARTICLES` / `LAWBOT_V4_SYSTEM` | 아니요 | v4 조문 CSV / 4B system 파일 (기본 `data/v4/articles_v4.csv`, `data/v4/system_v4.txt`) |
| `LAWBOT_RERANKER_ENABLED` | 아니요 | `1`이면 임베딩 상위 후보를 Qwen3-Reranker-4B로 다시 정렬해 상위 3개를 씀 (기본 `0`: 기존 임베딩 상위 3개). v4 프로필 전용 |
| `LAWBOT_RERANK_MODEL` | 아니요 | 리랭커 모델 id (기본 `qwen3-reranker-4b`) |
| `LAWBOT_RERANKER_CANDIDATES` | 아니요 | 리랭커에 넘길 임베딩 후보 수 (기본 `10`, 실험값. 3 이상) |
| `LAWBOT_DECREE_ENABLED` | 아니요 | `1`이면 시행령·시행규칙을 따로 찾아 법률 3개 뒤에 붙임 (기본 `0`: 법률 3개만). 리랭커 필요, v4 프로필 전용 |
| `LAWBOT_DECREE_TAU` | 아니요 | 법률 1위와 다른 법의 하위법령이 붙을 리랭커 점수 (기본 `0.9`, val로 고른 값) |
| `LAWBOT_DECREE_K` | 아니요 | 붙일 하위법령 최대 수 (기본 `2`) |
| `LAWBOT_DECREE_MODELS` | 아니요 | 하위법령을 붙일 답변 모델 key (기본 `pro`. Fast는 참고 조문 3개로 학습했고 첨부를 평가하지 않음) |
| `LAWBOT_V4_DECREE_ARTICLES` | 아니요 | 하위법령을 켰을 때 쓰는 812조문 CSV (기본 `data/v4/law_articles_v4_decree.csv`). 켜면 `LAWBOT_V4_ARTICLES` 대신 이 파일 |
| `LAWBOT_LLM_BASE_URL` | 아니요 | 생성만 다른 OpenAI 호환 API로 (예: `https://…/v1`). 없으면 모델 서버의 `/v1` |
| `LAWBOT_LLM_API_KEY` | 외부 API면 예 | 그 API의 키 (모델 서버 토큰은 외부로 보내지 않음) |
| `LAWBOT_LLM_ALLOW_EXTERNAL` | 외부 API면 예 | `1`: 질문이 이 맥 밖으로 나가는 것을 허용 (https만) |
| `LAWBOT_PORT` | 아니요 | 웹앱 포트 (1024–65535). 없으면 기존 기본값. 운영·개발을 함께 띄울 때는 서로 다른 값 |
| `LAWBOT_INSTANCE` | 아니요 | 화면·탭 제목에 붙일 이름 (예: `운영`, `개발`, 40자 이하) |
| `LAWBOT_CHAT_CONCURRENCY` | 아니요 | 웹앱이 동시에 처리하는 질문 수 (1–16, 기본 `1`). 모델 서버의 `LAWBOT_MAX_CONCURRENT`보다 크면 넘는 질문은 서버에서 기다림 |
| `LAWBOT_REQUEST_LOG` | 아니요 | 요청 로그 파일 (기본 레포 최상위 `logs/requests.jsonl`). 빈 값이면 기록하지 않음 |
| `LAWBOT_FEEDBACK` | 아니요 | `1`이면 답변마다 👍·깃발 버튼을 달고 누른 답을 저장 (기본 `0`, `0`·`1`만) |
| `LAWBOT_FEEDBACK_DIR` | 아니요 | 피드백 저장 폴더 (기본 레포 최상위 `feedback/`). 레포 안이면 `.gitignore`에 걸리는 경로만 허용. 운영은 레포 밖 절대 경로 권장 |

모델 서버(`lawbot-model-server`, `lawbot_model_server.check`):

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `LAWBOT_MODEL_SERVER_PORT` | 예 | 모델 서버 포트 (1024–65535, 기본값 없음). 주소는 127.0.0.1 고정 |
| `LAWBOT_MODEL_TOKEN` | 예 | 위와 같은 토큰 (16자 이상) |
| `LAWBOT_SERVE_CHAT` / `LAWBOT_SERVE_EMBEDDING` / `LAWBOT_SERVE_RERANK` | 하나 이상 | 제공할 모델 `id=폴더`를 쉼표로 (예: `qwen3.8-27b-4bit=models/v4/qwen3.8_27b_mlx_4bit`) |
| `LAWBOT_SERVE_PRELOAD` | 아니요 | 시작할 때 올릴 chat 모델 id (기본: chat 목록 앞에서부터 상주 수만큼). 임베딩·리랭커는 항상 시작할 때 |
| `LAWBOT_MAX_RESIDENT` | 아니요 | 동시에 올려 둘 chat 모델 수 (기본 `2`, LRU로 교체). 32GB에서 27B를 쓰면 `1` |
| `LAWBOT_MAX_CONCURRENT` | 아니요 | 동시에 생성하는 chat 요청 수 (1–16, 기본 `1`). 생성은 mlx_lm `BatchGenerator`로 하고, 이 값이 배치 크기. `1`이면 이전 순차 생성과 같은 답, `2` 이상은 처리량이 늘지만 요청마다 느려지고 답 표현이 조금 달라질 수 있음([기록](evals/2026-11-01-batch-generation.md)). 넘는 요청은 받은 순서대로 기다림 |

`LAWBOT_USER`와 `LAWBOT_PW`가 하나라도 없거나 비어 있으면 모델을 불러오기 전에 `RuntimeError`로 실행을 거부합니다. 인증 없이 서버를 띄우는 방법은 없습니다.

## 리랭커 (선택, v4)

켜면 검색 흐름이 `임베딩 상위 10개 → Qwen3-Reranker-4B(튜닝 없음) → 리랭킹 상위 3개 → 답변 모델`이 됩니다. Fast·Pro가 같은 리랭킹 결과를 참고하고, 리랭커는 모델 서버가 시작할 때 올려 상주합니다. 끄면(기본) 웹앱이 리랭커를 부르지 않고 이전과 같은 임베딩 상위 3개를 씁니다. 채점 방식(프롬프트, 최대 2,560토큰, yes/no 로짓)은 v4 리랭커 실험 노트북과 같고(`services/model-server/src/lawbot_model_server/rerank.py`), 작업 설명(instruction)과 정렬(동점은 임베딩 순위)은 웹앱이 정합니다(`services/chatbot/src/lawbot/reranker.py`).

`.env.example`에는 켜져 있습니다(`LAWBOT_RERANKER_ENABLED=1`). 모델 준비는 위 "모델 준비"의 변환 명령을 씁니다.

```zsh
uv run python -m lawbot_model_server.check     # 리랭커 폴더·양자화·yes/no 토큰 검사
uv run pytest -m models services/model-server   # 실제 로딩·채점 스모크 테스트 (임베딩·답변 모델 포함)
LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app    # 한 번만 끄고 실행
```

리랭커 오류(점수가 NaN 등)는 임베딩 순위로 몰래 되돌리지 않고 화면에 오류로 표시합니다. 모델 서버에 리랭커 모델 id가 없으면 웹앱이 시작을 거부합니다. 화면의 조문 카드에는 리랭커 점수와 원래 임베딩 순위가, 측정 줄에는 리랭킹 시간이 나오고, 터미널에는 질문마다 검색·리랭킹·첫 토큰·완료 시간이 한 줄씩 남습니다.

## 시행령·시행규칙 첨부 (선택, v4)

켜면(`LAWBOT_DECREE_ENABLED=1`, `.env.example`은 켜짐) 법률 검색은 그대로 두고 하위법령 397개를 따로 찾아 붙입니다.

```
법률 415개: 임베딩 상위 10개 → 리랭커 → 상위 3개 (꺼짐과 같은 순위·점수·프롬프트)
하위법령 397개: 임베딩 상위 10개 → 같은 리랭커 → 필터 → 최대 2개 첨부 ([4]·[5])
  필터: 법률 1위와 같은 법의 하위법령은 그대로, 다른 법은 리랭커 점수 ≥ 0.9일 때만 (lawbot.decree)
```

- 통과한 하위법령이 없으면 붙이지 않고, 프롬프트는 꺼짐과 글자 단위로 같습니다. 참고 조문 형식은 법률과 같은 `[n] 법령명 제N조(제목)`입니다(예: `[4] 근로기준법 시행령 제2조(…)`).
- 첨부는 `LAWBOT_DECREE_MODELS`의 모델만(기본 Pro) 받습니다. Fast는 하위법령을 찾지도 않으므로 지금과 같습니다.
- 법률 415개는 지금과 같은 호출(max_seq 2,304)로 따로 인코딩하고, 하위법령은 3,072토큰으로 따로 인코딩합니다(최장 3,017토큰). 법률 임베딩이 비트 단위로 같은지는 첨부 작업 때 확인했습니다([기록](evals/2026-10-31-decree-attach.md)).
- 리랭커가 꺼져 있으면 모델 서버에 접속하기 전에 실행을 거부합니다. 하위법령 리랭킹 오류도 법률과 같이 화면에 오류로 표시합니다(되돌리지 않음).
- 화면: 하위법령 카드는 `〔시행령〕`·`〔시행규칙〕` 표시와 함께 법률 카드 뒤에 놓이고, 인용한 조문은 펼치고 나머지는 접습니다. 간결형 근거 줄·경고는 법률과 같은 규칙입니다. 측정 줄에 하위법령 시간과 첨부 수, 요청 로그에 `decree_search_ms`·`decree_rerank_ms`·`decree`·`n_decree`가 남습니다.
- 812조문 매니페스트는 `data/v4/manifest_decree.json`(데이터 `v4-decree`)입니다. 기존 `data/v4/manifest.json`은 415조문 그대로입니다.

Colab 실험(transformers fp16)과의 순위 일치는 리랭커 도입 때 val로 대조했습니다(`results/reranker_verify_val_mlx4bit.csv`).

## 모델 서버 (OpenAI 호환 API)

모델 서버(`services/model-server`, FastAPI)는 조문 데이터나 챗봇 설정을 모르는 범용 추론 서버입니다. 챗봇 외의 앱(예: 법령 모니터링의 요약)도 OpenAI SDK로 그대로 부를 수 있습니다.

| 엔드포인트 | 형식 | 비고 |
| --- | --- | --- |
| `POST /v1/chat/completions` | OpenAI | 스트림(SSE)·`stream_options.include_usage`. `temperature`가 없으면 0(탐욕적 생성, OpenAI 기본값 1과 다름). 확장: `chat_template_kwargs.enable_thinking`(기본 끔), 응답의 `timings`(프리필·생성 속도, MLX 최대 메모리) |
| `POST /v1/embeddings` | OpenAI | `encoding_format` `float`·`base64`. 확장: `input_type`(`query`·`document`), `max_seq_length`. 목록 하나를 encode 한 번으로 처리 |
| `POST /v1/rerank` | Cohere/Jina | `results: [{index, relevance_score}]`. 확장: `instruction`(작업 설명) |
| `GET /v1/models` | OpenAI | 확장: `type`(chat·embedding·rerank), `loaded` |
| `GET /health` | | `{ready}` (모델을 불러오는 중에도 응답) |

```python
from openai import OpenAI

client = OpenAI(base_url='http://127.0.0.1:<모델 서버 포트>/v1', api_key='<LAWBOT_MODEL_TOKEN>')
client.chat.completions.create(
    model='qwen3.8-27b-4bit', messages=[{'role': 'user', 'content': '다음 개정 내용을 요약해 줘: …'}]
)
```

- 127.0.0.1에만 열고, 모든 요청(`/health` 포함)에 토큰이 필요합니다. 모르는 필드(`tools`, `n`>1 등)는 400으로 거절합니다. 질문·문서 내용은 로그에 남기지 않습니다(경로·모델·상태·시간만).
- 모델 연산은 GPU 스레드 하나가 맡습니다(MLX·MPS를 여러 스레드에서 동시에 돌리지 않음). chat 생성은 `LAWBOT_MAX_CONCURRENT`개까지 동시에(2 이상이면 한 배치로) 하고, 넘는 요청은 받은 순서대로 기다립니다. embeddings·rerank는 생성 스텝 사이에 실행되어 그동안 생성 중인 스트림이 잠깐 멈춥니다. 스트림 중 연결이 끊기면(정지 버튼) 다음 스텝에서 그 요청을 빼고 자리를 비웁니다.
- 다른 chat 모델 요청이 오면 진행 중인 생성이 끝난 뒤 모델을 바꿉니다(그 뒤에 온 요청도 순서대로 기다림).
- 웹앱은 임베딩·리랭크를 항상 이 서버에서 쓰고, 생성만 `LAWBOT_LLM_BASE_URL`로 다른 OpenAI 호환 API(클라우드 모델 등)로 바꿀 수 있습니다. 이 맥 밖 주소는 질문이 외부로 나가므로 `LAWBOT_LLM_ALLOW_EXTERNAL=1`과 https가 있어야 허용합니다.

## 상시 운영과 개발 동시 실행

맥 미니에서 운영 웹앱을 계속 켜 두고(휴대폰은 Tailscale로 접속) 같은 맥에서 개발 웹앱도 띄울 때도 모델 서버 하나를 두 웹앱이 함께 씁니다. 웹앱마다 27B와 리랭커를 따로 올리면 32GB를 넘기 때문입니다.

```
휴대폰 ──(Tailscale HTTPS, tailscale serve)──▶ 127.0.0.1:<운영 포트>  운영 웹앱 (Gradio, 모델 없음)
맥 브라우저 ─────────────────────────────────▶ 127.0.0.1:<개발 포트>  개발 웹앱 (Gradio, 모델 없음)
                                                   │  둘 다 OpenAI 호환 HTTP + 토큰
                                                   ▼
                                     127.0.0.1:<모델 서버 포트>  모델 서버 (1개)
```

| 프로세스 | 포트 | 실행 위치 | 외부 노출 |
| --- | --- | --- | --- |
| 운영 웹앱 | `<운영 포트>` | 운영 worktree | `tailscale serve`로 이 포트만 |
| 개발 웹앱 | `<개발 포트>` | 개발 레포 | 없음 |
| 모델 서버 | `<모델 서버 포트>` | 운영 worktree | 없음 (127.0.0.1 고정) |

포트 번호는 직접 정해 각 `.env`에만 적습니다(레포에 올리지 않음). 운영·개발 포트는 서로 떨어뜨리고, 모델 서버 포트도 따로 둡니다. 모델 서버는 토큰이나 포트가 없으면 실행을 거부합니다.

1. 토큰을 한 번 만들어 운영·개발 `.env`의 `LAWBOT_MODEL_TOKEN`에 같은 값으로 적습니다: `openssl rand -hex 32`
2. 운영 worktree를 만듭니다(배포할 태그 기준, 1회).

   ```zsh
   cd ~/lawbot
   git worktree add --detach ~/lawbot-prod <태그>
   cd ~/lawbot-prod
   ln -s ~/lawbot/models models                 # 모델 파일 공유
   cp ~/lawbot/.env .env && chmod 600 .env      # .env.example대로 (모델 서버 블록 + 맨 아래 운영 블록)
   uv sync --frozen
   ```

   운영 `.env`는 `.env.example` 그대로(모델 서버 + 웹앱) `LAWBOT_INSTANCE=운영`, `LAWBOT_PORT`를 더하고, 개발 레포 `.env`는 `.env.dev.example`대로 웹앱 설정만 적습니다. 두 `.env`의 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`은 같아야 하고, 웹앱이 고르는 모델 id(`LAWBOT_*_MODEL`)는 운영 `.env`의 `LAWBOT_SERVE_*`에 있어야 합니다.
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
     sed -e "s|__UV__|$(which uv)|g" -e "s|__PROD_DIR__|$HOME/lawbot-prod|g" -e "s|__LOG_DIR__|$HOME/Library/Logs/lawbot|g" \
       deploy/launchd/com.lawbot.$k.plist.example > ~/Library/LaunchAgents/com.lawbot.$k.plist
     launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.$k.plist   # 등록·시작
   done
   launchctl print gui/$(id -u)/com.lawbot.web | grep state
   tail -f ~/Library/Logs/lawbot/model-server.log
   launchctl kickstart -k gui/$(id -u)/com.lawbot.web          # 재시작 (모델 서버 코드가 바뀌면 com.lawbot.model-server도)
   launchctl bootout gui/$(id -u)/com.lawbot.web               # 해제 (모델 서버도 같은 방식)
   ```

   종료되면 launchd가 다시 띄웁니다(`KeepAlive`). 모델 로딩에 실패하면 모델 서버는 종료 코드 1로 끝나고 30초 간격(`ThrottleInterval`)으로 다시 시도합니다. 맥이 잠자기에 들어가지 않게 설정합니다.
5. 휴대폰에서는 Tailscale로 운영 웹앱 포트만 tailnet 안에 HTTPS로 노출합니다. 공개 인터넷에 여는 `tailscale funnel`은 쓰지 않습니다. 인자 형식은 Tailscale 버전마다 다를 수 있으니 `tailscale serve --help`로 확인합니다.

   ```zsh
   tailscale serve --bg <운영 포트>
   tailscale serve status
   ```

이 구조의 제약:

- 웹앱 쪽 코드(`services/chatbot`: 검색·리랭킹 정렬·하위법령 필터·프롬프트 조립·화면, system 파일, 조문 데이터)는 개발 웹앱만 다시 띄워 시험합니다.
- 모델 서버 코드(`services/model-server`)는 개발용 모델 서버로 시험합니다. 운영 모델 서버를 멈추고(`launchctl bootout …/com.lawbot.model-server`), 개발 레포에서 다른 포트로 띄운 뒤(`LAWBOT_MODEL_SERVER_PORT=<시험 포트> uv run lawbot-model-server`) 개발 웹앱을 그 포트로 연결합니다(`LAWBOT_MODEL_SERVER=http://127.0.0.1:<시험 포트> uv run lawbot-app`). 메모리 때문에 모델 서버는 한 번에 하나만 띄웁니다.
- 모델 서버는 기본(`LAWBOT_MAX_CONCURRENT=1`)으로 생성을 한 번에 하나만 합니다. 개발 화면에서 질문하는 동안 휴대폰 질문은 기다립니다. 정지 버튼으로 연결이 끊기면 바로 다음 요청을 받지만, 휴대폰이 연결을 끊지 않고 사라지면 생성이 최대 길이(1024토큰)에서 끝날 때까지 기다립니다.
- 웹앱은 시작할 때 조문 인덱스를 모델 서버의 임베딩으로 만듭니다(812조문 기준 수십 초). 운영·개발 웹앱이 각자 만듭니다.
- 같은 브라우저에서 운영·개발 화면에 번갈아 로그인할 때 로그인 쿠키가 겹치면, 개발 화면은 `http://localhost:<개발 포트>`로 접속합니다(호스트가 다르면 쿠키가 분리됨).
- `eval_v4`도 같은 모델 서버를 씁니다. 결과의 `peak_gb`는 모델 서버 프로세스 값입니다.

## 법령 데이터 버전 (매니페스트)

조문 CSV 옆의 `manifest.json`(`data/v4/manifest.json`)에 데이터 버전·기준일·수집일·출처·조문 수(법령별·종류별)·sha256과, 임베딩·4B LoRA가 학습한 데이터 버전을 적어 둡니다. 검색 인덱스는 파일이 아니라 웹앱이 시작할 때 만들므로, 대신 이 값으로 데이터와 모델이 맞는지 확인합니다. 조문 데이터는 웹앱에만 있습니다(모델 서버는 모름).

- 웹앱은 시작할 때 CSV를 검사해, 조문 수나 sha256이 다르면 모델 서버에 접속하기 전에 실행을 거부합니다. 매니페스트가 없으면 경고만 합니다.
- `uv run python -m lawbot.check_deploy`가 같은 검사를 OK/FAIL로 보여 줍니다. 시행일이 수집일보다 늦은 조문은 WARN입니다.
- 조문 CSV를 바꾸면 매니페스트도 새 데이터 버전으로 함께 바꾸고, 웹앱을 재시작합니다.
- 한 폴더에 조문 CSV가 여럿이면 `manifest_<이름>.json`을 더 둡니다. `articles_file`이 그 CSV인 매니페스트를 고릅니다(812조문: `data/v4/manifest_decree.json`).

## 요청 로그와 오류 안내

웹앱은 질문마다 `logs/requests.jsonl`(Git 제외)에 한 줄을 남깁니다. 단계별 시간(검색·리랭킹·모델 전환·첫 토큰·완료), 토큰 수, `peak_gb`, 상태(`ok`/`error`/`cancelled`), 오류 유형이 들어가고, 질문 원문·답변·조문 내용은 남기지 않습니다. 평가 스크립트도 같은 경로를 쓰므로 `caller`로 구분합니다.

```zsh
uv run python -m lawbot.log_summary                      # 상태별 건수, 단계별 중앙값·p95, 오류 유형별 건수
uv run python -m lawbot.log_summary --caller lawbot-app  # 평가 실행 제외
```

오류 유형은 화면 안내와 같습니다. `model_server`(서버 연결 실패·토큰 불일치·로딩 중)는 "모델 서버가 준비되지 않았습니다", `model_load`(모델 서버의 모델 폴더 없음·형식 오류, 웹앱의 system 파일 없음)는 "모델을 불러오지 못했습니다"로 안내합니다. 요청 로그의 `mode`는 `api`(모델 서버에서 생성) 또는 `external`(외부 생성 API)입니다. 리랭킹·생성 오류는 임베딩 순위로 되돌리지 않고 그대로 오류로 표시합니다.

## 답변 피드백 (선택, `LAWBOT_FEEDBACK=1`)

답변마다 👍과 깃발(이유: 검색 오류·답변 오류·불필요한 거절·기타)이 붙습니다. 마우스는 깃발에 올리면, 휴대폰은 깃발을 탭하면 이유가 보입니다. 누르면 그 답을 만든 입력 전체(질문, 참고 조문(첨부된 시행령·시행규칙 포함)과 첨부 설정, system, 답변 원문·화면 글, 인용 판정, 측정값, 데이터 버전, 커밋, `request_id`)를 `feedback/feedback_YYYY-MM.jsonl`(Git 제외, 권한 600)에 한 줄씩 덧붙입니다. 질문 원문은 이 파일에만, 버튼을 누른 답에만 남습니다. 앱을 다시 시작하기 전의 답이나 화면 글이 기록과 다른 답에는 저장하지 않습니다. Gradio 기본 flagging(`.gradio/flagged`)은 항상 끕니다.

저장한 줄은 바로 학습에 쓰지 않고 검수를 거칩니다.

```zsh
uv run python -m lawbot.feedback_review <운영 피드백 폴더> feedback/          # → feedback_review_<날짜>.csv (+ .jsonl)
uv run python -m lawbot.feedback_export feedback_review_<날짜>.csv --out data/feedback/
```

- `feedback_review`: 같은 답은 마지막 선택만 남기고 해제는 뺍니다. 평가 데이터(v4 test 2,075·val 1,110문항, 하위법령 질문 val 444·test 1,880문항, `oos_eval`, `scope_controls`, `refusal_hard_cases`, `labor_eval_keywords`)와 같은 질문(공백·문장부호 무시)과 유사도 0.9 이상인 질문을 빼고, 뺀 id를 출력합니다. 엑셀·Numbers에서 `h_ok`(맞음/틀림/모름), `h_gold`(`법령명 제N조`, 여러 개면 `;`), `h_scope`(범위 안/범위 밖), `h_note`를 채웁니다. `--keep 이전 CSV`로 채운 칸을 이어받습니다
- `feedback_export`(맥에서 실행): `fb_questions.csv`(dev 질문), `fb_emb_pairs.csv`(정답 조문 positive, 검색 오류는 검색 1위를 hard negative), `fb_sft.csv`(맞음 + 👍), `fb_kto.csv`(맞음/틀림 라벨). 프롬프트는 저장한 조문(첨부된 시행령·시행규칙 포함)으로 `llm.build_prompt_v4`를 다시 불러 만들고, `h_gold`에는 법률과 하위법령 조문(`근로기준법 시행령 제2조`)을 모두 쓸 수 있으며, 피드백 안에서 `random_state=42`로 val을 뗍니다. 평가는 계속 기존 test로만 합니다
- 깃발의 터치 보정(`feedback_ui.py`)은 Gradio 6.30.0 내부 이름에 기댑니다. Gradio를 올리면 맥 브라우저와 휴대폰에서 다시 확인하세요

> 주의: 비밀번호를 소스 코드, 셸 설정 파일, 스크립트, `.env.example` 등 Git에 올라가는 파일에 적지 마세요. 파일에 둘 때는 Git에서 제외된 `.env`(권한 600)에만 두고, 더 안전하게는 실행할 때 입력하거나 macOS 키체인을 쓰세요.

## 테스트 실행

기본 테스트는 모델 파일, 네트워크, 실제 비밀번호 없이 돌아갑니다. `uv sync`만 하면 됩니다. 두 서비스의 테스트(`services/*/tests`)를 함께 돌리고, 실제 모델 대신 가짜 모델·가짜 모델 서버를 넣으며, 테스트 중 외부 네트워크 접속은 막습니다. 챗봇 클라이언트와 실제 모델 서버 앱(가짜 모델)이 OpenAI SDK로 통하는지 보는 계약 테스트도 들어 있습니다. `data/`·`results/`는 읽기만 합니다.

```bash
uv run pytest              # 기본 테스트 전체 (모델 불필요)
uv run pytest -m models services/model-server   # 실제 모델 스모크 테스트 (models/ 필요, 기본 실행에서 제외)
uv run ruff check          # 린트 (규칙은 pyproject.toml의 [tool.ruff])
uv run ruff format         # 포맷 (작은따옴표, 120자)
```

GitHub Actions(`.github/workflows/ci.yml`)가 `main` 푸시와 모든 pull request에서 macOS(Apple Silicon) 러너로 `uv sync --locked`, 문법 검사(`compileall`), 린트·포맷 검사(`ruff check`·`ruff format --check`), `uv run pytest`를 실행합니다. 비밀정보는 쓰지 않으며, `-m models` 테스트와 평가 스크립트는 CI에서 실행하지 않습니다. 결과는 PR의 체크와 Actions 로그, `pytest-report` 아티팩트에서 볼 수 있습니다.
