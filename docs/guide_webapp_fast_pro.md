# 실습 가이드: 웹앱 Fast/Pro 모델 선택 · 간결형 스트리밍 · v4 검색 (Mac mini 32GB)

- 작성일: 2026-10-24 · 기준 커밋: `main` `8dde1cf` (PR #7 병합 직후)
- 이 가이드는 이번 웹앱 작업 전용입니다. 기존 4B·14B 실습 가이드와 README는 수정하지 않습니다.
- 표기: **확인됨** = 이 가이드 작성 중 실제 실행으로 확인 / **미확인** = 맥 실측 또는 노트북 자료가 필요

---

## 0. 현재 레포 확인 결과 (요청서 스냅샷과의 차이)

| 항목 | 요청서 설명 | 실제 레포 (2026-10-24 `main`) | 영향 |
| --- | --- | --- | --- |
| `CONTEXT.md` | 있으면 읽기 | **없음** | README·코드·테스트로 파악 |
| 기존 4B/14B 실습 가이드 | 수정 금지 | **레포에 없음** (로컬에만 있는 것으로 보임) | 이 가이드는 `docs/`에 새로 만듦 |
| `app.py` 스트리밍 | generator로 `yield text, src` | 일치. `make_chat(retriever, llm)` 안의 `chat(message, history)` | 이 경로를 그대로 살려 입력만 늘림 |
| 오른쪽 '카드' | 조문 3개 카드 | 실제로는 `gr.Markdown` 하나에 `---`로 구분한 구획 | 구획 형식 유지 (v4에서는 법령명 추가) |
| `service.py` | 단일 모델 `load_models()` | 일치 | `load_models()`·`answer_stream()`은 그대로 두고 웹앱용 `load_app()`·`ask()` 추가 |
| `llm.py` | v1 4섹션 system 하드코딩, `max_tokens=1024` | 일치 | system·참고 조문 형식을 인자로 받도록 확장 (기본값은 v1 그대로) |
| `retriever.py` | v1 130조문, `max_seq_length=512` | 일치 | 문서 형식·길이를 인자로 (기본값은 v1 그대로) |
| v4 자산 | 415조문 CSV, v4 임베딩, v4 system, 4B LoRA/14B 폴더 | **레포 `data/`에 없음** (`models/`는 gitignore) | 모두 **자리표시(placeholder)** + `check_deploy`로 확인 |
| 잠금 버전 | Gradio ≥6.30, mlx-lm ≥0.32.2 | `uv.lock`: gradio 6.30.0, gradio-client 2.7.2, mlx 0.32.4, mlx-lm 0.32.2, torch 2.14.1, sentence-transformers 6.1.0, transformers 5.20.1 | 맥에서 `lawbot.versions`로 실제 설치 버전 확인 (1단계) |
| 열린 PR | - | **PR #8 (`claude/law-version-tracking`) 미병합**: `app.py`·`service.py`·`retriever.py`·`tests/fakes.py`를 수정 | 아래 '충돌 주의' 참고 |

**작성 중 실제로 확인한 사실 (Linux 컨테이너, 잠금 파일과 같은 Gradio 6.30.0 / mlx-lm 0.32.2 / mlx 0.32.4 CPU 빌드)**

1. `gr.ChatInterface(additional_inputs=[...])`를 쓰면 API 입력이 `[질문, 모델, 형식]`으로 늘어나고, **Gradio 6.30.0은 빠진 입력을 기본값으로 채우지 않습니다.** `{"data": ["질문"]}`만 보내면 `didn't receive enough input values` 오류가 납니다. → `/gradio_api/call/chat` **이름은 유지**하되 입력 개수가 바뀌므로 통합 테스트도 함께 고칩니다.
2. `additional_inputs`가 있으면 `examples`는 **리스트의 리스트**여야 합니다(`ValueError`). `[[질문, None, None]]`처럼 추가 입력을 `None`으로 두면 예시가 대화창 안에 표시되고, 예시를 눌러도 **현재 모델·형식 선택이 유지**됩니다.
3. 추가 입력 컴포넌트를 `ChatInterface` 앞에서 먼저 그리면(render) 아코디언에 다시 그리지 않습니다. 같은 컴포넌트를 두 번 `render()`하면 `DuplicateBlockError`.
4. `/gradio_api/call/chat` 스트림은 `event: generating` 마다 **누적 답변 전체**를 보냅니다 → 첫 글자 도착 시간을 클라이언트에서 잴 수 있음 (`lawbot.bench_ui`).
5. `mlx_lm.stream_generate()`가 돌려주는 `GenerationResponse` 필드: `text, token, logprobs, from_draft, prompt_tokens, prompt_tps, generation_tokens, generation_tps, peak_memory, finish_reason`. `mlx.core`에 `get_active_memory / get_peak_memory / clear_cache / device_info`가 있음.
6. `mlx_lm.load()`에 **없는 절대 경로**를 주면 `HFValidationError`(`ValueError`)로 바로 실패하고 네트워크에 접속하지 않습니다. **없는 상대 경로**(`models/x`)는 Hugging Face 저장소 이름으로 해석될 수 있어, 배포 설정은 모든 경로를 절대 경로로 바꿉니다.
7. 기존 테스트 107개 통과 → 이 가이드의 코드를 적용한 뒤 **128개 통과**(인증 통합 테스트 포함), `compileall` 통과, 가짜 모델로 웹 화면 스크린샷·`bench_models`·`bench_ui`·`eval_concise` 끝까지 실행 확인.

**확인하지 못한 것 (맥에서 직접 확인 필요)**: Apple Silicon MPS·Metal 실행, 실제 모델 로딩, 메모리·속도 수치, `-m models` 테스트, macOS `sysctl` 메모리 압박 값. 컨테이너의 torch는 CUDA 빌드였고 MPS는 테스트에서 가짜로 대체했습니다.

**충돌 주의 (PR #8)**: 이 가이드는 `main` 기준입니다. PR #8을 먼저 병합하면 `app.py`(`fmt_sources`·`chat` 인용 대조), `service.py`(`source_notes`·`trace_citations`), `retriever.py`(`load_articles`·`meta`), `tests/fakes.py`가 겹칩니다. 순서는 **이 작업을 먼저 끝내고 PR #8을 그 위로 다시 맞추는 것**을 권합니다. PR #8의 인용 대조(`trace_citations`)는 이 가이드의 `answer_format.cited_hits`와 역할이 겹치므로 그때 하나로 합칩니다.

---

## 1. 목표와 범위

| 목표 | 이 가이드에서 하는 일 | 판정 |
| --- | --- | --- |
| Fast(4B)·Pro(14B) 선택 | `deploy.py` 레지스트리 + 화면 라디오 → `chat(message, history, model_key, fmt)` | 코드 확인됨, 맥 미확인 |
| 같은 검색·같은 참고 조문 | 검색기 1개를 두 모델이 공유, `[참고 조문]` 입력은 그대로 | 테스트로 확인됨 |
| 간결형 스트리밍 | 모델 원문(4섹션)은 그대로 두고 화면에서만 `결론 + 근거: 실제 인용 조문` | 코드 확인됨, **val 효과 미확인** |
| 15초 안에 첫 글자 | 검색·전환·첫 토큰·첫 글자·tok/s·완료·메모리 측정 도구 | **미확인 (실측 필요, 보장 아님)** |
| v4 검색 | 415조문·MAX_SEQ 2304·`법령명 제N조(제목)\n본문` 형식, system 파일 | 코드 확인됨, **자산·형식 대조 미확인** |
| 안전장치 유지 | 127.0.0.1·로그인 필수·`share=False` 그대로, `service.py` gradio 미사용 | 테스트로 확인됨 |

**하지 않는 것**: 서버·큐 인프라 추가, 두 모델 동시 생성, 인증 우회, 기존 평가 결과 파일 덮어쓰기, test 650문항으로 형식 선택.

### 설계 한눈에

```
질문 ──► Retriever (v1 또는 v4, 1개 공유) ──► 상위 3개 조문 ──┬──► 오른쪽 패널 (원문 3개, 생성 내내 고정)
                                                               │
          라디오: 모델(fast/pro) · 형식(간결/전체)                 ▼
          (요청마다 chat 인자로 전달, 공유 상태 없음) ──► ModelManager.get(key)  ── 상주 1~2개, LRU
                                                               │  (안 올라와 있으면 '⏳ 불러오는 중…' 먼저 표시)
                                                               ▼
                                         LLM.stream (system + [참고 조문] + [질문], temp 0)
                                                               │  r.text 누적 (기존 스트리밍 경로)
                                                               ▼
                         answer_format.render: 전체 = 원문 / 간결 = 결론 + "근거: 실제 인용 조문"
                                                               ▼
                                 대화창 + 측정 줄 (검색·전환·첫 토큰·첫 글자·tok/s·완료·잘림)
```

| 결정 | 이유 |
| --- | --- |
| 기본 프로필 `v1` 유지, `LAWBOT_PROFILE=v4`로 전환 | v4 자산이 없어도 지금 앱·테스트·평가가 그대로 동작. v4는 `check_deploy` 통과 후 켬 |
| 간결형은 **후처리** (모델 입력·원문 불변) | 4섹션으로 학습한 모델에 프롬프트만 바꾸면 형식이 흔들릴 수 있음. 프롬프트 변경은 `eval_concise`의 `prompt` 조건으로 따로 잼 |
| '근거:' 줄은 모델이 【근거 조문】에 쓴 조문만 | 검색 3개를 자동으로 '근거'라고 붙이지 않음. 검색 결과에 없는 번호는 ⚠로 표시 |
| 조기 종료(`LAWBOT_EARLY_STOP=1`)는 기본 꺼짐 | 【설명】부터 생성을 멈추면 완료 시간이 크게 줄지만, val로 확인 전에는 켜지 않음 |
| 상주 수 `LAWBOT_MAX_RESIDENT` (기본 2) | 먼저 동시 상주를 실측하고, 압박·스왑이 생기면 1(전환 때 다시 로딩)로 |
| `concurrency_limit=1` | 두 모델이 동시에 생성하지 않게. 대기 요청은 Gradio 큐에서 순서 대기 |

---

## 2. 선행 조건과 미확인 체크리스트

**선행 조건**: Mac mini 32GB, fish 셸, `uv`, 이 레포 `main`, 기존 `models/emb_final_v3_fp16`·`models/sllm_mlx_4bit`(v1 Fast).

**노트북(4B v4 / 14B)에서 받아야 확정되는 것** — 받기 전에는 아래 값이 자리표시입니다.

| # | 확인 항목 | 현재 가이드의 가정 (미확인) | 어디에 반영 |
| --- | --- | --- | --- |
| A | v4 조문 CSV 파일명·열 이름 | `data/v4/articles_v4.csv`, 열 `law, article_id, title, deleted, text`, 비삭제 415행 | `paths.V4_ARTICLES`, `check_deploy.COLUMNS` |
| B | v4 검색 문서 문자열 | `법령명 제N조(제목)\n본문` (본문의 `<개정…>` 표시는 **그대로** 둠) | `retriever.DOC_STYLES['v4']` |
| C | v4 검색 query 프롬프트 | 임베딩 폴더 `config_sentence_transformers.json`의 `prompts['query']` | `check_deploy`가 출력, 노트북과 대조 |
| D | v4 MAX_SEQ | 2304 | `deploy.get_profile('v4').max_seq` |
| E | v4 `[참고 조문]` 블록 형식 | `[1] 근로기준법 제56조(제목)\n본문` (본문의 `<개정…>` 표시는 **지움**, v1과 같음) | `llm.build_prompt_v4` |
| F | v4 system 원문 | `data/v4/system_v4.txt`에 글자 그대로 저장 | `paths.V4_SYSTEM` |
| G | v4 출력 형식 | 4섹션 `【결론】【근거 조문】【설명】【참고】` 유지 여부 | `answer_format.SECTION`, `refusal.py` |
| H | 4B LoRA 병합·MLX 변환 폴더 | `models/v4/sllm_4b_lora_mlx_4bit` | `paths.V4_FAST` |
| I | 14B 폴더 (QLoRA 전에는 베이스) | `models/v4/sllm_14b_mlx_4bit`, v1 시험용 `models/qwen3_14b_mlx_4bit` | `paths.V4_PRO`, `paths.PRO_BASE` |
| J | v4 val 문항 파일 (키워드 채점 열 포함 여부) | `--val`로 지정 | `eval_concise` |
| K | 노트북의 v4 검색 Recall@3, 4B/14B 답변 점수 | - | 결과 기록 표의 '노트북 기준값' |

경로는 코드를 고치지 않고 환경 변수로도 바꿀 수 있습니다: `LAWBOT_V4_ARTICLES`, `LAWBOT_V4_EMB`, `LAWBOT_V4_SYSTEM`, `LAWBOT_FAST_PATH`, `LAWBOT_PRO_PATH`, `LAWBOT_EMB`. 상대 경로는 레포 최상위 기준입니다.

---

## 3. 바뀌는 파일

| 파일 | 구분 | 내용 |
| --- | --- | --- |
| `src/lawbot/paths.py` | 수정 | v4·14B 자리표시 경로 추가 (v1 경로 그대로) |
| `src/lawbot/deploy.py` | 새 파일 | 프로필(v1/v4)·Fast/Pro 레지스트리·상주 수·조기 종료 설정 |
| `src/lawbot/retriever.py` | 수정 | `max_seq_length`·`doc_style` 인자, v4 결과에 `law` |
| `src/lawbot/llm.py` | 수정 | `system`·`prompt_style` 인자, `build_prompt_v4` |
| `src/lawbot/answer_format.py` | 새 파일 | 간결형 후처리, 실제 인용 조문 찾기 |
| `src/lawbot/service.py` | 수정 | `ModelManager`(LRU), `load_app()`, `ask()`(단계·측정값). 기존 함수 유지 |
| `src/lawbot/app.py` | 수정 | 모델·형식 라디오, 측정 줄, 전환 대기 표시, 오류 안내 |
| `src/lawbot/check_deploy.py` | 새 파일 | 자산 검사 (CSV·system·임베딩 dtype·MLX 양자화·어댑터·토크나이저) |
| `src/lawbot/versions.py` | 새 파일 | 실제 설치 버전·맥 사양 출력 |
| `src/lawbot/bench_models.py` | 새 파일 | 모델별 지연·메모리·전환 시간 (UI 없이) |
| `src/lawbot/bench_ui.py` | 새 파일 | 실행 중인 웹앱에서 '질문 → 첫 글자 도착' |
| `src/lawbot/eval_concise.py` | 새 파일 | 간결형 val 실험 (test 파일 거부) |
| `tests/fakes.py`, `tests/conftest.py`, `tests/test_pipeline.py`, `tests/test_auth_integration.py` | 수정 | 새 호출 형식·환경 변수 정리 |
| `tests/test_webapp.py` | 새 파일 | 선택·전환·간결형·v4 형식·배포 검사 테스트 20개 |

**동작이 바뀌는 점 (의도된 변경)**

- 웹앱 API 입력: `[질문]` → `[질문, 'fast'|'pro', 'concise'|'full']`, 출력: `[답변, 대화창, 조문]` → `[답변, 대화창, 조문, 측정]`. 함수 이름 `chat`은 유지.
- 웹앱은 이제 `LLM_PATH` 대신 `LAWBOT_FAST_PATH`를 봅니다. `LLM_PATH`는 `load_models()`·평가 스크립트(`eval_oos`·`eval_refusal`)용으로 그대로 남습니다.
- 앱 기본 형식은 간결형. 4섹션 원문은 '전체' 선택으로 볼 수 있습니다.

---

## 4. 실습 단계

각 단계는 **코드 묶음 → 실행 명령 → 확인 표** 순서입니다. 묶음 하나를 넣고 확인한 뒤 다음으로 넘어갑니다. 모든 명령은 fish 기준이고 레포 최상위에서 실행합니다.

### 1단계. 브랜치와 실제 버전 확인

```fish
cd ~/lawbot            # 레포 위치에 맞게
git switch main; and git pull
git switch -c webapp-fast-pro
uv sync --locked
```

`src/lawbot/versions.py` (새 파일)

```python
# src/lawbot/versions.py  →  uv run python -m lawbot.versions
# 실제 설치된 패키지·맥 사양을 출력 (가이드 결과 표의 '환경' 칸에 붙여 넣음). 모델은 올리지 않는다
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

PACKAGES = ('gradio', 'gradio-client', 'mlx', 'mlx-metal', 'mlx-lm', 'torch', 'sentence-transformers',
            'transformers', 'pandas', 'numpy')

def sysctl(name):
    r = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else '?'

def main():
    print('python', sys.version.split()[0], '·', platform.platform())
    for p in PACKAGES:
        try:
            print(f'{p:22} {version(p)}')
        except PackageNotFoundError:
            print(f'{p:22} (설치 안 됨)')
    if platform.system() == 'Darwin':
        print('칩', sysctl('machdep.cpu.brand_string'), '· 메모리', int(sysctl('hw.memsize')) / 2**30, 'GiB')
        import mlx.core as mx
        info = mx.device_info() if hasattr(mx, 'device_info') else mx.metal.device_info()
        print('MLX 권장 작업 메모리', round(info.get('max_recommended_working_set_size', 0) / 2**30, 1), 'GiB')

if __name__ == '__main__':
    main()
```

```fish
uv run python -m lawbot.versions
uv run pytest -q        # 시작 전 기준: 107 passed, 1 deselected
```

| 확인 | 기대 | 결과 |
| --- | --- | --- |
| gradio | 6.30.0 (다르면 알려 주세요. 1~4번 사실을 다시 확인) | |
| mlx / mlx-lm | 0.32.4 / 0.32.2 | |
| torch / sentence-transformers | 2.14.1 / 6.1.0 | |
| 칩·메모리·MLX 권장 작업 메모리 | 32 GiB, 권장값 기록 | |
| 기존 테스트 | 107 passed | |

### 2단계. 경로·레지스트리·자산 검사

`src/lawbot/paths.py` — 파일 끝에 추가 (기존 줄은 그대로)

```diff
diff --git a/src/lawbot/paths.py b/src/lawbot/paths.py
index 739428e..f88f8c2 100644
--- a/src/lawbot/paths.py
+++ b/src/lawbot/paths.py
@@ -7,3 +7,12 @@ ARTICLES = DATA / "labor_articles_20261001.csv"
 EVAL = DATA / "labor_eval_keywords.csv"
 EMB = MODELS / "emb_final_v3_fp16"
 LLM_4BIT = MODELS / "sllm_mlx_4bit"
+
+# ---- v4 웹앱 배포 (Fast/Pro). 모두 미확인 자리표시: 실제 산출물 이름을 확인한 뒤 바꾼다 ----
+# 환경 변수(LAWBOT_V4_ARTICLES 등, lawbot.deploy)로도 바꿀 수 있고, uv run python -m lawbot.check_deploy로 검사
+V4_ARTICLES = DATA / "v4" / "articles_v4.csv"      # 7개 법률 비삭제 415조문
+V4_SYSTEM = DATA / "v4" / "system_v4.txt"          # v4 학습 때 쓴 system 원문 (글자 그대로)
+V4_EMB = MODELS / "v4" / "emb_v4_fp16"             # v4 튜닝 Qwen3-Embedding-0.6B
+V4_FAST = MODELS / "v4" / "sllm_4b_lora_mlx_4bit"  # Qwen3-4B + v4 LoRA 병합 → MLX 4비트
+V4_PRO = MODELS / "v4" / "sllm_14b_mlx_4bit"       # Qwen3-14B MLX 4비트 (v4 QLoRA 완성 시 교체)
+PRO_BASE = MODELS / "qwen3_14b_mlx_4bit"           # v1 프로필에서 시험할 14B (튜닝 전)
```

`src/lawbot/deploy.py` (새 파일)

```python
# src/lawbot/deploy.py  →  웹앱 배포 설정: 검색 프로필(v1/v4)과 답변 모델(Fast/Pro) 레지스트리
# 모델 라이브러리를 import하지 않는다 (check_deploy·테스트가 모델 없이 읽음)
import os
from dataclasses import dataclass
from pathlib import Path
from lawbot import paths

@dataclass(frozen=True)
class ModelSpec:
    key: str                 # UI·API 값 ('fast' / 'pro')
    label: str               # 화면 표시명
    info: str                # 크기·속도 설명 (정확도 비교는 쓰지 않음: 실측 전)
    path: Path               # 로컬 MLX 폴더 (절대 경로)

@dataclass(frozen=True)
class Profile:
    name: str                # 'v1' / 'v4'
    title: str               # 웹앱 제목
    articles: Path           # 조문 CSV
    emb: Path                # 검색 임베딩 폴더
    max_seq: int             # 조문 인코딩 최대 토큰
    doc_style: str           # 검색 문서·참고 조문 형식 (retriever.DOC_STYLES, llm.build_messages)
    system_file: Path | None # None이면 llm.SYSTEM (v1 학습 system)
    models: tuple            # (Fast, Pro). 첫 번째가 기본 선택

def _path(env, default):
    """환경 변수가 있으면 그 경로. 상대 경로는 레포 최상위 기준 절대 경로로 바꾼다
    (mlx_lm.load는 없는 상대 경로를 Hugging Face 저장소 이름으로 보고 내려받으려 함)"""
    p = Path(os.environ.get(env) or default).expanduser()
    return p if p.is_absolute() else paths.ROOT / p

def get_profile(name=None):
    name = name or os.environ.get('LAWBOT_PROFILE', 'v1')
    if name == 'v1':         # 현재 동작: 근로기준법 130조문, v1 검색·4B. Pro는 튜닝 전 14B로 속도·메모리만 시험
        return Profile('v1', '근로기준법 챗봇', paths.ARTICLES, _path('LAWBOT_EMB', paths.EMB), 512, 'v1', None, (
            ModelSpec('fast', 'Lawbot Fast', '4B · 빠른 응답', _path('LAWBOT_FAST_PATH', paths.LLM_4BIT)),
            ModelSpec('pro', 'Lawbot Pro', '14B · 큰 모델, 응답이 더 느림', _path('LAWBOT_PRO_PATH', paths.PRO_BASE))))
    if name == 'v4':         # 7개 법률 415조문, v4 검색 (MAX_SEQ 2304). 경로는 paths.py의 미확인 자리표시
        return Profile('v4', '노동법 챗봇', _path('LAWBOT_V4_ARTICLES', paths.V4_ARTICLES),
                       _path('LAWBOT_V4_EMB', paths.V4_EMB), 2304, 'v4', _path('LAWBOT_V4_SYSTEM', paths.V4_SYSTEM), (
            ModelSpec('fast', 'Lawbot Fast', '4B · 빠른 응답', _path('LAWBOT_FAST_PATH', paths.V4_FAST)),
            ModelSpec('pro', 'Lawbot Pro', '14B · 큰 모델, 응답이 더 느림', _path('LAWBOT_PRO_PATH', paths.V4_PRO))))
    raise ValueError(f'알 수 없는 LAWBOT_PROFILE: {name!r} (v1 또는 v4)')

def max_resident():
    """동시에 메모리에 올려 두는 답변 모델 수. 2: Fast·Pro 함께 상주, 1: 선택한 모델만 (전환 때 다시 로딩)"""
    n = int(os.environ.get('LAWBOT_MAX_RESIDENT', '2'))
    if n not in (1, 2):
        raise ValueError(f'LAWBOT_MAX_RESIDENT는 1 또는 2: {n}')
    return n

def early_stop():
    """간결형에서 【설명】이 시작되면 생성을 멈출지. val 실험(eval_concise)으로 확인하기 전에는 끈다"""
    return os.environ.get('LAWBOT_EARLY_STOP', '0') == '1'
```

`src/lawbot/check_deploy.py` (새 파일)

```python
# src/lawbot/check_deploy.py  →  uv run python -m lawbot.check_deploy [v1|v4]
# 웹앱이 쓸 조문 CSV·system·임베딩·MLX 모델 폴더를 모델을 올리지 않고 검사한다 (파일 헤더만 읽음)
# 실패 항목이 있으면 종료 코드 1. LoRA 병합 여부는 파일로 확정할 수 없어 '어댑터 파일 없음'까지만 본다
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path
import pandas as pd
from lawbot.deploy import get_profile

EXPECTED_ARTICLES = {'v1': 130, 'v4': 415}
COLUMNS = {'v1': {'article_id', 'title', 'deleted', 'text'}, 'v4': {'law', 'article_id', 'title', 'deleted', 'text'}}
ADAPTER_FILES = ('adapters.safetensors', 'adapter_config.json', 'adapter_model.safetensors')
HIDDEN_SIZE = {2560: 'Qwen3-4B', 5120: 'Qwen3-14B'}       # config.json hidden_size로 크기 확인

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def safetensors_dtypes(folder):
    """폴더 안 safetensors 텐서의 dtype별 개수 (헤더만 읽음)"""
    n = Counter()
    for f in sorted(Path(folder).glob('*.safetensors')):
        with f.open('rb') as fh:
            size = struct.unpack('<Q', fh.read(8))[0]
            header = json.loads(fh.read(size))
        n.update(v['dtype'] for k, v in header.items() if k != '__metadata__')
    return dict(n)

def folder_gb(folder):
    return sum(f.stat().st_size for f in Path(folder).rglob('*') if f.is_file()) / 1e9

def check_articles(p, out):
    if not p.articles.is_file():
        return out.append(('FAIL', f'조문 CSV 없음: {p.articles}'))
    law = pd.read_csv(p.articles, encoding='utf-8-sig')
    missing = COLUMNS[p.doc_style] - set(law.columns)
    if missing:
        return out.append(('FAIL', f'조문 CSV 열 없음 {sorted(missing)} (있는 열: {list(law.columns)})'))
    live = law[law['deleted'] == 0]
    key = ['law', 'article_id'] if p.doc_style == 'v4' else ['article_id']
    dup = live[live.duplicated(key, keep=False)]
    n_ok = len(live) == EXPECTED_ARTICLES[p.name]
    out.append(('OK' if n_ok else 'FAIL', f'비삭제 조문 {len(live)}개 (기대 {EXPECTED_ARTICLES[p.name]}) · sha256 {sha256(p.articles)[:12]}'))
    if len(dup):
        out.append(('FAIL', f'중복 조문 {len(dup)}행: {dup[key].head().values.tolist()}'))
    if p.doc_style == 'v4':
        out.append(('INFO', f"법령별 조문 수: {live['law'].value_counts().to_dict()}"))

def check_system(p, out):
    if p.system_file is None:
        from lawbot.llm import SYSTEM   # v1: 코드에 든 학습 system
        return out.append(('OK', f'system: llm.SYSTEM (v1) · sha256 {hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]}'))
    if not p.system_file.is_file():
        return out.append(('FAIL', f'system 파일 없음: {p.system_file} (v4 학습 노트북의 system 원문을 글자 그대로 저장)'))
    text = p.system_file.read_text(encoding='utf-8').strip()
    out.append(('OK', f'system {len(text)}자 · sha256 {hashlib.sha256(text.encode()).hexdigest()[:12]} · 첫 줄: {text.splitlines()[0][:60]}'))

def check_emb(p, out):
    d = p.emb
    if not d.is_dir():
        return out.append(('FAIL', f'임베딩 폴더 없음: {d}'))
    dtypes = safetensors_dtypes(d)
    out.append(('OK' if dtypes.get('F16') else 'WARN', f'임베딩 {d.name}: dtype {dtypes} · {folder_gb(d):.2f}GB '
                '(앱은 fp16으로 올림. F32·BF16이면 노트북과 같은 정밀도인지 확인)'))
    st = d / 'config_sentence_transformers.json'
    prompts = json.loads(st.read_text())['prompts'] if st.is_file() else None
    out.append(('OK' if prompts and 'query' in prompts else 'FAIL', f"query 프롬프트: {(prompts or {}).get('query')!r} (노트북 검색 때와 같아야 함)"))
    for f in ('modules.json', 'tokenizer.json'):
        if not (d / f).is_file():
            out.append(('FAIL', f'임베딩 폴더에 {f} 없음'))
    out.append(('INFO', f'조문 인코딩 max_seq_length {p.max_seq} · 문서 형식 {p.doc_style}'))

def check_llm(spec, out):
    d = spec.path
    if not d.is_dir():
        return out.append(('FAIL', f'{spec.label}: 모델 폴더 없음 {d} (준비 전이면 이 모델만 선택 불가로 표시됨)'))
    cfg = json.loads((d / 'config.json').read_text())
    q = cfg.get('quantization') or cfg.get('quantization_config') or {}
    size = HIDDEN_SIZE.get(cfg.get('hidden_size'), f"hidden_size {cfg.get('hidden_size')}")
    out.append(('OK' if q.get('bits') == 4 else 'FAIL',
                f"{spec.label}: {cfg.get('model_type')} {size} · 양자화 {q or '없음'} · {folder_gb(d):.2f}GB"))
    adapters = [f for f in ADAPTER_FILES if (d / f).exists()]
    out.append(('FAIL' if adapters else 'OK', f'{spec.label}: 어댑터 파일 ' + (f'남아 있음 {adapters} (병합 전 폴더)' if adapters
                                                                         else '없음 (병합 산출물로 보임, 출력 대조로 확인)')))
    tc = d / 'tokenizer_config.json'
    has_template = (d / 'chat_template.jinja').is_file() or (tc.is_file() and 'chat_template' in json.loads(tc.read_text()))
    out.append(('OK' if (d / 'tokenizer.json').is_file() and has_template else 'FAIL',
                f'{spec.label}: tokenizer.json·chat template ' + ('있음' if has_template else '없음')))

def run(name=None):
    p = get_profile(name)
    out = [('INFO', f'프로필 {p.name}: {p.title}')]
    check_articles(p, out)
    check_system(p, out)
    check_emb(p, out)
    for spec in p.models:
        check_llm(spec, out)
    return out

def main():
    out = run(sys.argv[1] if len(sys.argv) > 1 else None)
    for level, msg in out:
        print(f'[{level:4}] {msg}')
    sys.exit(1 if any(level == 'FAIL' for level, _ in out) else 0)

if __name__ == '__main__':
    main()
```

```fish
uv run python -m lawbot.check_deploy v1
uv run python -m lawbot.check_deploy v4
```

| 확인 | 기대 (지금 시점) | 결과 |
| --- | --- | --- |
| v1 조문·system·임베딩·Fast | 모두 `OK` (임베딩 dtype에 `F16`) | |
| v1 Pro | 14B 준비 전이면 `FAIL 모델 폴더 없음` (정상, 8단계에서 준비) | |
| v4 | 자산이 없으면 5개 `FAIL` — 받은 자산을 넣고 다시 실행 | |
| 임베딩 `query 프롬프트` | v1 값 기록 → v4 때 노트북 값과 대조 | |

종료 코드는 `FAIL`이 하나라도 있으면 1입니다. LoRA 병합 여부는 파일만으로 확정할 수 없어 '어댑터 파일이 남아 있지 않음'까지만 확인합니다(병합 확인은 8·11단계 출력 대조).

### 3단계. 검색기·sLLM 확장 (기본값은 v1 그대로)

`src/lawbot/retriever.py` (전체 교체)

```python
import numpy as np, pandas as pd, torch
from sentence_transformers import SentenceTransformer
from lawbot.paths import ARTICLES, EMB

# 검색 문서 문자열: 임베딩 학습 때와 글자 단위로 같아야 한다
DOC_STYLES = {
    'v1': lambda law: law['article_id'] + ' ' + law['title'] + '\n' + law['text'],        # 제26조 해고의 예고\n본문
    'v4': lambda law: law['law'] + ' ' + law['article_id'] + '(' + law['title'] + ')\n' + law['text'],  # 근로기준법 제26조(해고의 예고)\n본문
}

class Retriever:
    def __init__(self, model_dir=EMB, law_csv=ARTICLES, max_seq_length=512, doc_style='v1'):
        law = pd.read_csv(law_csv, encoding='utf-8-sig')
        law = law[law['deleted'] == 0].reset_index(drop=True)   # v1 130개, v4 415개 조문
        self.ids = law['article_id'].to_numpy()
        self.titles = law['title'].to_numpy()
        self.laws = law['law'].to_numpy() if doc_style == 'v4' else None   # v4: 법령마다 조문 번호가 겹침
        self.docs = DOC_STYLES[doc_style](law).tolist()
        self.starter = set(law.loc[law['starter'] == 1, 'article_id']) if 'starter' in law else set()
        self.m = SentenceTransformer(str(model_dir), device='mps',
                                     model_kwargs={'torch_dtype': torch.float16})
        self.m.max_seq_length = max_seq_length
        self.D = self.m.encode(self.docs, normalize_embeddings=True, batch_size=4,
                               convert_to_numpy=True).astype(np.float32)
        torch.mps.empty_cache()                                  # 조문 인코딩에 쓴 임시 메모리 반납

    def _encode_queries(self, queries, batch_size=16):
        return self.m.encode(list(queries), prompt_name='query', normalize_embeddings=True,
                             batch_size=batch_size, convert_to_numpy=True).astype(np.float32)

    def rank_all(self, queries):
        return np.argsort(-(self._encode_queries(queries) @ self.D.T), axis=1)

    def _hit(self, i, score):
        h = {'article_id': self.ids[i], 'title': self.titles[i], 'doc': self.docs[i], 'score': score}
        return h if self.laws is None else h | {'law': self.laws[i]}

    def search(self, query, k=3):
        s = self.D @ self._encode_queries([query])[0]
        return [self._hit(i, float(s[i])) for i in np.argsort(-s)[:k]]
```

`src/lawbot/llm.py` — 변경 부분 (나머지 그대로)

```diff
diff --git a/src/lawbot/llm.py b/src/lawbot/llm.py
index f6c2eb1..db1a848 100644
--- a/src/lawbot/llm.py
+++ b/src/lawbot/llm.py
@@ -23,14 +23,23 @@ def build_prompt(ids, question):
     blocks = [f'[{i}] {a}({ART[a][0]})\n{ART[a][1]}' for i, a in enumerate(ids, 1)]
     return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'
 
+def build_prompt_v4(hits, question):
+    """v4: 검색 결과의 법령명·조문을 그대로 쓴다 (7개 법률은 조문 번호가 겹쳐 ART로 찾을 수 없음)
+    미확인: v4 학습 노트북의 [참고 조문] 형식과 글자 단위로 대조해야 함 (가이드 0단계)"""
+    blocks = [f"[{i}] {h['law']} {h['article_id']}({h['title']})\n{_clean(h['doc'].split(chr(10), 1)[1])}"
+              for i, h in enumerate(hits, 1)]
+    return '[참고 조문]\n' + '\n\n'.join(blocks) + f'\n\n[질문]\n{question}'
+
 class LLM:
-    def __init__(self, path=LLM_4BIT, temp=0.0):
+    def __init__(self, path=LLM_4BIT, temp=0.0, system=SYSTEM, prompt_style='v1'):
         self.model, self.tok = load(str(path))
         self.sampler = make_sampler(temp=temp)
+        self.system, self.prompt_style = system, prompt_style    # v4: deploy 프로필의 system 파일
 
     def build_messages(self, question, hits):
-        return [{'role': 'system', 'content': SYSTEM},
-                {'role': 'user', 'content': build_prompt([h['article_id'] for h in hits], question)}]
+        user = (build_prompt_v4(hits, question) if self.prompt_style == 'v4'
+                else build_prompt([h['article_id'] for h in hits], question))
+        return [{'role': 'system', 'content': self.system}, {'role': 'user', 'content': user}]
 
     def stream(self, question, hits, max_tokens=1024):
         prompt = self.tok.apply_chat_template(
```

```fish
uv run pytest -q tests/test_pipeline.py -k "retriever or search or rank or prompt or llm"
```

v1 경로(`Retriever()`, `LLM(path)`)는 문서 문자열·system·참고 조문이 글자 단위로 이전과 같습니다(`test_prompt_matches_training_format` 650문항 통과로 확인).

### 4단계. 간결형 후처리

`src/lawbot/answer_format.py` (새 파일)

```python
# src/lawbot/answer_format.py  →  모델 원문(4섹션)을 화면용 간결형으로 바꾸는 후처리 (gradio 미사용)
# 모델 입력([참고 조문])과 원문 출력은 바꾸지 않는다. 평가는 원문과 간결형을 따로 잰다 (eval_concise)
import re
from lawbot.refusal import is_refusal

SECTION = re.compile(r'【(결론|근거 조문|설명|참고)】')
ARTICLE = re.compile(r'제\d+조(?:의\d+)?')

def sections(raw):
    """{'결론': ..., '근거 조문': ...}. 생성 중이면 마지막 섹션은 아직 쓰는 중"""
    marks = list(SECTION.finditer(raw))
    return {m[1]: raw[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(raw)].strip()
            for i, m in enumerate(marks)}

def hit_label(h):
    return f"{h.get('law', '근로기준법')} {h['article_id']}({h['title']})"

def cited_hits(cite_text, hits):
    """【근거 조문】 글에서 실제로 인용한 조문: (검색 결과의 조문, 검색 결과에 없거나 법령을 특정할 수 없는 번호)
    v4는 법령마다 번호가 겹치므로 '법령명 제N조'가 맞는 조문을 고르고, 법령명이 없으면 번호가 하나뿐일 때만 인정"""
    if cite_text.startswith('해당 없음'):           # 거절 형식의 제공 조문 목록은 인용이 아님 (refusal.cited_articles와 같음)
        return [], []
    flat = re.sub(r'\s+', '', cite_text)
    used, unknown = [], []
    for a in dict.fromkeys(ARTICLE.findall(cite_text)):
        cands = [h for h in hits if h['article_id'] == a]
        named = [h for h in cands
                 if re.search(re.escape(re.sub(r'\s+', '', h.get('law', ''))) + re.escape(a) + r'(?!의\d|\d)', flat)]
        pick = named or (cands if len(cands) == 1 else [])
        used += [h for h in pick if h not in used]
        if not pick:
            unknown.append(a)
    return used, unknown

def concise(raw, hits, done=True):
    """간결형: 결론 + '근거: ' 실제 인용 조문만. 4섹션 표시가 없으면 원문 그대로 (형식을 벗어난 답변을 숨기지 않음)"""
    sec = sections(raw)
    if '결론' not in sec:
        if not done and '【결론】'.startswith(raw.strip()):   # 표시 기호를 아직 쓰는 중
            return ''
        return raw.strip()
    out = sec['결론']
    if is_refusal(raw):                            # 거절은 근거 줄 없이 결론만
        return out
    if done or '설명' in sec or '참고' in sec:     # 근거 조문 섹션이 끝난 뒤에 한 번에 붙인다 (쓰는 중인 번호를 표시하지 않음)
        used, unknown = cited_hits(sec.get('근거 조문', ''), hits)
        if used:
            out += '\n\n근거: ' + ', '.join(hit_label(h) for h in used)
        if unknown:
            out += f"\n\n⚠ 검색된 조문에서 확인되지 않은 인용: {', '.join(unknown)}"
        if not used and not unknown:
            out += '\n\n⚠ 인용 조문 없음: 오른쪽 조문을 직접 확인하세요.'
    return out

def render(raw, hits, fmt, done=True):
    """fmt: 'concise'(간결형) / 'full'(모델 원문 4섹션)"""
    return concise(raw, hits, done) if fmt == 'concise' else raw
```

규칙:

| 원문 상태 | 간결형 화면 |
| --- | --- |
| `【결론】`을 쓰는 중 | 결론 글만 스트리밍 (`【결` 같은 기호 조각은 숨김) |
| `【근거 조문】`을 쓰는 중 | 결론만 (쓰다 만 번호를 붙이지 않음) |
| `【설명】`이 시작됐거나 생성 완료 | `결론\n\n근거: 법령 제N조(제목)` — **모델이 인용한 조문 중 검색 결과에 있는 것만** |
| 검색 결과에 없는 번호 인용 (예: 벌칙 조문) | `⚠ 검색된 조문에서 확인되지 않은 인용: 제109조` |
| v4에서 법령명 없이 번호만, 같은 번호가 여러 법령에 있음 | ⚠로 표시 (임의로 고르지 않음) |
| 거절 (`【결론】`에 '확인할 수 없') | 결론만, 근거 줄 없음 |
| 4섹션 형식이 아님 | 원문 그대로 (형식 이탈을 숨기지 않음) |

`【설명】`·`【참고】`(벌칙·예외·상담처)는 간결형 화면에 나오지 않습니다. 이 때문에 벌칙 안내가 빠지는 영향은 10단계에서 따로 잽니다(`벌칙 언급`, `근거 없는 벌칙 의심`). 참고로 test 모범 답안 650개 중 353개가 【참고】에 벌칙을 언급하고, 20문항은 채점 키워드에 벌칙·벌금이 들어 있습니다 → 기존 키워드 채점을 간결형 화면에 그대로 적용하면 점수가 떨어지는 것이 정상이며, 원문 점수와 따로 봐야 합니다.

### 5단계. 서비스 계층: 모델 관리·질의 흐름·측정

`src/lawbot/service.py` (전체 교체, 기존 `load_models`·`answer_stream` 유지)

```python
# src/lawbot/service.py  →  UI와 무관한 질의응답 흐름 (Gradio 앱이 쓰고, 이후 API 서버도 그대로 씀)
# 이 모듈은 gradio를 import하지 않는다 (tests/test_pipeline.py가 검사)
import gc
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from lawbot import deploy
from lawbot.paths import LLM_4BIT
from lawbot.retriever import Retriever
from lawbot.llm import LLM

TOP_K = 3                            # 답변 근거로 넘기는 조문 수 (Fast·Pro 공통)

def load_models():
    """검색기·sLLM을 불러온다. LLM_PATH 환경 변수로 다른 sLLM을 지정 (평가·기존 호출용, 웹앱은 load_app)"""
    return Retriever(), LLM(os.environ.get('LLM_PATH', LLM_4BIT))

def answer_stream(retriever, llm, question, k=TOP_K):
    """(근거 조문 목록, 답변 조각 생성기). 조문은 생성 전에 정해지므로 먼저 돌려준다"""
    hits = retriever.search(question, k=k)
    return hits, (r.text for r in llm.stream(question, hits))

def free_memory():
    """내린 모델의 Metal 버퍼를 반납 (MLX는 해제한 버퍼를 캐시에 남겨 둠)"""
    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except ImportError:              # 테스트의 가짜 모듈 환경
        pass

class ModelManager:
    """답변 모델 지연 로딩 + LRU. 상주 모델 수는 max_resident (deploy.max_resident)
    선택은 요청마다 key로 받는다: '현재 모델' 같은 공유 상태를 두지 않아 이전 선택이 다음 질문에 섞이지 않음"""
    def __init__(self, specs, loader, max_resident=2):
        self.specs = {s.key: s for s in specs}
        self.loader, self.max_resident = loader, max_resident
        self.loaded = OrderedDict()
        self.lock = threading.Lock()     # 로딩·교체를 한 번에 하나만 (생성 자체는 Gradio concurrency_limit=1)

    def is_loaded(self, key):
        return key in self.loaded

    def get(self, key):
        """(모델, 이번에 로딩한 초). 이미 올라 있으면 로딩 초는 None"""
        spec = self.specs[key]           # 없는 key는 KeyError
        with self.lock:
            if key in self.loaded:
                self.loaded.move_to_end(key)
                return self.loaded[key], None
            while len(self.loaded) >= self.max_resident:   # 새 모델을 올리기 전에 내려서 최대 메모리를 줄임
                self.loaded.popitem(last=False)
                free_memory()
            t0 = time.perf_counter()
            llm = self.loader(spec)
            self.loaded[key] = llm
            return llm, time.perf_counter() - t0

@dataclass
class LawbotApp:
    profile: deploy.Profile
    retriever: object
    models: ModelManager
    early_stop: bool = False

def load_app(profile=None):
    """웹앱용: 검색기 1개 + Fast/Pro 레지스트리. 기본 모델(Fast)은 미리 올리고, 2개 상주면 Pro도 시도"""
    p = profile or deploy.get_profile()
    retriever = Retriever(p.emb, p.articles, max_seq_length=p.max_seq, doc_style=p.doc_style)
    kw = {'prompt_style': p.doc_style}
    if p.system_file:
        kw['system'] = p.system_file.read_text(encoding='utf-8').strip()
    models = ModelManager(p.models, lambda spec: LLM(spec.path, **kw), deploy.max_resident())
    models.get(p.models[0].key)          # 기본 모델이 없으면 여기서 실행 중단
    if models.max_resident >= 2:
        for spec in p.models[1:]:
            try:
                models.get(spec.key)
            except Exception as e:       # Pro 준비 전에도 Fast로는 실행 (선택하면 오류 안내)
                print(f'[lawbot] {spec.label} 미리 불러오기 실패: {type(e).__name__}: {e}')
    print(f"[lawbot] 프로필 {p.name} · 조문 {len(retriever.ids)}개 · 상주 {list(models.loaded)}")
    return LawbotApp(p, retriever, models, deploy.early_stop())

def ask(app, question, model_key, k=TOP_K, early_stop=False):
    """질문 하나: (단계, 검색 조문, 답변 원문, 측정값)을 차례로 돌려준다
    단계 'load'(모델 전환 대기) → 'generate'(토큰마다) → 'done'. 조문은 생성 전에 정해짐
    측정값(초): search, load(이번에 로딩했을 때만), ttft(생성 호출→첫 토큰, 프리필), first_text(질문 수신→첫 글자),
    total, early_stop_at(간결형 조기 종료 시점) / tps, prompt_tokens, prompt_tps(프리필 속도), gen_tokens, peak_gb, finish"""
    t0 = time.perf_counter()
    st = {'model': model_key, 'load': None}
    hits = app.retriever.search(question, k=k)
    st['search'] = time.perf_counter() - t0
    if not app.models.is_loaded(model_key):
        yield 'load', hits, '', st
    llm, st['load'] = app.models.get(model_key)
    raw, last, t_gen = '', None, time.perf_counter()
    for r in llm.stream(question, hits):
        if last is None:
            st['ttft'] = time.perf_counter() - t_gen
        last = r
        if r.text and not raw:
            st['first_text'] = time.perf_counter() - t0
        raw += r.text
        yield 'generate', hits, raw, st
        if early_stop and '【설명】' in raw:     # 간결형은 결론·근거 조문까지만 보여 줌
            st['early_stop_at'] = time.perf_counter() - t0
            break
    st['total'] = time.perf_counter() - t0
    for name, attr in (('tps', 'generation_tps'), ('prompt_tokens', 'prompt_tokens'), ('prompt_tps', 'prompt_tps'),
                       ('gen_tokens', 'generation_tokens'), ('peak_gb', 'peak_memory'),
                       ('finish', 'finish_reason')):
        st[name] = getattr(last, attr, None)
    yield 'done', hits, raw, st
```

| 측정값 | 정의 |
| --- | --- |
| `search` | 질문 수신 → 검색 끝 |
| `load` | 이번 요청에서 모델을 올린 시간 (모델 전환). 이미 상주하면 없음 |
| `ttft` | `llm.stream` 호출 → 첫 토큰 (= 프리필. 참고 조문이 길수록 김) |
| `first_text` | 질문 수신 → 첫 글자(빈 문자열이 아닌 첫 조각). 검색·전환·프리필 포함 |
| `total` | 질문 수신 → 생성 끝 (조기 종료 시 그 시점) |
| `tps`, `prompt_tps`, `prompt_tokens`, `gen_tokens`, `peak_gb`, `finish` | `mlx_lm` 마지막 응답 값. `finish == 'length'`면 `max_tokens`(1024)에서 잘림 |

'첫 글자 화면 표시'는 여기에 Gradio 큐·전송 지연이 더해집니다 → 9단계 `bench_ui`로 잽니다.

### 6단계. 웹앱 화면

`src/lawbot/app.py` (전체 교체)

```python
# src/lawbot/app.py  →  uv run lawbot-app
import gradio as gr
from lawbot.answer_format import render
from lawbot.service import ask, load_app
from lawbot.security import get_local_launch_config

EXAMPLES = ['수습 기간에도 해고 예고를 해야 하나요?',
            '주휴수당은 어떤 조건에서 받을 수 있나요?',
            '회사가 연차를 못 쓰게 하면 어떻게 되나요?']
EMPTY = '답변을 생성하지 못했습니다. 다시 질문해 주세요.'

def fmt_sources(hits):
    return '\n\n---\n\n'.join(
        f"**{h['law'] + ' ' if 'law' in h else ''}{h['article_id']} {h['title']}** · 유사도 {h['score']:.3f}\n\n"
        f"{h['doc'].split(chr(10), 1)[1]}"
        for h in hits)

def fmt_stats(label, stage, st):
    """측정 칸: 서버 기준 시간. 브라우저 표시 지연은 lawbot.bench_ui로 따로 잰다"""
    if stage == 'load':
        return f'⏳ {label} 불러오는 중… (모델 전환 대기, 검색 {st["search"]:.2f}초)'
    parts = [label, f"검색 {st['search']:.2f}초"]
    if st['load'] is not None:
        parts.append(f"모델 전환 {st['load']:.1f}초")
    if 'ttft' in st:
        parts.append(f"첫 토큰 {st['ttft']:.1f}초")
    if 'first_text' in st:
        parts.append(f"첫 글자 {st['first_text']:.1f}초")
    if stage == 'done':
        if st.get('tps'):
            parts.append(f"{st['tps']:.1f} tok/s")
        parts.append(f"완료 {st['total']:.1f}초")
        if 'early_stop_at' in st:
            parts.append('간결형 조기 종료')
        if st.get('finish') == 'length':
            parts.append('⚠ 최대 길이에서 잘림')
    return ' · '.join(parts)

def make_chat(app):
    # 이름이 chat인 함수를 넘겨야 Gradio API 이름(/gradio_api/call/chat)이 유지됨
    # API 입력은 [질문, 모델 key, 형식] 세 개 (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)
    def chat(message, history, model_key, fmt):      # 단일 턴: history는 쓰지 않음
        if not (message or '').strip():
            yield '질문을 입력해 주세요.', '', ''
            return
        spec = app.models.specs.get(model_key)
        if spec is None or fmt not in ('concise', 'full'):
            raise gr.Error(f'알 수 없는 선택: {model_key!r}, {fmt!r}')
        early = fmt == 'concise' and app.early_stop
        try:
            for stage, hits, raw, st in ask(app, message, model_key, early_stop=early):
                src = fmt_sources(hits)          # 오른쪽 조문 3개는 생성 내내 같음
                if stage == 'load':
                    yield f'⏳ {spec.label} 불러오는 중…', src, fmt_stats(spec.label, stage, st)
                    continue
                text = render(raw, hits, fmt, done=stage == 'done')
                if stage == 'done' and not raw.strip():
                    text = EMPTY
                yield text, src, fmt_stats(spec.label, stage, st)
        except (OSError, ValueError) as e:       # 모델 폴더 없음·형식 오류 (mlx_lm.load)
            if app.models.is_loaded(model_key):
                raise                            # 생성 중 오류는 그대로 올림 (Gradio가 오류로 표시)
            raise gr.Error(f'{spec.label} 모델을 불러오지 못했습니다: {spec.path} ({type(e).__name__}). '
                           'uv run python -m lawbot.check_deploy로 확인하세요.') from e
    return chat

def model_choices(app):
    return [(f"{s.label} — {s.info}" + ('' if s.path.is_dir() else ' (모델 폴더 없음)'), s.key)
            for s in app.models.specs.values()]

def build_demo(app):                 # 화면만 만든다: 인증 검사·모델 로딩은 main에서
    p = app.profile
    with gr.Blocks(title=p.title) as demo:
        gr.Markdown(f'# {p.title}\n질문과 관련된 조문 3개를 찾아 그 조문을 근거로 답합니다. '
                    '법률 자문이 아닌 참고용 답변입니다.')
        sources = gr.Markdown(render=False)
        stats = gr.Markdown(render=False)
        with gr.Row():
            with gr.Column(scale=3):
                with gr.Row():
                    model = gr.Radio(model_choices(app), value=p.models[0].key, label='답변 모델',
                                     info='Fast는 4B, Pro는 14B 모델입니다. 같은 조문을 참고하며, Pro는 답변이 더 느립니다.')
                    fmt = gr.Radio([('간결', 'concise'), ('전체 (4단 형식)', 'full')], value='concise',
                                   label='답변 형식')
                gr.ChatInterface(
                    fn=make_chat(app),
                    additional_inputs=[model, fmt],
                    additional_outputs=[sources, stats],
                    examples=[[q, None, None] for q in EXAMPLES],   # None: 예시를 눌러도 현재 모델·형식 선택 유지
                    concurrency_limit=1)                            # 생성은 한 번에 하나 (두 모델 동시 생성 금지)
                stats.render()
            with gr.Column(scale=2):
                gr.Markdown('### 검색된 조문 (상위 3개)')
                sources.render()
    return demo

def main():
    config = get_local_launch_config()   # 인증 정보가 없으면 모델 로딩 전에 실행 거부
    demo = build_demo(load_app())
    demo.launch(**config)            # 127.0.0.1:7860, 로그인 필수, 공유 링크 없음

if __name__ == '__main__':
    main()
```

| 상황 | 화면 동작 |
| --- | --- |
| 빈 질문 | `질문을 입력해 주세요.` (검색하지 않음) |
| 선택한 모델이 메모리에 없음 | 조문 3개를 먼저 표시하고 `⏳ Lawbot Pro 불러오는 중…` → 답변 스트리밍, 측정 줄에 `모델 전환 N초` |
| 모델 폴더 없음·불러오기 실패 | Gradio 오류 창 `Lawbot Pro 모델을 불러오지 못했습니다: <경로>` (Fast는 계속 사용 가능). 라디오에 `(모델 폴더 없음)` 표시 |
| 생성 중 오류 | 예외를 그대로 올림 (빈 답변·부분 답변으로 끝내지 않음, 기존 동작) |
| 빈 응답 | `답변을 생성하지 못했습니다. 다시 질문해 주세요.` |
| 최대 길이에서 잘림 | 측정 줄에 `⚠ 최대 길이에서 잘림` |
| 중지 버튼 | Gradio가 생성기 소비를 멈춤. 그 요청의 측정 줄은 남지 않음 |
| 모델 전환 도중 다른 요청 | `concurrency_limit=1`로 큐에서 순서 대기 (Gradio 대기 표시) |

### 7단계. 테스트 갱신

`tests/fakes.py`, `tests/conftest.py`, `tests/test_pipeline.py`, `tests/test_auth_integration.py` 변경:

```diff
diff --git a/tests/fakes.py b/tests/fakes.py
index 146b342..eb80366 100644
--- a/tests/fakes.py
+++ b/tests/fakes.py
@@ -9,9 +9,11 @@ HITS = [{'article_id': '제26조', 'title': '해고의 예고', 'score': 0.91234
 
 class FakeRetriever:
     created = 0
+    ids = [h['article_id'] for h in HITS]
 
-    def __init__(self):
+    def __init__(self, *args, **kw):    # service.load_app이 넘기는 경로·설정을 기록
         FakeRetriever.created += 1
+        self.args = (args, kw)
 
     def search(self, query, k=3):
         self.query = query
@@ -21,9 +23,9 @@ class FakeLLM:
     created = 0
     chunks = ['【결론】', '예고해야', ' 합니다.']
 
-    def __init__(self, path):
+    def __init__(self, path, **kw):
         FakeLLM.created += 1
-        self.path = path
+        self.path, self.kw = path, kw
 
     def stream(self, question, hits):
         self.args = (question, hits)
```

```diff
diff --git a/tests/conftest.py b/tests/conftest.py
index 4c633d7..db4f5f0 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -4,6 +4,8 @@ import socket
 import pytest
 
 ENV_KEYS = ('LAWBOT_USER', 'LAWBOT_PW', 'LLM_PATH',
+            'LAWBOT_PROFILE', 'LAWBOT_EMB', 'LAWBOT_FAST_PATH', 'LAWBOT_PRO_PATH', 'LAWBOT_MAX_RESIDENT',
+            'LAWBOT_EARLY_STOP', 'LAWBOT_V4_ARTICLES', 'LAWBOT_V4_EMB', 'LAWBOT_V4_SYSTEM',
             'GRADIO_SERVER_NAME', 'GRADIO_SERVER_PORT', 'GRADIO_SHARE')
 
 @pytest.fixture(autouse=True)
```

```diff
diff --git a/tests/test_pipeline.py b/tests/test_pipeline.py
index 02b8884..dc2e57b 100644
--- a/tests/test_pipeline.py
+++ b/tests/test_pipeline.py
@@ -11,7 +11,7 @@ import lawbot.retriever as retriever_mod
 import lawbot.service as service
 from conftest import set_auth
 from fakes import HITS, FakeLLM, FakeRetriever
-from lawbot.llm import LLM, SYSTEM, build_prompt
+from lawbot.llm import LLM, SYSTEM, build_prompt, build_prompt_v4
 from lawbot.paths import ARTICLES, EVAL
 
 # ---- 검색 ----
@@ -124,6 +124,19 @@ def test_llm_stream_failure_propagates(fake_llm):
     with pytest.raises(RuntimeError, match='generation failed'):
         ''.join(r.text for r in m.stream('질문', [{'article_id': '제1조'}]))
 
+def test_llm_v4_prompt_and_system(fake_llm, env):
+    # v4: 프로필의 system 문자열과 법령명이 붙은 참고 조문을 쓴다 (v1 기본값은 위 테스트)
+    _, tok, _ = fake_llm
+    m = LLM('models/fake', system='V4 SYSTEM', prompt_style='v4')
+    hits = [{'law': '최저임금법', 'article_id': '제6조', 'title': '최저임금의 효력',
+             'doc': '최저임금법 제6조(최저임금의 효력)\n① 사용자는 지급하여야 한다. <개정 2020.5.26>'}]
+    list(m.stream('최저임금?', hits))
+    messages, _ = tok.calls[-1]
+    assert messages[0] == {'role': 'system', 'content': 'V4 SYSTEM'}
+    assert messages[1]['content'] == ('[참고 조문]\n[1] 최저임금법 제6조(최저임금의 효력)\n① 사용자는 지급하여야 한다.'
+                                      '\n\n[질문]\n최저임금?')
+    assert messages[1]['content'] == build_prompt_v4(hits, '최저임금?')
+
 def test_llm_load_failure_propagates(env):
     def missing(path):
         raise FileNotFoundError(path)
@@ -197,9 +210,11 @@ def test_import_does_not_check_auth_or_load_models(env):
         sys.modules.pop('lawbot.app', None)
 
 def test_build_demo_with_fakes_needs_no_auth(app):
-    demo = app.build_demo(FakeRetriever(), FakeLLM('models/fake'))
+    lawbot = service.load_app()
+    FakeRetriever.created = FakeLLM.created = 0
+    demo = app.build_demo(lawbot)
     assert isinstance(demo, app.gr.Blocks) and demo.title == '근로기준법 챗봇'
-    assert FakeRetriever.created == 1 and FakeLLM.created == 1   # 넘겨준 객체 외에 모델을 만들지 않음
+    assert FakeRetriever.created == 0 and FakeLLM.created == 0   # 넘겨준 객체 외에 모델을 만들지 않음
     assert app.launched == []
 
 def test_app_refuses_to_start_without_auth_before_loading_models(app):
@@ -217,36 +232,39 @@ def test_app_launches_local_only(app, env):
     assert demo.title == '근로기준법 챗봇'
     assert launched == {'server_name': '127.0.0.1', 'server_port': 7860,
                         'auth': ('tester', 'test-only-pw'), 'share': False}
-    assert FakeRetriever.created == 1 and FakeLLM.created == 1
+    assert FakeRetriever.created == 1 and FakeLLM.created == 2   # 검색기 1개 + Fast·Pro (기본 2개 상주)
 
 def test_app_loads_models_via_service(app, env):
-    # main은 service.load_models로 불러온 모델(LLM_PATH 반영)을 화면에 넘긴다
+    # main은 service.load_app으로 불러온 검색기·모델(LAWBOT_FAST_PATH 반영)을 화면에 넘긴다
     set_auth(env)
-    env.setenv('LLM_PATH', 'models/sllm_mlx_8bit')
-    build, models = app.build_demo, []
-    env.setattr(app, 'build_demo', lambda r, m: models.append((r, m)) or build(r, m))
+    env.setenv('LAWBOT_FAST_PATH', 'models/sllm_mlx_8bit')
+    build, apps = app.build_demo, []
+    env.setattr(app, 'build_demo', lambda a: apps.append(a) or build(a))
     app.main()
-    [(r, m)] = models
-    assert isinstance(r, FakeRetriever) and m.path == 'models/sllm_mlx_8bit'
+    [a] = apps
+    assert isinstance(a.retriever, FakeRetriever)
+    assert a.models.loaded['fast'].path == service.deploy.paths.ROOT / 'models/sllm_mlx_8bit'
     assert len(app.launched) == 1
 
 def test_chat_streams_answer_with_sources(app):
-    r, m = FakeRetriever(), FakeLLM('models/fake')
-    chat = app.make_chat(r, m)
+    lawbot = service.load_app()
+    chat = app.make_chat(lawbot)
     assert chat.__name__ == 'chat'                  # Gradio API 이름(/gradio_api/call/chat)
-    steps = list(chat('수습 기간에도 해고 예고?', []))
-    assert [t for t, _ in steps] == ['【결론】', '【결론】예고해야', '【결론】예고해야 합니다.']
+    steps = list(chat('수습 기간에도 해고 예고?', [], 'fast', 'full'))
+    # 토큰마다 누적 답변, 마지막은 생성 완료 후 측정값(완료 시간·tok/s)을 채운 같은 답변
+    assert [t for t, _, _ in steps] == ['【결론】', '【결론】예고해야', '【결론】예고해야 합니다.', '【결론】예고해야 합니다.']
     src = steps[-1][1]
-    assert all(s == src for _, s in steps)          # 근거 조문 칸은 답변 내내 같음
+    assert all(s == src for _, s, _ in steps)       # 근거 조문 칸은 답변 내내 같음
     assert '**제26조 해고의 예고** · 유사도 0.912' in src
     assert '30일 전에 예고' in src
     assert src.count('제26조 해고의 예고') == 1     # 조문 본문 첫 줄(제목 줄)은 중복 표시 안 함
-    assert r.query == '수습 기간에도 해고 예고?'
-    assert m.args == ('수습 기간에도 해고 예고?', HITS)
+    assert lawbot.retriever.query == '수습 기간에도 해고 예고?'
+    assert lawbot.models.loaded['fast'].args == ('수습 기간에도 해고 예고?', HITS)
+    assert steps[-1][2].startswith('Lawbot Fast · 검색') and '완료' in steps[-1][2]
 
 def test_chat_generation_failure_is_not_hidden(app):
     # 생성 실패를 빈 답변이나 부분 답변으로 끝내지 않고 예외로 올린다 (Gradio가 오류로 표시)
     FakeLLM.chunks = ['【결론】', RuntimeError('generation failed')]
-    chat = app.make_chat(FakeRetriever(), FakeLLM('models/fake'))
+    chat = app.make_chat(service.load_app())
     with pytest.raises(RuntimeError, match='generation failed'):
-        list(chat('질문', []))
+        list(chat('질문', [], 'fast', 'full'))
```

```diff
diff --git a/tests/test_auth_integration.py b/tests/test_auth_integration.py
index 56d5b1e..690841a 100644
--- a/tests/test_auth_integration.py
+++ b/tests/test_auth_integration.py
@@ -72,9 +72,10 @@ def chat_calls(server):
 def login(c, user, pw):
     return c.post('/login', data={'username': user, 'password': pw})
 
-def ask(c, question):
-    """chat API 호출. 성공하면 마지막 출력 (답변, 대화창, 근거 조문)을, 실패하면 응답 객체를 돌려준다"""
-    r = c.post('/gradio_api/call/chat', json={'data': [question]})
+def ask(c, question, model='fast', fmt='full'):
+    """chat API 호출. 성공하면 마지막 출력 (답변, 대화창, 근거 조문, 측정)을, 실패하면 응답 객체를 돌려준다
+    입력은 [질문, 모델 key, 답변 형식] (Gradio 6.30은 빠진 입력을 기본값으로 채우지 않음)"""
+    r = c.post('/gradio_api/call/chat', json={'data': [question, model, fmt]})
     if r.status_code != 200:
         return r
     events = c.get(f"/gradio_api/call/chat/{r.json()['event_id']}").text
@@ -91,7 +92,7 @@ def assert_locked_out(c, server):
     for path in PROTECTED_GET:
         assert c.get(path).status_code == 401, path
     for path in PROTECTED_POST:
-        assert c.post(path, json={'data': [QUESTION], 'fn_index': 0}).status_code == 401, path
+        assert c.post(path, json={'data': [QUESTION, 'fast', 'full'], 'fn_index': 0}).status_code == 401, path
     assert len(chat_calls(server)) == before      # chat 함수가 실행되지 않았음
 
 # A. 로그인 없이 접근하면 차단
@@ -130,7 +131,10 @@ def test_valid_login_can_use_chatbot(client, server):
     assert cfg.status_code == 200 and cfg.json()['title'] == '근로기준법 챗봇'
 
     before = len(chat_calls(server))
-    answer, _, sources = ask(client, QUESTION)
+    answer, _, sources, stats = ask(client, QUESTION)
     assert answer == '【결론】예고해야 합니다.'
     assert '제26조 해고의 예고' in sources
-    assert chat_calls(server)[before:] == [QUESTION]
+    assert stats.startswith('Lawbot Fast')
+    answer, _, _, stats = ask(client, QUESTION, 'pro', 'concise')   # 같은 로그인으로 모델·형식 선택
+    assert answer.startswith('예고해야 합니다.') and stats.startswith('Lawbot Pro')
+    assert chat_calls(server)[before:] == [QUESTION, QUESTION]
```

`tests/test_webapp.py` (새 파일)

```python
# tests/test_webapp.py  →  Fast/Pro 모델 선택·전환, 간결형 답변, v4 검색 형식, 배포 검사 (가짜 모델, 모델 파일 불필요)
import json
import struct
import types
import pandas as pd
import pytest
import lawbot.app as app_mod
import lawbot.retriever as retriever_mod
import lawbot.service as service
from fakes import HITS, FakeLLM, FakeRetriever
from lawbot import check_deploy, deploy, paths
from lawbot.answer_format import cited_hits, concise

class PathLLM(FakeLLM):
    """모델 폴더 이름을 답으로 내는 가짜 sLLM: 어떤 모델이 답했는지 확인용"""
    def stream(self, question, hits):
        self.args = (question, hits)
        for c in ('【결론】', self.path.name, '\n【근거 조문】\n근로기준법 제26조(해고의 예고)\n【설명】\n- 설명'):
            yield types.SimpleNamespace(text=c, generation_tps=20.0, finish_reason='stop')

def make_app(env, resident=2, llm=PathLLM, early_stop=False):
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', llm)
    env.setenv('LAWBOT_MAX_RESIDENT', str(resident))
    if early_stop:
        env.setenv('LAWBOT_EARLY_STOP', '1')
    return service.load_app()

# ---- 레지스트리 ----
def test_profiles_define_fast_and_pro(env):
    for name in ('v1', 'v4'):
        p = deploy.get_profile(name)
        assert [m.key for m in p.models] == ['fast', 'pro']
        assert [m.label for m in p.models] == ['Lawbot Fast', 'Lawbot Pro']
        assert all(m.path.is_absolute() for m in p.models)
        assert '정확' not in ' '.join(m.info for m in p.models)     # 검증 전 정확도 단정 금지
    v1, v4 = deploy.get_profile('v1'), deploy.get_profile('v4')
    assert (v1.articles, v1.emb, v1.max_seq, v1.doc_style) == (paths.ARTICLES, paths.EMB, 512, 'v1')
    assert v1.models[0].path == paths.LLM_4BIT and v1.system_file is None
    assert (v4.max_seq, v4.doc_style, v4.system_file) == (2304, 'v4', paths.V4_SYSTEM)
    assert {v4.articles, v4.emb, v4.models[0].path} & {v1.articles, v1.emb, v1.models[0].path} == set()

def test_profile_env_override_is_absolute(env):
    env.setenv('LAWBOT_PRO_PATH', 'models/qwen3_14b_qlora')
    assert deploy.get_profile('v1').models[1].path == paths.ROOT / 'models/qwen3_14b_qlora'
    env.setenv('LAWBOT_PROFILE', 'v9')
    with pytest.raises(ValueError, match='LAWBOT_PROFILE'):
        deploy.get_profile()
    env.setenv('LAWBOT_MAX_RESIDENT', '3')
    with pytest.raises(ValueError):
        deploy.max_resident()

# ---- 모델 관리 ----
def test_manager_lru_one_resident():
    loads = []
    specs = deploy.get_profile('v1').models
    m = service.ModelManager(specs, lambda s: loads.append(s.key) or s.key, max_resident=1)
    assert m.get('fast')[0] == 'fast' and m.get('fast')[1] is None
    llm, sec = m.get('pro')
    assert llm == 'pro' and sec is not None and list(m.loaded) == ['pro']   # Fast를 내린 뒤 Pro
    m.get('fast')
    assert loads == ['fast', 'pro', 'fast']
    with pytest.raises(KeyError):
        m.get('ultra')

def test_load_app_preloads_both_when_resident_two(env):
    a = make_app(env, resident=2)
    assert list(a.models.loaded) == ['fast', 'pro']
    assert a.retriever.args[1] == {'max_seq_length': 512, 'doc_style': 'v1'}
    assert a.models.loaded['fast'].kw == {'prompt_style': 'v1'}      # v1은 llm.SYSTEM 그대로

def test_load_app_v4_reads_system_file(env, tmp_path):
    (tmp_path / 'system.txt').write_text('V4 SYSTEM\n', encoding='utf-8')
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / 'system.txt'))
    a = make_app(env, resident=1)
    assert a.models.loaded['fast'].kw == {'prompt_style': 'v4', 'system': 'V4 SYSTEM'}
    assert a.retriever.args[1] == {'max_seq_length': 2304, 'doc_style': 'v4'}

def test_pro_preload_failure_keeps_fast(env, capsys):
    class ProMissing(PathLLM):
        def __init__(self, path, **kw):
            if path.name == paths.PRO_BASE.name:
                raise ValueError('Repo id must be in the form')     # mlx_lm.load가 없는 절대 경로에 내는 오류
            super().__init__(path, **kw)
    a = make_app(env, resident=2, llm=ProMissing)
    assert list(a.models.loaded) == ['fast']
    assert 'Lawbot Pro 미리 불러오기 실패' in capsys.readouterr().out
    chat = app_mod.make_chat(a)
    with pytest.raises(app_mod.gr.Error, match='Lawbot Pro 모델을 불러오지 못했습니다'):
        list(chat('질문', [], 'pro', 'full'))
    assert list(chat('질문', [], 'fast', 'full'))[-1][0].startswith('【결론】sllm_mlx_4bit')

# ---- 모델 선택 ----
def test_selection_is_per_request(env):
    # 요청마다 고른 모델이 답한다: 이전 선택이 다음 질문에 남지 않음
    chat = app_mod.make_chat(make_app(env, resident=2))
    names = {'fast': paths.LLM_4BIT.name, 'pro': paths.PRO_BASE.name}
    for key in ('fast', 'pro', 'fast', 'pro', 'pro', 'fast'):
        text, _, stats = list(chat('해고 예고?', [], key, 'full'))[-1]
        assert names[key] in text and stats.startswith(f"Lawbot {key.title()}")

def test_fast_and_pro_get_same_context(env):
    a = make_app(env, resident=2)
    chat = app_mod.make_chat(a)
    src = [list(chat('수습 해고 예고?', [], k, 'full'))[-1][1] for k in ('fast', 'pro')]
    assert src[0] == src[1]
    assert a.models.loaded['fast'].args == a.models.loaded['pro'].args == ('수습 해고 예고?', HITS)

def test_switch_shows_loading_then_answer(env):
    chat = app_mod.make_chat(make_app(env, resident=1))
    steps = list(chat('질문', [], 'pro', 'concise'))
    assert steps[0][0] == '⏳ Lawbot Pro 불러오는 중…' and '모델 전환 대기' in steps[0][2]
    assert steps[0][1] == steps[-1][1] != ''            # 조문 카드는 로딩 중에도 먼저 보임
    assert '모델 전환' in steps[-1][2]
    assert '모델 전환' not in list(chat('질문', [], 'pro', 'concise'))[-1][2]   # 이미 올라 있으면 전환 없음

def test_invalid_inputs(env):
    a = make_app(env)
    chat = app_mod.make_chat(a)
    assert list(chat('   ', [], 'fast', 'concise')) == [('질문을 입력해 주세요.', '', '')]
    assert not hasattr(a.retriever, 'query')            # 빈 질문은 검색하지 않음
    for key, fmt in (('ultra', 'concise'), ('fast', 'long')):
        with pytest.raises(app_mod.gr.Error, match='알 수 없는 선택'):
            list(chat('질문', [], key, fmt))

def test_empty_and_truncated_answer(env):
    class Odd(FakeLLM):
        def stream(self, question, hits):
            yield types.SimpleNamespace(text=Odd.text, finish_reason=Odd.finish)
    Odd.text, Odd.finish = ' ', 'stop'
    chat = app_mod.make_chat(make_app(env, llm=Odd))
    assert list(chat('질문', [], 'fast', 'concise'))[-1][0] == app_mod.EMPTY
    Odd.text, Odd.finish = '【결론】길게', 'length'
    assert '⚠ 최대 길이에서 잘림' in list(chat('질문', [], 'fast', 'full'))[-1][2]

def test_early_stop_cuts_generation(env):
    seen = []
    class Long(FakeLLM):
        def stream(self, question, hits):
            for c in ('【결론】', '예고해야 합니다.', '\n【근거 조문】\n제26조(해고의 예고)\n', '【설명】', '- 긴 설명', '【참고】'):
                seen.append(c)
                yield types.SimpleNamespace(text=c)
    chat = app_mod.make_chat(make_app(env, llm=Long, early_stop=True))
    text, _, stats = list(chat('질문', [], 'fast', 'concise'))[-1]
    assert text == '예고해야 합니다.\n\n근거: 근로기준법 제26조(해고의 예고)'
    assert '간결형 조기 종료' in stats and seen[-1] == '【설명】'
    seen.clear()                                        # 전체 형식은 조기 종료하지 않음
    assert list(chat('질문', [], 'fast', 'full'))[-1][0].endswith('【참고】') and seen[-1] == '【참고】'

# ---- 간결형 ----
FULL = ('【결론】\n연장근로수당은 통상임금의 50% 이상을 가산해 지급하는 것이 원칙입니다.\n\n'
        '【근거 조문】\n근로기준법 제56조(연장·야간 및 휴일 근로) 제1항\n\n'
        '【설명】\n- 제56조: 사용자는 ...\n\n【참고】\n- 위반 시 3년 이하의 징역 또는 3천만원 이하의 벌금')
H56 = [{'article_id': a, 'title': t, 'score': 0.9, 'doc': f'{a} {t}\n본문'}
       for a, t in (('제56조', '연장·야간 및 휴일 근로'), ('제55조', '휴일'), ('제50조', '근로시간'))]

def test_concise_shows_only_cited_articles():
    out = concise(FULL, H56)
    assert out == ('연장근로수당은 통상임금의 50% 이상을 가산해 지급하는 것이 원칙입니다.\n\n'
                   '근거: 근로기준법 제56조(연장·야간 및 휴일 근로)')
    assert '제55조' not in out and '벌금' not in out and '【' not in out

def test_concise_streaming_steps():
    assert concise('【결', H56, done=False) == ''                       # 표시 기호를 쓰는 중
    partial = FULL[:FULL.index('제1항')]
    assert '근거:' not in concise(partial, H56, done=False)             # 근거 조문을 쓰는 중이면 붙이지 않음
    assert concise(FULL[:FULL.index('【설명】') + 5], H56, done=False).endswith('휴일 근로)')

def test_concise_refusal_and_fallbacks():
    refusal = '【결론】\n제공된 조문으로는 확인할 수 없습니다.\n\n【근거 조문】\n해당 없음 (제공된 조문: 제56조, 제55조)'
    assert concise(refusal, H56) == '제공된 조문으로는 확인할 수 없습니다.'
    assert concise('형식 없는 답변', H56) == '형식 없는 답변'                # 형식을 벗어난 답은 숨기지 않음
    outside = FULL.replace('제56조(연장·야간 및 휴일 근로) 제1항', '제56조, 제109조(벌칙)')
    assert concise(outside, H56).endswith('⚠ 검색된 조문에서 확인되지 않은 인용: 제109조')
    no_cite = '【결론】\n가산해야 합니다.\n\n【설명】\n- 설명'
    assert '⚠ 인용 조문 없음' in concise(no_cite, H56)

def test_cited_hits_v4_law_names():
    hits = [{'law': '근로기준법', 'article_id': '제2조', 'title': '정의'},
            {'law': '최저임금법', 'article_id': '제2조', 'title': '정의'},
            {'law': '최저임금법', 'article_id': '제6조', 'title': '최저임금의 효력'}]
    used, unknown = cited_hits('최저임금법 제2조(정의), 제6조(최저임금의 효력)', hits)
    assert used == hits[1:] and unknown == []
    used, unknown = cited_hits('제2조(정의)', hits)                     # 법령을 특정할 수 없음
    assert used == [] and unknown == ['제2조']
    assert cited_hits('근로기준법 제2조의2', hits) == ([], ['제2조의2'])   # 번호가 비슷한 조문과 구분

# ---- v4 검색 문서 형식 ----
class FakeEncoder:
    def __init__(self, path, device=None, model_kwargs=None):
        pass

    def encode(self, texts, prompt_name=None, **kw):
        import numpy as np
        return np.eye(len(texts), 8, dtype=np.float32) if prompt_name is None else np.ones((len(texts), 8), np.float32)

def test_retriever_v4_format(env, tmp_path):
    csv = tmp_path / 'v4.csv'
    pd.DataFrame({'law': ['근로기준법', '최저임금법', '최저임금법'], 'article_id': ['제2조', '제2조', '제3조'],
                  'title': ['정의', '정의', '삭제'], 'deleted': [0, 0, 1], 'text': ['본문 A', '본문 B', '']}
                 ).to_csv(csv, index=False, encoding='utf-8-sig')
    env.setattr(retriever_mod, 'SentenceTransformer', FakeEncoder)
    env.setattr(retriever_mod.torch.mps, 'empty_cache', lambda: None)
    r = retriever_mod.Retriever('models/fake', csv, max_seq_length=2304, doc_style='v4')
    assert r.docs == ['근로기준법 제2조(정의)\n본문 A', '최저임금법 제2조(정의)\n본문 B']
    assert r.m.max_seq_length == 2304 and r.starter == set()
    hits = r.search('정의', k=2)
    assert {(h['law'], h['article_id']) for h in hits} == {('근로기준법', '제2조'), ('최저임금법', '제2조')}
    assert app_mod.fmt_sources(hits).count('**최저임금법 제2조 정의**') == 1

# ---- 배포 검사 ----
def write_safetensors(path, dtype):
    header = json.dumps({'w': {'dtype': dtype, 'shape': [1], 'data_offsets': [0, 2]}}).encode()
    path.write_bytes(struct.pack('<Q', len(header)) + header + b'\0\0')

def fake_mlx(folder, bits=4, hidden=2560, adapter=False):
    folder.mkdir(parents=True)
    (folder / 'config.json').write_text(json.dumps({'model_type': 'qwen3', 'hidden_size': hidden,
                                                    'quantization': {'group_size': 64, 'bits': bits}}))
    (folder / 'tokenizer.json').write_text('{}')
    (folder / 'tokenizer_config.json').write_text(json.dumps({'chat_template': '{{ messages }}'}))
    write_safetensors(folder / 'model.safetensors', 'U32')
    if adapter:
        (folder / 'adapters.safetensors').write_bytes(b'')

def test_check_deploy_reports_each_asset(env, tmp_path):
    emb = tmp_path / 'emb'
    emb.mkdir()
    write_safetensors(emb / 'model.safetensors', 'F16')
    (emb / 'config_sentence_transformers.json').write_text(json.dumps({'prompts': {'query': 'Instruct: q\nQuery:'}}))
    (emb / 'modules.json').write_text('[]')
    (emb / 'tokenizer.json').write_text('{}')
    fake_mlx(tmp_path / 'fast')
    fake_mlx(tmp_path / 'pro', bits=4, hidden=5120, adapter=True)
    env.setenv('LAWBOT_EMB', str(emb))
    env.setenv('LAWBOT_FAST_PATH', str(tmp_path / 'fast'))
    env.setenv('LAWBOT_PRO_PATH', str(tmp_path / 'pro'))
    out = dict((msg, level) for level, msg in check_deploy.run('v1'))
    text = '\n'.join(f'{lv} {m}' for m, lv in out.items())
    assert 'OK 비삭제 조문 130개 (기대 130)' in text
    assert "dtype {'F16': 1}" in text and "query 프롬프트: 'Instruct: q\\nQuery:'" in text
    assert 'OK Lawbot Fast: qwen3 Qwen3-4B' in text and 'Qwen3-14B' in text
    assert "FAIL Lawbot Pro: 어댑터 파일 남아 있음 ['adapters.safetensors']" in text

def test_check_deploy_v4_placeholders_fail(env, tmp_path):
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / '없음.csv'))
    env.setenv('LAWBOT_V4_SYSTEM', str(tmp_path / '없음.txt'))
    env.setenv('LAWBOT_V4_EMB', str(tmp_path / '없음'))
    env.setenv('LAWBOT_FAST_PATH', str(tmp_path / '없음4b'))
    env.setenv('LAWBOT_PRO_PATH', str(tmp_path / '없음14b'))
    fails = [m for level, m in check_deploy.run('v4') if level == 'FAIL']
    assert len(fails) == 5 and all('없' in m for m in fails)

def test_eval_concise_refuses_test_set(env):
    import lawbot.eval_concise as ec
    env.setattr('sys.argv', ['eval_concise', '--val', str(paths.EVAL), '--tag', 'x'])
    with pytest.raises(SystemExit, match='평가 전용'):
        ec.main()
```

```fish
uv run python -m compileall -q src tests
uv run pytest -q          # 기대: 128 passed, 1 deselected
```

| 회귀 항목 | 테스트 |
| --- | --- |
| `chat` 이름 유지 (`/gradio_api/call/chat`) | `test_chat_streams_answer_with_sources`, 통합 `test_valid_login_can_use_chatbot` |
| `service.py` gradio 미사용 | `test_service_does_not_import_gradio` |
| 인증 필수·127.0.0.1·`share=False` | `test_security.py`, `test_app_launches_local_only`, 통합 테스트 전체 |
| 단일 질문 스트리밍·조문 3건 고정 표시 | `test_chat_streams_answer_with_sources`, `test_switch_shows_loading_then_answer` |
| 모델 선택이 요청마다 독립 | `test_selection_is_per_request` |
| Fast·Pro가 같은 조문 컨텍스트 | `test_fast_and_pro_get_same_context` |
| 모델 로딩 실패 | `test_pro_preload_failure_keeps_fast`, `test_llm_load_failure_propagates` |
| 생성 실패 숨기지 않음 | `test_chat_generation_failure_is_not_hidden` |
| 간결형: 실제 인용만 근거, 거절, 형식 이탈, v4 법령명 | `test_concise_*`, `test_cited_hits_v4_law_names` |
| v1 학습 프롬프트 글자 일치 (650문항) | `test_prompt_matches_training_format` (변경 없음) |
| test 셋으로 형식 선택 금지 | `test_eval_concise_refuses_test_set` |

여기까지 커밋합니다.

```fish
git add -A
git commit -m "feat: 웹앱 Fast/Pro 모델 선택, 간결형 답변, v4 검색 프로필"
```

> 한 번에 적용하려면: 같은 내용의 패치가 `docs/webapp_fast_pro.patch`에 있습니다. `git apply --check docs/webapp_fast_pro.patch; and git apply docs/webapp_fast_pro.patch` (작성 시점 `main`에 적용 확인됨). 단계별 실습을 원하면 위 묶음을 차례로 넣으세요.

### 8단계. Pro(14B) 준비와 v1 프로필 첫 실행

v4 14B QLoRA가 나오기 전에는 **튜닝 전 Qwen3-14B 4비트**로 속도·메모리·전환만 먼저 봅니다. 4B와 같은 양자화 설정(4비트, group 64)으로 직접 변환하는 쪽을 권합니다 (bf16 원본 약 30GB를 내려받고, 결과는 약 8GB 예상 — 미확인).

```fish
uv run mlx_lm.convert --hf-path Qwen/Qwen3-14B --mlx-path models/qwen3_14b_mlx_4bit -q --q-bits 4 --q-group-size 64
uv run python -m lawbot.check_deploy v1      # Pro: qwen3 Qwen3-14B · 양자화 bits 4 · 어댑터 없음
```

> 튜닝 전 14B는 v1 4섹션 형식으로 학습되지 않았으므로 답변 형식·품질은 비교 대상이 아닙니다(간결형이 형식 이탈 시 원문을 그대로 보여 줌). 이 단계의 목적은 **지연·메모리·전환** 측정입니다.

웹앱 실행 (비밀번호는 기존 README 방식 그대로):

```fish
set -gx LAWBOT_USER '원하는 사용자명'
read -gxs -P '비밀번호: ' LAWBOT_PW
uv run lawbot-app
```

| 화면 확인 | 결과 |
| --- | --- |
| 시작 로그 `[lawbot] 프로필 v1 · 조문 130개 · 상주 ['fast', 'pro']` | |
| Fast 간결형: `결론` + `근거: 근로기준법 제N조(제목)` | |
| 같은 질문 Pro: 오른쪽 조문 3개가 Fast와 같음 | |
| 형식 '전체'에서 4섹션 원문 | |
| 측정 줄 값 (아래 결과 표에 기록) | |
| 예시 질문 클릭 시 선택한 모델 유지 | |

### 9단계. 측정: 동시 상주 → 필요하면 1개 상주

웹앱을 끄고(같은 프로세스가 메모리를 잡으므로) UI 없는 측정부터 합니다.

`src/lawbot/bench_models.py` (새 파일)

```python
# src/lawbot/bench_models.py  →  uv run python -m lawbot.bench_models --tag m32_v1_r2
# 웹앱과 같은 경로(service.load_app·ask)로 Fast/Pro를 번갈아 질문하며 지연·메모리를 잰다 (UI 없이, 맥 전용)
#   LAWBOT_MAX_RESIDENT=2: 동시 상주 / 1: 전환 때마다 다시 로딩 (전환 시간 측정)
# 결과: results/bench_<tag>.csv (질문마다 한 행) + 모델별 중앙값 표
import argparse
import platform
import re
import subprocess
import time
import pandas as pd
from lawbot.answer_format import concise
from lawbot.paths import RESULTS
from lawbot.service import ask, load_app

# 지연 측정용 고정 질문 (평가 데이터가 아님). 첫 질문은 가이드의 간결형 예시
QUESTIONS = ['연장근로수당은 얼마나 더 받나요?',
             '수습 기간에도 해고 예고를 해야 하나요?',
             '주휴수당은 어떤 조건에서 받을 수 있나요?',
             '회사가 연차를 못 쓰게 하면 어떻게 되나요?',
             '퇴직금은 어떻게 계산하나요?']
PRESSURE = {1: '정상', 2: '경고', 4: '위험'}

def sysctl(name):
    if platform.system() != 'Darwin':
        return None
    r = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None

def memory():
    """macOS 메모리 압박 단계·스왑 사용량, MLX·PyTorch MPS가 잡은 메모리(GB)"""
    m = {}
    level = sysctl('kern.memorystatus_vm_pressure_level')
    m['pressure'] = PRESSURE.get(int(level), level) if level else None
    swap = sysctl('vm.swapusage')          # 'total = 2048.00M  used = 1001.25M  free = ...'
    used = re.search(r'used = ([\d.]+)M', swap or '')
    m['swap_used_gb'] = round(float(used[1]) / 1024, 2) if used else None
    try:
        import mlx.core as mx
        m['mlx_active_gb'] = round(mx.get_active_memory() / 1e9, 2)
        m['mlx_peak_gb'] = round(mx.get_peak_memory() / 1e9, 2)
    except ImportError:
        pass
    try:
        import torch
        m['mps_driver_gb'] = round(torch.mps.driver_allocated_memory() / 1e9, 2)
    except (ImportError, RuntimeError, AttributeError):
        pass
    return m

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True, help='결과 파일 이름 (예: m32_v1_r2)')
    ap.add_argument('--models', default='fast,pro', help='번갈아 물을 모델 key 순서')
    ap.add_argument('--rounds', type=int, default=2, help='질문 목록 반복 횟수 (1회차는 워밍업 포함)')
    ap.add_argument('--early-stop', action='store_true', help='간결형 조기 종료 조건으로 측정')
    args = ap.parse_args()
    pd.set_option('display.width', 200, 'display.max_columns', None)

    print('시작 메모리', memory())
    t0 = time.perf_counter()
    app = load_app()
    print(f'앱 준비 {time.perf_counter() - t0:.1f}초 · 상주 {list(app.models.loaded)} ·', memory())
    rows = []
    for rnd in range(args.rounds):
        for q in QUESTIONS:
            for key in args.models.split(','):
                for stage, hits, raw, st in ask(app, q, key, early_stop=args.early_stop):
                    pass
                rows.append({'round': rnd, 'model': key, 'question': q, **st, **memory(),
                             'answer_len': len(raw), 'concise_len': len(concise(raw, hits)),
                             'resident': ','.join(app.models.loaded), 'answer': raw})
                r = rows[-1]
                print(f"[{rnd}] {key:4} load={r['load'] or 0:5.1f} ttft={r.get('ttft', 0):5.2f} "
                      f"first={r.get('first_text', 0):5.2f} tps={r['tps'] or 0:5.1f} total={r['total']:5.1f} "
                      f"mlx_peak={r.get('mlx_peak_gb')} pressure={r['pressure']} swap={r['swap_used_gb']}")
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'bench_{args.tag}.csv', index=False, encoding='utf-8-sig')
    warm = o[o['round'] > 0] if args.rounds > 1 else o
    cols = ['search', 'ttft', 'first_text', 'tps', 'total', 'gen_tokens', 'prompt_tokens', 'prompt_tps']
    print(f"\n[중앙값, {'워밍업(1회차) 제외 ' if args.rounds > 1 else ''}{len(warm)}건]")
    print(warm.groupby('model')[[c for c in cols if c in warm]].median().round(2))
    print('\n전환(로딩) 초:', o.loc[o['load'].notna(), ['round', 'model', 'load']].round(1).values.tolist())
    print('최대 MLX 메모리(GB):', o['mlx_peak_gb'].max() if 'mlx_peak_gb' in o else None,
          '· 최악 압박:', o['pressure'].dropna().unique().tolist(), '· 최대 스왑(GB):', o['swap_used_gb'].max())
    print('잘림(finish=length):', o.groupby('model')['finish'].apply(lambda s: (s == 'length').sum()).to_dict())

if __name__ == '__main__':
    main()
```

`src/lawbot/bench_ui.py` (새 파일)

```python
# src/lawbot/bench_ui.py  →  (다른 터미널에서 lawbot-app 실행 중) uv run python -m lawbot.bench_ui --tag m32_v1_ui
# 실행 중인 웹앱에 로그인해 /gradio_api/call/chat 스트림을 받으며 '질문 전송 → 첫 글자 도착' 시간을 잰다
# 서버 측 측정(bench_models)에 Gradio 큐·전송 지연이 더해진 값. 브라우저 그리기 시간(수십 ms)은 포함하지 않음
# 접속 대상은 security.py의 127.0.0.1:7860 고정. 계정은 웹앱과 같은 LAWBOT_USER/LAWBOT_PW 환경 변수
import argparse
import json
import os
import time
import httpx
import pandas as pd
from lawbot.bench_models import QUESTIONS
from lawbot.paths import RESULTS
from lawbot.security import SERVER_NAME, SERVER_PORT

def ask_timed(c, question, model_key, fmt):
    t0 = time.perf_counter()
    r = c.post('/gradio_api/call/chat', json={'data': [question, model_key, fmt]})
    r.raise_for_status()
    first = last = None
    answer, stats, error = '', '', None
    with c.stream('GET', f"/gradio_api/call/chat/{r.json()['event_id']}", timeout=600) as s:
        event = None
        for line in s.iter_lines():
            if line.startswith('event: '):
                event = line.removeprefix('event: ')
            elif line.startswith('data: '):
                data = json.loads(line.removeprefix('data: '))
                if event == 'error':
                    error = data
                    break
                if isinstance(data, list) and data and data[0]:
                    answer, stats = data[0], data[-1]
                    if first is None and not answer.startswith('⏳'):
                        first = time.perf_counter() - t0
                last = time.perf_counter() - t0
    return {'model': model_key, 'fmt': fmt, 'question': question, 'ui_first_text': first, 'ui_done': last,
            'server_stats': stats, 'error': error, 'answer': answer}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--models', default='fast,pro')
    ap.add_argument('--fmt', default='concise', choices=['concise', 'full'])
    args = ap.parse_args()
    pd.set_option('display.width', 200, 'display.max_columns', None)
    user, pw = os.environ.get('LAWBOT_USER', ''), os.environ.get('LAWBOT_PW', '')
    if not user.strip() or not pw.strip():
        raise SystemExit('LAWBOT_USER/LAWBOT_PW 환경 변수가 필요합니다 (웹앱과 같은 값)')
    rows = []
    with httpx.Client(base_url=f'http://{SERVER_NAME}:{SERVER_PORT}', timeout=30) as c:
        if c.post('/login', data={'username': user, 'password': pw}).status_code != 200:
            raise SystemExit('로그인 실패')
        for q in QUESTIONS:
            for key in args.models.split(','):
                rows.append(ask_timed(c, q, key, args.fmt))
                r = rows[-1]
                print(f"{key:4} 첫 글자 {r['ui_first_text'] or 0:5.1f}초 · 완료 {r['ui_done'] or 0:5.1f}초 · {r['server_stats']}")
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'bench_ui_{args.tag}.csv', index=False, encoding='utf-8-sig')
    print(o.groupby('model')[['ui_first_text', 'ui_done']].median().round(2))
    print('15초 안에 첫 글자:', o.groupby('model')['ui_first_text'].apply(lambda s: f'{(s <= 15).sum()}/{len(s)}').to_dict())

if __name__ == '__main__':
    main()
```

```fish
# (1) 동시 상주: Fast·Pro를 번갈아 5문항 × 2회
set -gx LAWBOT_MAX_RESIDENT 2
uv run python -m lawbot.bench_models --tag m32_v1_r2

# (2) 1개 상주: 매번 전환(다시 로딩) 시간까지
set -gx LAWBOT_MAX_RESIDENT 1
uv run python -m lawbot.bench_models --tag m32_v1_r1

# (3) 조기 종료 조건의 완료 시간 (화면 품질은 10단계에서)
set -gx LAWBOT_MAX_RESIDENT 2
uv run python -m lawbot.bench_models --tag m32_v1_r2_early --early-stop

# (4) 화면 기준 첫 글자: 터미널 A에서 웹앱 실행, 터미널 B에서 (같은 LAWBOT_USER/PW)
uv run lawbot-app                                      # 터미널 A
uv run python -m lawbot.bench_ui --tag m32_v1_ui       # 터미널 B
```

측정 중 `활성 상태 보기`(Activity Monitor) → 메모리 탭의 '메모리 압력' 그래프 색과 '스왑 사용'도 함께 봐 주세요. 스크립트는 `sysctl kern.memorystatus_vm_pressure_level`(1 정상 / 2 경고 / 4 위험)과 `vm.swapusage`를 기록합니다(macOS 값 이름은 미확인 — 비어 있으면 Activity Monitor 값을 표에 적기).

**동시 상주 판단 기준**

| 관찰 | 결정 |
| --- | --- |
| 압박 `정상`, 스왑 증가 없음, tok/s가 1개 상주 때와 비슷 | `LAWBOT_MAX_RESIDENT=2` 유지 (전환 0초) |
| 압박 `경고` 이상 또는 측정 중 스왑 증가, 또는 tok/s 눈에 띄게 하락 | `LAWBOT_MAX_RESIDENT=1`: 전환 대기 시간이 화면에 표시됨 |
| 1개 상주에서도 압박 | Pro `max_tokens`·참고 조문 길이(프롬프트 토큰) 확인 후 상의 — 참고 조문을 빼는 방법은 쓰지 않음 |

**15초 목표 판단**: `first_text`(서버)와 `ui_first_text`(화면)가 모두 15초 이하인 비율. 넘으면 `prompt_tokens`와 `prompt_tps`로 원인을 나눕니다(첫 토큰 = 프롬프트 토큰 ÷ 프리필 속도). v4는 참고 조문이 길어(조문당 최대 2304토큰 인코딩 기준) v1보다 프리필이 길어질 수 있습니다 — 수치는 미확인.

### 10단계. 간결형 val 실험 (기존 평가와 분리)

`src/lawbot/eval_concise.py` (새 파일)

```python
# src/lawbot/eval_concise.py  →  uv run python -m lawbot.eval_concise --val <val.csv> --model fast --tag v4_fast
# 간결형 답변 실험 (val 전용, 기존 평가 결과 파일과 분리: results/concise_<tag>.csv)
#   full   : 모델 원문 4섹션 (기준)
#   concise: 같은 원문을 answer_format.concise로 후처리 (재생성 없음)
#   early  : concise와 같은 화면, 【설명】이 나온 시점에 생성을 멈췄다면 걸렸을 시간 (temp 0이라 원문 앞부분과 같음)
#   prompt : --concise-system 파일의 system으로 다시 생성한 뒤 concise 후처리 (프롬프트만 바꾼 효과)
# val CSV 열: id, question, article_id (v4는 law도). must_include 등 키워드 열이 있으면 score_one으로 채점
import argparse
import os
import time
from pathlib import Path
import pandas as pd
from lawbot.answer_format import cited_hits, concise, sections
from lawbot.paths import DATA, RESULTS
from lawbot.refusal import is_refusal
from lawbot.score_answers import score_one
from lawbot.service import ask, load_app

# 평가 전용 데이터(README '평가 데이터 사용 제한'): 형식 선택에 쓰지 않는다
EVAL_ONLY = {DATA / f for f in ('labor_eval_keywords.csv', 'oos_eval.csv', 'scope_controls.csv', 'refusal_hard_cases.csv')}
PENALTY = ('벌칙', '벌금', '징역', '과태료')

def judge(view, raw, hits, row, finish, sec_to_view):
    """화면 글(view) 기준 지표. 인용은 원문 【근거 조문】에서 뽑은 조문 (간결형 '근거:' 줄과 같은 규칙)"""
    cite = sections(raw).get('근거 조문', '')
    used, unknown = cited_hits(cite, hits)
    gold = (row.get('law'), row['article_id']) if 'law' in row else (None, row['article_id'])
    keys = [(h.get('law') if gold[0] else None, h['article_id']) for h in used]
    retrieved = any((h.get('law') if gold[0] else None, h['article_id']) == gold for h in hits)
    d = {'retrieved': retrieved, 'refusal': is_refusal(raw), 'cite_gold': gold in keys,
         'n_cited': len(used), 'unknown_cites': len(unknown), 'cites_all_hits': len(used) == len(hits) > 1,
         'penalty_in_view': any(p in view for p in PENALTY),
         # 근거 없는 벌칙 언급 의심: 화면에 벌칙 표현이 있는데 참고 조문 중 벌칙·과태료 조문이 없음 (사람 검토용 표시)
         'penalty_unsupported': any(p in view for p in PENALTY) and not any(
             any(p in h['title'] for p in PENALTY) for h in hits),
         'truncated': finish == 'length', 'view_len': len(view), 'sec_to_view': sec_to_view}
    if isinstance(row.get('must_include'), str) and isinstance(row.get('must_not_include'), str):
        s = score_one(row, view)
        d |= {'kw_group': s['group_score'], 'kw_total': s['total'], 'must_not_ok': s['must_not_ok']}
    return d

def run(app, question, model_key):
    """(원문, 검색 조문, 측정값, 【설명】이 처음 나온 시점 초)"""
    t0, t_early = time.perf_counter(), None
    for stage, hits, raw, st in ask(app, question, model_key):
        if t_early is None and '【설명】' in raw:
            t_early = time.perf_counter() - t0
    return raw, hits, st, t_early

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--val', required=True, help='val 문항 CSV (평가 전용 test 파일은 거부)')
    ap.add_argument('--model', default='fast', help='deploy 프로필의 모델 key')
    ap.add_argument('--tag', required=True, help='results/concise_<tag>.csv')
    ap.add_argument('--n', type=int, default=0, help='앞에서 n문항만 (0이면 전체)')
    ap.add_argument('--concise-system', help='prompt 조건용 system 파일 (없으면 prompt 조건 생략)')
    args = ap.parse_args()
    pd.set_option('display.width', 200, 'display.max_columns', None)
    val_path = Path(args.val).resolve()
    if val_path in EVAL_ONLY:
        raise SystemExit(f'{val_path.name}은 평가 전용입니다. 간결형 선택은 val 문항으로 하세요.')
    val = pd.read_csv(val_path, encoding='utf-8-sig', dtype=str)
    val = val.head(args.n) if args.n else val

    os.environ['LAWBOT_MAX_RESIDENT'] = '1'     # 실험은 한 모델만 올림 (Pro 실험이면 미리 올린 Fast는 내려감)
    app = load_app()
    alt = Path(args.concise_system).read_text(encoding='utf-8').strip() if args.concise_system else None
    rows = []
    for _, row in val.iterrows():
        row = row.to_dict()
        base = {'id': row['id'], 'question': row['question'], 'article_id': row['article_id']}
        raw, hits, st, t_early = run(app, row['question'], args.model)
        rows.append(base | {'mode': 'full'} | judge(raw, raw, hits, row, st['finish'], st['total']) | {'raw': raw})
        rows.append(base | {'mode': 'concise'} | judge(concise(raw, hits), raw, hits, row, st['finish'], st['total'])
                    | {'raw': raw})
        cut = raw.split('【설명】')[0]                # 조기 종료했다면 받았을 원문
        rows.append(base | {'mode': 'early'} | judge(concise(cut, hits), cut, hits, row, None, t_early or st['total'])
                    | {'raw': cut})
        if alt:
            llm, _ = app.models.get(args.model)
            sys0, llm.system = llm.system, alt
            try:
                raw2, hits2, st2, _ = run(app, row['question'], args.model)
            finally:
                llm.system = sys0
            rows.append(base | {'mode': 'prompt'} | judge(concise(raw2, hits2), raw2, hits2, row, st2['finish'],
                                                           st2['total']) | {'raw': raw2})
        print(row['id'], ' / '.join(f"{r['mode']}:{'거절' if r['refusal'] else int(r['cite_gold'])}"
                                    for r in rows if r['id'] == row['id']))
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'concise_{args.tag}.csv', index=False, encoding='utf-8-sig')
    hit, miss = o[o['retrieved']], o[~o['retrieved']]
    summary = pd.DataFrame({
        '정답 조문 인용률 (검색 적중)': hit.groupby('mode')['cite_gold'].mean(),
        '거짓 거절률 (검색 적중)': hit.groupby('mode')['refusal'].mean(),
        '검색 실패 시 거절률': miss.groupby('mode')['refusal'].mean(),
        '확인 안 된 인용 포함': o.groupby('mode')['unknown_cites'].apply(lambda s: (s > 0).mean()),
        '3개 모두 인용': o.groupby('mode')['cites_all_hits'].mean(),
        '벌칙 언급': o.groupby('mode')['penalty_in_view'].mean(),
        '근거 없는 벌칙 의심': o.groupby('mode')['penalty_unsupported'].mean(),
        '잘림': o.groupby('mode')['truncated'].mean(),
        '화면 글자 수': o.groupby('mode')['view_len'].mean(),
        '화면 완성까지(초, 중앙값)': o.groupby('mode')['sec_to_view'].median()})
    if 'kw_total' in o:
        summary['키워드 종합 점수'] = o.groupby('mode')['kw_total'].mean()
    print(f'\n[간결형 실험] {args.model} · val {len(val)}문항 · 검색 적중 {hit["id"].nunique()}')
    print(summary.T.round(3))

if __name__ == '__main__':
    main()
```

| 조건 | 생성 | 화면 | 의미 |
| --- | --- | --- | --- |
| `full` | 기존 system | 원문 4섹션 | 기준 |
| `concise` | `full`과 같은 원문 (재생성 없음) | 후처리 | 후처리만의 영향 |
| `early` | `full` 원문의 【설명】 앞부분 (temp 0이라 실제 조기 종료 출력과 같음) | 후처리 | 조기 종료의 화면·시간 |
| `prompt` | `--concise-system` 파일로 다시 생성 | 후처리 | 4섹션 모델에 문구만 바꾼 효과 |

v1 간결형 system 초안 (`prompt` 조건용, 맥에서 `data/v4/`가 아닌 임시 위치에 저장해도 됨). 4섹션 표시 중 두 개는 남겨 거절 판정·인용 추출 규칙이 그대로 동작하게 했습니다:

```text
당신은 대한민국 근로기준법 상담 도우미입니다. 사용자 메시지의 [참고 조문]만 근거로 답변하세요.
답변은 다음 두 부분으로 짧게 작성합니다.
【결론】 질문에 대한 핵심 답과 꼭 필요한 조건·예외를 2~3문장으로
【근거 조문】 실제 근거로 쓴 조문만 '근로기준법 제N조(제목)' 형식으로 인용
조문 본문을 다시 옮겨 적지 마세요.
제공된 조문에서 근거를 찾을 수 없으면 추측하지 말고 확인할 수 없다고 답하세요.
```

```fish
# val 파일은 노트북의 v4 val(또는 v1 학습·검증 분할의 검증 문항). test 파일은 스크립트가 거부함
uv run python -m lawbot.eval_concise --val <val.csv> --model fast --tag v1_fast_concise --concise-system <concise_system.txt>
uv run python -m lawbot.eval_concise --val <val.csv> --model pro  --tag v1_pro_concise
```

> 레포 안에서 키워드 채점 열이 있는 파일은 test 650(`labor_eval_keywords.csv`)뿐이고, README 규칙상 설정 선택에 쓸 수 없습니다. 키워드 열이 없는 val이면 인용·거절·벌칙·잘림·길이·시간만 비교합니다. → **v4 val 파일(노트북)을 받으면 이 단계를 바로 진행할 수 있습니다.**

**채택 기준 (제안, 실험 전 고정)**

| 지표 (val, 검색 적중 문항) | `concise` 채택 | `early`(조기 종료) 채택 | `prompt` 채택 |
| --- | --- | --- | --- |
| 정답 조문 인용률 | `full`과 같음 (후처리라 같아야 정상) | `full` 대비 −0.01 이내 | `full` 대비 −0.02 이내 |
| 거짓 거절률 | 같음 | +0.01 이내 | +0.01 이내 |
| 검색 실패 시 거절률 | 같음 | −0.02 이내 | −0.02 이내 |
| 확인 안 된 인용 포함 / 3개 모두 인용 | 늘지 않음 | 늘지 않음 | 늘지 않음 |
| 잘림 | - | 0 | 늘지 않음 |
| 화면 완성까지(초) | - | 의미 있게 감소 | - |
| 사람 검토 20건 (결론만으로 오해 소지) | 문제 2건 이하 | 문제 2건 이하 | 문제 2건 이하 |

`근거 없는 벌칙 의심`은 '화면에 벌칙 표현이 있는데 참고 조문에 벌칙 조문이 없음'을 표시하는 **검토용 근사치**입니다. 확정 판정은 사람이 `results/concise_<tag>.csv`의 `raw`를 보고 합니다.

### 11단계. v4 전환 (자산 도착 후)

1. 노트북 값으로 2장 체크리스트 A~I를 채우고, 다르면 해당 상수만 고칩니다 (예: 열 이름이 다르면 `DOC_STYLES['v4']`·`check_deploy.COLUMNS`).
2. 자산 배치 → `uv run python -m lawbot.check_deploy v4`가 모두 `OK`.
3. **형식 대조 (필수)**: 노트북에 저장된 v4 학습/평가 프롬프트 1~3개와 `llm.build_prompt_v4` 결과를 글자 단위로 비교 — 필요하면 기존 `test_prompt_matches_training_format`처럼 v4 val 파일로 테스트를 추가합니다. 검색 문서 문자열(`DOC_STYLES['v4']`)도 노트북 인코딩 입력과 대조합니다.
4. 검색 회귀: v4 val로 Recall@3을 재고 노트북 값과 비교 (차이 0.005 이내가 기준. 측정 스크립트는 val 파일 형식을 본 뒤 추가합니다).
5. 실행: `set -gx LAWBOT_PROFILE v4; uv run lawbot-app` → 화면 제목 `노동법 챗봇`, 조문 카드에 법령명.
6. 9·10단계를 `--tag m32_v4_*`로 다시 실행해 결과 표의 v4 행을 채웁니다.
7. 14B QLoRA가 나오면 `models/v4/sllm_14b_mlx_4bit`를 교체(또는 `LAWBOT_PRO_PATH`) → `check_deploy`(어댑터 없음·Qwen3-14B·4비트) → 9·10단계 반복.

---

## 5. 롤백

| 상황 | 방법 |
| --- | --- |
| v4 동작이 이상함 | `set -e LAWBOT_PROFILE` (기본 v1으로 복귀, 코드 그대로) |
| 메모리 압박 | `set -gx LAWBOT_MAX_RESIDENT 1` |
| 조기 종료 문제 | `set -e LAWBOT_EARLY_STOP` |
| Pro만 문제 | Pro 폴더를 빼 두면 라디오에 `(모델 폴더 없음)`, Fast는 정상 동작 |
| 이번 작업 전체 되돌리기 (커밋 전) | `git restore --staged --worktree src tests; and git clean -fd src tests` — 지우기 전에 `git status`로 대상 확인 |
| 이번 작업 전체 되돌리기 (커밋 후, 브랜치) | `git switch main` (작업 브랜치는 남겨 둠) |
| `results/`에 생긴 측정 파일 | `bench_*`·`bench_ui_*`·`concise_*` 이름만 생성, 기존 기준선 파일은 건드리지 않음 |

---

## 6. 결과 기록 표 (실측 후 채움 — 현재 전부 미확인)

**환경**

| 항목 | 값 |
| --- | --- |
| 칩 / 메모리 / MLX 권장 작업 메모리 | 미확인 |
| macOS / Python / gradio / mlx / mlx-lm / torch | 미확인 |
| 측정일 · 커밋 | 미확인 |

**지연 (중앙값, 워밍업 제외)**

| 프로필 | 모델 | 상주 | 검색(초) | 전환(초) | 첫 토큰 TTFT(초) | 첫 글자 서버(초) | 첫 글자 화면(초) | 출력 tok/s | 프리필 tok/s | 프롬프트 토큰 | 완료(초) | 완료·조기 종료(초) | 15초 내 첫 글자 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | Fast 4B | 2 | | - | | | | | | | | | /5 |
| v1 | Pro 14B(베이스) | 2 | | - | | | | | | | | | /5 |
| v1 | Fast 4B | 1 | | | | | | | | | | | /5 |
| v1 | Pro 14B(베이스) | 1 | | | | | | | | | | | /5 |
| v4 | Fast 4B LoRA | | | | | | | | | | | | /5 |
| v4 | Pro 14B (QLoRA) | | | | | | | | | | | | /5 |

**메모리**

| 프로필 | 상주 | 앱 준비 후 MLX(GB) | 최대 MLX(GB) | MPS(GB) | 최악 압박 | 스왑 증가(GB) | 판단 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | 2 | | | | | | |
| v1 | 1 | | | | | | |
| v4 | | | | | | | |

**간결형 val 실험**

| 모델 | 조건 | 정답 인용률 | 거짓 거절 | 검색 실패 시 거절 | 확인 안 된 인용 | 3개 모두 인용 | 벌칙 언급 | 근거 없는 벌칙 의심 | 잘림 | 화면 글자 수 | 화면 완성(초) | 키워드 점수 | 채택 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Fast | full | | | | | | | | | | | | 기준 |
| Fast | concise | | | | | | | | | | | | |
| Fast | early | | | | | | | | | | | | |
| Fast | prompt | | | | | | | | | | | | |
| Pro | full | | | | | | | | | | | | 기준 |
| Pro | concise | | | | | | | | | | | | |

**14B의 추가 성능이 늦은 응답을 감수할 가치가 있는가 (판단 표)**

| 근거 | Fast 4B | Pro 14B | 비고 |
| --- | --- | --- | --- |
| 노트북 답변 점수 (같은 val·같은 검색 조건) | 미확인 | 미확인 | 노트북 자료 필요 |
| 정답 조문 인용률 / 검색 실패 시 거절률 (val) | 미확인 | 미확인 | 10단계 |
| 첫 글자 화면(초) / 완료(초) | 미확인 | 미확인 | 9단계 |
| 사람이 본 사용성 (같은 질문 10개 블라인드 비교) | 미확인 | 미확인 | 어느 쪽 답이 더 쓸 만한가 |

판단 규칙(제안): 14B가 val에서 정답 인용률·거절 지표를 **의미 있게** 높이지 못하면(예: 0.02 미만) 기본은 Fast, Pro는 '느리지만 큰 모델' 선택지로만 둡니다. 14B가 확실히 낫더라도 첫 글자가 15초를 자주 넘으면 간결형 조기 종료 채택 여부를 함께 봅니다.

---

## 7. 다음 단계 (기능 안정화 후 선택)

- 포트폴리오용 모델 비교 화면 또는 README의 Fast/Pro 표 (실측값이 채워진 뒤)
- PR #8(출처·인용 대조)을 이 구조 위로 다시 맞추기
- v4 val 형식이 정해지면 Recall@3 회귀 스크립트와 `build_prompt_v4` 글자 일치 테스트 추가
