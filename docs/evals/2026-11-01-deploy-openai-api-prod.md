# 기록: OpenAI 호환 모델 서버(PR #34) 운영 반영

- 날짜: 2026-11-01
- 운영 worktree: 8ca96ff (전용 API, 로컬/원격 모드) → **c29c31d** (main, PR #34 병합, 태그 **v4.4.0**)
- 운영 `.env`: 옛 모델 경로 변수 3개(`LAWBOT_PRO_PATH`, `LAWBOT_RERANKER_MODEL_PATH`, `LAWBOT_EARLY_STOP`)를 지우고, 모델 서버 `LAWBOT_SERVE_*` 3줄과 웹앱 모델 id 3줄(`LAWBOT_EMB_MODEL`·`LAWBOT_RERANK_MODEL`·`LAWBOT_PRO_MODEL`)을 추가했어요. Pro 폴더는 기존 `models/v4/qwen_27b_mlx_4bit`예요. 백업은 레포 밖에 권한 600으로 두었어요.
- launchd plist: 템플릿 차이가 주석 한 줄뿐이라 다시 만들지 않았어요(실행 명령 `lawbot-model-server`·`lawbot-app` 그대로).
- 배포한 코드는 main과 같아요(c29c31d). PR #35(동시 추론)는 병합 전이라 넣지 않았어요.

## 결과

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | 두 서비스 `bootout` → `checkout --detach c29c31d` → `uv sync --frozen` | 오류 없음 | **OK** |
| 2 | `lawbot_model_server.check` | FAIL 없음 | **OK**. 27B 4비트 16.08GB, 임베딩 F16, 리랭커 4비트·yes/no 토큰 |
| 3 | 모델 서버 `ready` | 세 모델 상주 | **OK**. 불러오기 9.6초 (이전 44.4초: 조문 인덱스를 웹앱이 만들게 바뀜) |
| 4 | `check_deploy` | FAIL 없음 | **OK**. 모델 서버 접속·토큰, 필요한 모델 모두 있음, 하위법령 397·법률 조문 일치 |
| 5 | 운영 웹앱 시작 | 시작 거부 없음 | **OK**. 조문 인덱스 35.1초 (법률 415 + 하위법령 397), 생성 모델 서버 (`qwen3.8-27b-4bit`) |
| 6 | 하위법령 스모크 3문항 (운영 트리, 운영 서버) | 10-29 원격 모드 결과와 조문·답변 동일 | **OK**. `조문·답변 동일: True`, MLX peak 19.96GB |

## 되돌리기
두 서비스 `bootout` → 운영 worktree `checkout --detach 8ca96ff` + `.env` 백업 복원 → `uv sync --frozen` → `bootstrap`.
