# Lawbot: 근로기준법 RAG 챗봇

[한국어](#한국어) | [English](#english)

---

## 한국어

### 소개

Lawbot은 대한민국 근로기준법 질문에 조문을 근거로 답하는 검색 증강 생성(RAG) 챗봇입니다. 튜닝한 임베딩 모델로 현행 130개 조문 중 관련 조문 3개를 찾고, 튜닝한 소형 언어 모델(sLLM)이 그 조문만 근거로 네 부분 형식의 답변을 생성합니다. 모든 구성 요소가 Apple Silicon 맥 한 대에서 로컬로 동작합니다.

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

종합 점수 = 키워드 그룹 충족률 × 조문 인용 정확 여부 × (금지 표현 시 0.5)이고, 만점은 1.0입니다.

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

**로컬 실행 (MacBook Air 8GB, MLX 4비트, dev 100문항)**

| 지표 | 값 |
| --- | --- |
| 종합 점수 (검색 조문 / 정답 조문) | 0.812 / 0.945 |
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

학습된 모델은 용량이 커서 저장소에 포함하지 않습니다. 아래 경로에 두면 됩니다. 조문·평가 데이터(`data/`)와 평가 결과(`results/`)는 저장소에 포함되어 있습니다.

```
models/
├── emb_final_v3_fp16/     # 튜닝 임베딩 (sentence-transformers 형식, fp16)
└── sllm_mlx_4bit/         # 튜닝 sLLM (MLX 4비트)
```

sLLM은 병합한 Hugging Face 모델을 MLX 4비트로 변환해서 만듭니다.

```bash
uv run mlx_lm.convert --hf-path models/sllm_merged --mlx-path models/sllm_mlx_4bit -q --q-bits 4 --q-group-size 64
```

**웹앱 실행**

```bash
uv run lawbot-app
```

브라우저에서 `http://localhost:7860`으로 접속합니다. 선택 환경 변수는 다음과 같습니다.

| 변수 | 용도 |
| --- | --- |
| `LAWBOT_USER`, `LAWBOT_PW` | 접속 시 로그인 요구 |
| `LLM_PATH` | 다른 sLLM 폴더 사용 (예: `models/sllm_mlx_8bit`) |

**평가 실행**

```bash
uv run lawbot-eval --n 0      # 검색 성능만 (650문항)
uv run lawbot-eval --n 100    # 검색 + 답변 생성·채점 (무작위 100문항)
```

문항별 결과는 `results/scored_<모델폴더>.csv`에 저장됩니다.

### 프로젝트 구조

```
src/lawbot/
├── paths.py           # 데이터·모델 경로
├── retriever.py       # 임베딩 검색
├── llm.py             # 프롬프트 구성, MLX 생성
├── app.py             # Gradio 웹앱
├── eval_dev.py        # 검색·답변 평가
├── score_answers.py   # 키워드 기반 채점기
├── diag_oracle.py     # 정답 조문 제공 조건 답변 평가
├── diag_fail.py       # 검색은 맞았지만 인용이 틀린 문항 분석
└── diag_pos.py        # 정답 조문 위치별 인용 정확도 분석
```

### 데이터와 평가 방법

- **조문**: 근로기준법 현행 130개 조문. 삭제 조문 2개는 제외했고, 기준일은 2026-10-01입니다.
- **질문**: 총 2,602개(130조문 × 질문 유형 5종 × 약 4개)입니다.
  - 질문 유형: 구어체, 사용자(사업주) 관점, 간접 질문, 키워드, 상황 설명
  - 테스트: 650개(조문 × 유형마다 1개)
  - 학습·검증: 나머지 1,952개
  - 모델 선택과 하이퍼파라미터 결정은 검증 셋으로만 했습니다.
- **답변 채점**: 문항마다 필수 키워드 그룹, 금지 표현, 정답 조문 번호를 지정해 자동 채점합니다. 제43조와 제43조의2처럼 비슷한 번호는 구분합니다. 평가 파일의 모범 답변은 650문항 모두 1.0점입니다.

### 한계와 향후 계획

- **검색 실패**: 상위 3개에 정답 조문이 없는 경우가 약 6%이고, 이 경우 답변 품질이 크게 떨어집니다.
- **위치 편향**: 정답 조문이 두 번째·세 번째로 주어질 때 인용 정확도가 0.68·0.50으로 낮습니다. 첫 번째일 때는 0.975입니다.
- **범위**: 근로기준법만 다루며, 범위 밖 질문에 대한 거절 성능은 아직 측정하지 않았습니다.
- **향후 계획**:
  - BM25·하이브리드 검색 비교
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

Lawbot is a retrieval-augmented generation (RAG) chatbot that answers questions about the Korean Labor Standards Act, citing the articles it relies on. A fine-tuned embedding model retrieves the three most relevant of the 130 current articles. A fine-tuned small language model then writes a four-part answer grounded only in those articles. The whole stack runs locally on a single Apple Silicon Mac.

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

Total score = keyword-group coverage × correct article citation × (0.5 if a forbidden phrase appears). The maximum is 1.0.

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

**Local inference (MacBook Air 8 GB, MLX 4-bit, 100 dev questions)**

| Metric | Value |
| --- | --- |
| Total score (retrieved / gold articles) | 0.812 / 0.945 |
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

Trained models are too large for the repository. Place them as follows. The article and evaluation data (`data/`) and evaluation results (`results/`) are included in the repository.

```
models/
├── emb_final_v3_fp16/     # fine-tuned embedding (sentence-transformers format, fp16)
└── sllm_mlx_4bit/         # fine-tuned SLM (MLX 4-bit)
```

To build the MLX model, convert the merged Hugging Face model:

```bash
uv run mlx_lm.convert --hf-path models/sllm_merged --mlx-path models/sllm_mlx_4bit -q --q-bits 4 --q-group-size 64
```

**Run the web app**

```bash
uv run lawbot-app
```

Then open `http://localhost:7860`. Optional environment variables:

| Variable | Purpose |
| --- | --- |
| `LAWBOT_USER`, `LAWBOT_PW` | Require a login |
| `LLM_PATH` | Use a different SLM folder (e.g. `models/sllm_mlx_8bit`) |

**Run the evaluation**

```bash
uv run lawbot-eval --n 0      # retrieval only (650 questions)
uv run lawbot-eval --n 100    # retrieval + generation + scoring (100 random questions)
```

Per-question results are written to `results/scored_<model-folder>.csv`.

### Project layout

```
src/lawbot/
├── paths.py           # data and model paths
├── retriever.py       # embedding retrieval
├── llm.py             # prompt construction, MLX generation
├── app.py             # Gradio web app
├── eval_dev.py        # retrieval and answer evaluation
├── score_answers.py   # keyword-based answer scorer
├── diag_oracle.py     # answer evaluation with gold articles
├── diag_fail.py       # failures where retrieval hit but citation missed
└── diag_pos.py        # citation accuracy by gold-article position
```

### Data and evaluation

- **Articles**: The 130 current articles of the Labor Standards Act. Two repealed articles are excluded. The text is as of 2026-10-01.
- **Questions**: 2,602 in total (130 articles × 5 question types × about 4 each).
  - Question types: colloquial, employer perspective, indirect, keyword, situational
  - Test: 650 questions (one per article and type)
  - Training and validation: the remaining 1,952 questions
  - Model selection and hyperparameters were decided on validation data only.
- **Answer scoring**: Each test question specifies required keyword groups, forbidden phrases, and the gold article. Scoring is automatic and distinguishes similar article numbers such as Article 43 and Article 43-2. The reference answers in the evaluation file score 1.0 on all 650 questions.

### Limitations and roadmap

- **Retrieval misses**: For about 6% of questions, the gold article is not in the top 3, and answer quality drops sharply on those.
- **Position bias**: When the gold article is given second or third, citation accuracy falls to 0.68 and 0.50 respectively, against 0.975 when it is first.
- **Scope**: Only the Labor Standards Act is covered. Refusal on out-of-scope questions has not been measured yet.
- **Planned work**:
  - BM25 and hybrid retrieval comparison
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
