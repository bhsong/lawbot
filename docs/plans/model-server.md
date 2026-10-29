# 작업 계획: 모델 서버 분리 + 운영/개발 포트 분리

- 대상 레포: `bhsong/lawbot` (`main` 기준, 2026-10-26 zip으로 확인)
- 작업 브랜치: `feat/model-server`
- 작업자: Claude Code 에이전트 (구현 → 테스트 → PR 생성까지). **병합은 사용자가 한다.**
- 작성: 2026-10-27

---

## 0. 목표와 범위

맥미니 32GB에서 챗봇을 계속 켜 두고(운영) 휴대폰에서 Tailscale로 쓴다. 같은 맥에서 개발 버전도 띄울 수 있어야 한다. 이때 다음 두 문제가 생기지 않게 한다.

1. **포트 충돌.** 지금은 `security.py`가 `127.0.0.1`과 고정 포트(기존 기본값)를 쓰므로 두 번째 인스턴스가 뜨지 않는다.
2. **메모리 초과.** 지금은 웹앱 프로세스마다 임베딩, 리랭커, 27B를 직접 올린다. 10-25 측정에서 27B와 리랭커만으로 MLX 최대 20.4GB였으므로, 두 벌을 올리면 32GB를 넘는다.

해결 구조는 다음과 같다.

```
휴대폰 ──(Tailscale HTTPS, tailscale serve)──▶ 127.0.0.1:<운영 포트>  운영 웹앱 (Gradio, 모델 없음)
맥 브라우저 ─────────────────────────────────▶ 127.0.0.1:<개발 포트>  개발 웹앱 (Gradio, 모델 없음)
                                                   │  둘 다 HTTP + 토큰
                                                   ▼
                                     127.0.0.1:<모델 서버 포트>  모델 서버 (1개)
                                     임베딩 0.6B + 리랭커 4B + 답변 모델(27B 등)을 한 번만 올림
```

| 프로세스 | 포트 | 실행 위치 | 외부 노출 |
| --- | --- | --- | --- |
| 운영 웹앱 | `<운영 포트>` | 운영 worktree | `tailscale serve`로 이 포트만 노출 |
| 개발 웹앱 | `<개발 포트>` | 개발 레포 | 노출 안 함 |
| 모델 서버 | `<모델 서버 포트>` | 운영 worktree | 노출 안 함 (127.0.0.1 고정) |

### 포트 번호 규칙 (레포가 public이므로 중요)
- **실제 포트 번호는 사용자가 자기 `.env`에만 적는다.** 커밋되는 어떤 파일에도 쓰지 않는다. 대상은 코드 기본값, `.env.example`, `.env.dev.example`, README, 이 계획 문서, 테스트, launchd 템플릿, 커밋 메시지, PR 본문이다. 문서와 예시에는 `<운영 포트>`, `<개발 포트>`, `<모델 서버 포트>` 같은 자리표시만 쓴다.
- 운영 포트와 개발 포트는 서로 붙이지 않고 멀리 떨어뜨린다(사용자 요청). 모델 서버 포트도 두 웹앱 포트와 떨어진 값을 쓴다. 번호는 사용자가 정한다.
- 이미 공개된 기존 기본값 7860은 하위 호환을 위해 `LAWBOT_PORT`가 없을 때만 쓴다. 문서에는 "운영에서는 `.env`에 `LAWBOT_PORT`를 따로 정하라"고 안내한다.
- 모델 서버 포트에는 **코드 기본값을 두지 않는다.** `LAWBOT_MODEL_SERVER_PORT`가 없으면 실행을 거부하고 ".env에 LAWBOT_MODEL_SERVER_PORT를 적으세요"라고 안내한다.
- 테스트는 `free_port()`나 포트 0처럼 실행할 때 비어 있는 포트를 쓴다. 검증 테스트에 고정 숫자가 필요하면 의미 없는 값(1023, 65536 같은 범위 경계)만 쓴다.
- 에이전트는 작업을 끝내고 **사용자에게 보내는 최종 보고**(PR 본문 아님)에서 이렇게 안내한다: "운영 `.env`에 `LAWBOT_PORT`, `LAWBOT_MODEL_SERVER_PORT`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN`을, 개발 `.env`에 `LAWBOT_PORT`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN`을 적으세요." 포트 번호를 제안하지는 않는다. 번호는 사용자가 이미 정해 두었다.

**사용자 전제:** 혼자 쓰고 혼자 개발하므로, 운영과 개발이 동시에 모델에 질의하는 일은 없다. 그래서 모델 서버는 요청을 한 번에 하나만 처리한다. 겹치면 뒤 요청이 기다린다.

### 범위 밖 (이 PR에서 하지 않음)
- FastAPI 백엔드 분리(로드맵 3), Docker. 이번 모델 서버는 그 전 단계의 내부용 서버다.
- 프롬프트, 모델, 검색 로직, 평가 수치의 변경. **답변 결과는 바뀌지 않아야 한다.**
- launchd 등록, `tailscale serve` 실행, `.env` 수정 같은 사용자 맥의 설정 변경. 이 PR은 템플릿과 문서만 넣는다.
- 테스트 650/2,075문항으로 무엇을 고르는 일.

---

## 1. 에이전트 작업 규칙

1. 시작:
   ```zsh
   git switch main && git pull --ff-only
   git switch -c feat/model-server
   ```
   시작 전에 `gh pr list`와 `git log --oneline -15`로 로드맵 2.6 브랜치(B1 `refactor/citation-core`, B2 `feat/law-data-manifest`, B4 `feat/request-log`, B5 `feat/ui-citations`)의 상태를 확인한다. 열린 PR이 `service.py`나 `app.py`를 건드리면 PR 본문의 "충돌 가능" 항목에 적는다. 그 PR들을 기다리지는 않는다.
2. 이 문서는 첫 커밋에서 `docs/plans/model-server.md`로 레포에 넣는다.
3. 아래 6장의 단계마다 커밋한다. 커밋 메시지 형식은 `git log`에서 쓰고 있는 형식을 따른다.
4. 커밋할 때마다 `uv run python -m compileall -q src tests && uv run pytest`가 통과해야 한다. 기존 테스트 138개는 수정하지 않는 것이 원칙이다. 기존 기본 포트를 검사하는 테스트처럼 고쳐야 하면 그 이유를 PR 본문에 적는다.
5. 의존성을 추가하지 않는다. 서버와 클라이언트 모두 표준 라이브러리(`http.server`, `urllib`, `json`)로 만든다. `pyproject.toml`에서는 `[project.scripts]`에 한 줄만 추가하고, `uv.lock`은 바뀌지 않아야 한다.
6. 하지 말 것:
   - PR 병합, `main`에 직접 push, force push
   - `.env`나 `models/` 수정, 모델 다운로드
   - launchd 등록(`launchctl`), `tailscale` 명령 실행
   - 사용자가 띄워 둔 웹앱이나 모델 서버 프로세스를 종료
7. 코드 주석과 사용자 메시지는 기존 파일처럼 한국어로 쓴다. 파일 첫 줄에는 기존 관례대로 `# src/lawbot/xxx.py  →  설명`을 넣는다.
8. 끝나면 8장 형식으로 PR을 만들고(`gh pr create`), `gh pr checks --watch`로 CI(macos-15)가 통과하는지 확인한다. 실패하면 고쳐서 같은 브랜치에 push한다.

---

## 2. 현재 구조 (2026-10-26 zip에서 확인한 사실)

| 위치 | 내용 | 이번 작업과의 관계 |
| --- | --- | --- |
| `security.py` | `SERVER_NAME='127.0.0.1'`, `SERVER_PORT=7860` 상수, `get_local_launch_config()` | 포트를 `LAWBOT_PORT`로 받게 바꾼다. 주소는 계속 고정 |
| `envfile.py` | `.env`에서 `LAWBOT_*`와 `LLM_PATH`만 읽는다. 셸 변수가 우선. `LAWBOT_PW`가 있는데 파일 권한이 넓으면 경고 | 새 변수는 모두 `LAWBOT_` 접두사를 쓰므로 그대로 읽힌다. 경고 대상에 토큰을 추가 |
| `service.py` | `load_app()`이 `Retriever`, `Reranker`(선택), `ModelManager`(LRU, `loader`로 `LLM` 생성)를 묶은 `LawbotApp`을 만든다. `ask()`는 검색 → (리랭킹) → `models.get` → `llm.stream` 순서로 진행한다 | 원격 모드에서는 같은 인터페이스를 가진 원격 객체로 교체 |
| `service.py` 맨 위 | `from lawbot.retriever import Retriever`(torch, sentence_transformers), `from lawbot.llm import LLM, SYSTEM`(mlx_lm, 그리고 import 시 조문 CSV를 읽음), `from lawbot.reranker import Reranker`(mlx) | 원격 모드 웹앱이 무거운 라이브러리를 import하지 않도록 지연 import로 바꾼다 |
| `app.py` | `make_chat`에서 `ask()`를 호출. 모델을 아직 올리지 않은 상태에서 `(OSError, ValueError)`가 나면 안내 메시지 `gr.Error` | 원격 로딩 실패도 `OSError`로 올려 같은 안내가 나오게 한다 |
| `llm.LLM.stream(question, hits, max_tokens=1024, system=None)` | 응답 객체 속성: `text`, `generation_tps`, `prompt_tokens`, `prompt_tps`, `generation_tokens`, `peak_memory`, `finish_reason` (`service.ask`가 읽음) | 원격 스트림도 같은 속성을 가진 객체를 낸다 |
| `Retriever` | `search(q, k)` → dict 목록(`article_id`, `title`, `doc`, `score`, v4는 `law`). `rank_all(queries)` → 인덱스 배열. 속성 `ids`, `titles`, `docs`, `laws` | `eval_v4`가 `rank_all`과 속성들을 쓴다(`hit_table`, `run_retrieval`, `refusal_cases`) |
| `Reranker` | `rerank(q, candidates, top_k)`, 속성 `candidates` | `service.retrieve`와 `eval_v4.run_retrieval`이 쓴다 |
| `load_app()` 사용처 | `app.main`, `eval_v4`, `eval_concise`, `bench_models` | 원격 모드를 지원하면 평가도 운영 모델 서버를 공유할 수 있다 |
| 직접 모델 생성 | `eval_dev`, `eval_refusal`, `eval_oos`, `diag_oracle`(v1, `LLM()`), `verify_reranker`(`Reranker()`) | 이번에는 바꾸지 않는다. 로컬 전용이라고 문서에 적는다 |
| `bench_ui.py` | `SERVER_NAME`, `SERVER_PORT`로 접속 | 설정된 포트를 쓰도록 바꾼다 |
| `tests/conftest.py` | `ENV_KEYS`의 변수를 지우고, 기본 테스트에서는 외부 접속을 막는다. `integration` 마커가 있는 테스트만 127.0.0.1 접속을 허용 | 새 환경 변수를 `ENV_KEYS`에 추가. 원격 테스트에는 `integration` 마커를 붙인다 |
| `tests/integration_server.py` | `security.SERVER_PORT = port`로 포트를 바꿔 실제 `app.main()`을 띄운다 | 포트 기능을 바꾼 뒤에도 계속 동작해야 한다 |

---

## 3. 설계 결정

| # | 결정 | 이유 |
| --- | --- | --- |
| D1 | 임베딩, 리랭커, 답변 모델을 **모두** 모델 서버가 가진다. 원격 모드 웹앱은 모델 라이브러리를 import하지 않는다 | 메모리 중복을 완전히 없앤다. 임베딩 0.6B fp16과 torch도 한 번만 올라간다 |
| D2 | `mlx_lm.server`가 아니라 **자체 서버**(`lawbot.model_server`)를 쓴다 | 리랭커는 yes/no 로짓 채점이고 임베딩은 sentence-transformers라 `mlx_lm.server`로는 서빙할 수 없다. 27B도 `close_thinking`/`hide_thinking`과 측정값(`peak_memory` 등)을 그대로 유지해야 평가 수치가 같다 |
| D3 | 모델 서버 안에서는 기존 `service.load_app()`(로컬 모드)을 그대로 호출하고, HTTP로 감싸기만 한다 | 모델 로딩, 검증, LRU 코드를 다시 쓰지 않는다. 결과가 로컬 모드와 같다는 것이 보장된다 |
| D4 | API 경계는 **기본 연산 단위**로 한다: search, rank_all, rerank, load, generate. `service.ask()`의 흐름과 측정은 웹앱 쪽에 그대로 둔다 | `ask`, `app.py`, `eval_v4`를 거의 고치지 않는다. 검색과 리랭킹 시간이 지금처럼 따로 측정된다 |
| D5 | system 문구는 **웹앱이 요청마다 보낸다.** 웹앱이 자기 레포의 system 파일을 읽어 보내고, 서버의 기본 system은 쓰지 않는다 | 개발 레포에서 `system_v4_concise.txt`를 고치면 운영 모델 서버를 재시작하지 않고 바로 시험할 수 있다 |
| D6 | 서버는 전역 락 하나로 모델 연산을 **한 번에 하나만** 처리한다(`/health`, `/info` 제외) | 사용자 전제(동시 질의 없음). MLX와 MPS의 동시 실행 위험을 피한다. Gradio `concurrency_limit=1`과 같은 원칙이다 |
| D7 | 서버는 `127.0.0.1`에만 바인드한다(상수, 환경 변수로 바꿀 수 없음). 토큰 `LAWBOT_MODEL_TOKEN`이 없으면 실행을 거부하고, 모든 요청에 `Authorization: Bearer` 헤더를 요구한다(`hmac.compare_digest`) | 기존 보안 정책(로컬 전용, 인증 없으면 실행 거부)과 같게 한다. 사용자 정의 헤더는 CORS preflight가 필요하므로, 브라우저를 거친 DNS rebinding 요청도 막힌다 |
| D8 | 클라이언트는 `LAWBOT_MODEL_SERVER`가 `http://127.0.0.1:<포트>` 형식일 때만 받는다. 다른 호스트는 거부 | 질문 내용이 맥 밖으로 나가지 않게 한다 |
| D9 | 모드는 `LAWBOT_MODEL_SERVER`로 고른다. 비어 있으면 지금과 똑같은 **로컬 모드(기본)** | 기존 테스트, 소형 맥, 평가 스크립트의 동작이 그대로다 |
| D10 | 스트리밍은 NDJSON(한 줄에 JSON 하나)으로, 응답 끝은 연결 종료로 표시한다 | 표준 라이브러리만으로 구현할 수 있고 디버깅이 쉽다 |
| D11 | `/info`에 `api_version`을 넣고, 클라이언트는 값이 다르면 시작을 거부한다 | 운영 worktree(이전 태그)와 개발 레포(새 코드)의 버전 차이를 일찍 발견한다 |
| D12 | 검색 설정(프로필, 리랭커 사용 여부, 후보 수)은 **서버가 기준**이다. 웹앱은 시작할 때 서버의 프로필 이름과 `doc_style`이 자기 프로필과 같은지만 확인한다 | 설정이 두 군데에서 어긋나는 일을 막는다. 원격 모드 웹앱에서는 `LAWBOT_RERANKER_*`를 무시하고, 서버 값을 로그에 출력한다 |

### 이 구조의 제약 (README와 PR에 적을 것)
- 웹앱 쪽 코드(`app.py`, `service.ask`, `answer_format.py`, system 파일)를 바꾸면 개발 웹앱만 재시작해서 시험할 수 있다.
- 서버 쪽 코드(`llm.py`의 프롬프트 조립, `reranker.py`, `retriever.py`, `model_server.py`)를 바꾸면 **개발용 모델 서버**로 시험해야 한다. 운영 모델 서버를 멈추고 개발 레포에서 운영과 다른 포트(셸에서 `LAWBOT_MODEL_SERVER_PORT=… `로 일회성 지정)로 서버를 띄운 뒤, 개발 웹앱을 그 포트로 연결한다. 메모리 때문에 모델 서버는 한 번에 하나만 띄운다.
- 개발 중에 질문을 보내면 휴대폰 질문은 그동안 기다린다(D6).

---

## 4. 환경 변수

| 변수 | 읽는 쪽 | 기본값 | 검증 |
| --- | --- | --- | --- |
| `LAWBOT_PORT` | 웹앱 | 없으면 기존 기본값(하위 호환). 빈 값도 없는 것으로 본다 | 정수 1024–65535. `LAWBOT_MODEL_SERVER`의 포트와 같으면 거부 |
| `LAWBOT_INSTANCE` | 웹앱 | 없음 | 있으면 화면 제목과 브라우저 탭 제목에 붙인다. 예: `노동법 챗봇 · 개발`. 휴대폰과 맥에서 운영/개발을 헷갈리지 않게 하는 용도. 40자 이하 |
| `LAWBOT_MODEL_SERVER` | 웹앱, 평가 스크립트 | 없음(로컬 모드) | `http://127.0.0.1:<포트>`만 허용(D8) |
| `LAWBOT_MODEL_SERVER_PORT` | 모델 서버 | **없음 (필수)** | 없거나 비어 있으면 실행 거부. 정수 1024–65535 |
| `LAWBOT_MODEL_TOKEN` | 둘 다 | 없음 | 서버: 없거나 16자 미만이면 실행 거부. 클라이언트: 원격 모드인데 없으면 거부. 값은 어떤 메시지에도 출력하지 않는다 |
| `LAWBOT_MODEL_SERVER_WAIT` | 웹앱 | `300` (초) | 시작할 때 서버가 `ready`가 될 때까지 기다리는 최대 시간. 27B 로딩 시간을 고려한 값이다. launchd로 둘을 동시에 띄우는 경우에 필요하다 |

- `GRADIO_SERVER_PORT` 같은 Gradio 변수는 지금처럼 무시한다.
- 모델 서버 프로세스는 `LAWBOT_MODEL_SERVER`가 설정되어 있어도 **항상 로컬 모드로** `load_app`을 호출해야 한다. 운영 `.env` 하나를 웹앱과 서버가 같이 읽기 때문이다. 이 경우가 테스트에 있어야 한다.
- `envfile.py`의 권한 경고 대상에 `LAWBOT_MODEL_TOKEN`을 추가한다.

---

## 5. 모델 서버 API (v1)

모든 요청은 `Authorization: Bearer <토큰>` 헤더가 필요하다. 요청과 응답 본문은 JSON(UTF-8)이다. 요청 본문은 2MB를 넘으면 거부한다(`/rank_all`의 질문 2,075개는 이 한도 안에 들어간다).

| 메서드 · 경로 | 요청 | 응답 | 락 |
| --- | --- | --- | --- |
| `GET /health` | – | `{"ready": bool}` (로딩 중이면 false) | 없음 |
| `GET /info` | – | `api_version`, `profile`(name, doc_style), `models`(키 목록, 설정 순서), `loaded`(상주 키), `max_resident`, `reranker`(null 또는 `{"candidates": 10}`), `n_articles`, `memory`(MLX active/peak/cache GB, 못 재면 null) | 없음 |
| `GET /articles` | – | `{"ids":[], "titles":[], "docs":[], "laws":[] 또는 null}` | 없음 |
| `POST /search` | `{"question", "k"}` | `{"hits":[…]}` (`Retriever.search`와 같은 dict) | 있음 |
| `POST /rank_all` | `{"questions":[…]}` | `{"rank":[[int…]…]}` | 있음 |
| `POST /rerank` | `{"question", "candidates":[…], "top_k"}` | `{"hits":[…]}` (`rerank_score`, `emb_rank` 포함) | 있음 |
| `POST /load` | `{"model"}` | `{"load_s": float 또는 null}` (이미 올라 있으면 null) | 있음 |
| `POST /generate` | `{"model", "question", "hits", "system", "max_tokens"}` | NDJSON 스트림. 줄마다 `{"text", "generation_tps", "prompt_tokens", "prompt_tps", "generation_tokens", "peak_memory", "finish_reason"}`. 오류가 나면 마지막 줄이 `{"error": {"type", "message"}}` | 있음 (스트림 전체 동안) |

- 오류 응답 형식: `{"error": {"type": "FileNotFoundError", "message": "..."}}`. 상태 코드는 400(입력 오류, 없는 모델 키), 401(토큰), 503(로딩 중), 500(그 밖).
- `/generate` 중에 클라이언트가 연결을 끊으면(간결형 조기 종료, Gradio 정지 버튼, 브라우저 닫기) 다음 쓰기에서 `BrokenPipeError`나 `ConnectionResetError`가 난다. 이때 생성을 멈추고 락을 푼다. 서버는 죽지 않아야 한다.
- 질문 내용은 서버 로그에 남기지 않는다. 기존 `log_timing` 원칙과 같다. 요청마다 경로, 모델, 처리 시간만 한 줄 남긴다.
- numpy 값(`float32`, `int64`, `str_`)은 JSON으로 바꾸기 전에 파이썬 기본 타입으로 변환한다.
- MLX 메모리 API 이름(`mx.get_active_memory`/`mx.get_peak_memory`/`mx.get_cache_memory`인지, `mx.metal.*`인지)은 잠금 버전 mlx 0.32.4에서 확인하지 않았다. 설치된 패키지에서 확인하고, 없으면 null을 돌려준다.

---

## 6. 단계별 작업 (커밋 단위)

### 단계 0: 계획 문서
- 이 문서를 `docs/plans/model-server.md`로 추가한다.

### 단계 1: 웹앱 포트와 인스턴스 이름
- `security.py`
  - `SERVER_NAME`은 상수로 유지한다.
  - 기존 `SERVER_PORT` 상수를 하위 호환 기본값으로 남기고 `server_port()`를 추가한다. `LAWBOT_PORT`를 검증해서 돌려준다.
  - `get_local_launch_config()`가 `server_port()`를 쓰게 한다.
  - `tests/integration_server.py`는 `security.SERVER_PORT = port`로 포트를 바꾼다. 이 방식이 계속 통하게 하거나, 환경 변수 `LAWBOT_PORT`로 넘기도록 바꾼다. 둘 중 단순한 쪽을 고른다.
- `app.py`: `LAWBOT_INSTANCE`가 있으면 `gr.Blocks(title=…)`과 맨 위 제목에 붙인다.
- `bench_ui.py`: `security.server_port()`를 쓴다.
- 테스트(`test_security.py`에 추가):
  - `LAWBOT_PORT`가 없거나 비어 있으면 기존 기본값
  - `LAWBOT_PORT`를 정하면 그 값(테스트 값은 `free_port()`로 얻음)
  - 범위 밖, 숫자 아님, 빈 값이면 `ValueError`
  - `GRADIO_SERVER_PORT`는 여전히 무시
  - 인스턴스 이름이 제목에 붙는지(`build_demo` 수준에서 확인)
- `conftest.ENV_KEYS`에 `LAWBOT_PORT`, `LAWBOT_INSTANCE`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_SERVER_PORT`, `LAWBOT_MODEL_TOKEN`, `LAWBOT_MODEL_SERVER_WAIT`을 추가한다.

### 단계 2: 동작 변화 없는 정리 (원격 모드 준비)
- `llm.SYSTEM`을 가벼운 모듈(예: `src/lawbot/prompts.py`)로 옮기고 `llm.py`에서 다시 export한다. 기존 `from lawbot.llm import SYSTEM`은 그대로 동작해야 한다. 웹앱이 mlx_lm 없이 v1 기본 system을 읽기 위한 준비다.
- `service.py`의 `Retriever`, `LLM`, `Reranker` import를 함수 안 지연 import로 옮긴다.
  - **주의:** 기존 테스트는 가짜 모델을 넣는다. 예를 들어 `tests/integration_server.py`는 `sys.modules['lawbot.llm']`과 `sys.modules['lawbot.retriever']`를 가짜 모듈로 바꾼다. 가짜 `lawbot.llm`에는 `LLM`과 `SYSTEM`만 있다. 먼저 `tests/test_webapp.py`, `test_pipeline.py`, `test_reranker.py`, `integration_server.py`가 어떤 방식으로 가짜를 넣는지 확인한다. 그다음 기존 테스트를 고치지 않고 통과하는 방식을 고른다. 예: 모듈 수준 이름은 남겨 두고 처음 쓸 때 채우는 방식, 또는 `load_app` 안에서 `from lawbot.llm import LLM`.
- 이 단계에서 기존 테스트 138개가 그대로 통과해야 한다.

### 단계 3: 모델 서버 `src/lawbot/model_server.py`
- `main()`의 순서:
  1. `envfile.load()`
  2. 토큰과 포트 검증
  3. 포트를 먼저 연다(`/health`는 `ready:false`)
  4. 백그라운드 스레드에서 `service.load_app(local=True)`를 호출
  5. 로딩이 끝나면 `ready:true`
  - 로딩이 실패하면 오류를 출력하고 종료 코드 1로 끝낸다. launchd `KeepAlive`가 무한히 재시작하지 않도록 `ThrottleInterval`을 문서에 적는다.
- `create_server(app, port, token) -> ThreadingHTTPServer`를 분리한다. 테스트에서 가짜 `LawbotApp`을 넣어 포트 0으로 띄울 수 있어야 한다.
- 핸들러는 5장 API를 구현한다. `protocol_version`은 HTTP/1.0으로 두고 스트림 끝에서 연결을 닫는다. `daemon_threads=True`.
- `/generate`는 `app.models.get(model)`으로 모델을 받은 뒤 `llm.stream(question, hits, max_tokens=…, system=system)`을 호출한다. system이 비어 있으면 400으로 거부한다(D5: 웹앱이 항상 보낸다).
- `pyproject.toml`의 `[project.scripts]`에 `lawbot-model-server = "lawbot.model_server:main"`을 추가한다.
- 이 모듈은 gradio를 import하지 않는다. 테스트로 확인한다.

### 단계 4: 클라이언트 `src/lawbot/remote.py`와 `load_app` 원격 모드
- `ModelServerClient(url, token)`:
  - `urllib.request` 기반. `get_json`, `post_json`, `post_stream`(줄 단위 제너레이터)
  - 접속 실패 메시지: `모델 서버에 연결할 수 없습니다: <LAWBOT_MODEL_SERVER 값> (uv run lawbot-model-server 실행 여부 확인)`
  - 401은 토큰 불일치로 안내한다. 토큰 값은 출력하지 않는다.
- `RemoteRetriever`:
  - 초기화할 때 `/articles`를 읽어 `ids`, `titles`, `docs`, `laws`를 numpy 배열로 채운다. `eval_v4`가 `keys[r]` 같은 배열 인덱싱을 한다.
  - `search()`, `rank_all()`(np.ndarray로 반환)
- `RemoteReranker`: `candidates` 속성, `rerank()`
- `RemoteLLM(client, key)`:
  - `stream(question, hits, max_tokens=1024, system=None)`. system이 None이면 그 모델의 기본 system을 웹앱 쪽에서 정해서 보낸다(`service.system_text(profile, spec)`, 없으면 `prompts.SYSTEM`).
  - 줄마다 `types.SimpleNamespace`를 낸다. 제너레이터가 닫히면 응답 객체를 닫아 연결을 끊는다(`try/finally`).
  - `error` 줄을 받으면 `RuntimeError`를 올린다.
- `RemoteModelManager(specs, client)`:
  - `specs`(웹앱 프로필 기준), `is_loaded(key)`(`/info`의 loaded), `get(key)` → `(RemoteLLM, load_s)`(`/load`), `loaded`(property, 서버 목록), `max_resident`(서버 값)
  - `/load`가 실패하면 서버가 보낸 오류 타입에 맞춰 `OSError`나 `ValueError`의 하위 클래스로 올린다. 그래야 `app.py`의 기존 안내가 그대로 나온다.
- `service.load_app(profile=None, local=False)`:
  - `LAWBOT_MODEL_SERVER`가 있고 `local`이 False이면 원격 모드로 동작한다.
    1. 프로필의 system 파일만 확인한다(조문 CSV와 모델 폴더는 서버가 확인).
    2. 서버가 `ready`가 될 때까지 `LAWBOT_MODEL_SERVER_WAIT`초 동안 기다린다.
    3. `/info`를 읽고 `api_version`, 프로필 이름, `doc_style`이 같은지 확인한다. 웹앱의 `LAWBOT_MODELS` 키가 서버 모델 목록에 다 있는지도 확인한다. 하나라도 다르면 `RuntimeError`를 내고 차이를 출력한다.
    4. `LawbotApp(profile, RemoteRetriever, RemoteModelManager, early_stop, RemoteReranker 또는 None, full_system)`을 만든다.
  - 로그 한 줄에 원격 주소, 서버 프로필, 리랭커, 상주 모델을 출력한다.
  - `service.ask`와 `format_system`은 바꾸지 않는 것이 목표다.

### 단계 5: 운영 도구
- `check_deploy`: 원격 모드이면 파일 검사 대신(또는 파일 검사에 더해) 서버 접속, 토큰, `api_version`, 프로필 일치를 검사하고 결과를 PASS/FAIL로 출력한다.
- `bench_models`: 원격 모드에서는 자기 프로세스 메모리 대신 `/info`의 서버 MLX 메모리를 출력한다.
- `eval_v4`, `eval_concise`: 코드 변경 없이 원격 모드로 돌아가는지 확인한다. 결과 CSV의 `peak_gb`는 서버 기준이라고 README에 적는다.
- `verify_reranker`, `eval_dev`, `eval_refusal`, `eval_oos`, `diag_*`: 바꾸지 않는다. README에 "로컬 모드 전용, 실행 전에 모델 서버를 멈출 것"이라고 적는다.

### 단계 6: 배포 템플릿과 문서
- `.env.example`에 운영 예시 블록을 **주석으로** 추가한다. 포트 자리는 비우고 숫자를 쓰지 않는다. 예:
  ```
  # ---- 상시 운영 (모델 서버 공유). 포트 번호는 직접 정해서 적기 (레포에 올리지 않음) ----
  # LAWBOT_INSTANCE=운영
  # LAWBOT_PORT=
  # LAWBOT_MODEL_SERVER_PORT=
  # LAWBOT_MODEL_SERVER=http://127.0.0.1:<모델 서버 포트>
  # LAWBOT_MODEL_TOKEN=
  ```
  주석 처리해 두므로 기본 동작은 바뀌지 않는다.
- `.env.dev.example`을 새로 만든다: 인증 값, `LAWBOT_INSTANCE=개발`, `LAWBOT_PORT=`(비움), `LAWBOT_MODEL_SERVER=http://127.0.0.1:<모델 서버 포트>`, `LAWBOT_MODEL_TOKEN=`, `LAWBOT_PROFILE`, `LAWBOT_MODELS`, `LAWBOT_PRO_SYSTEM`. 모델 경로는 서버가 쓰므로 넣지 않는다.
- `deploy/launchd/com.lawbot.model-server.plist.example`, `deploy/launchd/com.lawbot.web.plist.example`:
  - `__PROD_DIR__`, `__UV__` 자리표시를 둔다. launchd의 PATH에는 uv가 없으므로 `which uv`로 찾은 절대 경로를 쓴다.
  - `ProgramArguments`는 `[__UV__, run, --frozen, lawbot-model-server]` 또는 `lawbot-app`
  - `WorkingDirectory`, `RunAtLoad`, `KeepAlive`, `ThrottleInterval 30`
  - 로그 경로는 `~/Library/Logs/lawbot/*.log`. launchd는 `~`를 풀지 않으므로 절대 경로 자리표시를 쓴다.
  - **비밀 값은 plist에 넣지 않는다.** `.env`는 `envfile`이 레포 최상위에서 읽는다.
- README의 한국어와 영어 두 곳에 "상시 운영과 개발 동시 실행" 절을 추가한다:
  - 구성도와 포트 표(0장)
  - 토큰 만들기: `openssl rand -hex 32`
  - 운영 worktree 만들기(10장 B)
  - launchd 등록, 해제, 재시작 명령
  - `tailscale serve`(운영 웹앱 포트만 노출, `funnel` 금지. 명령 예시도 `<운영 포트>` 자리표시)
  - 3장의 제약
  - 로컬 모드 전용 스크립트 목록
- README의 기존 문장 "서버는 `127.0.0.1:7860`에서만 열리고 … 포트는 바꿀 수 없습니다"를 "주소는 127.0.0.1 고정, 포트는 `.env`의 `LAWBOT_PORT`(없으면 기존 기본값)"로 고친다. 한국어(283행 부근)와 영어(707행 부근) 모두. 새로 쓰는 문장에는 포트 숫자를 넣지 않는다.
- README 프로젝트 구조 목록에 `model_server.py`, `remote.py`, `prompts.py`, `deploy/launchd/`를 추가한다.

### 단계 7: PR
- 8장 형식. 제목: `모델 서버 분리: 운영/개발 웹앱이 모델 한 벌을 공유 (+ LAWBOT_PORT)`

---

## 7. 테스트 계획 (모두 모델 없이 CI에서 돌아야 함)

새 파일 `tests/test_model_server.py`를 만든다. 서버를 띄우는 테스트에는 `pytestmark = pytest.mark.integration`을 붙여 127.0.0.1 접속을 허용한다. 가짜 `LawbotApp`은 `tests/fakes.py`의 `FakeRetriever`, `FakeLLM`과 가짜 리랭커로 구성하고, `create_server(app, 0, token)`을 스레드로 띄운다.

| # | 검사 |
| --- | --- |
| T1 | **로컬과 원격의 결과가 같다.** 같은 가짜 앱으로 `service.ask`를 로컬 모드와 원격 모드로 실행하면 단계 순서(load/generate/done), hits, raw, `st`의 키 집합이 같다. 리랭커가 있을 때와 없을 때 모두 확인 |
| T2 | `format_system`이 고른 system(`fmt='full'` + concise 모델)과 기본 system이 서버의 `llm.stream`에 그대로 전달된다 |
| T3 | 토큰이 없거나 틀리면 401. 응답과 로그에 토큰 값이 없다 |
| T4 | 서버는 토큰이 없거나 짧으면 시작을 거부한다. 바인드 주소가 127.0.0.1이다(`server.server_address[0]`) |
| T5 | 클라이언트는 `LAWBOT_MODEL_SERVER`의 호스트가 127.0.0.1이 아니면 거부한다(`http://0.0.0.0:<포트>`, `http://example.com`, https 등. 포트는 `free_port()` 값) |
| T6 | 스트림 중간에 클라이언트가 끊으면(제너레이터 `close()`) 서버가 생성을 멈추고 락을 푼다. 다음 요청이 바로 처리되고 서버 스레드가 살아 있다 |
| T7 | 생성 중 예외 → `error` 줄 → 클라이언트 `RuntimeError`. 로딩 실패(`FileNotFoundError`) → `/load` 500 → 클라이언트가 `OSError` 계열로 올려서 `app.make_chat`에 기존 안내 메시지가 나온다 |
| T8 | `api_version`, 프로필, `doc_style`이 다르거나 모델 키가 없으면 원격 `load_app`이 `RuntimeError`를 내고 메시지에 차이가 보인다 |
| T9 | 서버가 `ready:false`인 동안 원격 `load_app`은 기다리고, 대기 시간을 넘기면 명확한 오류를 낸다(테스트에서는 짧은 대기 시간 사용) |
| T10 | 모델 서버는 `LAWBOT_MODEL_SERVER`가 설정된 환경에서도 로컬 모드로 `load_app`을 호출한다(자기 자신에게 접속하지 않음) |
| T11 | **원격 모드 웹앱은 무거운 라이브러리를 올리지 않는다.** 새 인터프리터(subprocess)에서 가짜 서버를 상대로 원격 `load_app`을 실행한 뒤 `sys.modules`에 `mlx`, `mlx_lm`, `torch`, `sentence_transformers`가 없어야 한다. 기존 `test_no_model_libraries_loaded`와 같은 방식 |
| T12 | `lawbot.model_server`는 gradio를 import하지 않는다 |
| T13 | `RemoteRetriever`의 `ids`, `laws`, `docs`, `rank_all`로 `eval_v4.hit_table`과 `run_retrieval`의 키 계산이 로컬과 같은 결과를 낸다(가짜 v4 형식 조문 몇 개로 확인) |
| T14 | 요청 본문 한도 초과는 413, 깨진 JSON이나 필드 누락은 400 |
| T15 | `envfile`: `.env`에 `LAWBOT_MODEL_TOKEN`이 있는데 파일 권한이 넓으면 경고한다(값은 출력하지 않음) |
| 기존 | 포트 관련 이외의 기존 테스트는 수정 없이 통과 |

---

## 8. PR 본문 형식

```markdown
## 요약
운영/개발 웹앱을 같은 맥에서 동시에 띄울 수 있게 웹앱 포트를 .env로 설정 가능하게 하고(LAWBOT_PORT),
임베딩·리랭커·답변 모델을 한 번만 올리는 내부 모델 서버(lawbot-model-server, 127.0.0.1 전용, 포트는 .env 필수, 토큰 필수)를 추가했습니다.
LAWBOT_MODEL_SERVER가 비어 있으면 기존과 동일한 로컬 모드입니다.

## 구성
(0장의 구성도. 포트는 자리표시로만)

## 변경 파일
- …

## 답변 결과가 바뀌지 않는다는 근거
- T1(로컬/원격 같은 결과) …
- 프롬프트, 모델, 검색 코드는 변경 없음 (변경 파일 목록 참고)

## 테스트
- uv run pytest: N passed (기존 138 + 신규 M)
- 실제 모델 검증은 하지 않음 → 사용자 맥에서 docs/plans/model-server.md 9장 체크리스트로 확인 필요

## 충돌 가능
- (열린 PR이 service.py/app.py를 건드리면 적기)

## 미확인
- MLX 메모리 API 이름, Gradio 6.30의 HTTPS 프록시(tailscale serve) 뒤 로그인 동작, 같은 호스트의 다른 포트에서 로그인 쿠키가 겹치는지 …
```

---

## 9. 병합 전 사용자 수동 검증 (맥미니)

에이전트가 할 수 없는 실제 모델 확인이다. PR 브랜치를 체크아웃해서 진행한다. 측정값은 아래 표에 옮겨 적는다.

```zsh
gh pr checkout <PR번호>
uv sync --locked
# 토큰 1회 생성 → 개발 레포 .env에 LAWBOT_MODEL_TOKEN=... 추가
openssl rand -hex 32
```

0. 개발 레포 `.env`에 이 세션에서 정한 포트와 토큰을 먼저 적는다(`LAWBOT_PORT`, `LAWBOT_MODEL_SERVER_PORT`, `LAWBOT_MODEL_SERVER`, `LAWBOT_MODEL_TOKEN`). 아래 명령은 셸에서 일부 값만 덮어쓴다(셸 변수가 `.env`보다 우선).
1. 원격 모드 없이 기존처럼 실행(회귀 확인): `LAWBOT_MODEL_SERVER= uv run lawbot-app` → `http://127.0.0.1:<LAWBOT_PORT>`
2. 터미널 A: `uv run lawbot-model-server` → `ready` 로그와 로딩 시간 확인
3. 터미널 B (운영 역할): `LAWBOT_INSTANCE=운영 LAWBOT_PORT=<운영 포트> uv run lawbot-app`
4. 터미널 C (개발 역할): `LAWBOT_INSTANCE=개발 LAWBOT_PORT=<개발 포트> uv run lawbot-app`
5. 두 화면에서 예시 질문 3개 → 같은 조문, 같은 답변인지 확인. 활성 상태 보기(Activity Monitor) 메모리 기록
6. 원격 평가 소표본: `uv run lawbot-eval-v4 answers --n 20 --tag remote_check` (`.env`의 `LAWBOT_MODEL_SERVER` 사용)
   - 같은 20문항을 로컬 모드로 한 번 더 돌린다(`LAWBOT_MODEL_SERVER= uv run lawbot-eval-v4 answers --n 20 --tag local_check`, 모델 서버와 웹앱을 모두 멈춘 뒤). 답변이 글자 단위로 같은지 비교한다.
   - `--n 20`은 test에서 A_lsa와 B_newlaw를 10개씩 고정 시드로 뽑는다(`eval_v4.answer_sample`에서 확인). 설정을 고르는 용도가 아니라 동일성 확인이므로 test 표본을 써도 규칙 위반은 아니다.
7. 개발 화면에서 생성 중에 정지 → 운영 화면에서 바로 다음 질문이 처리되는지 확인(락 해제)

### 결과 기록 표

| 항목 | 로컬 모드 (기존) | 원격 모드 | 비고 |
| --- | --- | --- | --- |
| 모델 서버 로딩 시간 (초) | – | | 서버 로그 |
| 메모리: 모델 서버 프로세스 (GB) | – | | 활성 상태 보기 |
| 메모리: 운영 웹앱 프로세스 (GB) | | | 로컬은 모델 포함 |
| 메모리: 개발 웹앱 프로세스 (GB) | 실행 불가 | | |
| MLX peak (GB, `/info` 또는 측정 줄) | 20.4 (10-25) | | |
| 첫 글자 (초, 예시 질문 1) | | | 측정 줄 |
| 완료 (초, 예시 질문 1) | | | 측정 줄 |
| 20문항 답변 글자 단위 일치 | 기준 | /20 | `answers_remote_check.csv` vs `answers_local_check.csv` |
| 정지 후 다음 요청 대기 (초) | – | | 7번 |
| 휴대폰(Tailscale HTTPS) 로그인·답변 | – | 성공 / 실패 | 병합 후 운영 전환 때 |

---

## 10. 병합 후 사용자 명령어 (zsh)

### A. 병합과 main 정리

```zsh
cd ~/lawbot                                  # 개발 레포 경로 (실제 위치로 바꾸기)

# 1) 병합: 웹에서 Merge 버튼을 눌러도 되고, 터미널에서 해도 됨
gh pr checks <PR번호>                         # CI 통과 확인
gh pr merge <PR번호> --squash --delete-branch # 원격 브랜치 삭제 (기존 히스토리가 merge commit 방식이면 --merge)

# 2) main으로 돌아가 최신화
git switch main
git pull --ff-only
git fetch --prune                            # 지워진 원격 브랜치 참조 정리

# 3) 로컬 작업 브랜치 삭제 (gh가 이미 지웠으면 'not found'가 나와도 정상)
git branch -D feat/model-server              # squash 병합은 -d로 안 지워지므로 -D

# 4) 환경 맞추고 테스트
uv sync --locked
uv run pytest

# 5) 운영 배포용 태그
git tag -a v4.1.0 -m "모델 서버 분리, LAWBOT_PORT"
git push origin v4.1.0
```

### B. 운영 worktree 처음 만들기 (1회)

```zsh
cd ~/lawbot
git worktree add --detach ~/lawbot-prod v4.1.0
cd ~/lawbot-prod
ln -s ~/lawbot/models models                 # 모델 파일 공유 (디스크 두 배 방지)
cp ~/lawbot/.env .env && chmod 600 .env      # 그다음 운영 값으로 수정 (.env.example의 운영 블록 참고)
uv sync --frozen
uv run python -m lawbot.check_deploy         # 모델 파일 검사
```

개발 레포 `~/lawbot/.env`는 `.env.dev.example`을 참고해서 개발 값(`<개발 포트>`, `LAWBOT_INSTANCE=개발`, 원격 모드)으로 바꾼다. 두 `.env`의 `LAWBOT_MODEL_SERVER`와 `LAWBOT_MODEL_TOKEN`은 같은 값이어야 한다. 실제 포트 번호는 이 계획을 만든 Claude 세션에서 정한 값을 쓴다.

### C. 상시 실행 (launchd, 1회)

```zsh
mkdir -p ~/Library/Logs/lawbot
# deploy/launchd/*.plist.example의 자리표시(__PROD_DIR__, __UV__, 로그 경로)를 채워 복사
cp deploy/launchd/com.lawbot.model-server.plist.example ~/Library/LaunchAgents/com.lawbot.model-server.plist
cp deploy/launchd/com.lawbot.web.plist.example ~/Library/LaunchAgents/com.lawbot.web.plist
# (편집 후)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.model-server.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.web.plist
launchctl print gui/$(id -u)/com.lawbot.web | grep state

# Tailscale: 운영 웹앱만 tailnet 안에 HTTPS로 노출 (funnel 금지)
tailscale serve --bg <운영 포트>
tailscale serve status
```

- 맥미니가 잠자기에 들어가지 않게 설정한다(시스템 설정 → 에너지).
- `tailscale serve`의 인자 형식은 Tailscale 버전마다 다를 수 있다. 확인하지 않았으므로 `tailscale serve --help`로 확인한다.

### D. 이후 운영 업데이트

```zsh
cd ~/lawbot-prod
git fetch --tags
git switch --detach v4.2.0                   # 새 태그
uv sync --frozen
launchctl kickstart -k gui/$(id -u)/com.lawbot.model-server   # 서버 쪽 코드가 바뀌었을 때만
launchctl kickstart -k gui/$(id -u)/com.lawbot.web
```

---

## 11. 위험과 미확인 항목

| 항목 | 내용 | 확인 방법 |
| --- | --- | --- |
| Gradio 로그인 쿠키 | 브라우저 쿠키는 포트를 구분하지 않는다. 그래서 맥 브라우저에서 `127.0.0.1:<운영 포트>`와 `127.0.0.1:<개발 포트>`에 번갈아 로그인하면 Gradio 쿠키가 서로 덮어쓸 수 있다. Gradio 6.30의 쿠키 이름에 포트나 앱 구분이 들어가는지는 확인하지 않았다 | 9장 4–5번에서 두 탭으로 확인. 문제가 있으면 개발 화면은 `localhost:<개발 포트>`로 접속한다(호스트가 다르면 쿠키가 분리됨) |
| HTTPS 프록시 뒤 Gradio | `tailscale serve`(HTTPS → http://127.0.0.1:<운영 포트>) 뒤에서 로그인과 스트리밍이 동작하는지 확인하지 않았다 | 병합 후 휴대폰으로 확인. 9장 표 마지막 줄 |
| MLX 메모리 API | 5장 참고 | 에이전트가 설치된 패키지에서 확인 |
| 지연 import와 기존 테스트 | 단계 2의 가짜 모델 주입 방식과 충돌할 수 있다 | 에이전트가 기존 테스트를 고치지 않는 방식을 고른다 |
| 락 점유 | 휴대폰이 생성 중에 연결을 끊지 않고 사라지면, 생성이 `max_tokens`(1024)에서 끝날 때까지 락이 잡혀 있다 | 시간 한도가 있으므로 허용. README에 적기 |
| 로드맵 2.6 브랜치 | B4(요청 로그)와 B5(UI)가 `service.py`와 `app.py`를 바꾼다 | 병합 순서에 따라 나중 PR을 rebase |
