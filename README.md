# Lawbot: 노동법 RAG 챗봇

[한국어](#한국어) | [English](#english)

---

## 한국어

### 소개

Lawbot은 대한민국 노동법 질문에 조문을 근거로 답하는 검색 증강 생성(RAG) 챗봇입니다. 근로기준법 등 7개 노동 관련 법률의 415개 조문에서 튜닝한 임베딩 모델이 후보를 찾고, 리랭커가 3개로 좁힌 뒤, 답변 모델이 그 조문만 근거로 답합니다. 모든 구성 요소가 Apple Silicon 맥 한 대에서 로컬로 동작합니다.

학습·포트폴리오 목적의 프로젝트이며, 답변은 법률 자문이 아닌 참고 정보입니다.

### 주요 기능

- **조문 근거 답변**: 답변마다 근거 조문을 `법령명 제N조`로 밝히고, 화면에 인용한 조문과 검색만 된 조문을 나눠 보여 줍니다.
- **근거 부족 시 거절**: 제공된 조문으로 답할 수 없으면 추측하지 않고 "확인할 수 없습니다"라고 답합니다.
- **시행령·시행규칙 첨부**: 관련 하위법령을 따로 찾아 법률 조문 뒤에 붙입니다 (Pro 모델).
- **법령 변경 수집·요약**: 추적 대상 법령·행정규칙·별표서식의 변경을 매일 수집하고 요약합니다 ([services/monitor](services/monitor/README.md)).
- **로컬 실행**: 임베딩·리랭커·답변 모델이 맥 한 대의 모델 서버(OpenAI 호환 API)에서 돌고, 질문이 맥 밖으로 나가지 않습니다.

### 구성

```
브라우저 ──▶ 웹앱 (services/chatbot, Gradio)  ──┐
             조문 검색·정렬·프롬프트·화면         │  OpenAI 호환 HTTP + 토큰 (127.0.0.1)
                                                  ▼
             법령 변경 수집기 (services/monitor) ──▶ 모델 서버 (services/model-server)
             git 저장소·법제처 API → 이벤트·요약       임베딩 · 리랭커 · 답변 모델 (MLX)
```

자세한 흐름과 레포 구성은 [docs/architecture.md](docs/architecture.md)에 있습니다.

### 주요 지표

현재 웹앱 구성(v4: 튜닝 임베딩 → 리랭커 → Qwen3.8-27B)의 최종 결과입니다. 모든 평가 기록은 [docs/evals/](docs/evals/README.md)에 있습니다.

| 평가 | 결과 |
| --- | --- |
| 웹앱 test 2,075문항 (2026-10-25) | 검색 Recall@3 0.940 · 조문 인용 정확도 0.905 · 범위 밖 거절률 0.96 · 질문당 15.1초 ([기록](docs/evals/2026-10-25-v4-webapp-test.md)) |
| 시행령·시행규칙 첨부 val (2026-10-31) | 정답 하위법령 첨부 0.91 · 인용 0.885 ([기록](docs/evals/2026-10-31-decree-attach.md)) |

### 사용법

**요구 사항**: Apple Silicon 맥(27B 모델은 32GB), [uv](https://docs.astral.sh/uv/), 학습된 모델 파일(용량이 커서 저장소에 없음, [모델 준비](docs/operations.md#모델-준비)).

```zsh
git clone https://github.com/bhsong/lawbot.git && cd lawbot
uv sync
cp .env.example .env && chmod 600 .env   # 로그인 정보, 모델 서버 포트·주소·토큰(openssl rand -hex 32)을 채움

uv run lawbot-model-server               # 터미널 1: 모델을 올린 뒤 'ready'
uv run lawbot-app                        # 터미널 2: http://127.0.0.1:<LAWBOT_PORT> 에서 로그인
```

법령 변경 수집기는 `services/monitor/.env`에 법제처 인증키를 넣고 `uv run law-monitor bootstrap`으로 기준점을 잡은 뒤 `uv run law-monitor daily`로 실행합니다 ([사용법](services/monitor/README.md)).

```zsh
uv run pytest        # 기본 테스트 (모델·네트워크 불필요)
```

설정 변수 전체, 상시 운영(launchd·Tailscale), 피드백, 요청 로그는 [docs/operations.md](docs/operations.md)에 있습니다.

### 문서

| 문서 | 내용 |
| --- | --- |
| [docs/operations.md](docs/operations.md) | 설치·설정 변수·실행·상시 운영·테스트 |
| [docs/architecture.md](docs/architecture.md) | 질문 처리 흐름, 서비스와 모듈 구성 |
| [docs/evals/](docs/evals/README.md) | 평가 방법, 평가 기록, 실패한 실험 |
| [docs/data-provenance.md](docs/data-provenance.md) | 법령 데이터 출처·버전·갱신 방법 |
| [docs/plans/](docs/plans/) · [docs/archives/](docs/archives/) | 진행 중인 계획 · 끝난 계획 |

### 고지

- 베이스 모델(Qwen3-4B, Qwen3-Embedding-0.6B, Qwen3-Reranker-4B, Qwen3.8-27B)은 각 모델의 라이선스를 따릅니다.
- 법령 원문은 저작권법 제7조에 따라 보호받지 않는 저작물입니다.
- 이 챗봇의 답변은 법률 자문이 아닙니다. 구체적인 사안은 고용노동부 고객상담센터(국번 없이 1350)나 공인노무사와 상담하시기 바랍니다.

---

## English

### Overview

Lawbot is a retrieval-augmented generation (RAG) chatbot that answers questions about Korean labor law, citing the articles it relies on. Over 415 articles from seven labor-related laws, a fine-tuned embedding model finds candidates, a reranker narrows them to 3, and the answer model answers using only those articles. Everything runs locally on a single Apple Silicon Mac.

This is a learning and portfolio project. Its answers are informational and do not constitute legal advice.

### Features

- **Article-grounded answers**: Every answer names its articles (`law name + article number`), and the UI separates cited articles from merely retrieved ones.
- **Refusal when evidence is missing**: If the provided articles cannot support an answer, it says so instead of guessing.
- **Enforcement decrees and rules**: Related decree/rule articles are retrieved separately and attached after the Act articles (Pro model).
- **Law change monitoring**: Changes to tracked laws, administrative rules and forms are collected and summarized daily ([services/monitor](services/monitor/README.md), Korean).
- **Local inference**: Embedding, reranker and answer models run in a model server (OpenAI-compatible API) on the same Mac; questions never leave it.

### Components

```
Browser ──▶ Web app (services/chatbot, Gradio)  ──┐
            retrieval, ranking, prompt, UI          │  OpenAI-compatible HTTP + token (127.0.0.1)
                                                    ▼
            Law change monitor (services/monitor) ──▶ Model server (services/model-server)
            git repos, law.go.kr API → events, summaries   embedding · reranker · answer models (MLX)
```

Details are in [docs/architecture.md](docs/architecture.md) (Korean).

### Key results

Final results of the current web app setup (v4: fine-tuned embedding → reranker → Qwen3.8-27B). All evaluation records are in [docs/evals/](docs/evals/README.md) (Korean).

| Evaluation | Result |
| --- | --- |
| Web app, 2,075 test questions (2026-10-25) | Recall@3 0.940 · citation accuracy 0.905 · out-of-scope refusal 0.96 · 15.1 s per question ([record](docs/evals/2026-10-25-v4-webapp-test.md)) |
| Decree attachment, val (2026-10-31) | correct decree attached 0.91 · cited 0.885 ([record](docs/evals/2026-10-31-decree-attach.md)) |

### Usage

**Requirements**: Apple Silicon Mac (32 GB for the 27B model), [uv](https://docs.astral.sh/uv/), trained model files (not in the repository because of their size; see [model preparation](docs/operations.md#모델-준비)).

```zsh
git clone https://github.com/bhsong/lawbot.git && cd lawbot
uv sync
cp .env.example .env && chmod 600 .env   # fill in login, model server port/address/token (openssl rand -hex 32)

uv run lawbot-model-server               # terminal 1: loads models, then 'ready'
uv run lawbot-app                        # terminal 2: log in at http://127.0.0.1:<LAWBOT_PORT>
```

For the law change monitor, put the law.go.kr API key in `services/monitor/.env`, run `uv run law-monitor bootstrap` once, then `uv run law-monitor daily` ([usage](services/monitor/README.md)).

```zsh
uv run pytest        # default tests (no models or network needed)
```

All settings, always-on operation (launchd, Tailscale), feedback and request logs are described in [docs/operations.md](docs/operations.md) (Korean).

### Documents

| Document | Contents |
| --- | --- |
| [docs/operations.md](docs/operations.md) | install, settings, running, always-on operation, tests |
| [docs/architecture.md](docs/architecture.md) | request flow, services and modules |
| [docs/evals/](docs/evals/README.md) | evaluation method, records, failed experiments |
| [docs/data-provenance.md](docs/data-provenance.md) | statute data sources, versions, updates |
| [docs/plans/](docs/plans/) · [docs/archives/](docs/archives/) | active plans · finished plans |

All documents under `docs/` are in Korean.

### Notices

- The base models (Qwen3-4B, Qwen3-Embedding-0.6B, Qwen3-Reranker-4B, Qwen3.8-27B) are subject to their own licenses.
- Korean statutes are not protected by copyright (Article 7 of the Korean Copyright Act).
- Answers from this chatbot are not legal advice. For a specific case, consult the Ministry of Employment and Labor helpline (1350 in Korea) or a certified labor attorney.
