# 작업 계획: 모노레포 전환 + OpenAI 호환 모델 서버 (FastAPI) + 동시 추론

- 대상 레포: `bhsong/lawbot` (`main` 8f0747e 기준)
- 작성: 2026-11-01
- 작업자: Claude Code (구현 → 테스트 → PR). **병합은 사용자가 한다.**
- 사용자 결정 (2026-11-01):
  1. 모델 서버는 범용 API만 둔다. 조문 인덱스·검색·프롬프트 조립은 챗봇이 맡는다.
  2. 동시 추론은 별도 PR로 나눈다. 먼저 한 번에 하나 추론으로 API를 바꾸고 결과가 글자 단위로 같은지 확인한 뒤 배치 생성을 넣는다.
  3. 챗봇의 로컬 모드(웹앱 프로세스가 mlx·torch로 모델을 직접 올림)를 없앤다. 챗봇과 평가는 항상 API를 쓴다.
  4. 챗봇의 생성 호출은 `openai` 공식 SDK로 한다.
  5. 완료된 실험 스크립트는 삭제한다 (결과는 `docs/evals`·`results/`, 코드는 Git 기록에 있음). PR 1 첫 커밋에서 한다.
  6. 모델 id: `qwen3.8-27b-4bit`, `lawbot-fast-v4`, `lawbot-emb-v4`, `qwen3-reranker-4b`.
  7. 폴더 이동만 하는 PR 1을 따로 둔다.

---

## 0. 목표

```
챗봇 웹앱 (Gradio)          법령 모니터링 (예정)          평가 스크립트
  조문 인덱스·검색·필터        요약 요청만                  챗봇 코드 재사용
  프롬프트 조립
     │ openai SDK            │ openai SDK                 │
     │ + httpx (rerank)      │                            │
     ▼                       ▼                            ▼
  ┌──────────────── 모델 서버 (FastAPI, 127.0.0.1, Bearer 토큰) ────────────────┐
  │ GET  /v1/models                                                            │
  │ POST /v1/chat/completions   27B·4B (MLX)  ← 클라우드로 바꿀 수 있는 부분      │
  │ POST /v1/embeddings         튜닝 Qwen3-Embedding-0.6B (torch MPS)           │
  │ POST /v1/rerank             Qwen3-Reranker-4B (MLX), Cohere/Jina 형식        │
  │ GET  /health                                                               │
  └────────────────────────────────────────────────────────────────────────────┘
```

- 모델 서버는 조문 CSV, 프로필, 하위법령 설정을 모른다. 어느 앱이든 OpenAI 형식으로 부를 수 있다.
- 챗봇은 `base_url`과 모델명만 바꾸면 생성을 클라우드(OpenAI 호환) 모델로 바꿀 수 있다. 임베딩과 리랭킹은 튜닝한 로컬 모델이므로 항상 모델 서버를 쓴다.

### 범위 밖
- 법령 모니터링 기능 자체 (서비스 자리만 마련한다)
- 프롬프트, 모델, 검색 규칙, 하위법령 필터의 변경. **PR 1·2에서 답변은 바뀌지 않아야 한다.**
- launchd 등록, `.env` 수정 같은 사용자 맥의 설정 변경 (템플릿과 문서만 고친다)
- 클라우드 모델 실제 호출 (설정 경로만 만들고, 테스트는 가짜 서버로 한다)

### 지켜 온 규칙 (그대로)
- 실제 포트 번호는 커밋되는 어떤 파일에도 쓰지 않는다. 문서에는 `<모델 서버 포트>` 같은 자리표시만 쓴다.
- 모델 서버는 `127.0.0.1`에만 열고, 모든 요청(헬스 포함)에 토큰이 필요하다. 질문 내용은 로그에 남기지 않는다.
- 챗봇의 임베딩·리랭크 서버 주소는 지금처럼 `http://127.0.0.1:<포트>`만 허용한다 (질문이 맥 밖으로 나가지 않게).

---

## 1. PR 나누기

| PR | 브랜치 | 내용 | 동작 변화 |
| --- | --- | --- | --- |
| 1 | `chore/monorepo` | 실험 스크립트 삭제 + uv workspace로 폴더 이동 | 웹앱·평가 동작 없음 (삭제한 테스트 2개 외 pytest 같음) |
| 2 | `feat/openai-api` | FastAPI 모델 서버, 챗봇 API 전환, 로컬 모드 삭제 | API 형식만. 답변은 글자 단위로 같아야 함 |
| 3 | `feat/batch-generation` | `BatchGenerator`로 동시 추론 | 동시 요청 가능. 답변이 다를 수 있어 측정 |

PR 1을 따로 두는 이유: 파일 이동(`git mv`)과 코드 변경이 한 PR에 섞이면 PR 2의 diff를 읽을 수 없다.

---

## 2. PR 1: 실험 스크립트 정리 + 모노레포 구조

### 2.1 실험 스크립트 삭제 (첫 커밋)

- 삭제: `eval_dev`, `eval_oos`, `eval_refusal`, `eval_decree`, `verify_reranker`, `verify_decree_emb`, `mlx_decree_scores`, `bench_models`. 이 스크립트만 쓰던 `bench_ui`(`bench_models.QUESTIONS`), `build_decree_ctx`, `decree_filter`도 함께 삭제. `lawbot-eval` 명령 삭제.
- 남김: `eval_v4`·`eval_concise`·`smoke_decree`(웹앱 경로 `service.ask`), `report`·`refusal`·`score_answers`(`eval_v4`가 씀), `diff_answers`, 실험 데이터(`data/decree_exp/`, `data/v4/decree_ctx_*.csv`).
- 테스트: `test_decree.py`의 `decree.attach` 대 실험 결과 대조는 남긴다. 저장된 val 수치(0.921·0.055)와 저장된 참고 조문 400개가 기준이다. `decree_filter.run`을 기준으로 삼던 테스트 1개와 `bench_models` 테스트 1개만 삭제한다 (405 → 403).
- README: 삭제한 명령을 지우고 "완료된 실험" 한 단락으로 결과 위치를 안내한다.

### 2.2 폴더 이동 (둘째 커밋)

```
pyproject.toml               # workspace 루트: members, dev 의존성(pytest·ruff), ruff 설정
uv.lock                      # 하나 (workspace 공통)
services/
  chatbot/
    pyproject.toml           # 패키지 lawbot (이름 유지: lawbot-app 등 명령 그대로)
    src/lawbot/...
    tests/...
  model-server/
    pyproject.toml           # 패키지 lawbot_model_server (PR 2에서 만든다. 빈 패키지를 미리 두지 않음)
    src/lawbot_model_server/
    tests/
  monitor/                   # 만들지 않음. README에 자리만 적음
data/  models/  results/  docs/  deploy/   # 레포 최상위에 그대로
```

- `paths.ROOT`는 레포 최상위를 계속 가리키도록 고친다 (`parents[4]`, 테스트로 고정).
- PR 1에서는 기존 `model_server.py`·`remote.py`가 챗봇 패키지 안에 그대로 있다 (PR 2에서 바뀜).
- CI: `compileall`·pytest 경로를 `services/*`로. pytest는 루트에서 모든 서비스 테스트를 실행한다.
- `.git-blame-ignore-revs`는 이동만으로는 필요 없다 (git이 이름 변경을 추적).
- 루트는 가상 workspace 루트(패키지 없음)이고, dev 의존성 그룹에 `lawbot`을 넣어 루트에서 `uv sync`·`uv run lawbot-app`이 그대로 동작한다 (launchd 템플릿 변경 없음).
- 확인: `uv sync --locked`, pytest 403 + `paths.ROOT` 테스트 1개 = 404 passed, ruff 그대로.

---

## 3. PR 2: OpenAI 호환 모델 서버 + 챗봇 전환

### 3.1 모델 서버 (`services/model-server`)

의존성: `fastapi`, `uvicorn`, `mlx-lm`, `sentence-transformers`. gradio·pandas·조문 데이터를 쓰지 않는다 (테스트로 검사).

모델 코드는 챗봇 패키지에서 옮긴다. 법령 전용 부분은 챗봇에 남긴다.

| 지금 | 옮길 곳 | 남는 곳 (챗봇) |
| --- | --- | --- |
| `llm.py` `LLM.stream`, `close_thinking`, `hide_thinking` | `model-server/chat.py` (messages를 받아 템플릿 적용) | `build_prompt_v4`·`build_prompt` → `prompt.py` |
| `retriever.py` SentenceTransformer 인코딩 | `model-server/embeddings.py` | 조문 배열, `DOC_STYLES`, 코사인 검색 → `index.py` (numpy만) |
| `reranker.py` 채점 (`encode`, `_no_yes_logits`) | `model-server/rerank.py` | `TASK` 문구, `rank_order` 정렬 (동점은 임베딩 순서) |
| `service.ModelManager` (LRU) | `model-server/registry.py` | |

**엔드포인트**

- `POST /v1/chat/completions`
  - 받는 값: `model`, `messages`, `max_tokens`(또는 `max_completion_tokens`), `temperature`, `stream`, `stream_options.include_usage`, `chat_template_kwargs`(확장).
  - `temperature`가 없으면 0 (탐욕적 생성, 지금과 같음). OpenAI 기본값(1)과 다르다는 점을 README에 적는다.
  - `chat_template_kwargs.enable_thinking` 기본 false. 꺼져 있으면 지금처럼 `close_thinking`과 `hide_thinking`을 적용한다.
  - 스트리밍은 SSE(`data: {chunk}` … `data: [DONE]`). 마지막 청크에 `usage`(`prompt_tokens`, `completion_tokens`, `total_tokens`)와 `finish_reason`(`stop`·`length`)을 넣는다. 확장 필드 `timings`(`prompt_tps`, `generation_tps`, `peak_memory_gb`)는 요청 로그에 쓴다. SDK는 모르는 필드를 그대로 둔다.
  - 지원하지 않는 값(`n`>1, `tools`, `logprobs` 등)은 400으로 거절한다. 모르는 값을 조용히 무시하지 않는다.
- `POST /v1/embeddings`: `model`, `input`(문자열 또는 목록), `encoding_format`(`float`·`base64`). 확장 필드는 둘이다.
  - `input_type`: `query`(SentenceTransformer `prompt_name='query'`) 또는 `document`
  - `max_seq_length`: 이번 요청의 최대 토큰 (법률 2304, 하위법령 `DECREE_MAX_SEQ`)
  - 목록 하나를 지금과 같은 `encode` 호출 하나로 처리한다 (문서 batch 4, 질문 batch 16). 그래야 길이 정렬·패딩이 같아 fp16 값이 같다. 챗봇은 `base64`(float32 그대로)로 받는다.
- `POST /v1/rerank` (Cohere/Jina/vLLM 형식): `model`, `query`, `documents`, `top_n`, 확장 필드 `instruction`. 응답은 `results: [{index, relevance_score}]`(점수 내림차순, 동점은 index순). 법령 전용 `TASK` 문구는 챗봇이 `instruction`으로 보낸다.
- `GET /v1/models`: 서버가 제공하는 모델 id 목록 (`owned_by`, 확장 필드 `type`: chat·embedding·rerank, `loaded`).
- `GET /health`: `{ready}` (모델 로딩 중에도 응답).
- 오류는 OpenAI 형식 `{"error": {"message", "type", "code"}}`. 401·400·404(없는 모델)·413·503(로딩 중)을 쓴다.

**설정** (모델 서버 `.env`, 경로는 레포 최상위 기준. 이름은 PR에서 확정한다)

```
LAWBOT_MODEL_SERVER_PORT=          # 필수, 코드 기본값 없음 (지금과 같음)
LAWBOT_MODEL_TOKEN=                # 필수, 16자 이상
LAWBOT_SERVE_CHAT=qwen3.8-27b-4bit=models/v4/qwen3.8_27b_mlx_4bit,lawbot-fast-v4=models/v4/sllm_4b_lora_mlx_4bit
LAWBOT_SERVE_EMBEDDING=lawbot-emb-v4=models/v4/emb_v4_fp16
LAWBOT_SERVE_RERANK=qwen3-reranker-4b=models/v4/reranker_4b_mlx_4bit
LAWBOT_SERVE_PRELOAD=qwen3.8-27b-4bit   # 시작할 때 올릴 chat 모델 (나머지는 처음 요청 때)
LAWBOT_MAX_RESIDENT=1              # chat 모델 상주 수 (LRU, 지금과 같음)
```

**동시성 (PR 2)**: 지금처럼 모든 모델 연산을 락 하나로 처리한다. 스트리밍은 작업 스레드가 토큰을 큐에 넣고 async 제너레이터가 SSE로 내보낸다. 클라이언트가 끊기면(`request.is_disconnected()`·취소) 취소 플래그로 생성을 멈추고 락을 푼다. 이 부분은 테스트로 고정한다 (지금 `/generate`의 끊김 처리와 같은 보장).

### 3.2 챗봇 (`services/chatbot`)

의존성에서 `mlx-lm`과 `sentence-transformers`(torch)를 뺀다. 남는 것은 `gradio`, `pandas`, `numpy`, `openai`, `httpx`.

- `index.py`: 조문 CSV → 배열(ids·titles·docs·laws·kinds·parent_laws, 지금 `Retriever`와 같은 순서). 시작할 때 법률 문서와 하위법령 문서를 각각 `/v1/embeddings` 한 번씩 보내 인덱스를 만든다. 검색은 `D @ q`와 `argsort`로, 지금 코드와 같은 식이다. `search`·`search_decrees`·`rank_all`의 인터페이스는 그대로 둔다 (`service.ask`·평가 코드 그대로).
- `rerank` 클라이언트: `/v1/rerank`로 점수를 받고, 정렬은 지금처럼 `rank_order`로 한다 (동점은 임베딩 순위).
- 생성: `OpenAI(base_url=…, api_key=…)`, `chat.completions.create(stream=True, stream_options={'include_usage': True}, temperature=0, max_tokens=1024)`. 응답을 지금 `LLM.stream`과 같은 속성(text·generation_tps·prompt_tokens·…·finish_reason)으로 바꿔 `service.ask`에 넘긴다. 조기 종료와 정지 버튼을 누르면 스트림을 닫는다.
- 모델 레지스트리(`deploy.ModelSpec`)에 `model`(API 모델명)을 더한다. `path`는 챗봇에서 쓰지 않으므로 뺀다.

**챗봇 설정**

```
LAWBOT_MODEL_SERVER=http://127.0.0.1:<모델 서버 포트>   # 임베딩·리랭크 (127.0.0.1만 허용, 지금과 같음)
LAWBOT_MODEL_TOKEN=
LAWBOT_EMB_MODEL=lawbot-emb-v4
LAWBOT_RERANK_MODEL=qwen3-reranker-4b
LAWBOT_PRO_MODEL=qwen3.8-27b-4bit
LAWBOT_FAST_MODEL=lawbot-fast-v4
# 생성만 다른 곳으로 (선택). 없으면 LAWBOT_MODEL_SERVER/v1 + LAWBOT_MODEL_TOKEN
# LAWBOT_LLM_BASE_URL=https://…/v1
# LAWBOT_LLM_API_KEY=
# LAWBOT_LLM_ALLOW_EXTERNAL=1      # 127.0.0.1 밖 주소는 이 값이 있어야 허용 (질문이 맥 밖으로 나감을 명시)
```

- 클라우드 모델은 `chat_template_kwargs`를 모를 수 있다. 그래서 이 값은 `LAWBOT_LLM_BASE_URL`이 로컬 모델 서버일 때만 보낸다.
- 시작 검사(`check_info` 대체): `/v1/models`에 설정한 모델 id가 있는지 확인한다. 없으면 시작을 거부한다. 하위법령·프로필 일치 검사는 이제 챗봇 안의 설정이라 필요 없다.

### 3.3 삭제·이동

- 삭제: `model_server.py`(옛 자체 API), `remote.py`, `service.load_remote`, `service.load_app(local=…)` 분기, `API_VERSION` 검사.
- `check_deploy`: 모델 폴더 검사는 모델 서버 쪽 `python -m lawbot_model_server.check`로, 조문 CSV·system·서버 모델 목록 검사는 챗봇에 둔다.
- 완료된 실험 스크립트는 PR 1에서 이미 삭제한다 (§2.1).
- `eval_v4`, `eval_concise`, `smoke_decree`: 챗봇 경로(`service.ask`)를 쓰므로 API로 그대로 동작한다.
- launchd 템플릿 두 개: 실행 명령과 작업 폴더를 새 구조에 맞춘다. README와 `.env.example`·`.env.dev.example`을 고친다.

### 3.4 테스트 (구현 전에 먼저)

- 모델 서버 (가짜 모델 + FastAPI `TestClient`)
  - 토큰 없음·틀림 → 401. `/health`도 토큰이 필요하다. `127.0.0.1` 바인딩, 토큰 16자 미만이면 시작 거부.
  - chat: 스트림·비스트림 형식, `usage`·`finish_reason`, 없는 모델 404, 지원하지 않는 필드 400, temperature 기본 0.
  - **SDK 계약 테스트**: `OpenAI(base_url='http://testserver/v1', http_client=TestClient(app))`로 SDK가 응답을 그대로 파싱하는지 확인한다.
  - 스트림 중 끊기면 생성기가 닫히고 다음 요청이 바로 처리된다 (락이 풀림).
  - embeddings: `input_type`·`max_seq_length`가 encode 호출에 그대로 전달되는지, base64가 float32와 같은 값인지.
  - rerank: 동점일 때 index 순서, `top_n`.
  - 패키지가 gradio·pandas·`lawbot`을 import하지 않음.
- 챗봇
  - 인덱스 검색이 지금 `Retriever`와 같은 순위·점수를 내는지 (같은 가짜 벡터로 비교).
  - 기존 `test_pipeline`·`test_webapp`·`test_feedback`이 새 클라이언트 위에서 그대로 통과한다 (가짜 서버).
  - 외부 LLM 주소는 `LAWBOT_LLM_ALLOW_EXTERNAL` 없으면 거부, 임베딩 서버는 127.0.0.1만.
  - 챗봇 패키지가 mlx·torch·sentence_transformers를 import하지 않음.

### 3.5 실제 모델 검증 (맥 미니, 사용자 승인 후 실행)

- val 전체를 새 구조(모델 서버 + 챗봇 API)로 `eval_v4` 실행 → `diff_answers`로 기준 `results/v4/answers_pro_concise_rr_dec_val.csv`와 비교한다. **기대: 검색 순위·첨부·답변이 글자 단위로 모두 같음.** 다르면 단계별(임베딩·리랭크·프롬프트)로 원인을 찾는다.
- 지연(완료·첫 글자)과 MLX 최대 메모리를 기준과 비교한다. HTTP·SSE 경로가 달라진 만큼의 차이만 있어야 한다.
- 기록: `docs/evals/<날짜>-openai-api.md`, README에 한 줄.

---

## 4. PR 3: 동시 추론 (continuous batching)

- `mlx-lm 0.32.2`의 `BatchGenerator`를 쓴다 (`mlx_lm.server`도 이걸로 배치 생성을 한다).
- MLX는 여러 스레드에서 동시에 연산하면 안전하지 않다. 그래서 **GPU 스케줄러 스레드 하나**가 모든 모델 연산을 맡는다.
  - chat 요청은 큐로 받아 배치에 넣고, 한 스텝마다 요청별 토큰을 각 SSE 큐로 나눠 보낸다.
  - embeddings·rerank 요청은 생성 스텝 사이에 끼워 실행한다. 생성 중인 스트림이 그 시간만큼 잠깐 멈춘다.
  - 끊긴 요청은 배치에서 뺀다 (`remove(uid)`).
- 상한: `LAWBOT_MAX_CONCURRENT`(기본 4). 넘는 요청은 대기열에서 기다린다. 요청마다 KV 캐시가 늘어나므로 32GB에서 실측해 기본값을 정한다.
- 모델 교체(LRU): 내리려는 모델에 진행 중인 요청이 있으면 끝날 때까지 기다렸다가 교체한다.
- `hide_thinking`은 요청마다 상태를 따로 둔다.
- 검증 (승인 후 실행)
  - 동시 1건: val 답변이 PR 2 결과와 같은지 (배치 경로라 다를 수 있음 → 다른 문항 수와 채점 지표 차이를 기록)
  - 동시 2·4건: 처리량(문항/분), 요청별 완료 시간, MLX 최대 메모리, 채점 지표
  - 결과에 따라 기본값을 정하거나, 얻는 것이 적으면 배치를 끄는 설정(`LAWBOT_MAX_CONCURRENT=1`)을 기본으로 둔다.

---

## 5. 위험

| 위험 | 대응 |
| --- | --- |
| 임베딩을 HTTP로 옮기면서 batch 구성이 달라져 fp16 값이 바뀜 | 목록 하나 = encode 호출 하나로 고정. base64 float32로 전송. val 검색 순위 전체 비교 |
| SSE 끊김을 서버가 늦게 알아서 락이 오래 잡힘 | 취소 플래그 + 테스트로 고정 (PR 2), 배치에서 제거 (PR 3) |
| 챗봇 시작이 느려짐 (812조문 인코딩을 서버에 요청) | 지금도 서버 시작 때 같은 계산을 한다. 느리면 PR 2 이후 인덱스 캐시(조문 sha256 + 모델 id)를 따로 검토 |
| 클라우드 사용 시 질문이 외부로 나감 | `LAWBOT_LLM_ALLOW_EXTERNAL=1` 명시 필요, 시작 로그에 외부 주소 표시 |
| PR 2가 큼 | 커밋을 서버 → 챗봇 클라이언트 → 삭제·문서 순으로 나눈다 |

---

## 6. 사용자 확인 (2026-11-01 답변 완료)

맨 위 결정 5~7 참고.
