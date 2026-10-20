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
export LAWBOT_USER='원하는 사용자명'
read -rs 'LAWBOT_PW?비밀번호: ' && export LAWBOT_PW   # zsh(맥 기본). bash는 read -rsp '비밀번호: ' LAWBOT_PW
uv run lawbot-app
```

fish 셸은 다음과 같습니다.

```fish
set -gx LAWBOT_USER '원하는 사용자명'
read -gxs -P '비밀번호: ' LAWBOT_PW   # -g 전역, -x 내보내기, -s 입력 숨김
uv run lawbot-app
```

비밀번호를 매번 입력하지 않으려면 macOS 키체인에 저장해 두고 꺼내 쓸 수 있습니다.

```fish
security add-generic-password -a lawbot -s lawbot -w   # 최초 1회, 비밀번호 입력
set -gx LAWBOT_PW (security find-generic-password -a lawbot -s lawbot -w)
```

fish의 `set -Ux`(유니버설 변수)는 값을 `~/.config/fish/fish_variables`에 평문으로 저장하므로 비밀번호에 쓰지 마세요.

웹앱은 이 맥에서만 쓰는 로컬 전용입니다. 서버는 `127.0.0.1:7860`에서만 열리고, 브라우저에서 `http://127.0.0.1:7860`으로 접속해 로그인합니다. 같은 네트워크의 다른 기기에서는 접속할 수 없으며, Gradio 공개 공유 링크(`share`)도 만들지 않습니다. 주소·포트·공유 설정은 `GRADIO_SERVER_NAME` 같은 환경 변수로 바꿀 수 없습니다(`src/lawbot/security.py`).

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `LAWBOT_USER` | 예 | 로그인 사용자명 (앞뒤 공백 제거 후 비어 있으면 안 됨) |
| `LAWBOT_PW` | 예 | 로그인 비밀번호 (빈 값·공백만 있는 값은 안 됨) |
| `LLM_PATH` | 아니요 | 다른 sLLM 폴더 사용 (예: `models/sllm_mlx_8bit`) |

`LAWBOT_USER`와 `LAWBOT_PW`가 하나라도 없거나 비어 있으면 모델을 불러오기 전에 `RuntimeError`로 실행을 거부합니다. 인증 없이 서버를 띄우는 방법은 없습니다.

> 주의: 비밀번호를 소스 코드, 셸 설정 파일, 스크립트 등 Git에 올라가는 파일에 적지 마세요. 위처럼 실행할 때 입력하거나 macOS 키체인 같은 비밀 저장소를 쓰세요.

**테스트 실행**

기본 테스트는 모델 파일, 네트워크, 실제 비밀번호 없이 돌아갑니다. `uv sync`만 하면 됩니다. 실제 모델 대신 가짜 임베딩·sLLM을 넣고, 테스트 중 외부 네트워크 접속은 막습니다. `data/`·`results/`는 읽기만 합니다.

```bash
uv run pytest              # 기본 테스트 전체 (모델 불필요)
uv run pytest -m models    # 실제 모델 테스트 (models/ 필요, 기본 실행에서 제외)
```

GitHub Actions(`.github/workflows/ci.yml`)가 `main` 푸시와 모든 pull request에서 macOS(Apple Silicon) 러너로 `uv sync --locked`, 문법 검사(`compileall`), `uv run pytest`를 실행합니다. 비밀정보는 쓰지 않으며, `-m models` 테스트와 평가 스크립트는 CI에서 실행하지 않습니다. 결과는 PR의 체크와 Actions 로그, `pytest-report` 아티팩트에서 볼 수 있습니다.

**평가 실행**

```bash
uv run lawbot-eval --n 0      # 검색 성능만 (650문항)
uv run lawbot-eval --n 100    # 검색 + 답변 생성·채점 (무작위 100문항), 검색 순위는 results/retrieval_<임베딩폴더>.csv
uv run python -m lawbot.summ_dev scored_sllm_mlx_4bit.csv   # 위 결과의 거짓 거절률·검색 실패 시 거절률
uv run python -m lawbot.eval_oos       # 범위 밖 질문 102개 거절률·억지 적용률 → results/oos_v2_4bit.csv
uv run python -m lawbot.eval_refusal   # 무관한 조문 / 비슷한 조문만 줄 때 거절률 (각 100문항) → results/refusal_v2_4bit.csv
uv run python -m lawbot.eval_refusal --hard   # 근거 없이 주장했던 81건을 같은 참고 조문으로 다시 → results/refusal_hard_4bit.csv
```

문항별 결과는 `results/`에 저장됩니다. 마지막 인자로 파일 이름을 바꿀 수 있습니다(예: `uv run python -m lawbot.eval_oos oos_v3_4bit.csv`). `oos_v1_4bit.csv`(82문항)와 `refusal_v1_4bit.csv`는 README 수치와 테스트의 기준선이므로 기본 설정으로는 덮어쓰지 않습니다. `eval_oos`와 `eval_refusal`은 웹앱처럼 `LLM_PATH`의 모델을 씁니다.

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
├── lawdata.py         # 조문 데이터 검증, 출처·시점 메타데이터, 최신성 안내
├── retriever.py       # 임베딩 검색
├── llm.py             # 프롬프트 구성, MLX 생성
├── service.py         # UI와 무관한 질의응답 흐름 (모델 로딩, 검색 → 답변 스트리밍)
├── app.py             # Gradio 웹앱
├── security.py        # 로컬 전용 실행 설정 (127.0.0.1, 로그인 필수, 공유 링크 없음)
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
```

### 데이터와 평가 방법

- **조문**: 근로기준법 130개 조문(기준일 2026-10-01). [Legalize KR](https://github.com/legalize-kr/legalize-kr)의 근로기준법 법률 제21373호(2026-02-19 공포, 2026-08-20 시행) 본문이며, 2026-10-20에 조문 단위로 대조했습니다. 삭제·실효 조문 2개(제35조·제16조)는 제외했습니다. 본문의 `<개정 …>` 날짜는 항별 개정 표시일 뿐 시행일이 아닙니다. `[실효: …]` 표시(제16조, 제53조 ③·⑥)는 가공 중 덧붙인 것입니다.
  - **최신이 아님**: 기준일 이후 공포된 개정 법률(제21472·21533·21784호)로 18개 조문(제13조, 제54조, 제101조~제116조 등)의 본문이 바뀌었고, 일부는 이미 시행되었습니다. 두 모델이 기준일 본문으로 튜닝되었으므로 데이터는 고정하고, 이 조문이 검색되면 근거 패널에서 알립니다.
  - 출처·기준일은 `data/labor_articles_20261001.meta.json`에 확인한 값만 적고(모르는 값은 `null`), 파일 해시로 데이터와 맞는지 확인합니다. 검색 결과에는 조문별 출처 메타데이터가 붙고, 근거 패널에 출처를 표시하며, 답변이 검색 결과에 없는 조문을 인용하거나 질문에 특정 연도가 있거나 실효 표시가 있는 조문이 검색되면 알립니다. 자세한 내용과 이후 개정 기록 갱신 절차는 [docs/data-provenance.md](docs/data-provenance.md)에 있습니다.
  - 데이터 점검: `uv run python -m lawbot.lawdata` (검증, 파일 해시, 메타데이터 일치 여부)
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
- **평가 데이터 사용 제한**: `data/labor_eval_keywords.csv`, `data/oos_eval.csv`, `data/scope_controls.csv`, `data/refusal_hard_cases.csv`는 평가 전용입니다. 모델 학습이나 설정 선택에 쓰지 마세요.
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
  - 법 개정 반영 (새 기준일 본문으로 두 모델 재튜닝, 학습·평가 기준 재작성. 그 전까지 데이터는 2026-10-01 기준으로 고정)

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
export LAWBOT_USER='your-username'
read -rs 'LAWBOT_PW?Password: ' && export LAWBOT_PW   # zsh (macOS default). bash: read -rsp 'Password: ' LAWBOT_PW
uv run lawbot-app
```

In the fish shell:

```fish
set -gx LAWBOT_USER 'your-username'
read -gxs -P 'Password: ' LAWBOT_PW   # -g global, -x export, -s hide input
uv run lawbot-app
```

To avoid typing the password each time, store it in the macOS Keychain and read it from there:

```fish
security add-generic-password -a lawbot -s lawbot -w   # once; prompts for the password
set -gx LAWBOT_PW (security find-generic-password -a lawbot -s lawbot -w)
```

Do not use fish's `set -Ux` (universal variables) for the password: it stores the value in plain text in `~/.config/fish/fish_variables`.

The web app is local-only, for use on this Mac. The server listens only on `127.0.0.1:7860`; open `http://127.0.0.1:7860` in a browser and log in. Other devices on the network cannot connect, and no public Gradio share link is created. The address, port and share setting cannot be changed through environment variables such as `GRADIO_SERVER_NAME` (`src/lawbot/security.py`).

| Variable | Required | Purpose |
| --- | --- | --- |
| `LAWBOT_USER` | Yes | Login username (must not be empty after trimming whitespace) |
| `LAWBOT_PW` | Yes | Login password (must not be empty or whitespace only) |
| `LLM_PATH` | No | Use a different SLM folder (e.g. `models/sllm_mlx_8bit`) |

If either `LAWBOT_USER` or `LAWBOT_PW` is missing or empty, the app refuses to start with a `RuntimeError` before loading any model. There is no way to run the server without a login.

> Warning: never put the password in source code, shell profiles, scripts or any other file that goes into Git. Type it at launch as above, or use a secret store such as the macOS Keychain.

**Run the tests**

The default tests need no model files, network access or real password; `uv sync` is enough. They use fake embedding and SLM models, block outbound network access, and only read `data/` and `results/`.

```bash
uv run pytest              # all default tests (no models needed)
uv run pytest -m models    # real-model tests (needs models/, excluded by default)
```

GitHub Actions (`.github/workflows/ci.yml`) runs `uv sync --locked`, a syntax check (`compileall`) and `uv run pytest` on a macOS (Apple Silicon) runner for every push to `main` and every pull request. It uses no secrets; `-m models` tests and the evaluation scripts are not run in CI. Results appear as PR checks, in the Actions log and as the `pytest-report` artifact.

**Run the evaluation**

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

See the Korean section above for the full command sequence: first complete the baseline with the current model (Recall@3, controls, 102 out-of-scope questions, hard-case re-evaluation), then run the same evaluations for a new model under different file names and compare with `--baseline v1_4bit_full --strict`. `eval_oos` and `eval_refusal` use the model in `LLM_PATH`, like the web app.

`metrics.csv` already holds two rows built from existing results: `v1_4bit` (baseline) and `v1_4bit_hybrid` (the failed hybrid retrieval experiment). Hybrid retrieval raised out-of-scope refusal from 0.671 to 0.683, but its forced-answer rate also rose from 0.256 to 0.268, so higher refusal alone was not an improvement.

### Project layout

```
src/lawbot/
├── paths.py           # data and model paths
├── lawdata.py         # article data validation, source and date metadata, freshness notes
├── retriever.py       # embedding retrieval
├── llm.py             # prompt construction, MLX generation
├── service.py         # UI-independent Q&A flow (model loading, retrieval → streamed answer)
├── app.py             # Gradio web app
├── security.py        # local-only launch settings (127.0.0.1, login required, no share link)
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
```

### Data and evaluation

- **Articles**: 130 articles of the Labor Standards Act as of 2026-10-01, taken from [Legalize KR](https://github.com/legalize-kr/legalize-kr) (Act No. 21373, promulgated 2026-02-19, effective 2026-08-20) and checked article by article on 2026-10-20. Two articles (Article 35, repealed; Article 16, expired) are excluded. The `<개정 …>` dates in the text mark amendments, not effective dates. The `[실효: …]` expiry notes (Article 16; Article 53, paragraphs 3 and 6) were added during processing.
  - **Not current**: Acts promulgated after the as-of date (Nos. 21472, 21533, 21784) changed 18 articles (Articles 13, 54, 101-116, and others), and some of those changes are already in force. Both models were tuned on the as-of text, so the data stays fixed and the source panel flags these articles when they are retrieved.
  - `data/labor_articles_20261001.meta.json` records only verified facts (unknown values are `null`) and is checked against the file hash. Search results carry per-article source metadata, and the source panel shows the data source and flags citations outside the retrieved articles, questions about a specific year, and articles with expiry notes. See [docs/data-provenance.md](docs/data-provenance.md) (Korean) for details and how to refresh the later-amendment record.
  - Data check: `uv run python -m lawbot.lawdata` (validation, file hash, metadata match)
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
- **Evaluation-only data**: `data/labor_eval_keywords.csv`, `data/oos_eval.csv`, `data/scope_controls.csv` and `data/refusal_hard_cases.csv` are for evaluation only. Do not use them for training or model selection.
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
  - Incorporating law amendments (retune both models on a new as-of text and rebuild the training and evaluation data; until then the data stays fixed at 2026-10-01)

### Notices

- The base models Qwen3-4B and Qwen3-Embedding-0.6B are subject to their own licenses (Apache License 2.0).
- Korean statutes are not protected by copyright (Article 7 of the Korean Copyright Act).
- Answers from this chatbot are not legal advice. For a specific case, consult the Ministry of Employment and Labor helpline (1350 in Korea) or a certified labor attorney.
