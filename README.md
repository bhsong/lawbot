# Lawbot: 근로기준법 RAG 챗봇

[한국어](#한국어) | [English](#english)

---

## 한국어

### 소개

Lawbot은 대한민국 노동법 질문에 조문을 근거로 답하는 검색 증강 생성(RAG) 챗봇입니다. 현재 웹앱 기본 구성(v4)은 근로기준법 등 7개 노동 관련 법률의 415개 조문을 대상으로, 튜닝한 임베딩 모델이 후보 10개를 찾고 리랭커가 3개로 좁힌 뒤 답변 모델이 그 조문만 근거로 답합니다. 첫 구성(v1)은 근로기준법 130개 조문과 튜닝한 소형 언어 모델(Qwen3-4B)을 썼습니다. 모든 구성 요소가 Apple Silicon 맥 한 대에서 로컬로 동작합니다.

이 챗봇은 학습·포트폴리오 목적의 프로젝트이며, 답변은 법률 자문이 아닌 참고 정보입니다.

### 주요 특징

- **조문 근거 답변**: 모든 답변이 `【결론】【근거 조문】【설명】【참고】` 네 부분으로 구성되고, 근거 조문 번호를 명시합니다.
- **근거 부족 시 거절**: 제공된 조문으로 답할 수 없으면 추측하지 않고 "확인할 수 없습니다"라고 답하도록 학습했습니다.
- **두 단계 튜닝**:
  - 임베딩: Qwen3-Embedding-0.6B를 질문-조문 대조학습과 hard negative로 튜닝
  - 생성: Qwen3-4B를 LoRA로 튜닝
- **로컬 실행**: 맥에서 MLX 4비트 sLLM과 fp16 임베딩을 함께 구동하며, 8GB 메모리에서도 동작합니다.
- **재현 가능한 평가**:
  - 학습에 쓰지 않은 650문항(130조문 × 5개 질문 유형)으로 검색과 답변을 평가합니다.
  - 답변 채점은 조문 인용, 키워드 그룹, 금지 표현 기준으로 자동화했습니다.

### 구조

v4 (현재 기본, `.env.example`):

```
질문
 └─ 검색: 튜닝 임베딩 (Qwen3-Embedding-0.6B, fp16, MPS) → 415개 조문 중 상위 10개
     └─ 리랭커: Qwen3-Reranker-4B (튜닝 없음, MLX 4비트) → 상위 3개
         ├─ (Pro만) 시행령·시행규칙 397개 중 임베딩 상위 10개 → 같은 리랭커 → 필터 → 최대 2개를 법률 3개 뒤에 첨부
         └─ 답변: Qwen3.8-27B (튜닝 없음, MLX 4비트, 간결형) / 소형 맥은 Qwen3-4B + v4 LoRA (법률 3개만)
             └─ 답변 + 근거 조문 표시 (Gradio)
```

모델(임베딩·리랭커·답변)은 모두 모델 서버(`services/model-server`, OpenAI 호환 API)에서 돌고, 웹앱(`services/chatbot`)은 조문 인덱스·검색 정렬·하위법령 필터·프롬프트 조립·화면을 맡습니다. 생성만 OpenAI 호환 클라우드 모델로 바꿀 수 있습니다.

v1:

```
질문
 └─ 검색: 튜닝 임베딩 (Qwen3-Embedding-0.6B, fp16, MPS)
     └─ 130개 조문 중 상위 3개
         └─ 생성: 튜닝 sLLM (Qwen3-4B + LoRA, MLX 4비트)
             └─ 네 부분 형식 답변 + 근거 조문 표시 (Gradio)
```

| 구성 요소 | 모델·라이브러리 | 비고 |
| --- | --- | --- |
| 임베딩 검색 | Qwen3-Embedding-0.6B, sentence-transformers | 질문→조문 대조학습 + hard negative, 3에폭 |
| 답변 생성 | Qwen3-4B + LoRA (r=16), mlx-lm | 정답 조문 위주 학습 + 거절 샘플 135개 포함 |
| 웹 UI | Gradio | 답변 스트리밍, 근거 조문 패널 |
| 학습 | Google Colab (NVIDIA L4) | TRL SFTTrainer, PEFT |

### 성능

평가마다 결과 한 줄과 기록 파일 링크만 둡니다. 표·설정·명령은 [`docs/evals/`](docs/evals/)에 있습니다.

- **v4 웹앱 test (2026-10-25)**: 리랭킹 Recall@3 0.940, 조문 인용 정확도 0.905, 범위 밖 거절률 0.96, 질문당 15.1초 · MLX 최대 20.4GB → [기록](docs/evals/2026-10-25-v4-webapp-test.md)
- **OpenAI 호환 모델 서버 전환 (val, 2026-11-01)**: 답변 600개·검색 순위 1,110행·하위법령 첨부가 기준과 글자 단위로 같고, 지연 차이는 0.3초 이내 → [기록](docs/evals/2026-11-01-openai-api.md)
- **원격 모드 재확인 (2026-10-29)**: 답변 522개가 로컬 모드와 글자 단위로 같고, 지연 차이는 0.3초 이내 → [기록](docs/evals/2026-10-29-model-server.md)
- **시행령·시행규칙 첨부 실험 (val, 2026-10-29~31)**: 채택 규칙의 정답 하위법령 포함 0.921, 하위법령 질문 정답 인용 0.000 → 0.900, 법률 질문 답변은 그대로 → [기록](docs/evals/2026-10-31-decree-experiment.md)
- **시행령·시행규칙 웹앱 반영 검증 (val, 2026-10-31)**: 법률 검색 지표 기준선과 동일, 정답 하위법령 첨부 0.91·인용 0.885, 다른 법 첨부 문항 0.089, 법률 인용 0.865→0.855, 완료 15.5→19.6초 → [기록](docs/evals/2026-10-31-decree-attach.md)
- **v1 근로기준법 130조문 (2026-10-16~18)**: 검색 Recall@3 0.940, 종합 점수 0.854 (검색 조문) / 0.961 (정답 조문), 범위 밖 거절률 0.707 → [기록](docs/evals/2026-10-18-v1.md)

### 빠른 시작

**요구 사항**

- Apple Silicon 맥(M 시리즈), 메모리 8GB 이상
- [uv](https://docs.astral.sh/uv/) (Python 3.14는 `uv sync` 때 자동 설치)
- 학습된 모델 파일 (아래 "모델 준비" 참고)

**설치**

```bash
git clone https://github.com/bhsong/lawbot.git
cd lawbot
uv sync
```

**모델 준비**

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

이전 v1(근로기준법 130조문) 구성은 `LAWBOT_PROFILE=v1`로 실행하며, 아래 파일을 씁니다. 조문·평가 데이터(`data/`)와 평가 결과(`results/`)는 저장소에 포함되어 있고, v1 평가 결과도 이 파일 기준입니다.

```
models/
├── emb_final_v3_fp16/     # v1 튜닝 임베딩
├── sllm_mlx_4bit/         # v1 튜닝 sLLM = v1 Fast
└── qwen3_14b_mlx_4bit/    # Qwen3-14B 튜닝 전 = v1 Pro (system: data/system_v1_concise.txt)
```

파일이 설정과 맞는지는 모델을 올리지 않고 확인할 수 있습니다. 문제가 있는 항목은 `FAIL`로 표시됩니다.

```bash
uv run python -m lawbot_model_server.check  # 모델 서버: .env의 LAWBOT_SERVE_* 모델 폴더 (양자화·어댑터·토크나이저·query 프롬프트)
uv run python -m lawbot.check_deploy        # 웹앱: 조문 CSV·system·검색 설정 + 모델 서버 접속·필요 모델 (기본 v4)
uv run python -m lawbot.versions            # 설치된 패키지 버전·맥 사양
```

**실행 (모델 서버 + 웹앱, 설정 파일 .env)**

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

v4 파일(조문 CSV, 4B system)이 없으면 모델 서버에 접속하기 전에 실행을 거부하고 안내합니다. v4 준비 전에는 v1으로 실행합니다(모델 서버에도 v1 모델 id가 있어야 함).

```zsh
LAWBOT_PROFILE=v1 LAWBOT_MODELS= LAWBOT_RERANKER_ENABLED=0 LAWBOT_DECREE_ENABLED=0 uv run lawbot-app
```

`LAWBOT_MODELS`로 이 맥에서 쓸 답변 모델을 고릅니다(`.env.example`은 `pro`만). 빠진 모델은 화면에 "이 기기에서 사용 안 함"으로 비활성 표시합니다(고르면 기본 모델로 되돌림). 둘 다 쓰면(`fast,pro`) 화면에서 고르고, 두 모델은 같은 검색 결과(상위 3개 조문)를 참고합니다.

답변 형식에서 Fast(4항목으로 학습)는 항상 4항목으로 생성하고 "간결"은 화면에서 결론·근거만 보여 줍니다. 간결형이 기본인 Pro는 "전체 (4항목)"를 고르면 4항목 system(`data/v4/system_v4.txt`, v1은 학습 system)으로 다시 생성합니다.

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
| `LAWBOT_PROFILE` | 아니요 | `v4`(기본, 7개 법률 415조문) 또는 `v1`(근로기준법 130조문, 이전 구성) |
| `LAWBOT_EMB_MODEL` | 아니요 | 검색 임베딩 모델 id (기본 v4 `lawbot-emb-v4`, v1 `lawbot-emb-v1`) |
| `LAWBOT_FAST_MODEL` / `LAWBOT_PRO_MODEL` | 아니요 | 답변 모델 id (기본 v4 `lawbot-fast-v4` / `qwen3.8-27b-4bit`, v1 `lawbot-fast-v1` / `qwen3-14b-4bit`). 외부 API면 그쪽 모델 이름 |
| `LAWBOT_MODELS` | 아니요 | 쓸 답변 모델 key를 쉼표로 (`pro`, `fast`, `fast,pro`). 첫 번째가 기본 선택. 비우면 프로필의 Fast·Pro 모두 |
| `LAWBOT_ENV_FILE` | 아니요 | 읽을 설정 파일 (기본 레포 최상위 `.env`, 없으면 안 읽음). 빈 값이면 읽지 않음. 셸에서만 지정 |
| `LAWBOT_PRO_SYSTEM` | 아니요 | Pro 간결형 system 파일. 기본 v4 `data/v4/system_v4_concise.txt`, v1 `data/system_v1_concise.txt` |
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

`LAWBOT_USER`와 `LAWBOT_PW`가 하나라도 없거나 비어 있으면 모델을 불러오기 전에 `RuntimeError`로 실행을 거부합니다. 인증 없이 서버를 띄우는 방법은 없습니다.

**리랭커 (선택, v4)**

켜면 검색 흐름이 `임베딩 상위 10개 → Qwen3-Reranker-4B(튜닝 없음) → 리랭킹 상위 3개 → 답변 모델`이 됩니다. Fast·Pro가 같은 리랭킹 결과를 참고하고, 리랭커는 모델 서버가 시작할 때 올려 상주합니다. 끄면(기본) 웹앱이 리랭커를 부르지 않고 이전과 같은 임베딩 상위 3개를 씁니다. 채점 방식(프롬프트, 최대 2,560토큰, yes/no 로짓)은 v4 리랭커 실험 노트북과 같고(`services/model-server/src/lawbot_model_server/rerank.py`), 작업 설명(instruction)과 정렬(동점은 임베딩 순위)은 웹앱이 정합니다(`services/chatbot/src/lawbot/reranker.py`).

`.env.example`에는 켜져 있습니다(`LAWBOT_RERANKER_ENABLED=1`). 모델 준비는 위 "모델 준비"의 변환 명령을 씁니다.

```zsh
uv run python -m lawbot_model_server.check     # 리랭커 폴더·양자화·yes/no 토큰 검사
uv run pytest -m models services/model-server   # 실제 로딩·채점 스모크 테스트 (임베딩·답변 모델 포함)
LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app    # 한 번만 끄고 실행
```

리랭커 오류(점수가 NaN 등)는 임베딩 순위로 몰래 되돌리지 않고 화면에 오류로 표시합니다. 모델 서버에 리랭커 모델 id가 없거나 v1 프로필에서 켜면 웹앱이 시작을 거부합니다. 화면의 조문 카드에는 리랭커 점수와 원래 임베딩 순위가, 측정 줄에는 리랭킹 시간이 나오고, 터미널에는 질문마다 검색·리랭킹·첫 토큰·완료 시간이 한 줄씩 남습니다.

**시행령·시행규칙 첨부 (선택, v4)**

켜면(`LAWBOT_DECREE_ENABLED=1`, `.env.example`은 켜짐) 법률 검색은 그대로 두고 하위법령 397개를 따로 찾아 붙입니다.

```
법률 415개: 임베딩 상위 10개 → 리랭커 → 상위 3개 (꺼짐과 같은 순위·점수·프롬프트)
하위법령 397개: 임베딩 상위 10개 → 같은 리랭커 → 필터 → 최대 2개 첨부 ([4]·[5])
  필터: 법률 1위와 같은 법의 하위법령은 그대로, 다른 법은 리랭커 점수 ≥ 0.9일 때만 (lawbot.decree)
```

- 통과한 하위법령이 없으면 붙이지 않고, 프롬프트는 꺼짐과 글자 단위로 같습니다. 참고 조문 형식은 법률과 같은 `[n] 법령명 제N조(제목)`입니다(예: `[4] 근로기준법 시행령 제2조(…)`).
- 첨부는 `LAWBOT_DECREE_MODELS`의 모델만(기본 Pro) 받습니다. Fast는 하위법령을 찾지도 않으므로 지금과 같습니다.
- 법률 415개는 지금과 같은 호출(max_seq 2,304)로 따로 인코딩하고, 하위법령은 3,072토큰으로 따로 인코딩합니다(최장 3,017토큰). 법률 임베딩이 비트 단위로 같은지는 첨부 작업 때 확인했습니다([기록](docs/evals/2026-10-31-decree-attach.md)).
- 리랭커가 꺼져 있거나 v1 프로필이면 모델 서버에 접속하기 전에 실행을 거부합니다. 하위법령 리랭킹 오류도 법률과 같이 화면에 오류로 표시합니다(되돌리지 않음).
- 화면: 하위법령 카드는 `〔시행령〕`·`〔시행규칙〕` 표시와 함께 법률 카드 뒤에 놓이고, 인용한 조문은 펼치고 나머지는 접습니다. 간결형 근거 줄·경고는 법률과 같은 규칙입니다. 측정 줄에 하위법령 시간과 첨부 수, 요청 로그에 `decree_search_ms`·`decree_rerank_ms`·`decree`·`n_decree`가 남습니다.
- 812조문 매니페스트는 `data/v4/manifest_decree.json`(데이터 `v4-decree`)입니다. 기존 `data/v4/manifest.json`은 415조문 그대로입니다.

Colab 실험(transformers fp16)과의 순위 일치는 리랭커 도입 때 val로 대조했습니다(`results/reranker_verify_val_mlx4bit.csv`).

**모델 서버 (OpenAI 호환 API)**

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
- 모델 연산은 한 번에 하나입니다(MLX·MPS를 여러 스레드에서 동시에 돌리지 않음). 겹친 요청은 기다리고, 스트림 중 연결이 끊기면(정지 버튼) 다음 토큰에서 멈추고 바로 다음 요청을 받습니다.
- 웹앱은 임베딩·리랭크를 항상 이 서버에서 쓰고, 생성만 `LAWBOT_LLM_BASE_URL`로 다른 OpenAI 호환 API(클라우드 모델 등)로 바꿀 수 있습니다. 이 맥 밖 주소는 질문이 외부로 나가므로 `LAWBOT_LLM_ALLOW_EXTERNAL=1`과 https가 있어야 허용합니다.

**상시 운영과 개발 동시 실행**

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
- 모델 서버는 모델 연산을 한 번에 하나만 합니다. 개발 화면에서 질문하는 동안 휴대폰 질문은 기다립니다. 정지 버튼으로 연결이 끊기면 바로 다음 요청을 받지만, 휴대폰이 연결을 끊지 않고 사라지면 생성이 최대 길이(1024토큰)에서 끝날 때까지 기다립니다.
- 웹앱은 시작할 때 조문 인덱스를 모델 서버의 임베딩으로 만듭니다(812조문 기준 수십 초). 운영·개발 웹앱이 각자 만듭니다.
- 같은 브라우저에서 운영·개발 화면에 번갈아 로그인할 때 로그인 쿠키가 겹치면, 개발 화면은 `http://localhost:<개발 포트>`로 접속합니다(호스트가 다르면 쿠키가 분리됨).
- `eval_v4`도 같은 모델 서버를 씁니다. 결과의 `peak_gb`는 모델 서버 프로세스 값입니다.

**법령 데이터 버전 (매니페스트)**

조문 CSV 옆의 `manifest.json`(v4 `data/v4/manifest.json`, v1 `data/manifest.json`)에 데이터 버전·기준일·수집일·출처·조문 수(법령별·종류별)·sha256과, 임베딩·4B LoRA가 학습한 데이터 버전을 적어 둡니다. 검색 인덱스는 파일이 아니라 웹앱이 시작할 때 만들므로, 대신 이 값으로 데이터와 모델이 맞는지 확인합니다. 조문 데이터는 웹앱에만 있습니다(모델 서버는 모름).

- 웹앱은 시작할 때 CSV를 검사해, 조문 수나 sha256이 다르면 모델 서버에 접속하기 전에 실행을 거부합니다. 매니페스트가 없으면 경고만 합니다.
- `uv run python -m lawbot.check_deploy`가 같은 검사를 OK/FAIL로 보여 줍니다. 시행일이 수집일보다 늦은 조문은 WARN입니다.
- 조문 CSV를 바꾸면 매니페스트도 새 데이터 버전으로 함께 바꾸고, 웹앱을 재시작합니다.
- 한 폴더에 조문 CSV가 여럿이면 `manifest_<이름>.json`을 더 둡니다. `articles_file`이 그 CSV인 매니페스트를 고릅니다(812조문: `data/v4/manifest_decree.json`).

**요청 로그와 오류 안내**

웹앱은 질문마다 `logs/requests.jsonl`(Git 제외)에 한 줄을 남깁니다. 단계별 시간(검색·리랭킹·모델 전환·첫 토큰·완료), 토큰 수, `peak_gb`, 상태(`ok`/`error`/`cancelled`), 오류 유형이 들어가고, 질문 원문·답변·조문 내용은 남기지 않습니다. 평가 스크립트도 같은 경로를 쓰므로 `caller`로 구분합니다.

```zsh
uv run python -m lawbot.log_summary                      # 상태별 건수, 단계별 중앙값·p95, 오류 유형별 건수
uv run python -m lawbot.log_summary --caller lawbot-app  # 평가 실행 제외
```

오류 유형은 화면 안내와 같습니다. `model_server`(서버 연결 실패·토큰 불일치·로딩 중)는 "모델 서버가 준비되지 않았습니다", `model_load`(모델 서버의 모델 폴더 없음·형식 오류, 웹앱의 system 파일 없음)는 "모델을 불러오지 못했습니다"로 안내합니다. 요청 로그의 `mode`는 `api`(모델 서버에서 생성) 또는 `external`(외부 생성 API)입니다. 리랭킹·생성 오류는 임베딩 순위로 되돌리지 않고 그대로 오류로 표시합니다.

**답변 피드백 (선택, `LAWBOT_FEEDBACK=1`)**

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

**테스트 실행**

기본 테스트는 모델 파일, 네트워크, 실제 비밀번호 없이 돌아갑니다. `uv sync`만 하면 됩니다. 두 서비스의 테스트(`services/*/tests`)를 함께 돌리고, 실제 모델 대신 가짜 모델·가짜 모델 서버를 넣으며, 테스트 중 외부 네트워크 접속은 막습니다. 챗봇 클라이언트와 실제 모델 서버 앱(가짜 모델)이 OpenAI SDK로 통하는지 보는 계약 테스트도 들어 있습니다. `data/`·`results/`는 읽기만 합니다.

```bash
uv run pytest              # 기본 테스트 전체 (모델 불필요)
uv run pytest -m models services/model-server   # 실제 모델 스모크 테스트 (models/ 필요, 기본 실행에서 제외)
uv run ruff check          # 린트 (규칙은 pyproject.toml의 [tool.ruff])
uv run ruff format         # 포맷 (작은따옴표, 120자)
```

GitHub Actions(`.github/workflows/ci.yml`)가 `main` 푸시와 모든 pull request에서 macOS(Apple Silicon) 러너로 `uv sync --locked`, 문법 검사(`compileall`), 린트·포맷 검사(`ruff check`·`ruff format --check`), `uv run pytest`를 실행합니다. 비밀정보는 쓰지 않으며, `-m models` 테스트와 평가 스크립트는 CI에서 실행하지 않습니다. 결과는 PR의 체크와 Actions 로그, `pytest-report` 아티팩트에서 볼 수 있습니다.

**평가 실행 (v4 웹앱 구성)**

`lawbot.eval_v4`가 `.env` 구성 그대로(v4 검색 415조문 + 리랭커 + 답변 모델) 웹앱과 같은 경로로 평가합니다. 문항은 `data/v4/qa_full_v4.csv`·`split_v4.csv`의 test 2,075문항(근로기준법 A_lsa 650 + 신규 6개 법률 B_newlaw 1,425)입니다. 결과는 `results/v4/<단계>_<tag>.csv`에 문항마다 바로 쓰므로 중간에 멈춰도 같은 명령으로 이어집니다.

```zsh
uv run lawbot-eval-v4                       # 전체: 아래 네 단계를 차례로 실행한 뒤 요약 (오래 걸림)
uv run lawbot-eval-v4 retrieval             # test 2,075 전체: 임베딩 Recall@1·3·10, 리랭킹 Recall@1·3·MRR@3
uv run lawbot-eval-v4 answers               # 고정 시드 표본 200 (A_lsa·B_newlaw 100씩): 인용·거짓 거절, A_lsa는 키워드 채점
uv run lawbot-eval-v4 oos                   # 범위 밖 102 (7개 법률 출처 27문항은 범위 안으로 재분류) + 대조군 20
uv run lawbot-eval-v4 refusal               # 표본 중 100문항에 다른 법률 조문 / 비슷한 조문만 줄 때 거절률
uv run lawbot-eval-v4 summary --tag pro_concise_rr   # 결과 파일로 지표 표만 다시 → results/v4/summary_<tag>.csv
uv run lawbot-eval-v4 --split val            # 하위법령 첨부 구성이면 retrieval·answers·refusal + decree (tag 끝 _dec_val)
uv run lawbot-eval-v4 decree --split val     # 하위법령 정답 val 444 첨부 포함률 + 표본 200 답변 (val만)
```

- tag 기본값은 `<모델>_<형식>_<rr|emb>`입니다(예: `pro_concise_rr`). `--fmt full`이면 4항목 system으로 생성합니다.
- 하위법령 첨부 구성(그 모델이 `LAWBOT_DECREE_MODELS`에 있음)이면 tag에 `_dec`가 붙습니다(예: `pro_concise_rr_dec_val`). retrieval에 법률 질문에 붙은 하위법령(첨부 수, 첨부 중 다른 법 비율), answers에 `decrees` 열이 더해집니다. 열이 다른 기존 결과 파일에는 이어 쓰지 않고 멈춥니다. refusal의 정답 없는 참고 조문은 하위법령을 켜도 법률 415개에서 고릅니다(기준선과 같은 표본).
- `decree` 단계: ① `data/decree_exp/qa_decree_v4.csv`·`split_decree_v4.csv`의 val 444에서 정답 하위법령이 후보 10개·첨부에 들었는지(`dqretrieval_<tag>.csv`) ② `data/v4/decree_ctx_dq_val200.csv`의 id 200개를 웹앱 경로로 답하고 정답 하위법령 인용·거짓 거절·참고 조문 밖 인용을 채점(`dqanswers_<tag>.csv`). summary·compare에 '하위법령 검색'·'하위법령 답변' 절이 생깁니다.
- 인용은 법령명까지 맞아야 정답입니다(`근로기준법 제26조`와 `최저임금법 제26조`를 구분). 법령명 없이 이어 쓴 번호(`근로기준법 제26조, 제27조`)는 앞 법령으로 봅니다. 줄임말(`퇴직급여법`, `고평법` 등)도 인정하고, `근로기준법 시행령 제5조`는 법률 조문과 다른 조문으로 봅니다.
- 인용·거절 판정은 웹앱 화면과 같은 `lawbot.citation` 하나를 씁니다. `citation.check`는 인용마다 `ok`(검색 결과 안) / `outside`(존재하지만 검색 결과 밖) / `wrong_law`(같은 번호, 다른 법령) / `nonexistent`(전체 조문에 없음: 하위법령을 끄면 415조문, 켜면 812조문 기준) / `ambiguous`(법령명 없이 번호가 겹침)를 돌려줍니다.
- `summary`는 저장된 답변 원문으로 채점 열만 다시 계산합니다. 채점 규칙이 같으면 결과 파일은 바뀌지 않습니다.
- 두 실행의 답변 원문이 같은지는 `uv run python -m lawbot.diff_answers <기준.csv> <비교.csv>`로 봅니다(id로 맞춰 글자 단위 비교, 다르면 첫 차이 위치 출력, 모두 같으면 종료 코드 0).
- 2026.4.7 근로기준법 개정으로 임금체불 벌칙이 제109조(3년·3천만원)에서 제107조(5년·5천만원)로 옮겨졌습니다. v4 정답은 개정 후 기준이고, 키워드가 개정 전 형량인 2문항(Q2443·Q2453)만 평가 때 개정 후 형량으로 바꿉니다(`eval_v4.KEYWORD_FIX`, 평가 파일은 그대로).
- 범위 밖 질문 중 남녀고용평등법·최저임금법·퇴직급여법·기간제법·임금채권보장법·파견법이 출처인 27문항은 이제 범위 안이므로 거절하면 오답, 그 법을 인용하면 정답으로 봅니다. 나머지 75문항만 거절률을 잽니다.

**완료된 실험**

완료된 실험 스크립트(v1 평가, 리랭커·하위법령 검증, 벤치마크)는 2026-11-01에 정리했습니다. 결과는 `docs/evals/`와 `results/`에, 코드는 Git 기록에 남아 있습니다. 시행령·시행규칙 첨부 실험의 입력(`data/decree_exp/`, `data/v4/decree_ctx_*.csv`)은 테스트가 운영 필터와 대조하는 데 계속 씁니다. v1 범위 밖·거절 기준선(`oos_v1_4bit.csv`, `refusal_v1_4bit.csv`)도 README 수치와 테스트의 기준선으로 남깁니다.

**개선 전후 비교**

`lawbot.report`가 `results/`의 평가 결과를 한 행으로 모아 `results/metrics.csv`에 추가합니다. 모델 없이 바로 끝납니다. 검색 Recall@3, 답변 종합 점수, 거짓 거절률, 범위 안 대조군 거짓 거절률, 무관한·비슷한 조문 거절률, 범위 밖 거절률·억지 적용률을 함께 기록합니다. 범위 밖 지표는 기준선과 비교할 수 있도록 기존 82문항으로 재고, 추가 20문항은 따로 잽니다.

`--baseline`을 주면 기준 실행과 나란히 보여 주고, 다음처럼 정상 질문에 대한 성능이 떨어지면 부작용 경고를 냅니다. 거절률만 올리고 정상 질문에 답하지 못하게 되는 개선을 걸러 내기 위해서입니다.

| 지표 | 경고 기준 (기준 실행 대비) |
| --- | --- |
| 검색 Recall@3 | 0.005 넘게 하락 |
| 답변 종합 점수 (전체 / 검색 적중) | 0.02 넘게 하락 |
| 조문 인용 정확도 (일반 / 엄격) | 0.02 넘게 하락 |
| 거짓 거절률 (검색 적중 문항) | 0.01 넘게 상승 |
| 범위 안 대조군 거짓 거절률 / 종합 점수 | 0.05(20문항 중 1개) 넘게 나빠짐 |

```bash
uv run python -m lawbot.report --tag v1_4bit_full --retrieval retrieval_emb_final_v3_fp16.csv \
  --scored scored_sllm_mlx_4bit.csv --controls scored_controls_v1_4bit.csv --oos oos_v1_4bit_102.csv \
  --refusal refusal_v1_4bit.csv --hard refusal_hard_v1_4bit.csv --baseline v1_4bit
```

`metrics.csv`에는 기존 결과로 만든 `v1_4bit`(기준선)과 `v1_4bit_hybrid`(실패한 하이브리드 검색 실험) 두 행이 들어 있습니다. 하이브리드 검색은 범위 밖 거절률이 0.671 → 0.683으로 올랐지만 억지 적용률도 0.256 → 0.268로 올라, 거절률만으로는 개선이라고 볼 수 없었습니다.

### 프로젝트 구조

uv workspace 모노레포입니다. 레포 최상위에서 `uv sync`·`uv run …`을 실행하면 모든 서비스가 함께 설치됩니다. 데이터(`data/`), 모델(`models/`), 평가 결과(`results/`)는 레포 최상위에 그대로 둡니다. 서비스는 서로 import하지 않고 HTTP(OpenAI 호환 API)로만 통합니다.

```
pyproject.toml         # workspace 루트: 멤버 목록, pytest·ruff 설정
services/chatbot/      # 웹앱·평가 (패키지 lawbot, 모델 라이브러리 없음), 테스트는 services/chatbot/tests/
services/model-server/ # OpenAI 호환 모델 서버 (패키지 lawbot_model_server, MLX·torch), 테스트는 services/model-server/tests/
services/monitor/      # 법령 변경 수집기 (패키지 law_monitor, git만 사용). 사용법은 services/monitor/README.md
```

```
services/chatbot/src/lawbot/
├── paths.py           # 데이터 경로
├── retriever.py       # 조문 인덱스·코사인 검색 (벡터는 모델 서버 임베딩, 법률·하위법령 인덱스 따로)
├── reranker.py        # 리랭커 클라이언트: 모델 서버 점수 → 정렬 (동점은 임베딩 순위)
├── decree.py          # 시행령·시행규칙 첨부 필터 (same1+other0.9, 웹앱·평가 공통)
├── llm.py             # 프롬프트 구성, openai SDK로 생성 스트림
├── api_client.py      # 모델 서버 클라이언트 (임베딩·리랭크·모델 목록, 프록시 무시)
├── service.py         # UI와 무관한 질의응답 흐름 (모델 서버 접속, 검색 → 답변 스트리밍)
├── app.py             # Gradio 웹앱
├── security.py        # 로컬 전용 실행 설정 (127.0.0.1, 로그인 필수, 공유 링크 없음, 포트·모델 서버·생성 API 주소 검증)
├── prompts.py         # v1 기본 system 문구
├── citation.py        # 인용 파싱·형식 검증·거절 판정 (웹앱 화면과 eval_v4 채점이 같이 씀)
├── manifest.py        # 법령 데이터 매니페스트 검사 (조문 수·sha256·기준일)
├── log_summary.py     # 요청 로그(logs/requests.jsonl) 요약
├── feedback.py        # 답변 피드백 저장 (LAWBOT_FEEDBACK, feedback/*.jsonl)
├── feedback_ui.py     # 피드백 깃발의 터치 기기 보정 CSS·JS
├── feedback_review.py # 피드백 → 검수 CSV (평가 데이터와 겹치는 질문 제외)
├── feedback_export.py # 검수 CSV → dev 질문·임베딩 쌍·SFT·KTO CSV
├── eval_v4.py         # v4 웹앱 구성 평가 (lawbot-eval-v4)
├── diff_answers.py    # 두 평가 결과의 답변 원문 글자 단위 비교
├── score_answers.py   # 키워드 기반 채점기
├── refusal.py         # 거절 판정 (【결론】에 '확인할 수 없'), 범위 밖 답변 판정 (거절·안내·억지적용·미인용)
└── report.py          # 평가 결과를 metrics.csv 한 행으로 모으고 기준 실행과 비교
services/model-server/src/lawbot_model_server/
├── api.py             # OpenAI 호환 API (FastAPI): 인증·크기 제한·로그, 작업 스레드로 한 번에 하나
├── chat.py            # MLX 채팅 모델 (chat template·생각 블록 처리·탐욕적 생성)
├── embeddings.py      # sentence-transformers 임베딩 (MPS, fp16)
├── rerank.py          # Qwen3-Reranker 채점 (실험 노트북과 같은 입력)
├── registry.py        # 제공 모델 목록, chat 모델 LRU
├── config.py          # 포트·토큰·LAWBOT_SERVE_* 설정
├── check.py           # 모델 폴더 점검 (양자화·어댑터·토크나이저·query 프롬프트)
└── server.py          # uvicorn 실행 (lawbot-model-server)
deploy/launchd/        # 상시 운영용 launchd 템플릿 (모델 서버·웹앱)
```

### 데이터와 평가 방법

- **조문**: 근로기준법 현행 130개 조문. 삭제 조문 2개는 제외했고, 기준일은 2026-10-01입니다.
- **v4 조문**: 근로기준법 등 7개 법률의 비삭제 415개 조문(`data/v4/articles_v4.csv`). [Legalize KR](https://github.com/legalize-kr/legalize-kr)에서 2026-10-20에 수집했고, 버전·기준일·sha256은 `data/v4/manifest.json`에 있습니다. 시행령·시행규칙 397개를 더한 812조문(`data/v4/law_articles_v4_decree.csv`, 열 `kind`·`parent_law`·`parent_ids`·`child_ids`)은 `LAWBOT_DECREE_ENABLED=1`일 때 웹앱이 씁니다(매니페스트 `data/v4/manifest_decree.json`, 같은 2026-10-20 수집).
  - 21개 법령 파일 모두 2026-10-20에 시행 중인 조문입니다(Legalize KR 이력과 조문 단위로 대조). 그 뒤 시행되는 개정(2026-11-27 남녀고용평등법, 2026-12-08 근로기준법 등)은 데이터에 없습니다. 법령별 출처 버전, 이후 개정 목록, 갱신 방법은 [docs/data-provenance.md](docs/data-provenance.md)에 있습니다.
- **하위법령 질문**: 시행령·시행규칙이 정답인 4,096문항(`data/decree_exp/qa_decree_v4.csv`, 분할 `split_decree_v4.csv`: 임베딩 기준 train 1,772 / val 444 / test 1,880, 시드 42). `data/decree_exp/`에는 Colab 실험의 후보·리랭커 점수(fp16)와 맥 MLX 점수, 규칙 비교 결과가 함께 있습니다.
- **질문**: 총 2,602개(130조문 × 질문 유형 5종 × 약 4개)입니다.
  - 질문 유형: 구어체, 사용자(사업주) 관점, 간접 질문, 키워드, 상황 설명
  - 테스트: 650개(조문 × 유형마다 1개)
  - 학습·검증: 나머지 1,952개
  - 모델 선택과 하이퍼파라미터 결정은 검증 셋으로만 했습니다.
- **답변 채점**: 문항마다 필수 키워드 그룹, 금지 표현, 정답 조문 번호를 지정해 자동 채점합니다. 제43조와 제43조의2처럼 비슷한 번호는 구분합니다. 거절 답변은 `해당 없음 (제공된 조문: …)`에 정답 번호가 있어도 인용으로 보지 않습니다. `cite_strict`는 【근거 조문】에 정답이 있고 헷갈리는 조문이나 번호가 비슷한 조문을 함께 인용하지 않았는지를 따로 기록합니다(종합 점수에는 반영하지 않음). 다른 법의 같은 번호(예: 근로자퇴직급여 보장법 제34조)는 구분하지 못합니다. 평가 파일의 모범 답변은 650문항 모두 1.0점입니다.
- **범위 밖 질문**: 근로기준법으로 답할 수 없는 질문 102개로 거절률을 따로 잽니다(`data/oos_eval.csv`).
  - 2026-10-19에 근로기준법과 개념이 겹치는 다른 노동법 질문 20개(OOS083–102)를 추가했습니다. 근거 조문(`note`)은 [Legalize KR](https://github.com/legalize-kr/legalize-kr) 법령 원문으로 확인했습니다. v1 성능 수치([기록](docs/evals/2026-10-18-v1.md))는 추가 전 82문항 기준입니다.
  - 답변은 거절, 안내(`refer_lsa`에 적힌, 다른 법에 맡기는 조문만 인용), 억지적용(그 밖의 근로기준법 조문으로 답함), 미인용(거절 문구도 인용도 없음)으로 판정합니다. `near_lsa`는 README 수치의 기준이고, `near_lsa_v2`는 누락을 보완한 표시입니다.
- **범위 안 대조군**: 다른 노동법 질문과 닮았지만 근로기준법으로 답해야 하는 테스트 문항 20개입니다(`data/scope_controls.csv`, `twin_oos`에 짝 문항). 범위 밖 거절을 늘리는 개선이 이 문항들까지 거절하게 만들지 않는지 확인합니다.
- **근거 없는 주장 사례**: `refusal_v1_4bit.csv`에서 거절하지 않은 86건을 억지적용 77, 지어냄 4, 근거밖_정답 2, 결론타당 3으로 분류했습니다(`data/refusal_hard_cases.csv`). 억지적용·지어냄 81건은 같은 참고 조문으로 다시 평가했습니다(`results/refusal_hard_v1_4bit.csv`).
- **평가 데이터 사용 제한**: `data/labor_eval_keywords.csv`, `data/oos_eval.csv`, `data/scope_controls.csv`, `data/refusal_hard_cases.csv`와 `data/v4/split_v4.csv`·`data/decree_exp/split_decree_v4.csv`의 test 문항은 평가 전용입니다. 모델 학습이나 설정 선택에 쓰지 마세요.
- **`results/` 안내**: 채점 전 모델 원본 출력과 문항별 점수이며, 점수가 낮은 답변은 법적으로 틀린 내용을 포함합니다.

### 실패한 실험

| 실험 | 결과 | 판단 |
| --- | --- | --- |
| 리랭커 (Qwen3-Reranker-0.6B)를 튜닝 임베딩 뒤에 추가 | Recall이 튜닝 임베딩 단독보다 낮아짐 | 제외. 더 큰 리랭커는 로컬 메모리로 어려움 |
| sLLM v2: 검색 상위 조문으로 참고 조문 구성 + 정답 위치 무작위 + 거절 샘플 90개 추가 | 정답 2·3번째 인용 정확도 0.68·0.50 → 0.79·0.73, 비슷한 조문 거절 0.20 → 0.50. 하지만 거짓 거절 0.2% → 4.3%, 검색 조문 종합 점수 0.854 → 0.818 | 폐기, v1 유지 |
| 하이브리드 검색: 튜닝 임베딩 85% + BM25(문자 2~4그램) 15%, 가중치는 검증 셋으로 선택 | 검증 셋 MRR 0.836 → 0.856. 테스트 650은 Recall@3 0.940 → 0.943, MRR 0.868 → 0.880. 하지만 답변 종합 점수(100문항) 0.812 → 0.802, 범위 밖 거절률 0.671 → 0.683으로 차이 없음 | 제외. 답변 개선 근거 없이 의존성과 코드만 늘어남. BM25·TF-IDF 단독은 Recall@3 약 0.67 |

### 한계와 향후 계획

- **검색 실패**: 상위 3개에 정답 조문이 없는 경우가 약 6%이고, 이 경우 답변 품질이 크게 떨어집니다.
- **위치 편향**: 정답 조문이 두 번째·세 번째로 주어질 때 인용 정확도가 0.68·0.50으로 낮습니다. 첫 번째일 때는 0.975입니다.
- **범위**: 근로기준법만 다룹니다. 범위 밖 질문 거절률은 0.707(bf16)이고, 특히 근로기준법에 비슷한 조문이 있는 다른 노동법 질문(예: 퇴직금 계산, 산재 휴업급여)에 비슷한 조문으로 답하는 경우가 남아 있습니다.
- **시행령·시행규칙**: val로 정한 첨부 방식과 필터를 웹앱에 넣었습니다(Pro만). Fast(4B LoRA)는 첨부를 평가하지 않아 법률 3개만 받습니다. 웹앱 경로 val 수치는 맥 미니 수동 검증 뒤 적습니다. 별표·서식·고시(최저임금액 등)는 들어 있지 않습니다.
- **향후 계획**:
  - Fast 모델의 시행령·시행규칙 첨부 평가
  - LLM 기반 평가
  - FastAPI 백엔드 분리
  - 계산기 도구 호출
  - Docker
  - vLLM 서빙
  - 클라우드 배포
  - 법 개정 재인덱싱

### 고지

- 베이스 모델 Qwen3-4B와 Qwen3-Embedding-0.6B는 각 모델의 라이선스(Apache License 2.0)를 따릅니다.
- 법령 원문은 저작권법 제7조에 따라 보호받지 않는 저작물입니다.
- 이 챗봇의 답변은 법률 자문이 아닙니다. 구체적인 사안은 고용노동부 고객상담센터(국번 없이 1350)나 공인노무사와 상담하시기 바랍니다.

---

## English

### Overview

Lawbot is a retrieval-augmented generation (RAG) chatbot that answers questions about Korean labor law, citing the articles it relies on. The current default setup (v4) covers 415 articles from seven labor-related laws: a fine-tuned embedding model retrieves 10 candidates, a reranker narrows them to 3, and the answer model writes an answer grounded only in those articles. The first setup (v1) used the 130 articles of the Labor Standards Act and a fine-tuned small language model (Qwen3-4B). The whole stack runs locally on a single Apple Silicon Mac.

This is a learning and portfolio project. Its answers are informational and do not constitute legal advice.

### Features

- **Article-grounded answers**: Every answer has four parts (Conclusion, Cited articles, Explanation, Notes) and names the articles it relies on.
- **Refusal when evidence is missing**: The model is trained to say it cannot determine the answer, rather than guess, when the provided articles do not support one.
- **Two-stage fine-tuning**:
  - Retrieval: Qwen3-Embedding-0.6B trained with question-to-article contrastive learning and hard negatives
  - Generation: Qwen3-4B trained with LoRA
- **Local inference**: A 4-bit MLX language model and an fp16 embedding model run together on a Mac, including 8 GB machines.
- **Reproducible evaluation**:
  - Retrieval and answers are evaluated on 650 held-out questions (130 articles × 5 question types).
  - Answer scoring is automated, based on article citation, required keyword groups, and forbidden phrases.

### Architecture

v4 (current default, `.env.example`):

```
Question
 └─ Retrieval: fine-tuned embedding (Qwen3-Embedding-0.6B, fp16, MPS) → top 10 of 415 articles
     └─ Reranker: Qwen3-Reranker-4B (not fine-tuned, MLX 4-bit) → top 3
         ├─ (Pro only) embedding top 10 of 397 decree/rule articles → same reranker → filter → up to 2 attached after the 3 Act articles
         └─ Answer: Qwen3.8-27B (not fine-tuned, MLX 4-bit, concise) / small Macs: Qwen3-4B + v4 LoRA (Act articles only)
             └─ Answer + source article panel (Gradio)
```

All models (embedding, reranker, answer) run in the model server (`services/model-server`, an OpenAI-compatible API); the web app (`services/chatbot`) owns the article index, ranking, decree filter, prompt assembly and UI. Generation alone can be switched to an OpenAI-compatible cloud model.

v1:

```
Question
 └─ Retrieval: fine-tuned embedding (Qwen3-Embedding-0.6B, fp16, MPS)
     └─ Top 3 of 130 articles
         └─ Generation: fine-tuned SLM (Qwen3-4B + LoRA, MLX 4-bit)
             └─ Four-part answer + source article panel (Gradio)
```

| Component | Model / Library | Notes |
| --- | --- | --- |
| Retrieval | Qwen3-Embedding-0.6B, sentence-transformers | Contrastive question-to-article training with hard negatives, 3 epochs |
| Generation | Qwen3-4B + LoRA (r=16), mlx-lm | Trained mainly on gold-article contexts, plus 135 refusal samples |
| Web UI | Gradio | Streaming answers, source article panel |
| Training | Google Colab (NVIDIA L4) | TRL SFTTrainer, PEFT |

### Results

One line per evaluation with a link to its record; tables, settings and commands live in [`docs/evals/`](docs/evals/).

- **v4 web app, test (2026-10-25)**: reranked Recall@3 0.940, citation accuracy 0.905, out-of-scope refusal 0.96, 15.1 s per question, MLX peak 20.4GB → [record](docs/evals/2026-10-25-v4-webapp-test.md)
- **Switch to the OpenAI-compatible model server (val, 2026-11-01)**: all 600 answers, 1,110 retrieval rows and decree attachments identical to baseline, latency within 0.3 s → [record](docs/evals/2026-11-01-openai-api.md)
- **Remote mode recheck (2026-10-29)**: all 522 answers identical to local mode, latency within 0.3 s → [record](docs/evals/2026-10-29-model-server.md)
- **Enforcement-decree attachment experiment (val, 2026-10-29 to 31)**: chosen rule attaches the gold decree 0.921; gold-decree citation on decree questions 0.000 → 0.900; Act answers unchanged → [record](docs/evals/2026-10-31-decree-experiment.md)
- **Decree attachment in the web app (val, 2026-10-31)**: Act retrieval identical to baseline, correct decree attached 0.91 · cited 0.885, other-law attachment 0.089 of questions, Act citation 0.865→0.855, completion 15.5→19.6 s → [record](docs/evals/2026-10-31-decree-attach.md)
- **v1, 130 Labor Standards Act articles (2026-10-16 to 18)**: Recall@3 0.940, total score 0.854 (retrieved) / 0.961 (gold articles), out-of-scope refusal 0.707 → [record](docs/evals/2026-10-18-v1.md)

### Quick start

**Requirements**

- Apple Silicon Mac (M-series) with at least 8 GB of memory
- [uv](https://docs.astral.sh/uv/) (Python 3.14 is installed automatically by `uv sync`)
- The trained model files (see "Models" below)

**Install**

```bash
git clone https://github.com/bhsong/lawbot.git
cd lawbot
uv sync
```

**Models**

Trained models are too large for the repository. The web app's default profile is **v4** (415 articles from 7 labor laws). Place the files as follows. The names are not final yet; if yours differ, set the variables in the table below.

```
data/v4/
├── articles_v4.csv          # 415 current articles from 7 laws (columns: law, article_id, title, deleted, text, optional article_no)
├── system_v4.txt            # system prompt used to train the v4 4B model (four-part format)
└── system_v4_concise.txt    # concise Pro system prompt (draft for the untuned 27B, in the repo)
models/v4/
├── emb_v4_fp16/             # v4 fine-tuned Qwen3-Embedding-0.6B (sentence-transformers format, fp16)
├── reranker_4b_mlx_4bit/    # Qwen3-Reranker-4B (not fine-tuned, MLX 4-bit) = optional reranker
├── sllm_4b_lora_mlx_4bit/   # Qwen3-4B + v4 LoRA (MLX 4-bit) = web app Lawbot Fast (small Macs)
└── qwen3.8_27b_mlx_4bit/    # mlx-community/Qwen3.8-27B-4bit (untuned) = web app Lawbot Pro
```

If `article_id` includes the law name (`근로기준법 제26조`), add an `article_no` column with the number only (`제26조`). The app uses `article_no` as the article number when present.

To build the MLX models, convert the merged Hugging Face models:

```bash
uv run mlx_lm.convert --hf-path <merged 4B folder> --mlx-path models/v4/sllm_4b_lora_mlx_4bit -q --q-bits 4 --q-group-size 64
```

Pro (27B) is downloaded as an already-quantized MLX model; the reranker is converted to 4-bit:

```bash
uv run hf download mlx-community/Qwen3.8-27B-4bit --local-dir models/v4/qwen3.8_27b_mlx_4bit
uv run mlx_lm.convert --hf-path Qwen/Qwen3-Reranker-4B --mlx-path models/v4/reranker_4b_mlx_4bit -q --q-bits 4 --q-group-size 64 --dtype float16
```

The previous v1 setup (130 Labor Standards Act articles) runs with `LAWBOT_PROFILE=v1` and uses the files below. The article and evaluation data (`data/`) and evaluation results (`results/`) are included in the repository; the v1 evaluation results are based on these files.

```
models/
├── emb_final_v3_fp16/     # v1 fine-tuned embedding
├── sllm_mlx_4bit/         # v1 fine-tuned SLM = v1 Fast
└── qwen3_14b_mlx_4bit/    # base Qwen3-14B = v1 Pro (system: data/system_v1_concise.txt)
```

To check that the files match the settings without loading any model (problems are marked `FAIL`):

```bash
uv run python -m lawbot_model_server.check  # model server: LAWBOT_SERVE_* model folders (quantization, adapters, tokenizer, query prompt)
uv run python -m lawbot.check_deploy        # web app: article CSV, system prompts, retrieval settings + model server and required models
uv run python -m lawbot.versions            # installed package versions and Mac specs
```

**Run (model server + web app, settings file .env)**

All models run in the model server (`services/model-server`, an OpenAI-compatible API); the web app (`services/chatbot`) only does article retrieval, prompt assembly and the UI. Both read the same `.env` at the repository root. Copy `.env.example` (32GB Mac mini defaults: v4, 0.6B embedding, 10 candidates → 4-bit reranker → 3, Pro 27B only) and fill in the username, password and the model server port, URL and token:

```zsh
cp .env.example .env
chmod 600 .env            # owner-only (it holds the password and token)
openssl rand -hex 32      # token: paste into LAWBOT_MODEL_TOKEN
open -e .env              # fill LAWBOT_USER, LAWBOT_PW, LAWBOT_MODEL_SERVER_PORT, LAWBOT_MODEL_SERVER, then save
uv run lawbot-model-server   # terminal 1: opens the port, loads the models, prints 'ready'
uv run lawbot-app            # terminal 2: waits for the model server, builds the article index, starts
```

`.env` is git-ignored. Variables already exported in the shell win over the file (e.g. `LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app` for a one-off run). `LAWBOT_ENV_FILE=<file>` picks another file and `LAWBOT_ENV_FILE=` disables it. Only `LAWBOT_*` variables are allowed in the file. To keep the password out of the file, delete the `LAWBOT_PW=` line and use `read -rs 'LAWBOT_PW?Password: ' && export LAWBOT_PW` or the macOS Keychain (`LAWBOT_PW="$(security find-generic-password -a lawbot -s lawbot -w)" uv run lawbot-app`).

If the v4 files (article CSV, 4B system prompt) are missing, the app refuses to start before contacting the model server and says what is missing. Until v4 is ready, run v1 (the model server must serve the v1 model ids):

```zsh
LAWBOT_PROFILE=v1 LAWBOT_MODELS= LAWBOT_RERANKER_ENABLED=0 LAWBOT_DECREE_ENABLED=0 uv run lawbot-app
```

`LAWBOT_MODELS` selects the answer models used on this machine (`.env.example`: `pro` only). Models left out appear disabled in the UI ("이 기기에서 사용 안 함"; selecting one switches back to the default). With both (`fast,pro`) you choose in the UI, and both use the same retrieval results (top 3 articles). Fast (trained on the four-part format) always generates four parts and "간결" only trims the display; Pro (concise by default) regenerates with the four-part system prompt (`data/v4/system_v4.txt`) when "전체 (4항목)" is selected.

| Model | Setup (model id · v4 default folder) | Default answer format |
| --- | --- | --- |
| Lawbot Fast | Qwen3-4B + LoRA (`lawbot-fast-v4` · `models/v4/sllm_4b_lora_mlx_4bit`) | Full: four parts (Conclusion, Cited articles, Explanation, Notes) |
| Lawbot Pro | Qwen3.8-27B 4-bit, untuned (`qwen3.8-27b-4bit` · `models/v4/qwen3.8_27b_mlx_4bit`) with a draft concise system prompt (`data/v4/system_v4_concise.txt`) | Concise: conclusion + `근거:` the articles actually cited. Slower |

Switching models also switches the answer format to that model's default; you can change the format afterwards. A status line under the chat shows retrieval, first token, first character, generation speed and total time. Selecting a model the server has not loaded yet shows "loading" first, and the load time is part of the first-token time. The web app refuses to start if a model id it uses is not served by the model server.

The web app picks models by id (`LAWBOT_*_MODEL`), not by folder; model folders and the resident count are model server settings (`LAWBOT_SERVE_*`, `LAWBOT_MAX_RESIDENT`). The tables below list every variable (relative paths are resolved from the repository root).

The web app is local-only, for use on this Mac. The address is fixed to `127.0.0.1`; the port is `LAWBOT_PORT` in `.env` (the previous default if unset). Open `http://127.0.0.1:<port>` in a browser and log in. Other devices on the network cannot connect, and no public Gradio share link is created. The address and share setting cannot be changed, and Gradio variables such as `GRADIO_SERVER_NAME` and `GRADIO_SERVER_PORT` are ignored (`services/chatbot/src/lawbot/security.py`). For an always-on setup, set `LAWBOT_PORT` explicitly in `.env`.

Web app (`lawbot-app`, `check_deploy`, `eval_v4`):

| Variable | Required | Purpose |
| --- | --- | --- |
| `LAWBOT_USER` | Yes | Login username (must not be empty after trimming whitespace) |
| `LAWBOT_PW` | Yes | Login password (must not be empty or whitespace only) |
| `LAWBOT_MODEL_SERVER` | Yes | Model server URL `http://127.0.0.1:<model server port>` (this Mac only) |
| `LAWBOT_MODEL_TOKEN` | Yes | Model server token (16+ characters, `openssl rand -hex 32`); same value for web apps and server |
| `LAWBOT_MODEL_SERVER_WAIT` | No | Seconds to wait at startup for the model server to be ready (default `300`) |
| `LAWBOT_PROFILE` | No | `v4` (default, 415 articles from 7 laws) or `v1` (130 Labor Standards Act articles, previous setup) |
| `LAWBOT_EMB_MODEL` | No | Retrieval embedding model id (default v4 `lawbot-emb-v4`, v1 `lawbot-emb-v1`) |
| `LAWBOT_FAST_MODEL` / `LAWBOT_PRO_MODEL` | No | Answer model ids (default v4 `lawbot-fast-v4` / `qwen3.8-27b-4bit`, v1 `lawbot-fast-v1` / `qwen3-14b-4bit`); the provider's model name for an external API |
| `LAWBOT_MODELS` | No | Comma-separated answer model keys to use (`pro`, `fast`, `fast,pro`); the first is the default. Empty: both |
| `LAWBOT_ENV_FILE` | No | Settings file to read (default `.env` at the repo root, skipped if absent); empty disables it. Shell only |
| `LAWBOT_PRO_SYSTEM` | No | Pro concise system prompt file. Default v4 `data/v4/system_v4_concise.txt`, v1 `data/system_v1_concise.txt` |
| `LAWBOT_V4_ARTICLES` / `LAWBOT_V4_SYSTEM` | No | v4 article CSV / 4B system prompt file (defaults `data/v4/articles_v4.csv`, `data/v4/system_v4.txt`) |
| `LAWBOT_RERANKER_ENABLED` | No | `1` re-ranks the embedding candidates with Qwen3-Reranker-4B and keeps the top 3 (default `0`: plain embedding top 3). v4 profile only |
| `LAWBOT_RERANK_MODEL` | No | Reranker model id (default `qwen3-reranker-4b`) |
| `LAWBOT_RERANKER_CANDIDATES` | No | Embedding candidates passed to the reranker (default `10`, the experiment value; at least 3) |
| `LAWBOT_DECREE_ENABLED` | No | `1` retrieves enforcement decree/rule articles separately and attaches them after the 3 Act articles (default `0`: Act articles only). Needs the reranker; v4 profile only |
| `LAWBOT_DECREE_TAU` | No | Reranker score a decree of a different law than the top Act article needs to be attached (default `0.9`, chosen on validation) |
| `LAWBOT_DECREE_K` | No | Maximum decree articles attached (default `2`) |
| `LAWBOT_DECREE_MODELS` | No | Answer model keys that get decrees (default `pro`; Fast was trained with 3 articles and attachment was not evaluated for it) |
| `LAWBOT_V4_DECREE_ARTICLES` | No | 812-article CSV used when decrees are on (default `data/v4/law_articles_v4_decree.csv`); replaces `LAWBOT_V4_ARTICLES` while on |
| `LAWBOT_LLM_BASE_URL` | No | Send generation only to another OpenAI-compatible API (e.g. `https://…/v1`). Default: the model server's `/v1` |
| `LAWBOT_LLM_API_KEY` | For an external API | That API's key (the model server token is never sent outside) |
| `LAWBOT_LLM_ALLOW_EXTERNAL` | For an external API | `1` allows questions to leave this Mac (https only) |
| `LAWBOT_PORT` | No | Web app port (1024–65535). Previous default if unset. Use different values when running production and development together |
| `LAWBOT_INSTANCE` | No | Name appended to the page and tab title (e.g. `운영`, `개발`; at most 40 characters) |
| `LAWBOT_REQUEST_LOG` | No | Request log file (default `logs/requests.jsonl` at the repo root). Empty disables it |
| `LAWBOT_FEEDBACK` | No | `1` adds 👍 and flag buttons to each answer and saves the rated answers (default `0`; only `0` or `1`) |
| `LAWBOT_FEEDBACK_DIR` | No | Feedback folder (default `feedback/` at the repo root). Inside the repo it must be git-ignored. For production, an absolute path outside the repo is recommended |

Model server (`lawbot-model-server`, `lawbot_model_server.check`):

| Variable | Required | Purpose |
| --- | --- | --- |
| `LAWBOT_MODEL_SERVER_PORT` | Yes | Model server port (1024–65535, no default). The address is fixed to 127.0.0.1 |
| `LAWBOT_MODEL_TOKEN` | Yes | The same token as above (16+ characters) |
| `LAWBOT_SERVE_CHAT` / `LAWBOT_SERVE_EMBEDDING` / `LAWBOT_SERVE_RERANK` | At least one | Comma-separated `id=folder` entries (e.g. `qwen3.8-27b-4bit=models/v4/qwen3.8_27b_mlx_4bit`) |
| `LAWBOT_SERVE_PRELOAD` | No | Chat model ids loaded at startup (default: the first chat models up to the resident count). Embedding and reranker models always load at startup |
| `LAWBOT_MAX_RESIDENT` | No | Chat models kept in memory (default `2`, LRU). Use `1` with 27B on 32 GB |

If either `LAWBOT_USER` or `LAWBOT_PW` is missing or empty, the app refuses to start with a `RuntimeError` before loading any model. There is no way to run the server without a login.

**Reranker (optional, v4)**

When enabled, retrieval becomes `embedding top 10 → Qwen3-Reranker-4B (not fine-tuned) → reranked top 3 → answer model`. Fast and Pro share the reranked context, and the model server loads the reranker at startup and keeps it resident. When disabled (default) the web app does not call it and uses the embedding top 3 as before. Scoring (prompt, 2,560-token limit, yes/no logits) matches the v4 reranker experiment notebook (`services/model-server/src/lawbot_model_server/rerank.py`); the task instruction and the ordering (ties kept in embedding order) belong to the web app (`services/chatbot/src/lawbot/reranker.py`).

`.env.example` enables it (`LAWBOT_RERANKER_ENABLED=1`); see "Model setup" above for the conversion command.

```zsh
uv run python -m lawbot_model_server.check     # reranker folder, quantization, yes/no tokens
uv run pytest -m models services/model-server   # real load + scoring smoke tests (embedding and answer models too)
LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app    # one-off run without it
```

Reranker errors (e.g. NaN scores) are shown as errors instead of silently falling back to the embedding order. A reranker id the model server does not serve, or enabling it with the v1 profile, stops the web app at startup. Agreement with the Colab run (transformers fp16) was checked on val when the reranker was added (`results/reranker_verify_val_mlx4bit.csv`).

**Enforcement decrees and rules (optional, v4)**

When enabled (`LAWBOT_DECREE_ENABLED=1`; on in `.env.example`), Act retrieval is unchanged and the 397 decree/rule articles are retrieved separately and attached:

```
415 Act articles: embedding top 10 → reranker → top 3 (same ranks, scores and prompt as when off)
397 decree/rule articles: embedding top 10 → same reranker → filter → attach up to 2 ([4], [5])
  filter: keep decrees of the same law as the top Act article; other laws only with reranker score ≥ 0.9 (lawbot.decree)
```

- When nothing passes, nothing is attached and the prompt is identical, character for character, to the decree-off prompt. Attached articles use the same `[n] law Art. N(title)` format (e.g. `[4] 근로기준법 시행령 제2조(…)`).
- Only models in `LAWBOT_DECREE_MODELS` (default Pro) get decrees. Fast does not even search them, so it behaves as before.
- The 415 Act articles are encoded exactly as before (max_seq 2,304) and the decrees separately at 3,072 tokens (longest 3,017). The Act embeddings were checked to be bit-identical when decrees were added ([record](docs/evals/2026-10-31-decree-attach.md)).
- Enabling it without the reranker, or with the v1 profile, stops the app before it contacts the model server. Decree reranking errors are shown as errors, like Act reranking.
- Screen: decree cards carry a `〔시행령〕`/`〔시행규칙〕` tag and come after the Act cards; cited articles are expanded and the rest folded. The concise citation line and warnings follow the same rules as for Acts. The stats line shows decree time and count; the request log adds `decree_search_ms`, `decree_rerank_ms`, `decree` and `n_decree`.
- The 812-article manifest is `data/v4/manifest_decree.json` (data `v4-decree`); `data/v4/manifest.json` still describes the 415-article CSV.

**Model server (OpenAI-compatible API)**

The model server (`services/model-server`, FastAPI) is a general inference server that knows nothing about the article data or chatbot settings. Other apps (for example law-change monitoring that only needs summaries) can call it with the OpenAI SDK.

| Endpoint | Format | Notes |
| --- | --- | --- |
| `POST /v1/chat/completions` | OpenAI | Streaming (SSE) and `stream_options.include_usage`. Without `temperature` it decodes greedily (0, unlike OpenAI's default of 1). Extensions: `chat_template_kwargs.enable_thinking` (off by default), `timings` in responses (prefill and generation speed, MLX peak memory) |
| `POST /v1/embeddings` | OpenAI | `encoding_format` `float` or `base64`. Extensions: `input_type` (`query`/`document`), `max_seq_length`. One list is one encode call |
| `POST /v1/rerank` | Cohere/Jina | `results: [{index, relevance_score}]`. Extension: `instruction` (task description) |
| `GET /v1/models` | OpenAI | Extensions: `type` (chat/embedding/rerank), `loaded` |
| `GET /health` | | `{ready}` (answers while models are still loading) |

```python
from openai import OpenAI

client = OpenAI(base_url='http://127.0.0.1:<model server port>/v1', api_key='<LAWBOT_MODEL_TOKEN>')
client.chat.completions.create(
    model='qwen3.8-27b-4bit', messages=[{'role': 'user', 'content': 'Summarize this amendment: …'}]
)
```

- It listens only on 127.0.0.1 and every request (including `/health`) needs the token. Unknown fields (`tools`, `n` > 1, …) are rejected with 400. Questions and documents are never logged (path, model, status and time only).
- Model computation runs one at a time (MLX and MPS are never driven from several threads at once). Overlapping requests wait; when a stream is disconnected (stop button) generation stops at the next token and the next request starts.
- The web app always uses this server for embeddings and reranking; only generation can be pointed at another OpenAI-compatible API (e.g. a cloud model) with `LAWBOT_LLM_BASE_URL`. An address outside this Mac sends questions out, so it needs `LAWBOT_LLM_ALLOW_EXTERNAL=1` and https.

**Always-on production plus development**

To keep a production web app running on the Mac mini (reached from a phone over Tailscale) while also running a development web app on the same Mac, both web apps share one model server. Loading 27B and the reranker in every web app would exceed 32 GB.

```
phone ──(Tailscale HTTPS, tailscale serve)──▶ 127.0.0.1:<prod port>   production web app (Gradio, no models)
Mac browser ────────────────────────────────▶ 127.0.0.1:<dev port>    development web app (Gradio, no models)
                                                  │  both: OpenAI-compatible HTTP + token
                                                  ▼
                                    127.0.0.1:<model server port>  model server (one)
```

| Process | Port | Runs from | Exposed |
| --- | --- | --- | --- |
| Production web app | `<prod port>` | production worktree | only this port, via `tailscale serve` |
| Development web app | `<dev port>` | development repo | no |
| Model server | `<model server port>` | production worktree | no (fixed to 127.0.0.1) |

Choose the port numbers yourself and write them only in each `.env` (never in the repository); keep the production and development ports far apart and give the model server its own. The model server refuses to start without a token or port.

1. Create a token once and put the same value in `LAWBOT_MODEL_TOKEN` of both `.env` files: `openssl rand -hex 32`
2. Create the production worktree from the tag to deploy (once): `git worktree add --detach ~/lawbot-prod <tag>`, then in it `ln -s ~/lawbot/models models`, `cp ~/lawbot/.env .env && chmod 600 .env` and `uv sync --frozen`. The production `.env` follows `.env.example` (model server + web app) plus `LAWBOT_INSTANCE=운영` and `LAWBOT_PORT`; the development `.env` follows `.env.dev.example` (web app settings only). `LAWBOT_MODEL_SERVER` and `LAWBOT_MODEL_TOKEN` must match, and the model ids the web apps use (`LAWBOT_*_MODEL`) must be in the production `LAWBOT_SERVE_*`.
3. Check by hand: `uv run python -m lawbot_model_server.check` (model folders), `uv run lawbot-model-server` (opens the port first, prints `ready` once the models are loaded), `uv run python -m lawbot.check_deploy` (articles and settings plus server reachability, token and required models), `uv run lawbot-app` (waits up to `LAWBOT_MODEL_SERVER_WAIT` seconds for the server).
4. Run both under launchd (once). Fill the placeholders in the templates; secrets and ports stay in `.env`, not in the plist.

   ```zsh
   mkdir -p ~/Library/Logs/lawbot
   for k in model-server web; do
     sed -e "s|__UV__|$(which uv)|g" -e "s|__PROD_DIR__|$HOME/lawbot-prod|g" -e "s|__LOG_DIR__|$HOME/Library/Logs/lawbot|g" \
       deploy/launchd/com.lawbot.$k.plist.example > ~/Library/LaunchAgents/com.lawbot.$k.plist
     launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.$k.plist   # register and start
   done
   launchctl kickstart -k gui/$(id -u)/com.lawbot.web          # restart (also com.lawbot.model-server after model server changes)
   launchctl bootout gui/$(id -u)/com.lawbot.web               # unregister
   ```

   launchd restarts a process that exits (`KeepAlive`); if model loading fails the server exits with code 1 and is retried every 30 seconds (`ThrottleInterval`). Keep the Mac from sleeping.
5. Expose only the production web app port inside the tailnet over HTTPS with `tailscale serve --bg <prod port>` (check the argument form with `tailscale serve --help`). Do not use `tailscale funnel`.

Constraints:

- Web app changes (`services/chatbot`: retrieval, rerank ordering, decree filter, prompt assembly, UI, system prompt files, article data) are tested by restarting only the development web app.
- Model server changes (`services/model-server`) need a development model server: stop the production one, start one from the development repo on another port (`LAWBOT_MODEL_SERVER_PORT=<test port> uv run lawbot-model-server`) and point the development web app at it. Run only one model server at a time because of memory.
- The model server computes one request at a time, so a phone question waits while a development question is generating. Stop buttons disconnect and free it at once; a phone that disappears without disconnecting holds it until generation reaches the length limit (1024 tokens).
- The web app builds its article index with the model server's embeddings at startup (tens of seconds for 812 articles); production and development each build their own.
- If login cookies clash when switching between production and development in one browser, open development at `http://localhost:<dev port>` (a different host keeps separate cookies).
- `eval_v4` uses the same model server; `peak_gb` in its results is the model server's.

**Law data version (manifest)**

A `manifest.json` next to each article CSV (`data/v4/manifest.json`, `data/manifest.json` for v1; extra CSVs in the same folder get `manifest_<name>.json`, picked by `articles_file`, e.g. `data/v4/manifest_decree.json` for the 812 articles) records the data version, reference and collection dates, source, article counts (per law and per kind), the CSV's SHA-256, and the data version the embedding and 4B LoRA were trained on. The search index is built when the web app starts rather than stored, so these values are what tie the data to the models. The article data lives only in the web app (the model server never sees it).

- The web app checks the CSV at startup and refuses to start, before contacting the model server, if the count or SHA-256 differs. A missing manifest only warns.
- `uv run python -m lawbot.check_deploy` shows the same check as OK/FAIL; articles effective after the collection date are a WARN.
- When the article CSV changes, update the manifest with a new data version and restart the web app.

**Request log and error messages**

The web app appends one line per question to `logs/requests.jsonl` (git-ignored): stage timings (search, rerank, model switch, first token, total), token counts, `peak_gb`, status (`ok`/`error`/`cancelled`) and error type. It never stores the question, the answer or article text. Evaluation scripts go through the same path and are told apart by `caller`.

```zsh
uv run python -m lawbot.log_summary                      # counts by status, per-stage median and p95, error types
uv run python -m lawbot.log_summary --caller lawbot-app  # exclude evaluation runs
```

Error types match the on-screen messages: `model_server` (unreachable server, token mismatch, still loading) shows "the model server is not ready", `model_load` (model folder missing or invalid on the model server, or a missing system file in the web app) shows the load-failure message. The request log's `mode` is `api` (generated by the model server) or `external` (external generation API). Reranking and generation errors are shown as errors, never silently replaced by the embedding order.

**Answer feedback (optional, `LAWBOT_FEEDBACK=1`)**

Each answer gets a 👍 and a flag (reasons: retrieval error, answer error, unnecessary refusal, other). With a mouse the reasons appear when hovering over the flag; on a phone, tap the flag. A click appends the full input behind that answer (question, the context articles including attached decrees and the decree settings, system prompt, raw and displayed answer, citation checks, timings, data version, commit, `request_id`) as one line to `feedback/feedback_YYYY-MM.jsonl` (git-ignored, mode 600). Question text is stored only there and only for rated answers. Answers from before an app restart, or whose displayed text does not match the record, are not saved. Gradio's built-in flagging (`.gradio/flagged`) is always off.

Saved lines are reviewed before any training use:

```zsh
uv run python -m lawbot.feedback_review <production feedback folder> feedback/   # → feedback_review_<date>.csv (+ .jsonl)
uv run python -m lawbot.feedback_export feedback_review_<date>.csv --out data/feedback/
```

- `feedback_review`: keeps the last choice per answer and drops cleared ones. It removes questions that match the evaluation data (v4 test 2,075 and val 1,110, decree questions val 444 and test 1,880, `oos_eval`, `scope_controls`, `refusal_hard_cases`, `labor_eval_keywords`) ignoring spaces and punctuation, or with difflib similarity ≥ 0.9, and prints the removed ids. Fill in `h_ok` (맞음/틀림/모름), `h_gold` (`<law> 제N조`, `;`-separated), `h_scope` (범위 안/범위 밖) and `h_note` in Excel or Numbers. `--keep <previous CSV>` carries filled cells over
- `feedback_export` (run on the Mac): `fb_questions.csv` (dev questions), `fb_emb_pairs.csv` (gold-article positives; for retrieval errors the top hit becomes a hard negative), `fb_sft.csv` (correct + 👍), `fb_kto.csv` (correct/incorrect labels). Prompts are rebuilt from the stored articles (including attached decrees) with `llm.build_prompt_v4`, `h_gold` may name statute or decree articles (`근로기준법 시행령 제2조`), and a val split is drawn inside the feedback data with `random_state=42`. Evaluation stays on the existing test set
- The touch fix for the flag (`feedback_ui.py`) relies on Gradio 6.30.0 internals. Recheck on a Mac browser and a phone after upgrading Gradio

> Warning: never put the password in source code, shell profiles, scripts, `.env.example` or any other file that goes into Git. If you keep it in a file, use only the git-ignored `.env` (mode 600); typing it at launch or the macOS Keychain is safer.

**Run the tests**

The default tests need no model files, network access or real password; `uv sync` is enough. They run both services' tests (`services/*/tests`) with fake models and a fake model server, block outbound network access, and only read `data/` and `results/`. A contract test checks that the chatbot client talks to the real model server app (with fake models) through the OpenAI SDK.

```bash
uv run pytest              # all default tests (no models needed)
uv run pytest -m models services/model-server   # real-model smoke tests (needs models/, excluded by default)
uv run ruff check          # lint (rules in [tool.ruff] in pyproject.toml)
uv run ruff format         # format (single quotes, 120 columns)
```

GitHub Actions (`.github/workflows/ci.yml`) runs `uv sync --locked`, a syntax check (`compileall`), lint and format checks (`ruff check`, `ruff format --check`) and `uv run pytest` on a macOS (Apple Silicon) runner for every push to `main` and every pull request. It uses no secrets; `-m models` tests and the evaluation scripts are not run in CI. Results appear as PR checks, in the Actions log and as the `pytest-report` artifact.

**Run the evaluation (v4 web app setup)**

`lawbot.eval_v4` evaluates the `.env` setup (v4 retrieval over 415 articles + reranker + answer model) through the same path as the web app, on the 2,075 test questions in `data/v4/qa_full_v4.csv` / `split_v4.csv` (650 Labor Standards Act + 1,425 from the six added laws). Results are appended per question to `results/v4/<stage>_<tag>.csv`, so an interrupted run resumes with the same command.

```zsh
uv run lawbot-eval-v4                       # everything: the four stages below, then the summary
uv run lawbot-eval-v4 retrieval             # all 2,075: embedding Recall@1/3/10, reranked Recall@1/3, MRR@3
uv run lawbot-eval-v4 answers               # fixed-seed sample of 200 (100 per group): citation, false refusal, keyword scoring for the 650 set
uv run lawbot-eval-v4 oos                   # 102 out-of-scope (27 from the seven laws reclassified as in scope) + 20 controls
uv run lawbot-eval-v4 refusal               # 100 sampled questions with only other-law / similar articles: refusal rate
uv run lawbot-eval-v4 summary --tag pro_concise_rr
uv run lawbot-eval-v4 --split val            # with decrees on: retrieval, answers, refusal + decree (tag ends in _dec_val)
uv run lawbot-eval-v4 decree --split val     # decree-answer val 444 attachment rate + 200-question answer sample (val only)
```

With decrees attached for the model, the default tag gets `_dec` (e.g. `pro_concise_rr_dec_val`); retrieval adds the decrees attached to Act questions (count, share from another law) and answers adds a `decrees` column. Runs refuse to append to an existing file with different columns. Refusal contexts are still drawn from the 415 Act articles, so the sample matches the baseline. The `decree` stage writes `dqretrieval_<tag>.csv` (gold decree among the 10 candidates / attached, val 444) and `dqanswers_<tag>.csv` (the 200 ids in `data/v4/decree_ctx_dq_val200.csv` answered through the web-app path, scored for gold-decree citation, false refusal and citations outside the context); summary and compare get decree sections.

Citations count only with the right law name; abbreviations (`퇴직급여법`, `고평법`, …) are accepted, and `근로기준법 시행령 제5조` (Enforcement Decree) is a different article from the Act's Art. 5. Citation parsing, refusal detection and per-citation checks (`ok` / `outside` / `wrong_law` / `nonexistent` / `ambiguous`; `nonexistent` means not among all articles of the active setup: 415, or 812 with decrees on) live in `lawbot.citation`, shared with the web view. `summary` rescores saved answers only, so files stay unchanged when the rules do. `uv run python -m lawbot.diff_answers <base.csv> <new.csv>` checks that two runs produced the same answers (exit code 0 when identical). The 2026-04-07 amendment moved the wage-arrears penalty from Art. 109 (3 years / 30M won) to Art. 107 (5 years / 50M won); the v4 gold labels follow the amended law, and the two keyword rows that still expected the old penalty (Q2443, Q2453) are updated at scoring time (`eval_v4.KEYWORD_FIX`; the data file is unchanged).

**Finished experiments**

Finished experiment scripts (v1 evaluation, reranker and decree verification, benchmarks) were removed on 2026-11-01. Their results remain in `docs/evals/` and `results/`, and the code in the Git history. The decree-experiment inputs (`data/decree_exp/`, `data/v4/decree_ctx_*.csv`) are still used by tests that check the production filter against them, and the v1 out-of-scope and refusal baselines (`oos_v1_4bit.csv`, `refusal_v1_4bit.csv`) stay as the baselines behind the README numbers and tests.

**Comparing before and after a change**

`lawbot.report` gathers the evaluation results in `results/` into one row and appends it to `results/metrics.csv`. It needs no model and finishes immediately. It records retrieval Recall@3, the total answer score, false refusals, false refusals on the in-scope controls, refusal given unrelated or similar articles, and out-of-scope refusal and forced-answer rates. Out-of-scope metrics use the original 82 questions so that they stay comparable with the baseline; the 20 added questions are reported separately.

With `--baseline`, it shows the baseline run side by side and warns about side effects when performance on ordinary questions drops. This catches changes that raise refusal only by failing to answer questions the bot should answer.

| Metric | Warn when, versus the baseline |
| --- | --- |
| Retrieval Recall@3 | drops by more than 0.005 |
| Total answer score (all / retrieval hits) | drops by more than 0.02 |
| Citation accuracy (normal / strict) | drops by more than 0.02 |
| False refusal rate (retrieval hits) | rises by more than 0.01 |
| In-scope control false refusals / total score | worsens by more than 0.05 (1 of 20) |

See the Korean section above for an example `lawbot.report` command on the saved v1 results.

`metrics.csv` already holds two rows built from existing results: `v1_4bit` (baseline) and `v1_4bit_hybrid` (the failed hybrid retrieval experiment). Hybrid retrieval raised out-of-scope refusal from 0.671 to 0.683, but its forced-answer rate also rose from 0.256 to 0.268, so higher refusal alone was not an improvement.

### Project layout

The repository is a uv workspace monorepo. Running `uv sync` or `uv run …` at the top level installs every service. Data (`data/`), models (`models/`) and evaluation results (`results/`) stay at the top level. The services never import each other; they talk only over HTTP (the OpenAI-compatible API).

```
pyproject.toml         # workspace root: members, pytest and ruff settings
services/chatbot/      # web app and evaluation (package lawbot, no model libraries); tests in services/chatbot/tests/
services/model-server/ # OpenAI-compatible model server (package lawbot_model_server, MLX and torch); tests in services/model-server/tests/
services/monitor/      # Law-change collector (package law_monitor, git only). Usage in services/monitor/README.md
```

```
services/chatbot/src/lawbot/
├── paths.py           # data paths
├── retriever.py       # article index and cosine search (vectors from the model server; separate Act and decree indexes)
├── reranker.py        # reranker client: model server scores → ordering (ties kept in embedding order)
├── decree.py          # enforcement-decree attachment filter (same1+other0.9, shared by web app and eval)
├── llm.py             # prompt construction, generation stream through the openai SDK
├── api_client.py      # model server client (embeddings, rerank, model list; ignores proxies)
├── service.py         # UI-independent Q&A flow (model server connection, retrieval → streamed answer)
├── app.py             # Gradio web app
├── security.py        # local-only launch settings (127.0.0.1, login required, no share link, port, model server and generation API URL checks)
├── prompts.py         # v1 default system prompt
├── citation.py        # citation parsing, per-citation checks, refusal detection (shared by the web view and eval_v4)
├── manifest.py        # law data manifest checks (article counts, SHA-256, reference date)
├── log_summary.py     # summary of the request log (logs/requests.jsonl)
├── feedback.py        # answer feedback storage (LAWBOT_FEEDBACK, feedback/*.jsonl)
├── feedback_ui.py     # touch-device CSS/JS for the feedback flag
├── feedback_review.py # feedback → review CSV (drops questions overlapping the evaluation data)
├── feedback_export.py # review CSV → dev questions, embedding pairs, SFT and KTO CSVs
├── eval_v4.py         # v4 web app evaluation (lawbot-eval-v4)
├── diff_answers.py    # character-level comparison of answers from two evaluation runs
├── score_answers.py   # keyword-based answer scorer
├── refusal.py         # refusal detection ('확인할 수 없' in the conclusion), out-of-scope verdicts (refusal, referral, forced, uncited)
└── report.py          # combine evaluation results into a metrics.csv row and compare with a baseline
services/model-server/src/lawbot_model_server/
├── api.py             # OpenAI-compatible API (FastAPI): auth, size limit, logging, one computation at a time via a worker thread
├── chat.py            # MLX chat model (chat template, thinking-block handling, greedy decoding)
├── embeddings.py      # sentence-transformers embeddings (MPS, fp16)
├── rerank.py          # Qwen3-Reranker scoring (same inputs as the experiment notebook)
├── registry.py        # served models, chat-model LRU
├── config.py          # port, token, LAWBOT_SERVE_* settings
├── check.py           # model folder checks (quantization, adapters, tokenizer, query prompt)
└── server.py          # uvicorn launcher (lawbot-model-server)
deploy/launchd/        # launchd templates for the always-on setup (model server, web app)
```

### Data and evaluation

- **Articles**: The 130 current articles of the Labor Standards Act. Two repealed articles are excluded. The text is as of 2026-10-01.
- **v4 articles**: 415 current articles from seven labor laws (`data/v4/articles_v4.csv`), collected from [Legalize KR](https://github.com/legalize-kr/legalize-kr) on 2026-10-20; version, dates and SHA-256 are in `data/v4/manifest.json`. The 812-article set that adds 397 enforcement-decree and rule articles (`data/v4/law_articles_v4_decree.csv`, columns `kind`, `parent_law`, `parent_ids`, `child_ids`) is used by the web app when `LAWBOT_DECREE_ENABLED=1` (manifest `data/v4/manifest_decree.json`, same 2026-10-20 collection).
  - All 21 law files hold the articles in force on 2026-10-20 (checked article by article against the Legalize KR history). Later amendments (2026-11-27 Equal Employment Act, 2026-12-08 Labor Standards Act, and others) are not in the data. Per-file source versions, the list of later amendments and how to update it are in [docs/data-provenance.md](docs/data-provenance.md) (Korean).
- **Decree questions**: 4,096 questions answered by a decree or rule article (`data/decree_exp/qa_decree_v4.csv`; split `split_decree_v4.csv`: embedding train 1,772 / val 444 / test 1,880, seed 42). `data/decree_exp/` also holds the Colab candidates and fp16 reranker scores, the Mac MLX scores and the rule comparison.
- **Questions**: 2,602 in total (130 articles × 5 question types × about 4 each).
  - Question types: colloquial, employer perspective, indirect, keyword, situational
  - Test: 650 questions (one per article and type)
  - Training and validation: the remaining 1,952 questions
  - Model selection and hyperparameters were decided on validation data only.
- **Answer scoring**: Each test question specifies required keyword groups, forbidden phrases, and the gold article. Scoring is automatic and distinguishes similar article numbers such as Article 43 and Article 43-2. A refusal is never counted as a citation, even when its list of provided articles contains the gold article. `cite_strict` separately records whether the cited-articles section names the gold article without also citing a confusable or similarly numbered article (not part of the total score). The same number in another law (e.g. Article 34 of the Employee Retirement Benefit Security Act) is not distinguished. The reference answers in the evaluation file score 1.0 on all 650 questions.
- **Out-of-scope questions**: 102 questions the Labor Standards Act cannot answer measure refusal separately (`data/oos_eval.csv`).
  - On 2026-10-19, 20 other-labor-law questions that overlap with Labor Standards Act concepts (OOS083-102) were added. Their legal basis (`note`) was checked against statute text from [Legalize KR](https://github.com/legalize-kr/legalize-kr). The v1 results ([record](docs/evals/2026-10-18-v1.md)) use the original 82.
  - Answers are classified as refusal, referral (cites only an article in `refer_lsa` that delegates to another law), forced (answers with any other Labor Standards Act article), or uncited (neither a refusal phrase nor a citation). `near_lsa` is the basis of the README numbers; `near_lsa_v2` fills in missing labels.
- **In-scope controls**: 20 test questions that resemble other-labor-law questions but must be answered under the Labor Standards Act (`data/scope_controls.csv`, paired via `twin_oos`). They check that raising out-of-scope refusal does not make the model refuse these too.
- **Unsupported-claim cases**: The 86 non-refusals in `refusal_v1_4bit.csv` are labeled forced 77, fabricated 4, correct-but-unsupported 2 and reasonable 3 (`data/refusal_hard_cases.csv`). The 81 forced and fabricated cases were re-evaluated with the same articles (`results/refusal_hard_v1_4bit.csv`).
- **Evaluation-only data**: `data/labor_eval_keywords.csv`, `data/oos_eval.csv`, `data/scope_controls.csv`, `data/refusal_hard_cases.csv` and the test questions in `data/v4/split_v4.csv` and `data/decree_exp/split_decree_v4.csv` are for evaluation only. Do not use them for training or model selection.
- **About `results/`**: These are raw model outputs with per-question scores. Low-scoring answers contain legally incorrect statements.

### Failed experiments

| Experiment | Result | Decision |
| --- | --- | --- |
| Reranker (Qwen3-Reranker-0.6B) after the fine-tuned embedding | Recall dropped below the fine-tuned embedding alone | Dropped. Larger rerankers do not fit local memory |
| SLM v2: retrieved articles as context, random gold position, 90 extra refusal samples | Citation accuracy with gold 2nd/3rd 0.68/0.50 → 0.79/0.73, refusal with similar articles 0.20 → 0.50, but false refusals 0.2% → 4.3% and total score with retrieved articles 0.854 → 0.818 | Discarded, kept v1 |
| Hybrid retrieval: 85% fine-tuned embedding + 15% BM25 (char 2-4 grams), weight chosen on validation | Validation MRR 0.836 → 0.856. Test: Recall@3 0.940 → 0.943, MRR 0.868 → 0.880. Answer total score (100 questions) 0.812 → 0.802 and out-of-scope refusal 0.671 → 0.683, no real change | Dropped. No evidence of better answers for an extra dependency and code path. BM25 and TF-IDF alone reach only about 0.67 Recall@3 |

### Limitations and roadmap

- **Retrieval misses**: For about 6% of questions, the gold article is not in the top 3, and answer quality drops sharply on those.
- **Position bias**: When the gold article is given second or third, citation accuracy falls to 0.68 and 0.50 respectively, against 0.975 when it is first.
- **Scope**: Only the Labor Standards Act is covered. The out-of-scope refusal rate is 0.707 (bf16). Questions under other labor laws that resemble a Labor Standards Act article (e.g. severance pay calculation, industrial-accident leave benefits) are still often answered with the similar article.
- **Enforcement decrees**: The attachment setup and filter chosen on validation data are in the web app (Pro only). Fast (4B LoRA) was not evaluated with attachments and still gets the 3 Act articles only. Web-app-path validation numbers will be added after manual verification on the Mac mini. Appendices, forms and notices (such as the minimum wage amount) are not included.
- **Planned work**:
  - Evaluate decree attachment for the Fast model
  - LLM-as-judge evaluation
  - FastAPI backend
  - Calculator tool calling
  - Docker
  - vLLM serving
  - Cloud deployment
  - Re-indexing after law amendments

### Notices

- The base models Qwen3-4B and Qwen3-Embedding-0.6B are subject to their own licenses (Apache License 2.0).
- Korean statutes are not protected by copyright (Article 7 of the Korean Copyright Act).
- Answers from this chatbot are not legal advice. For a specific case, consult the Ministry of Employment and Labor helpline (1350 in Korea) or a certified labor attorney.
