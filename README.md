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
         └─ 답변: Qwen3.8-27B (튜닝 없음, MLX 4비트, 간결형) / 소형 맥은 Qwen3-4B + v4 LoRA
             └─ 답변 + 근거 조문 표시 (Gradio)
```

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

**v4 웹앱 구성 (7개 법률 415조문 · 리랭커 · Qwen3.8-27B 4비트)**

2026-10-25 맥 미니 32GB에서 `uv run lawbot-eval-v4`로 측정했습니다(문항별 결과 `results/v4/*_pro_concise_rr.csv`). 구성은 `.env.example` 그대로입니다: 튜닝 Qwen3-Embedding-0.6B 상위 10개 → Qwen3-Reranker-4B(튜닝 없음, MLX 4비트) 상위 3개 → Qwen3.8-27B(튜닝 없음, MLX 4비트, 간결형 답변). 문항은 학습·검증에 쓰지 않은 v4 test 2,075문항(근로기준법 650 + 신규 6개 법률 1,425)입니다.

*검색 (test 2,075문항, 415조문 대상)*

| 단계 | Recall@1 | Recall@3 | Recall@10 | MRR |
| --- | --- | --- | --- | --- |
| 튜닝 임베딩 | 0.765 | 0.913 | 0.976 | 0.845 (@10) |
| + 리랭커 (후보 10개 → 3개) | **0.829** | **0.940** | - | **0.880** (@3) |

근로기준법 650문항은 리랭킹 후 Recall@3 0.923, 신규 법률 1,425문항은 0.947입니다. Colab(L4, fp16) 실험의 같은 test 수치(임베딩 0.765·0.914, 리랭커 0.827·0.941·MRR@3 0.880)와 0.002 이내로 같습니다.

리랭커는 Colab과 같은 질문·임베딩 Top 10으로 따로 대조했습니다(val 1,110문항, `results/reranker_verify_val_mlx4bit.csv`).

| 리랭커 | Recall@1 | Recall@3 | MRR@3 | 질문당 시간 |
| --- | --- | --- | --- | --- |
| Colab (transformers fp16, L4) | 0.836 | 0.950 | 0.888 | 1.01초 |
| 맥 (MLX 4비트) | 0.819 | 0.946 | 0.877 | 1.93초 (p95 3.04) |

1위 조문은 90%가 Colab과 같지만, 4비트 양자화로 점수 크기는 크게 달라집니다(질문별 최대 점수 차 중앙값 0.28, 상위 3개 집합 일치 0.58). 그래도 정답 순위 지표는 Recall@1 0.017, Recall@3 0.004 차이에 그칩니다.

*답변 (test 표본 200문항: 근로기준법 100 + 신규 법률 100, 고정 시드)*

| 지표 | 전체 | 근로기준법 | 신규 6개 법률 |
| --- | --- | --- | --- |
| 검색 top3 적중률 | 0.915 | 0.93 | 0.90 |
| 조문 인용 정확도 (법령명까지) | 0.905 | 0.91 | 0.90 |
| 거짓 거절률 | 0.065 | 0.07 | 0.06 |
| 거짓 거절률 (검색 적중 문항) | 0.011 | 0.022 | 0.000 |
| 키워드 그룹 충족률 | - | 0.853 | - |
| 종합 점수 (검색 적중 문항) | - | 0.828 (0.891) | - |

- 검색이 정답을 놓친 17문항 중 11문항은 거절했고, 검색이 맞았는데 거절한 것은 2문항입니다. 검색이 맞고 거절하지 않은 문항은 모두 정답 조문을 인용했습니다.
- 키워드 채점은 기존 650문항 기준이 있는 근로기준법 문항만 합니다. 신규 법률 문항은 인용·거절만 봅니다.
- 2026.4.7 근로기준법 개정(임금체불 벌칙 제109조 → 제107조, 5년·5천만원)에 맞춰 키워드 2문항을 보정했습니다.
- v1(4B, 4항목 답변, 130조문, 무작위 100문항)의 종합 점수 0.812와는 답변 형식·조문 수·표본이 달라 직접 비교하기 어렵습니다.

*거절 동작*

| 지표 | 값 | 문항 |
| --- | --- | --- |
| 범위 밖 질문 거절률 | **0.96** | 75 |
| 범위 밖 질문 억지 적용률 (거절 않고 조문 인용) | 0.04 | 75 |
| 다른 법률 조문만 제공 시 거절률 | **1.00** | 100 |
| 비슷한 조문만 제공 시 거절률 | 0.69 | 100 |
| 범위 안이 된 7개 법률 질문: 거짓 거절률 / 해당 법률 인용률 | 0.111 / 0.889 | 27 |
| 범위 안 대조군(근로기준법): 거짓 거절률 / 정답 조문 인용률 | 0.10 / 0.80 | 20 |

- 범위 밖 102문항 중 출처가 7개 법률인 27문항은 이제 범위 안이므로 따로 셉니다. 그래서 v1의 범위 밖 거절률(82문항, 0.707)과 같은 문항이 아닙니다. 억지 적용 3건은 산재 신청(→ 근로기준법 제88조), 미성년자 계약 취소(→ 제67조), 직장폐쇄 임금(→ 제46조)입니다.
- 범위 안 27문항의 거짓 거절 3건 중 2건(올해 최저시급 금액, 육아기 근로시간 단축 신청 방법)은 그 내용이 조문이 아니라 고시·시행령에 있어 거절이 타당한 쪽입니다. 나머지 1건(1년 미만 퇴직금)은 검색이 근퇴법 제4조를 찾지 못했습니다.
- 비슷한 조문 중에는 실제로 답이 되는 조문이 섞일 수 있어 1.0이 목표는 아닙니다(v1 4B는 0.20, 맥 4비트 0.24).
- 튜닝하지 않은 27B는 v1 학습 문구("확인할 수 없") 대신 "근거를 찾을 수 없습니다", "답변할 수 없습니다"처럼 거절해, v4 평가는 이런 표현도 거절로 봅니다(`citation.REFUSAL`). 범위 안 답변 200문항에서는 이 확장으로 새로 거절로 잡힌 답변이 없었습니다. 웹앱 간결형 화면도 같은 판정을 써서, 이런 거절에는 근거 줄이나 경고를 붙이지 않습니다.

*속도·메모리 (답변 200문항, 맥 미니 32GB)*

| 지표 | 값 |
| --- | --- |
| 임베딩 검색 | 0.03초 |
| 리랭킹 (후보 10개) | 평균 1.95초, p95 3.1초 |
| 첫 토큰 (생성 시작부터) | 중앙값 2.8초 |
| 질문당 완료 (검색부터 답변 끝까지) | 평균 15.1초, p95 20.9초 |
| 생성 속도 | 9.7 토큰/초 |
| 답변 길이 | 평균 165자 (96토큰) |
| MLX 최대 메모리 (27B + 리랭커) | 20.4GB |

*운영 구성(원격 모드)에서의 재확인 (2026-10-29)*

웹앱이 모델 서버에 접속하는 운영 구성으로 answers·oos·refusal을 다시 돌려 `lawbot.diff_answers`로 위 결과와 비교했습니다. 답변 522개가 글자 단위로 모두 같아(answers 200 · oos 122 · refusal 200) 위 정확도 수치를 그대로 씁니다. 질문당 완료 평균 14.9초, 첫 토큰 중앙값 2.78초, MLX 최대 메모리 20.4GB로 HTTP 왕복에 따른 지연은 보이지 않았습니다(`results/v4/*_pro_concise_rr_remote.csv`).

**v1 (근로기준법 130조문 · Qwen3-4B LoRA)**

모든 수치는 학습·검증에 사용하지 않은 테스트 650문항 기준입니다(별도 표기 제외). 측정일은 2026-10-16부터 10-18까지입니다.

**검색 (130개 조문 대상)**

| 모델 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR |
| --- | --- | --- | --- | --- | --- |
| Qwen3-Embedding-0.6B (튜닝 전) | 0.622 | - | 0.837 | 0.888 | 0.718 |
| 튜닝 후 (hard negative) | **0.789** | **0.940** | **0.962** | **0.986** | **0.868** |

질문 분류 정확도(130개 클래스, 로지스틱 회귀)도 함께 측정했습니다.

| 모델 | 정확도 | Top-3 | MRR |
| --- | --- | --- | --- |
| TF-IDF (char 2~4그램) | 0.655 | 0.858 | 0.767 |
| 튜닝 전 임베딩 | 0.789 | 0.926 | 0.865 |
| 튜닝 임베딩 | **0.855** | **0.963** | **0.911** |

**답변 생성 (Colab, bf16)**

종합 점수 = 키워드 그룹 충족률 × 조문 인용 정확 여부 × (금지 표현 시 0.5)이고, 만점은 1.0입니다. 거절 답변은 0점입니다. 아래 Colab 수치는 2026-10-19 채점기 수정(거절 답변의 제공 조문 목록을 인용으로 세던 문제) 전에 측정했습니다. 수정 영향은 거짓 거절 문항에 한정되므로 최대 0.005입니다.

| 조건 | 조문 인용 정확도 | 키워드 그룹 충족률 | 종합 점수 |
| --- | --- | --- | --- |
| Qwen3-4B (튜닝 전), 정답 조문 제공 | 0.955 | 0.894 | 0.866 |
| Qwen3-4B + LoRA, 정답 조문 제공 | **0.983** | **0.967** | **0.961** |
| Qwen3-4B + LoRA, 검색 조문 제공 (실서비스 조건) | 0.869 | 0.884 | 0.854 |
| 위와 같음, 검색 적중 문항만 | - | - | 0.908 |

**거절 동작**

| 지표 | 튜닝 전 | 튜닝 후 |
| --- | --- | --- |
| 무관한 조문만 제공 시 거절률 (100문항) | 0.11 | **0.91** |
| 정답 조문이 있는데 거절한 비율 | 0.002 | 0.005 |
| 범위 밖 질문 거절률 (82문항, 검색 조문 제공) | 0.098 | **0.707** |
| 비슷한 조문만 제공 시 거절률 (100문항) | - | 0.20 |

범위 밖 질문은 일상 질문 25개, 다른 노동법 질문 32개(근퇴법·최저임금법·남녀고용평등법 등), 다른 법 질문 25개입니다(`data/oos_eval.csv`). 맥 4비트에서는 범위 밖 0.671, 비슷한 조문 0.24로 bf16과 비슷합니다. 근로기준법에 비슷한 조문이 있는 다른 노동법 질문은 튜닝 후에도 거절률이 0.250에 그쳐, 가장 큰 약점입니다. 거절하지 않은 답변을 나누어 보면, 다른 노동법 32문항 중 5개는 퇴직급여(제34조)·안전보건(제76조)처럼 다른 법에 맡기는 조문만 들어 그 법으로 안내했고, 13개(0.406)는 휴업보상(제79조)으로 산재 휴업급여를 답하는 식으로 근로기준법 조문을 억지로 적용했습니다. 82문항 전체의 억지 적용률은 0.256입니다(`refusal.oos_verdict`).

**로컬 실행 (MacBook Air 8GB, MLX 4비트, dev 100문항)**

| 지표 | 값 |
| --- | --- |
| 종합 점수 (검색 조문 / 정답 조문) | 0.812 / 0.937 |
| 첫 토큰까지 시간 (중앙값) | 5.4초 |
| 생성 속도 | 24.5 토큰/초 |
| 질문당 소요 시간 | 20.4초 |
| sLLM 최대 메모리 | 3.16GB |

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

이전 v1(근로기준법 130조문) 구성은 `LAWBOT_PROFILE=v1`로 실행하며, 아래 파일을 씁니다. 조문·평가 데이터(`data/`)와 평가 결과(`results/`)는 저장소에 포함되어 있고, 평가 스크립트(`lawbot-eval` 등)는 v1 파일을 씁니다.

```
models/
├── emb_final_v3_fp16/     # v1 튜닝 임베딩
├── sllm_mlx_4bit/         # v1 튜닝 sLLM = v1 Fast
└── qwen3_14b_mlx_4bit/    # Qwen3-14B 튜닝 전 = v1 Pro (system: data/system_v1_concise.txt)
```

파일이 웹앱 설정과 맞는지는 모델을 올리지 않고 확인할 수 있습니다. 문제가 있는 항목은 `FAIL`로 표시됩니다.

```bash
uv run python -m lawbot.check_deploy        # 현재 프로필 (기본 v4, LAWBOT_PROFILE=v1이면 v1)
uv run python -m lawbot.versions            # 설치된 패키지 버전·맥 사양
```

**웹앱 실행 (설정 파일 .env)**

설정은 레포 최상위의 `.env` 파일에 적어 두면 `uv run lawbot-app`이 시작할 때 읽습니다. `.env.example`(맥 미니 32GB 기본값: v4, 임베딩 0.6B, 후보 10개 → 리랭커 4비트 → 3개, Pro 27B만 사용)을 복사해서 사용자명과 비밀번호만 채우면 됩니다.

```zsh
cp .env.example .env
chmod 600 .env            # 나만 읽고 쓰기 (비밀번호가 들어가므로)
open -e .env              # LAWBOT_USER=, LAWBOT_PW= 뒤에 값 입력 후 저장
uv run lawbot-app
```

- `.env`는 `.gitignore`에 들어 있어 Git에 올라가지 않습니다. `.env.example`에는 비밀번호를 적지 마세요.
- 셸에서 `export`한 변수가 있으면 그 값이 `.env`보다 우선합니다. 한 번만 다르게 실행할 때는 `LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app`처럼 앞에 붙입니다.
- 다른 파일을 쓰려면 `LAWBOT_ENV_FILE=.env.small uv run lawbot-app`(예: 소형 맥의 Fast 4B 설정), 파일을 읽지 않으려면 `LAWBOT_ENV_FILE=`.
- `.env`에는 `LAWBOT_*`와 `LLM_PATH`만 적을 수 있습니다. 서버 주소는 바꿀 수 없고, 웹앱 포트는 `LAWBOT_PORT`로 정합니다(아래 참고).
- `lawbot-model-server`, `check_deploy`, `bench_models`, `bench_ui`, `verify_reranker`, `eval_concise`도 같은 `.env`를 읽습니다.
- 값에 공백이나 ` #`이 들어가면 작은따옴표로 감쌉니다: `LAWBOT_PW='a b#c'`.

비밀번호를 파일에 두지 않으려면 `.env`의 `LAWBOT_PW=` 줄을 지우고 실행할 때 입력하거나 macOS 키체인에서 꺼내 씁니다(zsh).

```zsh
read -rs 'LAWBOT_PW?비밀번호: ' && export LAWBOT_PW && uv run lawbot-app
security add-generic-password -a lawbot -s lawbot -w   # 키체인에 저장 (최초 1회)
LAWBOT_PW="$(security find-generic-password -a lawbot -s lawbot -w)" uv run lawbot-app
```

v4 파일(조문 CSV, 4B system)이 없으면 모델을 올리기 전에 실행을 거부하고 안내합니다. v4 준비 전에는 v1으로 실행합니다.

```zsh
LAWBOT_PROFILE=v1 LAWBOT_MODELS= LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app
```

`LAWBOT_MODELS`로 이 맥에서 쓸 답변 모델을 고릅니다(`.env.example`은 `pro`만). 빠진 모델은 올리지 않고 화면에 "이 기기에서 사용 안 함"으로 비활성 표시합니다(고르면 기본 모델로 되돌림). 둘 다 쓰면(`fast,pro`) 화면에서 고르고, 두 모델은 같은 검색 결과(상위 3개 조문)를 참고합니다.

답변 형식에서 Fast(4항목으로 학습)는 항상 4항목으로 생성하고 "간결"은 화면에서 결론·근거만 보여 줍니다. 간결형이 기본인 Pro는 "전체 (4항목)"를 고르면 4항목 system(`data/v4/system_v4.txt`, v1은 학습 system)으로 다시 생성합니다.

| 모델 | 구성 (v4 기본 경로) | 기본 답변 형식 |
| --- | --- | --- |
| Lawbot Fast | Qwen3-4B + LoRA (`models/v4/sllm_4b_lora_mlx_4bit`) | 전체: `【결론】【근거 조문】【설명】【참고】` 4항목 |
| Lawbot Pro | Qwen3.8-27B 4비트, 튜닝 전 (`models/v4/qwen3.8_27b_mlx_4bit`), 간결형 system(`data/v4/system_v4_concise.txt`, 초안) | 간결: 결론 + `근거: 실제 인용 조문`. 답변이 더 느림 |

모델을 바꾸면 답변 형식도 그 모델의 기본값으로 바뀌고, 형식은 직접 다시 고를 수 있습니다. 화면 아래 측정 줄에 검색·모델 전환·첫 토큰·첫 글자·생성 속도·완료 시간이 표시됩니다. Pro 폴더나 Pro system 파일이 없으면 Fast만으로 실행되고, Pro를 고르면 오류 안내가 나옵니다.

모델 경로나 메모리 설정은 `.env`에서 바꿉니다(상대 경로는 저장소 최상위 기준). 아래 표가 전체 변수입니다.

웹앱은 이 맥에서만 쓰는 로컬 전용입니다. 주소는 `127.0.0.1` 고정이고, 포트는 `.env`의 `LAWBOT_PORT`(없으면 기존 기본값)입니다. 브라우저에서 `http://127.0.0.1:<포트>`로 접속해 로그인합니다. 같은 네트워크의 다른 기기에서는 접속할 수 없으며, Gradio 공개 공유 링크(`share`)도 만들지 않습니다. 주소·공유 설정은 바꿀 수 없고, `GRADIO_SERVER_NAME`·`GRADIO_SERVER_PORT` 같은 Gradio 환경 변수는 무시합니다(`src/lawbot/security.py`). 상시 운영할 때는 `.env`에 `LAWBOT_PORT`를 따로 정하세요.

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `LAWBOT_USER` | 예 | 로그인 사용자명 (앞뒤 공백 제거 후 비어 있으면 안 됨) |
| `LAWBOT_PW` | 예 | 로그인 비밀번호 (빈 값·공백만 있는 값은 안 됨) |
| `LAWBOT_PROFILE` | 아니요 | `v4`(기본, 7개 법률 415조문) 또는 `v1`(근로기준법 130조문, 이전 구성) |
| `LAWBOT_FAST_PATH` | 아니요 | Fast(4B) 모델 폴더. 기본 v4 `models/v4/sllm_4b_lora_mlx_4bit`, v1 `models/sllm_mlx_4bit` |
| `LAWBOT_PRO_PATH` | 아니요 | Pro 모델 폴더. 기본 v4 `models/v4/qwen3.8_27b_mlx_4bit`(27B), v1 `models/qwen3_14b_mlx_4bit`(14B) |
| `LAWBOT_MODELS` | 아니요 | 쓸 답변 모델 key를 쉼표로 (`pro`, `fast`, `fast,pro`). 첫 번째가 기본 선택. 비우면 프로필의 Fast·Pro 모두 |
| `LAWBOT_ENV_FILE` | 아니요 | 읽을 설정 파일 (기본 레포 최상위 `.env`, 없으면 안 읽음). 빈 값이면 읽지 않음. 셸에서만 지정 |
| `LAWBOT_PRO_SYSTEM` | 아니요 | Pro 간결형 system 파일. 기본 v4 `data/v4/system_v4_concise.txt`, v1 `data/system_v1_concise.txt` |
| `LAWBOT_V4_ARTICLES` / `LAWBOT_V4_EMB` / `LAWBOT_V4_SYSTEM` | 아니요 | v4 조문 CSV / 임베딩 폴더 / 4B system 파일 (기본 `data/v4/articles_v4.csv`, `models/v4/emb_v4_fp16`, `data/v4/system_v4.txt`) |
| `LAWBOT_EMB` | 아니요 | v1 검색 임베딩 폴더 (기본 `models/emb_final_v3_fp16`) |
| `LAWBOT_MAX_RESIDENT` | 아니요 | 동시에 올려 둘 답변 모델 수. `2`(기본, Fast·Pro 모두 상주, 전환 대기 없음) 또는 `1`(선택한 모델만, 바꿀 때 다시 불러옴) |
| `LAWBOT_EARLY_STOP` | 아니요 | `1`이면 간결 형식에서 【설명】이 시작될 때 생성을 멈춤 (기본 꺼짐, 검증 전) |
| `LAWBOT_RERANKER_ENABLED` | 아니요 | `1`이면 임베딩 상위 후보를 Qwen3-Reranker-4B로 다시 정렬해 상위 3개를 씀 (기본 `0`: 리랭커를 올리지 않고 기존 임베딩 상위 3개). v4 프로필 전용 |
| `LAWBOT_RERANKER_MODEL_PATH` | 아니요 | 리랭커 MLX 폴더 (기본 `models/v4/reranker_4b_mlx_4bit`) |
| `LAWBOT_RERANKER_CANDIDATES` | 아니요 | 리랭커에 넘길 임베딩 후보 수 (기본 `10`, 실험값. 3 이상) |
| `LLM_PATH` | 아니요 | 평가 스크립트(`eval_oos`·`eval_refusal`)가 쓸 sLLM 폴더 (예: `models/sllm_mlx_8bit`). 웹앱은 쓰지 않음 |
| `LAWBOT_PORT` | 아니요 | 웹앱 포트 (1024–65535). 없으면 기존 기본값. 운영·개발을 함께 띄울 때는 서로 다른 값 |
| `LAWBOT_INSTANCE` | 아니요 | 화면·탭 제목에 붙일 이름 (예: `운영`, `개발`, 40자 이하) |
| `LAWBOT_MODEL_SERVER` | 아니요 | 모델 서버 주소 `http://127.0.0.1:<모델 서버 포트>`. 있으면 원격 모드(웹앱은 모델을 올리지 않음), 비우면 로컬 모드 |
| `LAWBOT_MODEL_SERVER_PORT` | 모델 서버 필수 | 모델 서버 포트 (1024–65535, 기본값 없음) |
| `LAWBOT_MODEL_TOKEN` | 모델 서버·원격 모드 필수 | 모델 서버 토큰 (16자 이상, `openssl rand -hex 32`). 웹앱과 모델 서버가 같은 값 |
| `LAWBOT_MODEL_SERVER_WAIT` | 아니요 | 원격 모드 웹앱이 시작할 때 모델 서버 준비를 기다리는 최대 초 (기본 `300`) |
| `LAWBOT_REQUEST_LOG` | 아니요 | 요청 로그 파일 (기본 레포 최상위 `logs/requests.jsonl`). 빈 값이면 기록하지 않음 |

`LAWBOT_USER`와 `LAWBOT_PW`가 하나라도 없거나 비어 있으면 모델을 불러오기 전에 `RuntimeError`로 실행을 거부합니다. 인증 없이 서버를 띄우는 방법은 없습니다.

**리랭커 (선택, v4)**

켜면 검색 흐름이 `임베딩 상위 10개 → Qwen3-Reranker-4B(튜닝 없음) → 리랭킹 상위 3개 → 답변 모델`이 됩니다. Fast·Pro가 같은 리랭킹 결과를 참고하고, 리랭커는 앱 시작 때 한 번 올려 상주합니다. 끄면(기본) 리랭커를 올리지 않고 이전과 같은 임베딩 상위 3개를 씁니다. 채점 방식(instruction, 프롬프트, 최대 2,560토큰, yes/no 로짓, 동점은 임베딩 순위)은 v4 리랭커 실험 노트북과 같습니다(`src/lawbot/reranker.py`).

`.env.example`에는 켜져 있습니다(`LAWBOT_RERANKER_ENABLED=1`). 모델 준비는 위 "모델 준비"의 변환 명령을 씁니다.

```zsh
uv run python -m lawbot.check_deploy           # .env 설정 그대로: 리랭커 폴더·양자화·yes/no 토큰 검사
uv run pytest -m models tests/test_reranker_models.py   # 실제 로딩·채점 스모크 테스트
LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app    # 한 번만 끄고 실행
```

리랭커 오류(점수가 NaN 등)는 임베딩 순위로 몰래 되돌리지 않고 화면에 오류로 표시합니다. 폴더가 없거나 v1 프로필에서 켜면 모델을 올리기 전에 실행을 거부합니다. 화면의 조문 카드에는 리랭커 점수와 원래 임베딩 순위가, 측정 줄에는 리랭킹 시간이 나오고, 터미널에는 질문마다 검색·리랭킹·첫 토큰·완료 시간이 한 줄씩 남습니다.

Colab 실험(transformers fp16)과의 순위 일치는 같은 질문·임베딩 Top 10으로 따로 대조합니다. 실험 폴더(Drive `labor_law/v4`)를 내려받아 지정합니다. 기본은 val이며, test는 기록된 수치와 대조할 때만 쓰고 설정 조정에 쓰지 않습니다.

```zsh
uv run python -m lawbot.verify_reranker --colab ~/Downloads/v4 --split val     # → results/reranker_verify_val_mlx4bit.csv
uv run python -m lawbot.bench_models --tag m32_v4_rr                            # .env 그대로 (리랭커 포함) 지연·메모리
LAWBOT_RERANKER_ENABLED=0 uv run python -m lawbot.bench_models --tag m32_v4     # 비교 기준
```

**상시 운영과 개발 동시 실행 (모델 서버)**

맥 미니에서 운영 웹앱을 계속 켜 두고(휴대폰은 Tailscale로 접속) 같은 맥에서 개발 웹앱도 띄울 때는, 임베딩·리랭커·답변 모델을 한 번만 올리는 내부 모델 서버(`lawbot-model-server`)를 두 웹앱이 함께 씁니다. 웹앱마다 27B와 리랭커를 따로 올리면 32GB를 넘기 때문입니다. `LAWBOT_MODEL_SERVER`가 비어 있으면 지금처럼 웹앱이 모델을 직접 올리는 로컬 모드입니다.

```
휴대폰 ──(Tailscale HTTPS, tailscale serve)──▶ 127.0.0.1:<운영 포트>  운영 웹앱 (Gradio, 모델 없음)
맥 브라우저 ─────────────────────────────────▶ 127.0.0.1:<개발 포트>  개발 웹앱 (Gradio, 모델 없음)
                                                   │  둘 다 HTTP + 토큰
                                                   ▼
                                     127.0.0.1:<모델 서버 포트>  모델 서버 (1개)
```

| 프로세스 | 포트 | 실행 위치 | 외부 노출 |
| --- | --- | --- | --- |
| 운영 웹앱 | `<운영 포트>` | 운영 worktree | `tailscale serve`로 이 포트만 |
| 개발 웹앱 | `<개발 포트>` | 개발 레포 | 없음 |
| 모델 서버 | `<모델 서버 포트>` | 운영 worktree | 없음 (127.0.0.1 고정) |

포트 번호는 직접 정해 각 `.env`에만 적습니다(레포에 올리지 않음). 운영·개발 포트는 서로 떨어뜨리고, 모델 서버 포트도 따로 둡니다. 모델 서버는 127.0.0.1에만 열리고, 토큰이나 포트가 없으면 실행을 거부합니다. 질문 내용은 모델 서버 로그에 남기지 않습니다(경로·모델·시간만).

1. 토큰을 한 번 만들어 운영·개발 `.env`의 `LAWBOT_MODEL_TOKEN`에 같은 값으로 적습니다: `openssl rand -hex 32`
2. 운영 worktree를 만듭니다(배포할 태그 기준, 1회).

   ```zsh
   cd ~/lawbot
   git worktree add --detach ~/lawbot-prod <태그>
   cd ~/lawbot-prod
   ln -s ~/lawbot/models models                 # 모델 파일 공유
   cp ~/lawbot/.env .env && chmod 600 .env      # .env.example 맨 아래 운영 블록대로 수정
   uv sync --frozen
   ```

   운영 `.env`에는 `LAWBOT_INSTANCE=운영`, `LAWBOT_PORT`, `LAWBOT_MODEL_SERVER_PORT`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN`을, 개발 레포 `.env`에는 `.env.dev.example`대로 `LAWBOT_INSTANCE=개발`, `LAWBOT_PORT`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN`을 적습니다. 두 `.env`의 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`·`LAWBOT_PROFILE`은 같아야 합니다.
3. 직접 실행해 확인합니다.

   ```zsh
   uv run lawbot-model-server                   # 운영 worktree. 포트를 먼저 열고 모델을 올린 뒤 'ready'
   uv run python -m lawbot.check_deploy         # 모델 파일 + 모델 서버 접속·토큰·api_version·프로필 일치
   uv run lawbot-app                            # 원격 모드: 모델 서버가 준비될 때까지 최대 LAWBOT_MODEL_SERVER_WAIT초 대기
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
   launchctl kickstart -k gui/$(id -u)/com.lawbot.web          # 재시작 (서버 쪽 코드가 바뀌면 com.lawbot.model-server도)
   launchctl bootout gui/$(id -u)/com.lawbot.web               # 해제 (모델 서버도 같은 방식)
   ```

   종료되면 launchd가 다시 띄웁니다(`KeepAlive`). 모델 로딩에 실패하면 모델 서버는 종료 코드 1로 끝나고 30초 간격(`ThrottleInterval`)으로 다시 시도합니다. 맥이 잠자기에 들어가지 않게 설정합니다.
5. 휴대폰에서는 Tailscale로 운영 웹앱 포트만 tailnet 안에 HTTPS로 노출합니다. 공개 인터넷에 여는 `tailscale funnel`은 쓰지 않습니다. 인자 형식은 Tailscale 버전마다 다를 수 있으니 `tailscale serve --help`로 확인합니다.

   ```zsh
   tailscale serve --bg <운영 포트>
   tailscale serve status
   ```

이 구조의 제약:

- 웹앱 쪽 코드(`app.py`, `service.ask`, `answer_format.py`, system 파일)는 개발 웹앱만 다시 띄워 시험합니다. system 문구는 웹앱이 자기 레포의 파일을 요청마다 보냅니다.
- 서버 쪽 코드(`llm.py`의 프롬프트 조립, `reranker.py`, `retriever.py`, `model_server.py`)는 개발용 모델 서버로 시험합니다. 운영 모델 서버를 멈추고(`launchctl bootout …/com.lawbot.model-server`), 개발 레포에서 다른 포트로 띄운 뒤(`LAWBOT_MODEL_SERVER_PORT=<시험 포트> uv run lawbot-model-server`) 개발 웹앱을 그 포트로 연결합니다(`LAWBOT_MODEL_SERVER=http://127.0.0.1:<시험 포트> uv run lawbot-app`). 메모리 때문에 모델 서버는 한 번에 하나만 띄웁니다.
- 모델 서버는 요청을 한 번에 하나만 처리합니다. 개발 화면에서 질문하는 동안 휴대폰 질문은 기다립니다. 정지 버튼·간결형 조기 종료로 연결이 끊기면 바로 다음 요청을 받지만, 휴대폰이 연결을 끊지 않고 사라지면 생성이 최대 길이(1024토큰)에서 끝날 때까지 기다립니다.
- 검색 설정(리랭커 사용·후보 수)과 상주 모델 수는 모델 서버(운영 `.env`)가 기준입니다. 원격 모드 웹앱의 `LAWBOT_RERANKER_*`·`LAWBOT_MAX_RESIDENT`는 쓰지 않습니다. 웹앱은 시작할 때 프로필·`doc_style`·모델 key·API 버전이 서버와 같은지 확인하고, 다르면 실행을 거부합니다.
- 같은 브라우저에서 운영·개발 화면에 번갈아 로그인할 때 로그인 쿠키가 겹치면, 개발 화면은 `http://localhost:<개발 포트>`로 접속합니다(호스트가 다르면 쿠키가 분리됨).
- `eval_v4`, `eval_concise`, `bench_models`는 원격 모드로도 돌아갑니다. 결과의 `peak_gb`·MLX 메모리는 모델 서버 프로세스 값입니다. `verify_reranker`, `eval_dev`, `eval_refusal`, `eval_oos`, `diag_*`는 모델을 직접 올리는 로컬 모드 전용이므로 실행 전에 모델 서버를 멈춥니다.

**법령 데이터 버전 (매니페스트)**

조문 CSV 옆의 `manifest.json`(v4 `data/v4/manifest.json`, v1 `data/manifest.json`)에 데이터 버전·기준일·수집일·출처·조문 수(법령별·종류별)·sha256과, 임베딩·4B LoRA가 학습한 데이터 버전을 적어 둡니다. 검색 인덱스는 파일이 아니라 모델 서버가 시작할 때 만들므로, 대신 이 값으로 데이터와 모델이 맞는지 확인합니다.

- 모델 서버(로컬 모드 웹앱 포함)는 시작할 때 CSV를 검사해, 조문 수나 sha256이 다르면 모델을 올리기 전에 실행을 거부합니다. 매니페스트가 없으면 경고만 합니다.
- `uv run python -m lawbot.check_deploy`가 같은 검사를 OK/FAIL로 보여 줍니다. 시행일이 수집일보다 늦은 조문은 WARN입니다.
- 모델 서버 `/info`가 `dataset`(버전·기준일·sha256)을 보내고, 원격 모드 웹앱은 자기 레포의 매니페스트와 다르면 경고합니다(시작은 막지 않음).
- 조문 CSV를 바꾸면 매니페스트도 새 데이터 버전으로 함께 바꾸고, 모델 서버와 웹앱을 모두 재시작합니다.

**요청 로그와 오류 안내**

웹앱은 질문마다 `logs/requests.jsonl`(Git 제외)에 한 줄을 남깁니다. 단계별 시간(검색·리랭킹·모델 전환·첫 토큰·완료), 토큰 수, `peak_gb`, 상태(`ok`/`error`/`cancelled`), 오류 유형이 들어가고, 질문 원문·답변·조문 내용은 남기지 않습니다. 평가 스크립트도 같은 경로를 쓰므로 `caller`로 구분합니다.

```zsh
uv run python -m lawbot.log_summary                      # 상태별 건수, 단계별 중앙값·p95, 오류 유형별 건수
uv run python -m lawbot.log_summary --caller lawbot-app  # 평가 실행 제외
```

오류 유형은 화면 안내와 같습니다. `model_server`(서버 연결 실패·토큰 불일치·로딩 중)는 "모델 서버가 준비되지 않았습니다", `model_load`(모델 폴더·system 파일 없음)는 기존 "모델을 불러오지 못했습니다"로 안내합니다. 리랭킹·생성 오류는 임베딩 순위로 되돌리지 않고 그대로 오류로 표시합니다.

> 주의: 비밀번호를 소스 코드, 셸 설정 파일, 스크립트, `.env.example` 등 Git에 올라가는 파일에 적지 마세요. 파일에 둘 때는 Git에서 제외된 `.env`(권한 600)에만 두고, 더 안전하게는 실행할 때 입력하거나 macOS 키체인을 쓰세요.

**테스트 실행**

기본 테스트는 모델 파일, 네트워크, 실제 비밀번호 없이 돌아갑니다. `uv sync`만 하면 됩니다. 실제 모델 대신 가짜 임베딩·sLLM을 넣고, 테스트 중 외부 네트워크 접속은 막습니다. `data/`·`results/`는 읽기만 합니다.

```bash
uv run pytest              # 기본 테스트 전체 (모델 불필요)
uv run pytest -m models    # 실제 모델 테스트 (models/ 필요, 기본 실행에서 제외)
```

GitHub Actions(`.github/workflows/ci.yml`)가 `main` 푸시와 모든 pull request에서 macOS(Apple Silicon) 러너로 `uv sync --locked`, 문법 검사(`compileall`), `uv run pytest`를 실행합니다. 비밀정보는 쓰지 않으며, `-m models` 테스트와 평가 스크립트는 CI에서 실행하지 않습니다. 결과는 PR의 체크와 Actions 로그, `pytest-report` 아티팩트에서 볼 수 있습니다.

**평가 실행 (v4 웹앱 구성)**

`lawbot.eval_v4`가 `.env` 구성 그대로(v4 검색 415조문 + 리랭커 + 답변 모델) 웹앱과 같은 경로로 평가합니다. 문항은 `data/v4/qa_full_v4.csv`·`split_v4.csv`의 test 2,075문항(근로기준법 A_lsa 650 + 신규 6개 법률 B_newlaw 1,425)입니다. 결과는 `results/v4/<단계>_<tag>.csv`에 문항마다 바로 쓰므로 중간에 멈춰도 같은 명령으로 이어집니다.

```zsh
uv run lawbot-eval-v4                       # 전체: 아래 네 단계를 차례로 실행한 뒤 요약 (오래 걸림)
uv run lawbot-eval-v4 retrieval             # test 2,075 전체: 임베딩 Recall@1·3·10, 리랭킹 Recall@1·3·MRR@3
uv run lawbot-eval-v4 answers               # 고정 시드 표본 200 (A_lsa·B_newlaw 100씩): 인용·거짓 거절, A_lsa는 키워드 채점
uv run lawbot-eval-v4 oos                   # 범위 밖 102 (7개 법률 출처 27문항은 범위 안으로 재분류) + 대조군 20
uv run lawbot-eval-v4 refusal               # 표본 중 100문항에 다른 법률 조문 / 비슷한 조문만 줄 때 거절률
uv run lawbot-eval-v4 summary --tag pro_concise_rr   # 결과 파일로 지표 표만 다시 → results/v4/summary_<tag>.csv
```

- tag 기본값은 `<모델>_<형식>_<rr|emb>`입니다(예: `pro_concise_rr`). `--fmt full`이면 4항목 system으로 생성합니다.
- 인용은 법령명까지 맞아야 정답입니다(`근로기준법 제26조`와 `최저임금법 제26조`를 구분). 법령명 없이 이어 쓴 번호(`근로기준법 제26조, 제27조`)는 앞 법령으로 봅니다. 줄임말(`퇴직급여법`, `고평법` 등)도 인정하고, `근로기준법 시행령 제5조`는 법률 조문과 다른 조문으로 봅니다.
- 인용·거절 판정은 웹앱 화면과 같은 `lawbot.citation` 하나를 씁니다. `citation.check`는 인용마다 `ok`(검색 결과 안) / `outside`(존재하지만 검색 결과 밖) / `wrong_law`(같은 번호, 다른 법령) / `nonexistent`(415조문에 없음) / `ambiguous`(법령명 없이 번호가 겹침)를 돌려줍니다.
- `summary`는 저장된 답변 원문으로 채점 열만 다시 계산합니다. 채점 규칙이 같으면 결과 파일은 바뀌지 않습니다.
- 두 실행의 답변 원문이 같은지는 `uv run python -m lawbot.diff_answers <기준.csv> <비교.csv>`로 봅니다(id로 맞춰 글자 단위 비교, 다르면 첫 차이 위치 출력, 모두 같으면 종료 코드 0).
- 2026.4.7 근로기준법 개정으로 임금체불 벌칙이 제109조(3년·3천만원)에서 제107조(5년·5천만원)로 옮겨졌습니다. v4 정답은 개정 후 기준이고, 키워드가 개정 전 형량인 2문항(Q2443·Q2453)만 평가 때 개정 후 형량으로 바꿉니다(`eval_v4.KEYWORD_FIX`, 평가 파일은 그대로).
- 범위 밖 질문 중 남녀고용평등법·최저임금법·퇴직급여법·기간제법·임금채권보장법·파견법이 출처인 27문항은 이제 범위 안이므로 거절하면 오답, 그 법을 인용하면 정답으로 봅니다. 나머지 75문항만 거절률을 잽니다.

아래는 이전 v1(근로기준법 130조문, 4B) 평가 스크립트입니다. README 성능 수치와 `results/`의 기준선이 이 결과입니다.

```bash
uv run lawbot-eval --n 0      # 검색 성능만 (650문항)
uv run lawbot-eval --n 100    # 검색 + 답변 생성·채점 (무작위 100문항), 검색 순위는 results/retrieval_<임베딩폴더>.csv
uv run python -m lawbot.summ_dev scored_sllm_mlx_4bit.csv   # 위 결과의 거짓 거절률·검색 실패 시 거절률
uv run python -m lawbot.eval_oos       # 범위 밖 질문 102개 거절률·억지 적용률 → results/oos_v2_4bit.csv
uv run python -m lawbot.eval_refusal   # 무관한 조문 / 비슷한 조문만 줄 때 거절률 (각 100문항) → results/refusal_v2_4bit.csv
uv run python -m lawbot.eval_refusal --hard   # 근거 없이 주장했던 81건을 같은 참고 조문으로 다시 → results/refusal_hard_4bit.csv
```

문항별 결과는 `results/`에 저장됩니다. 마지막 인자로 파일 이름을 바꿀 수 있습니다(예: `uv run python -m lawbot.eval_oos oos_v3_4bit.csv`). `oos_v1_4bit.csv`(82문항)와 `refusal_v1_4bit.csv`는 README 수치와 테스트의 기준선이므로 기본 설정으로는 덮어쓰지 않습니다. `eval_oos`와 `eval_refusal`은 `LLM_PATH`의 모델을 씁니다.

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
# 기준선 보강: 현재 모델로 한 번 (기록된 v1_4bit 행에는 Recall@3·대조군·102문항·재평가가 없음)
uv run lawbot-eval --n 0                                                        # results/retrieval_emb_final_v3_fp16.csv
uv run lawbot-eval --ids data/scope_controls.csv --out scored_controls_v1_4bit.csv
uv run python -m lawbot.eval_oos oos_v1_4bit_102.csv
uv run python -m lawbot.eval_refusal --hard refusal_hard_v1_4bit.csv
uv run python -m lawbot.report --tag v1_4bit_full --retrieval retrieval_emb_final_v3_fp16.csv \
  --scored scored_sllm_mlx_4bit.csv --controls scored_controls_v1_4bit.csv --oos oos_v1_4bit_102.csv \
  --refusal refusal_v1_4bit.csv --hard refusal_hard_v1_4bit.csv --baseline v1_4bit

# 개선한 모델: 같은 평가를 다른 파일 이름으로 돌리고 기준선과 비교 (--strict면 경고 시 종료 코드 1)
export LLM_PATH=models/sllm_v3_mlx_4bit
uv run lawbot-eval --llm $LLM_PATH --n 100 --out scored_v3_4bit.csv
uv run lawbot-eval --llm $LLM_PATH --ids data/scope_controls.csv --out scored_controls_v3_4bit.csv
uv run python -m lawbot.eval_oos oos_v3_4bit.csv
uv run python -m lawbot.eval_refusal refusal_v3_4bit.csv
uv run python -m lawbot.eval_refusal --hard refusal_hard_v3_4bit.csv
uv run python -m lawbot.report --tag v3_4bit --retrieval retrieval_emb_final_v3_fp16.csv \
  --scored scored_v3_4bit.csv --controls scored_controls_v3_4bit.csv --oos oos_v3_4bit.csv \
  --refusal refusal_v3_4bit.csv --hard refusal_hard_v3_4bit.csv --baseline v1_4bit_full --strict
```

`metrics.csv`에는 기존 결과로 만든 `v1_4bit`(기준선)과 `v1_4bit_hybrid`(실패한 하이브리드 검색 실험) 두 행이 들어 있습니다. 하이브리드 검색은 범위 밖 거절률이 0.671 → 0.683으로 올랐지만 억지 적용률도 0.256 → 0.268로 올라, 거절률만으로는 개선이라고 볼 수 없었습니다.

### 프로젝트 구조

```
src/lawbot/
├── paths.py           # 데이터·모델 경로
├── retriever.py       # 임베딩 검색
├── llm.py             # 프롬프트 구성, MLX 생성
├── service.py         # UI와 무관한 질의응답 흐름 (모델 로딩, 검색 → 답변 스트리밍)
├── app.py             # Gradio 웹앱
├── security.py        # 로컬 전용 실행 설정 (127.0.0.1, 로그인 필수, 공유 링크 없음, 포트·모델 서버 주소 검증)
├── prompts.py         # v1 기본 system 문구 (모델 라이브러리 없이 읽음)
├── model_server.py    # 내부 모델 서버: 임베딩·리랭커·답변 모델을 한 번만 올려 웹앱들이 공유 (127.0.0.1, 토큰)
├── remote.py          # 원격 모드 웹앱의 모델 서버 클라이언트 (LAWBOT_MODEL_SERVER)
├── citation.py        # 인용 파싱·형식 검증·거절 판정 (웹앱 화면과 eval_v4 채점이 같이 씀)
├── manifest.py        # 법령 데이터 매니페스트 검사 (조문 수·sha256·기준일)
├── log_summary.py     # 요청 로그(logs/requests.jsonl) 요약
├── eval_v4.py         # v4 웹앱 구성 평가 (lawbot-eval-v4)
├── diff_answers.py    # 두 평가 결과의 답변 원문 글자 단위 비교
├── eval_dev.py        # 검색·답변 평가
├── score_answers.py   # 키워드 기반 채점기
├── refusal.py         # 거절 판정 (【결론】에 '확인할 수 없'), 범위 밖 답변 판정 (거절·안내·억지적용·미인용)
├── summ_dev.py        # eval_dev 결과의 거절 지표 요약
├── report.py          # 평가 결과를 metrics.csv 한 행으로 모으고 기준 실행과 비교
├── eval_oos.py        # 범위 밖 질문 거절률
├── eval_refusal.py    # 무관한 / 비슷한 조문 제공 시 거절률, --hard: 근거 없이 주장했던 사례 재평가
├── diag_oracle.py     # 정답 조문 제공 조건 답변 평가
├── diag_fail.py       # 검색은 맞았지만 인용이 틀린 문항 분석
└── diag_pos.py        # 정답 조문 위치별 인용 정확도 분석
deploy/launchd/        # 상시 운영용 launchd 템플릿 (모델 서버·웹앱)
```

### 데이터와 평가 방법

- **조문**: 근로기준법 현행 130개 조문. 삭제 조문 2개는 제외했고, 기준일은 2026-10-01입니다.
- **v4 조문**: 근로기준법 등 7개 법률의 비삭제 415개 조문(`data/v4/articles_v4.csv`). [Legalize KR](https://github.com/legalize-kr/legalize-kr)에서 2026-10-20에 수집했고, 버전·기준일·sha256은 `data/v4/manifest.json`에 있습니다. 시행령·시행규칙을 더한 실험용 812조문(`data/v4/law_articles_v4_decree.csv`)은 아직 웹앱에 쓰지 않습니다.
- **질문**: 총 2,602개(130조문 × 질문 유형 5종 × 약 4개)입니다.
  - 질문 유형: 구어체, 사용자(사업주) 관점, 간접 질문, 키워드, 상황 설명
  - 테스트: 650개(조문 × 유형마다 1개)
  - 학습·검증: 나머지 1,952개
  - 모델 선택과 하이퍼파라미터 결정은 검증 셋으로만 했습니다.
- **답변 채점**: 문항마다 필수 키워드 그룹, 금지 표현, 정답 조문 번호를 지정해 자동 채점합니다. 제43조와 제43조의2처럼 비슷한 번호는 구분합니다. 거절 답변은 `해당 없음 (제공된 조문: …)`에 정답 번호가 있어도 인용으로 보지 않습니다. `cite_strict`는 【근거 조문】에 정답이 있고 헷갈리는 조문이나 번호가 비슷한 조문을 함께 인용하지 않았는지를 따로 기록합니다(종합 점수에는 반영하지 않음). 다른 법의 같은 번호(예: 근로자퇴직급여 보장법 제34조)는 구분하지 못합니다. 평가 파일의 모범 답변은 650문항 모두 1.0점입니다.
- **범위 밖 질문**: 근로기준법으로 답할 수 없는 질문 102개로 거절률을 따로 잽니다(`data/oos_eval.csv`).
  - 2026-10-19에 근로기준법과 개념이 겹치는 다른 노동법 질문 20개(OOS083–102)를 추가했습니다. 근거 조문(`note`)은 [Legalize KR](https://github.com/legalize-kr/legalize-kr) 법령 원문으로 확인했습니다. 위 성능 수치는 추가 전 82문항 기준입니다.
  - 답변은 거절, 안내(`refer_lsa`에 적힌, 다른 법에 맡기는 조문만 인용), 억지적용(그 밖의 근로기준법 조문으로 답함), 미인용(거절 문구도 인용도 없음)으로 판정합니다. `near_lsa`는 README 수치의 기준이고, `near_lsa_v2`는 누락을 보완한 표시입니다.
- **범위 안 대조군**: 다른 노동법 질문과 닮았지만 근로기준법으로 답해야 하는 테스트 문항 20개입니다(`data/scope_controls.csv`, `twin_oos`에 짝 문항). 범위 밖 거절을 늘리는 개선이 이 문항들까지 거절하게 만들지 않는지 확인합니다.
- **근거 없는 주장 사례**: `refusal_v1_4bit.csv`에서 거절하지 않은 86건을 억지적용 77, 지어냄 4, 근거밖_정답 2, 결론타당 3으로 분류했습니다(`data/refusal_hard_cases.csv`). 억지적용·지어냄 81건은 `eval_refusal --hard`로 다시 평가합니다.
- **평가 데이터 사용 제한**: `data/labor_eval_keywords.csv`, `data/oos_eval.csv`, `data/scope_controls.csv`, `data/refusal_hard_cases.csv`와 `data/v4/split_v4.csv`의 test 문항은 평가 전용입니다. 모델 학습이나 설정 선택에 쓰지 마세요.
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
- **향후 계획**:
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
         └─ Answer: Qwen3.8-27B (not fine-tuned, MLX 4-bit, concise) / small Macs: Qwen3-4B + v4 LoRA
             └─ Answer + source article panel (Gradio)
```

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

**v4 web app setup (415 articles from 7 laws · reranker · Qwen3.8-27B 4-bit)**

Measured on 2026-10-25 on a 32GB Mac mini with `uv run lawbot-eval-v4` (per-question results in `results/v4/*_pro_concise_rr.csv`), using the `.env.example` setup: fine-tuned Qwen3-Embedding-0.6B top 10 → Qwen3-Reranker-4B (not fine-tuned, MLX 4-bit) top 3 → Qwen3.8-27B (not fine-tuned, MLX 4-bit, concise answers). Questions are the 2,075 v4 test questions (650 Labor Standards Act + 1,425 from the six added laws), never used for training or validation.

| Retrieval (2,075 test) | Recall@1 | Recall@3 | Recall@10 | MRR |
| --- | --- | --- | --- | --- |
| Fine-tuned embedding | 0.765 | 0.913 | 0.976 | 0.845 (@10) |
| + reranker (10 → 3) | **0.829** | **0.940** | - | **0.880** (@3) |

These match the Colab (L4, fp16) run on the same test set within 0.002. On the same val questions and embedding top 10 (1,110 questions), the MLX 4-bit reranker gives Recall@1 0.819 / Recall@3 0.946 vs Colab 0.836 / 0.950; the top-1 article agrees 90% of the time, but quantization changes the raw scores a lot (median max score difference 0.28). Reranking takes 1.93 s per question (p95 3.04 s) vs 1.01 s on Colab L4.

| Answers (200 sampled: 100 + 100) | All | Labor Standards Act | Six added laws |
| --- | --- | --- | --- |
| Gold article in retrieved top 3 | 0.915 | 0.93 | 0.90 |
| Citation accuracy (law name included) | 0.905 | 0.91 | 0.90 |
| False refusal rate | 0.065 | 0.07 | 0.06 |
| False refusal rate (retrieval hits) | 0.011 | 0.022 | 0.000 |
| Keyword group coverage | - | 0.853 | - |
| Overall score (retrieval hits) | - | 0.828 (0.891) | - |

| Refusal behavior | Value | Questions |
| --- | --- | --- |
| Out-of-scope refusal rate | **0.96** | 75 |
| Out-of-scope forced answers (cites an article instead of refusing) | 0.04 | 75 |
| Refusal with only other-law articles | **1.00** | 100 |
| Refusal with only similar articles | 0.69 | 100 |
| Former out-of-scope questions now in scope: false refusal / cites that law | 0.111 / 0.889 | 27 |
| In-scope controls: false refusal / gold citation | 0.10 / 0.80 | 20 |

The 27 questions whose source is one of the seven laws are now in scope, so the out-of-scope set differs from v1 (82 questions, 0.707). Two of the three false refusals there (this year's minimum wage amount, how to apply for reduced hours during childcare) concern details set by notice or decree rather than the statute text. The untuned 27B refuses in its own words ("근거를 찾을 수 없습니다", "답변할 수 없습니다"), so the v4 evaluation also counts those phrases (`citation.REFUSAL`); this added no refusals among the 200 in-scope answers. The concise web view uses the same check, so such refusals show no citation line or warning. Keyword scoring uses the existing 650-question keyword set, with two rows updated for the 2026-04-07 amendment. Scores are not directly comparable with v1 (4B, four-part answers, 130 articles, different sample).

Speed and memory (200 answers): embedding search 0.03 s, reranking 1.95 s (p95 3.1 s), first token 2.8 s (median, from generation start), 15.1 s per question end to end (p95 20.9 s), 9.7 tokens/s, 165 characters (96 tokens) per answer, MLX peak memory 20.4GB (27B + reranker).

Rechecked in the production setup (remote mode, 2026-10-29): the web app talking to the model server reran answers, out-of-scope and refusal, and `lawbot.diff_answers` found all 522 answers identical character for character (200 · 122 · 200), so the accuracy numbers above stand. End-to-end time averaged 14.9 s, first token 2.78 s (median) and MLX peak memory 20.4GB, with no visible HTTP overhead (`results/v4/*_pro_concise_rr_remote.csv`).

**v1 (130 Labor Standards Act articles · Qwen3-4B LoRA)**

Unless noted otherwise, all numbers are on the 650-question test set, which was never used for training or model selection. Measured from 2026-10-16 to 2026-10-18.

**Retrieval (130 articles)**

| Model | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR |
| --- | --- | --- | --- | --- | --- |
| Qwen3-Embedding-0.6B (base) | 0.622 | - | 0.837 | 0.888 | 0.718 |
| Fine-tuned (hard negatives) | **0.789** | **0.940** | **0.962** | **0.986** | **0.868** |

Question classification over 130 classes (logistic regression):

| Model | Accuracy | Top-3 | MRR |
| --- | --- | --- | --- |
| TF-IDF (char 2-4 grams) | 0.655 | 0.858 | 0.767 |
| Base embedding | 0.789 | 0.926 | 0.865 |
| Fine-tuned embedding | **0.855** | **0.963** | **0.911** |

**Answer generation (Colab, bf16)**

Total score = keyword-group coverage × correct article citation × (0.5 if a forbidden phrase appears). The maximum is 1.0, and refusals score 0. The Colab numbers below were measured before the 2026-10-19 scorer fix (refusals listing the provided articles were counted as citations). The fix only affects false refusals, so its effect is at most 0.005.

| Setting | Citation accuracy | Keyword coverage | Total score |
| --- | --- | --- | --- |
| Qwen3-4B (base), gold articles | 0.955 | 0.894 | 0.866 |
| Qwen3-4B + LoRA, gold articles | **0.983** | **0.967** | **0.961** |
| Qwen3-4B + LoRA, retrieved articles (production setting) | 0.869 | 0.884 | 0.854 |
| Same, questions where retrieval hit only | - | - | 0.908 |

**Refusal behavior**

| Metric | Base | Fine-tuned |
| --- | --- | --- |
| Refusal rate given only unrelated articles (100 questions) | 0.11 | **0.91** |
| False refusal rate when the gold article is present | 0.002 | 0.005 |
| Refusal rate on out-of-scope questions (82, retrieved articles) | 0.098 | **0.707** |
| Refusal rate given only similar but non-answering articles (100) | - | 0.20 |

The out-of-scope set has 25 everyday questions, 32 questions governed by other labor laws (severance pay, minimum wage, equal employment, and so on), and 25 questions on other laws (`data/oos_eval.csv`). The 4-bit MLX model scores 0.671 and 0.24 on the last two rows, close to bf16. Questions under other labor laws that resemble a Labor Standards Act article are refused only 25% of the time even after fine-tuning, the largest remaining weakness. Of the 32 other-labor-law questions, 5 non-refusals only cite an article that delegates to another law (retirement benefits, Article 34; safety and health, Article 76) and point to that law, while 13 (0.406) force a Labor Standards Act article onto the question, such as answering an industrial-accident leave benefit question with Article 79. The forced-answer rate over all 82 questions is 0.256 (`refusal.oos_verdict`).

**Local inference (MacBook Air 8 GB, MLX 4-bit, 100 dev questions)**

| Metric | Value |
| --- | --- |
| Total score (retrieved / gold articles) | 0.812 / 0.937 |
| Time to first token (median) | 5.4 s |
| Generation speed | 24.5 tokens/s |
| Time per question | 20.4 s |
| Peak SLM memory | 3.16 GB |

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

The previous v1 setup (130 Labor Standards Act articles) runs with `LAWBOT_PROFILE=v1` and uses the files below. The article and evaluation data (`data/`) and evaluation results (`results/`) are included in the repository; the evaluation scripts (`lawbot-eval` and others) use the v1 files.

```
models/
├── emb_final_v3_fp16/     # v1 fine-tuned embedding
├── sllm_mlx_4bit/         # v1 fine-tuned SLM = v1 Fast
└── qwen3_14b_mlx_4bit/    # base Qwen3-14B = v1 Pro (system: data/system_v1_concise.txt)
```

To check that the files match the web app settings without loading any model (problems are marked `FAIL`):

```bash
uv run python -m lawbot.check_deploy        # current profile (default v4; v1 if LAWBOT_PROFILE=v1)
uv run python -m lawbot.versions            # installed package versions and Mac specs
```

**Run the web app (settings file .env)**

Settings in a `.env` file at the repository root are read when `uv run lawbot-app` starts. Copy `.env.example` (32GB Mac mini defaults: v4, 0.6B embedding, 10 candidates → 4-bit reranker → 3, Pro 27B only) and fill in only the username and password:

```zsh
cp .env.example .env
chmod 600 .env            # owner-only (it holds the password)
open -e .env              # fill LAWBOT_USER= and LAWBOT_PW=, then save
uv run lawbot-app
```

`.env` is git-ignored. Variables already exported in the shell win over the file (e.g. `LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app` for a one-off run). `LAWBOT_ENV_FILE=<file>` picks another file and `LAWBOT_ENV_FILE=` disables it. Only `LAWBOT_*` and `LLM_PATH` are allowed in the file. `lawbot-model-server`, `check_deploy`, `bench_models`, `bench_ui`, `verify_reranker` and `eval_concise` read the same file. To keep the password out of the file, delete the `LAWBOT_PW=` line and use `read -rs 'LAWBOT_PW?Password: ' && export LAWBOT_PW` or the macOS Keychain (`LAWBOT_PW="$(security find-generic-password -a lawbot -s lawbot -w)" uv run lawbot-app`).

If the v4 files (article CSV, 4B system prompt) are missing, the app refuses to start before loading any model and says what is missing. Until v4 is ready, run v1:

```zsh
LAWBOT_PROFILE=v1 LAWBOT_MODELS= LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app
```

`LAWBOT_MODELS` selects the answer models used on this machine (`.env.example`: `pro` only). Models left out are not loaded and appear disabled in the UI ("이 기기에서 사용 안 함"; selecting one switches back to the default). With both (`fast,pro`) you choose in the UI, and both use the same retrieval results (top 3 articles). Fast (trained on the four-part format) always generates four parts and "간결" only trims the display; Pro (concise by default) regenerates with the four-part system prompt (`data/v4/system_v4.txt`) when "전체 (4항목)" is selected.

| Model | Setup (v4 default paths) | Default answer format |
| --- | --- | --- |
| Lawbot Fast | Qwen3-4B + LoRA (`models/v4/sllm_4b_lora_mlx_4bit`) | Full: four parts (Conclusion, Cited articles, Explanation, Notes) |
| Lawbot Pro | Qwen3.8-27B 4-bit, untuned (`models/v4/qwen3.8_27b_mlx_4bit`) with a draft concise system prompt (`data/v4/system_v4_concise.txt`) | Concise: conclusion + `근거:` the articles actually cited. Slower |

Switching models also switches the answer format to that model's default; you can change the format afterwards. A status line under the chat shows retrieval, model switch, first token, first character, generation speed and total time. If the Pro folder or Pro system prompt is missing, the app runs with Fast only and shows an error when Pro is selected.

Change model paths or memory settings in `.env` (relative paths are resolved from the repository root); the table below lists every variable.

The web app is local-only, for use on this Mac. The address is fixed to `127.0.0.1`; the port is `LAWBOT_PORT` in `.env` (the previous default if unset). Open `http://127.0.0.1:<port>` in a browser and log in. Other devices on the network cannot connect, and no public Gradio share link is created. The address and share setting cannot be changed, and Gradio variables such as `GRADIO_SERVER_NAME` and `GRADIO_SERVER_PORT` are ignored (`src/lawbot/security.py`). For an always-on setup, set `LAWBOT_PORT` explicitly in `.env`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `LAWBOT_USER` | Yes | Login username (must not be empty after trimming whitespace) |
| `LAWBOT_PW` | Yes | Login password (must not be empty or whitespace only) |
| `LAWBOT_PROFILE` | No | `v4` (default, 415 articles from 7 laws) or `v1` (130 Labor Standards Act articles, previous setup) |
| `LAWBOT_FAST_PATH` | No | Fast (4B) model folder. Default v4 `models/v4/sllm_4b_lora_mlx_4bit`, v1 `models/sllm_mlx_4bit` |
| `LAWBOT_PRO_PATH` | No | Pro model folder. Default v4 `models/v4/qwen3.8_27b_mlx_4bit` (27B), v1 `models/qwen3_14b_mlx_4bit` (14B) |
| `LAWBOT_MODELS` | No | Comma-separated answer model keys to use (`pro`, `fast`, `fast,pro`); the first is the default. Empty: both |
| `LAWBOT_ENV_FILE` | No | Settings file to read (default `.env` at the repo root, skipped if absent); empty disables it. Shell only |
| `LAWBOT_PRO_SYSTEM` | No | Pro concise system prompt file. Default v4 `data/v4/system_v4_concise.txt`, v1 `data/system_v1_concise.txt` |
| `LAWBOT_V4_ARTICLES` / `LAWBOT_V4_EMB` / `LAWBOT_V4_SYSTEM` | No | v4 article CSV / embedding folder / 4B system prompt file (defaults `data/v4/articles_v4.csv`, `models/v4/emb_v4_fp16`, `data/v4/system_v4.txt`) |
| `LAWBOT_EMB` | No | v1 retrieval embedding folder (default `models/emb_final_v3_fp16`) |
| `LAWBOT_MAX_RESIDENT` | No | Answer models kept in memory: `2` (default, Fast and Pro both resident, no switch wait) or `1` (selected model only, reloads on switch) |
| `LAWBOT_EARLY_STOP` | No | `1` stops generation when 【설명】 starts in the concise format (off by default, not yet validated) |
| `LAWBOT_RERANKER_ENABLED` | No | `1` re-ranks the embedding candidates with Qwen3-Reranker-4B and keeps the top 3 (default `0`: no reranker is loaded, plain embedding top 3). v4 profile only |
| `LAWBOT_RERANKER_MODEL_PATH` | No | Reranker MLX folder (default `models/v4/reranker_4b_mlx_4bit`) |
| `LAWBOT_RERANKER_CANDIDATES` | No | Embedding candidates passed to the reranker (default `10`, the experiment value; at least 3) |
| `LLM_PATH` | No | SLM folder used by the evaluation scripts (`eval_oos`, `eval_refusal`), e.g. `models/sllm_mlx_8bit`. Not used by the web app |
| `LAWBOT_PORT` | No | Web app port (1024–65535). Previous default if unset. Use different values when running production and development together |
| `LAWBOT_INSTANCE` | No | Name appended to the page and tab title (e.g. `운영`, `개발`; at most 40 characters) |
| `LAWBOT_MODEL_SERVER` | No | Model server URL `http://127.0.0.1:<model server port>`. Set: remote mode (the web app loads no models). Empty: local mode |
| `LAWBOT_MODEL_SERVER_PORT` | Model server | Model server port (1024–65535, no default) |
| `LAWBOT_MODEL_TOKEN` | Model server, remote mode | Model server token (16+ characters, `openssl rand -hex 32`); same value for web apps and server |
| `LAWBOT_MODEL_SERVER_WAIT` | No | Seconds a remote-mode web app waits at startup for the model server to be ready (default `300`) |
| `LAWBOT_REQUEST_LOG` | No | Request log file (default `logs/requests.jsonl` at the repo root). Empty disables it |

If either `LAWBOT_USER` or `LAWBOT_PW` is missing or empty, the app refuses to start with a `RuntimeError` before loading any model. There is no way to run the server without a login.

**Reranker (optional, v4)**

When enabled, retrieval becomes `embedding top 10 → Qwen3-Reranker-4B (not fine-tuned) → reranked top 3 → answer model`. Fast and Pro share the reranked context, and the reranker is loaded once at startup and stays resident. When disabled (default) it is not loaded and the app uses the embedding top 3 as before. Scoring (instruction, prompt, 2,560-token limit, yes/no logits, ties kept in embedding order) matches the v4 reranker experiment notebook (`src/lawbot/reranker.py`).

`.env.example` enables it (`LAWBOT_RERANKER_ENABLED=1`); see "Model setup" above for the conversion command.

```zsh
uv run python -m lawbot.check_deploy           # with .env: reranker folder, quantization, yes/no tokens
uv run pytest -m models tests/test_reranker_models.py   # real load + scoring smoke test
LAWBOT_RERANKER_ENABLED=0 uv run lawbot-app    # one-off run without it
```

Reranker errors (e.g. NaN scores) are shown as errors instead of silently falling back to the embedding order. A missing folder, or enabling it with the v1 profile, stops the app before any model is loaded. Agreement with the Colab run (transformers fp16) is checked separately on the same questions and embedding top 10 with `uv run python -m lawbot.verify_reranker --colab <v4 folder> --split val`; latency and memory with `lawbot.bench_models` run once with the reranker on and once off.

**Always-on production plus development (model server)**

To keep a production web app running on the Mac mini (reached from a phone over Tailscale) while also running a development web app on the same Mac, both web apps share one internal model server (`lawbot-model-server`) that loads the embedding model, reranker and answer models once. Loading 27B and the reranker in every web app would exceed 32 GB. With `LAWBOT_MODEL_SERVER` empty, the web app loads its own models as before (local mode).

```
phone ──(Tailscale HTTPS, tailscale serve)──▶ 127.0.0.1:<prod port>   production web app (Gradio, no models)
Mac browser ────────────────────────────────▶ 127.0.0.1:<dev port>    development web app (Gradio, no models)
                                                  │  both: HTTP + token
                                                  ▼
                                    127.0.0.1:<model server port>  model server (one)
```

| Process | Port | Runs from | Exposed |
| --- | --- | --- | --- |
| Production web app | `<prod port>` | production worktree | only this port, via `tailscale serve` |
| Development web app | `<dev port>` | development repo | no |
| Model server | `<model server port>` | production worktree | no (fixed to 127.0.0.1) |

Choose the port numbers yourself and write them only in each `.env` (never in the repository); keep the production and development ports far apart and give the model server its own. The model server listens only on 127.0.0.1 and refuses to start without a token or port. Questions are not written to its log (path, model and timing only).

1. Create a token once and put the same value in `LAWBOT_MODEL_TOKEN` of both `.env` files: `openssl rand -hex 32`
2. Create the production worktree from the tag to deploy (once): `git worktree add --detach ~/lawbot-prod <tag>`, then in it `ln -s ~/lawbot/models models`, `cp ~/lawbot/.env .env && chmod 600 .env` (edit as in the production block at the end of `.env.example`) and `uv sync --frozen`. The production `.env` sets `LAWBOT_INSTANCE=운영`, `LAWBOT_PORT`, `LAWBOT_MODEL_SERVER_PORT`, `LAWBOT_MODEL_SERVER` and `LAWBOT_MODEL_TOKEN`; the development `.env` follows `.env.dev.example` (`LAWBOT_INSTANCE=개발`, `LAWBOT_PORT`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN`). `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN` and `LAWBOT_PROFILE` must match.
3. Check by hand: `uv run lawbot-model-server` (opens the port first, prints `ready` once the models are loaded), `uv run python -m lawbot.check_deploy` (model files plus server reachability, token, API version and profile), `uv run lawbot-app` (remote mode waits up to `LAWBOT_MODEL_SERVER_WAIT` seconds for the server).
4. Run both under launchd (once). Fill the placeholders in the templates; secrets and ports stay in `.env`, not in the plist.

   ```zsh
   mkdir -p ~/Library/Logs/lawbot
   for k in model-server web; do
     sed -e "s|__UV__|$(which uv)|g" -e "s|__PROD_DIR__|$HOME/lawbot-prod|g" -e "s|__LOG_DIR__|$HOME/Library/Logs/lawbot|g" \
       deploy/launchd/com.lawbot.$k.plist.example > ~/Library/LaunchAgents/com.lawbot.$k.plist
     launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.$k.plist   # register and start
   done
   launchctl kickstart -k gui/$(id -u)/com.lawbot.web          # restart (also com.lawbot.model-server after server-side changes)
   launchctl bootout gui/$(id -u)/com.lawbot.web               # unregister
   ```

   launchd restarts a process that exits (`KeepAlive`); if model loading fails the server exits with code 1 and is retried every 30 seconds (`ThrottleInterval`). Keep the Mac from sleeping.
5. Expose only the production web app port inside the tailnet over HTTPS with `tailscale serve --bg <prod port>` (check the argument form with `tailscale serve --help`). Do not use `tailscale funnel`.

Constraints:

- Web-app-side changes (`app.py`, `service.ask`, `answer_format.py`, system prompt files) are tested by restarting only the development web app; the web app sends its own system prompt with every request.
- Server-side changes (prompt assembly in `llm.py`, `reranker.py`, `retriever.py`, `model_server.py`) need a development model server: stop the production one, start one from the development repo on another port (`LAWBOT_MODEL_SERVER_PORT=<test port> uv run lawbot-model-server`) and point the development web app at it. Run only one model server at a time because of memory.
- The model server handles one request at a time, so a phone question waits while a development question is generating. Stop buttons and concise early stop disconnect and free it at once; a phone that disappears without disconnecting holds it until generation reaches the length limit (1024 tokens).
- Retrieval settings (reranker, candidate count) and resident model count come from the model server (production `.env`); a remote-mode web app ignores `LAWBOT_RERANKER_*` and `LAWBOT_MAX_RESIDENT`. At startup it checks that profile, `doc_style`, model keys and API version match the server and refuses to start otherwise.
- If login cookies clash when switching between production and development in one browser, open development at `http://localhost:<dev port>` (a different host keeps separate cookies).
- `eval_v4`, `eval_concise` and `bench_models` also run in remote mode; their `peak_gb` and MLX memory are the model server's. `verify_reranker`, `eval_dev`, `eval_refusal`, `eval_oos` and `diag_*` load models directly (local mode only), so stop the model server first.

**Law data version (manifest)**

A `manifest.json` next to each article CSV (`data/v4/manifest.json`, `data/manifest.json` for v1) records the data version, reference and collection dates, source, article counts (per law and per kind), the CSV's SHA-256, and the data version the embedding and 4B LoRA were trained on. The search index is built when the model server starts rather than stored, so these values are what tie the data to the models.

- The model server (and a local-mode web app) checks the CSV at startup and refuses to start, before loading any model, if the count or SHA-256 differs. A missing manifest only warns.
- `uv run python -m lawbot.check_deploy` shows the same check as OK/FAIL; articles effective after the collection date are a WARN.
- The model server's `/info` reports `dataset` (version, reference date, SHA-256); a remote-mode web app warns, without refusing, when it differs from its own repo's manifest.
- When the article CSV changes, update the manifest with a new data version and restart both the model server and the web app.

**Request log and error messages**

The web app appends one line per question to `logs/requests.jsonl` (git-ignored): stage timings (search, rerank, model switch, first token, total), token counts, `peak_gb`, status (`ok`/`error`/`cancelled`) and error type. It never stores the question, the answer or article text. Evaluation scripts go through the same path and are told apart by `caller`.

```zsh
uv run python -m lawbot.log_summary                      # counts by status, per-stage median and p95, error types
uv run python -m lawbot.log_summary --caller lawbot-app  # exclude evaluation runs
```

Error types match the on-screen messages: `model_server` (unreachable server, token mismatch, still loading) shows "the model server is not ready", `model_load` (missing model folder or system file) keeps the existing load-failure message. Reranking and generation errors are shown as errors, never silently replaced by the embedding order.

> Warning: never put the password in source code, shell profiles, scripts, `.env.example` or any other file that goes into Git. If you keep it in a file, use only the git-ignored `.env` (mode 600); typing it at launch or the macOS Keychain is safer.

**Run the tests**

The default tests need no model files, network access or real password; `uv sync` is enough. They use fake embedding and SLM models, block outbound network access, and only read `data/` and `results/`.

```bash
uv run pytest              # all default tests (no models needed)
uv run pytest -m models    # real-model tests (needs models/, excluded by default)
```

GitHub Actions (`.github/workflows/ci.yml`) runs `uv sync --locked`, a syntax check (`compileall`) and `uv run pytest` on a macOS (Apple Silicon) runner for every push to `main` and every pull request. It uses no secrets; `-m models` tests and the evaluation scripts are not run in CI. Results appear as PR checks, in the Actions log and as the `pytest-report` artifact.

**Run the evaluation (v4 web app setup)**

`lawbot.eval_v4` evaluates the `.env` setup (v4 retrieval over 415 articles + reranker + answer model) through the same path as the web app, on the 2,075 test questions in `data/v4/qa_full_v4.csv` / `split_v4.csv` (650 Labor Standards Act + 1,425 from the six added laws). Results are appended per question to `results/v4/<stage>_<tag>.csv`, so an interrupted run resumes with the same command.

```zsh
uv run lawbot-eval-v4                       # everything: the four stages below, then the summary
uv run lawbot-eval-v4 retrieval             # all 2,075: embedding Recall@1/3/10, reranked Recall@1/3, MRR@3
uv run lawbot-eval-v4 answers               # fixed-seed sample of 200 (100 per group): citation, false refusal, keyword scoring for the 650 set
uv run lawbot-eval-v4 oos                   # 102 out-of-scope (27 from the seven laws reclassified as in scope) + 20 controls
uv run lawbot-eval-v4 refusal               # 100 sampled questions with only other-law / similar articles: refusal rate
uv run lawbot-eval-v4 summary --tag pro_concise_rr
```

Citations count only with the right law name; abbreviations (`퇴직급여법`, `고평법`, …) are accepted, and `근로기준법 시행령 제5조` (Enforcement Decree) is a different article from the Act's Art. 5. Citation parsing, refusal detection and per-citation checks (`ok` / `outside` / `wrong_law` / `nonexistent` / `ambiguous`) live in `lawbot.citation`, shared with the web view. `summary` rescores saved answers only, so files stay unchanged when the rules do. `uv run python -m lawbot.diff_answers <base.csv> <new.csv>` checks that two runs produced the same answers (exit code 0 when identical). The 2026-04-07 amendment moved the wage-arrears penalty from Art. 109 (3 years / 30M won) to Art. 107 (5 years / 50M won); the v4 gold labels follow the amended law, and the two keyword rows that still expected the old penalty (Q2443, Q2453) are updated at scoring time (`eval_v4.KEYWORD_FIX`; the data file is unchanged).

The previous v1 evaluation scripts (130 articles, 4B), which produced the README numbers and the baselines in `results/`:

```bash
uv run lawbot-eval --n 0      # retrieval only (650 questions)
uv run lawbot-eval --n 100    # retrieval + generation + scoring (100 random questions); retrieval ranks go to results/retrieval_<embedding-folder>.csv
uv run python -m lawbot.summ_dev scored_sllm_mlx_4bit.csv   # false refusals and refusals on retrieval misses
uv run python -m lawbot.eval_oos       # refusal and forced-answer rate on 102 out-of-scope questions → results/oos_v2_4bit.csv
uv run python -m lawbot.eval_refusal   # refusal given unrelated / similar articles only (100 each) → results/refusal_v2_4bit.csv
uv run python -m lawbot.eval_refusal --hard   # re-ask the 81 unsupported-claim cases with the same articles → results/refusal_hard_4bit.csv
```

Per-question results are written to `results/`. A final argument changes the file name (e.g. `uv run python -m lawbot.eval_oos oos_v3_4bit.csv`). `oos_v1_4bit.csv` (82 questions) and `refusal_v1_4bit.csv` are the baselines behind the README numbers and tests, so the defaults never overwrite them.

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

See the Korean section above for the full command sequence: first complete the baseline with the current model (Recall@3, controls, 102 out-of-scope questions, hard-case re-evaluation), then run the same evaluations for a new model under different file names and compare with `--baseline v1_4bit_full --strict`. `eval_oos` and `eval_refusal` use the model in `LLM_PATH`.

`metrics.csv` already holds two rows built from existing results: `v1_4bit` (baseline) and `v1_4bit_hybrid` (the failed hybrid retrieval experiment). Hybrid retrieval raised out-of-scope refusal from 0.671 to 0.683, but its forced-answer rate also rose from 0.256 to 0.268, so higher refusal alone was not an improvement.

### Project layout

```
src/lawbot/
├── paths.py           # data and model paths
├── retriever.py       # embedding retrieval
├── llm.py             # prompt construction, MLX generation
├── service.py         # UI-independent Q&A flow (model loading, retrieval → streamed answer)
├── app.py             # Gradio web app
├── security.py        # local-only launch settings (127.0.0.1, login required, no share link, port and model server URL checks)
├── prompts.py         # v1 default system prompt (readable without model libraries)
├── model_server.py    # internal model server: loads embedding, reranker and answer models once for all web apps (127.0.0.1, token)
├── remote.py          # model server client for remote-mode web apps (LAWBOT_MODEL_SERVER)
├── citation.py        # citation parsing, per-citation checks, refusal detection (shared by the web view and eval_v4)
├── manifest.py        # law data manifest checks (article counts, SHA-256, reference date)
├── log_summary.py     # summary of the request log (logs/requests.jsonl)
├── eval_v4.py         # v4 web app evaluation (lawbot-eval-v4)
├── diff_answers.py    # character-level comparison of answers from two evaluation runs
├── eval_dev.py        # retrieval and answer evaluation
├── score_answers.py   # keyword-based answer scorer
├── refusal.py         # refusal detection ('확인할 수 없' in the conclusion), out-of-scope verdicts (refusal, referral, forced, uncited)
├── summ_dev.py        # refusal metrics from eval_dev results
├── report.py          # combine evaluation results into a metrics.csv row and compare with a baseline
├── eval_oos.py        # refusal on out-of-scope questions
├── eval_refusal.py    # refusal given unrelated / similar articles, --hard: re-evaluate unsupported-claim cases
├── diag_oracle.py     # answer evaluation with gold articles
├── diag_fail.py       # failures where retrieval hit but citation missed
└── diag_pos.py        # citation accuracy by gold-article position
deploy/launchd/        # launchd templates for the always-on setup (model server, web app)
```

### Data and evaluation

- **Articles**: The 130 current articles of the Labor Standards Act. Two repealed articles are excluded. The text is as of 2026-10-01.
- **v4 articles**: 415 current articles from seven labor laws (`data/v4/articles_v4.csv`), collected from [Legalize KR](https://github.com/legalize-kr/legalize-kr) on 2026-10-20; version, dates and SHA-256 are in `data/v4/manifest.json`. The experimental 812-article set with enforcement decrees and rules (`data/v4/law_articles_v4_decree.csv`) is not used by the web app yet.
- **Questions**: 2,602 in total (130 articles × 5 question types × about 4 each).
  - Question types: colloquial, employer perspective, indirect, keyword, situational
  - Test: 650 questions (one per article and type)
  - Training and validation: the remaining 1,952 questions
  - Model selection and hyperparameters were decided on validation data only.
- **Answer scoring**: Each test question specifies required keyword groups, forbidden phrases, and the gold article. Scoring is automatic and distinguishes similar article numbers such as Article 43 and Article 43-2. A refusal is never counted as a citation, even when its list of provided articles contains the gold article. `cite_strict` separately records whether the cited-articles section names the gold article without also citing a confusable or similarly numbered article (not part of the total score). The same number in another law (e.g. Article 34 of the Employee Retirement Benefit Security Act) is not distinguished. The reference answers in the evaluation file score 1.0 on all 650 questions.
- **Out-of-scope questions**: 102 questions the Labor Standards Act cannot answer measure refusal separately (`data/oos_eval.csv`).
  - On 2026-10-19, 20 other-labor-law questions that overlap with Labor Standards Act concepts (OOS083-102) were added. Their legal basis (`note`) was checked against statute text from [Legalize KR](https://github.com/legalize-kr/legalize-kr). The results above use the original 82.
  - Answers are classified as refusal, referral (cites only an article in `refer_lsa` that delegates to another law), forced (answers with any other Labor Standards Act article), or uncited (neither a refusal phrase nor a citation). `near_lsa` is the basis of the README numbers; `near_lsa_v2` fills in missing labels.
- **In-scope controls**: 20 test questions that resemble other-labor-law questions but must be answered under the Labor Standards Act (`data/scope_controls.csv`, paired via `twin_oos`). They check that raising out-of-scope refusal does not make the model refuse these too.
- **Unsupported-claim cases**: The 86 non-refusals in `refusal_v1_4bit.csv` are labeled forced 77, fabricated 4, correct-but-unsupported 2 and reasonable 3 (`data/refusal_hard_cases.csv`). The 81 forced and fabricated cases are re-evaluated with `eval_refusal --hard`.
- **Evaluation-only data**: `data/labor_eval_keywords.csv`, `data/oos_eval.csv`, `data/scope_controls.csv`, `data/refusal_hard_cases.csv` and the test questions in `data/v4/split_v4.csv` are for evaluation only. Do not use them for training or model selection.
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
- **Planned work**:
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
