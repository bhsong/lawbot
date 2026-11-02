# 구조

## 질문 처리 흐름 (v4)

```
질문
 └─ 검색: 튜닝 임베딩 (Qwen3-Embedding-0.6B, fp16, MPS) → 415개 조문 중 상위 10개
     └─ 리랭커: Qwen3-Reranker-4B (튜닝 없음, MLX 4비트) → 상위 3개
         ├─ (Pro만) 시행령·시행규칙 397개 중 임베딩 상위 10개 → 같은 리랭커 → 필터 → 최대 2개를 법률 3개 뒤에 첨부
         └─ 답변: Qwen3.8-27B (튜닝 없음, MLX 4비트, 간결형) / 소형 맥은 Qwen3-4B + v4 LoRA (법률 3개만)
             └─ 답변 + 근거 조문 표시 (Gradio)
```

모델(임베딩·리랭커·답변)은 모두 모델 서버(`services/model-server`, OpenAI 호환 API)에서 돌고, 웹앱(`services/chatbot`)은 조문 인덱스·검색 정렬·하위법령 필터·프롬프트 조립·화면을 맡습니다. 생성만 OpenAI 호환 클라우드 모델로 바꿀 수 있습니다.

## 학습 구성 요소

| 구성 요소 | 모델·라이브러리 | 비고 |
| --- | --- | --- |
| 임베딩 검색 | Qwen3-Embedding-0.6B, sentence-transformers | 질문→조문 대조학습 + hard negative, 3에폭 |
| 답변 생성 | Qwen3-4B + LoRA (r=16), mlx-lm | 정답 조문 위주 학습 + 거절 샘플 135개 포함 |
| 웹 UI | Gradio | 답변 스트리밍, 근거 조문 패널 |
| 학습 | Google Colab (NVIDIA L4) | TRL SFTTrainer, PEFT |

## 레포 구성

uv workspace 모노레포입니다. 레포 최상위에서 `uv sync`·`uv run …`을 실행하면 모든 서비스가 함께 설치됩니다. 데이터(`data/`), 모델(`models/`), 평가 결과(`results/`)는 레포 최상위에 그대로 둡니다. 서비스는 서로 import하지 않고 HTTP(OpenAI 호환 API)로만 통합니다. 세 서비스가 같은 규칙을 써야 하는 것(`.env` 파서, 포트 검증, 웹앱 ↔ 모델 서버 요청·응답 형식과 오류 type)만 `packages/lawbot-common`에 두고 함께 씁니다.

```
pyproject.toml         # workspace 루트: 멤버 목록, pytest·ruff 설정
services/chatbot/      # 웹앱 런타임 (패키지 lawbot, 모델 라이브러리 없음), 테스트는 services/chatbot/tests/ (평가 도구 테스트 포함)
services/model-server/ # OpenAI 호환 모델 서버 (패키지 lawbot_model_server, MLX·torch), 테스트는 services/model-server/tests/
services/monitor/      # 법령 변경 수집기 (패키지 law_monitor, git만 사용). 사용법은 ../services/monitor/README.md
packages/lawbot-common/ # 서비스 공통 규칙 (패키지 lawbot_common: .env 파서·포트 검증·웹앱 ↔ 모델 서버 API 계약 api.py)
tools/lawbot-tools/    # 평가·분석 도구 (패키지 lawbot_tools, uv run lawbot-eval-v4). lawbot을 import하고, lawbot은 이것을 import하지 않음
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
├── hits.py            # 검색 결과·측정값 dict의 모양 (TypedDict: 타입 검사용)
├── settings.py        # 실행 설정 객체: 시작할 때 한 번 읽어 모두 검증 (틀린 값은 한꺼번에)
├── security.py        # 로컬 전용 실행 설정 (127.0.0.1, 로그인 필수, 공유 링크 없음, 포트·모델 서버·생성 API 주소 검증)
├── citation.py        # 인용 파싱·형식 검증·거절 판정·비교용 정규화 (웹앱 화면과 eval_v4 채점이 같이 씀)
├── check_deploy.py    # 배포 점검 (조문 CSV·system·설정·모델 서버 접속, uv run python -m lawbot.check_deploy)
├── answer_format.py   # 간결형 화면 후처리 (결론 + 근거 줄)
├── manifest.py        # 법령 데이터 매니페스트 검사 (조문 수·sha256·기준일)
├── feedback.py        # 답변 피드백 저장 (LAWBOT_FEEDBACK, feedback/*.jsonl)
└── feedback_ui.py     # 피드백 깃발의 터치 기기 보정 CSS·JS
tools/lawbot-tools/src/lawbot_tools/
├── eval_v4.py         # v4 웹앱 구성 평가 (lawbot-eval-v4)
├── score_answers.py   # 키워드 기반 채점기
├── refusal.py         # 거절 판정 (【결론】에 '확인할 수 없'), 범위 밖 답변 판정 (거절·안내·억지적용·미인용)
├── diff_answers.py    # 두 평가 결과의 답변 원문 글자 단위 비교
├── smoke_decree.py    # 하위법령 첨부 웹앱 경로 스모크 (실제 모델)
├── feedback_review.py # 피드백 → 검수 CSV (평가 데이터와 겹치는 질문 제외)
├── feedback_export.py # 검수 CSV → dev 질문·임베딩 쌍·SFT·KTO CSV
├── log_summary.py     # 요청 로그(logs/requests.jsonl) 요약
└── versions.py        # 설치된 패키지·맥 사양
services/model-server/src/lawbot_model_server/
├── api.py             # OpenAI 호환 API (FastAPI): 인증·크기 제한·로그
├── scheduler.py       # GPU 스레드 하나: chat 동시 생성 상한·대기열·끊김, 생성 스텝 사이에 embeddings·rerank
├── chat.py            # MLX 채팅 모델 (chat template·생각 블록 처리·탐욕적 생성), 배치 생성 엔진 (mlx_lm BatchGenerator)
├── embeddings.py      # sentence-transformers 임베딩 (MPS, fp16)
├── rerank.py          # Qwen3-Reranker 채점 (실험 노트북과 같은 입력)
├── registry.py        # 제공 모델 목록, chat 모델 LRU
├── config.py          # 포트·토큰·LAWBOT_SERVE_* 설정
├── check.py           # 모델 폴더 점검 (양자화·어댑터·토크나이저·query 프롬프트)
└── server.py          # uvicorn 실행 (lawbot-model-server)
deploy/launchd/        # 상시 운영용 launchd 템플릿 (모델 서버·웹앱)
```
