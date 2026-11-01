# 기록: 동시 추론(PR #35) 운영 반영

- 날짜: 2026-11-01
- 운영 worktree: c29c31d (v4.4.0) → **33c5565** (main, PR #35 병합, 태그 **v4.4.1**)
- 운영 `.env`: 바꾸지 않았어요. 새 변수 `LAWBOT_MAX_CONCURRENT`(모델 서버)와 `LAWBOT_CHAT_CONCURRENCY`(웹앱)는 기본값이 1이라 지금과 같아요.
- 의존성(`uv.lock`)·launchd plist: 변경 없음
- 모델 서버만 내렸다가 올렸어요(메모리 때문). 웹앱은 코드가 바뀌어서 `kickstart -k`로 재시작했어요.
- 생성 경로: 상한 1도 `BatchEngine`(mlx_lm `BatchGenerator`)이에요. 배치 경로 1건은 val 답변 200개가 이전과 같았어요([기록](2026-11-01-batch-generation.md)).

## 결과

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | 모델 서버 `bootout` → `checkout --detach 33c5565` → `uv sync --frozen` | 오류 없음 | **OK** (첫 `bootstrap`은 bootout 직후라 I/O 오류 5, 몇 초 뒤 다시 해서 성공) |
| 2 | `lawbot_model_server.check` | FAIL 없음 | **OK**. `동시 생성 최대 1개 (mlx_lm BatchGenerator)` |
| 3 | 모델 서버 `ready` | 세 모델 상주 | **OK**. 불러오기 9.4초, 동시 생성 최대 1 |
| 4 | 웹앱 재시작 | 시작 거부 없음 | **OK**. 조문 인덱스 36.2초 (법률 415 + 하위법령 397) |
| 5 | `check_deploy` | FAIL 없음 | **OK**. FAIL·WARN 없음 |
| 6 | 하위법령 스모크 3문항 (운영 트리, 운영 서버) | 10-29 원격 모드 결과와 조문·답변 동일 | **OK**. `조문·답변 동일: True`, MLX peak 20.40GB (v4.4.0 19.96GB) |

- MLX peak가 0.44GB 늘었어요. 원인은 확인하지 않았어요. 배치 생성기의 캐시·버퍼 차이로 추정해요. val 동시 1 평가(서버 상한 4)의 최대값은 이전과 같은 20.53GB였어요.

## 되돌리기
모델 서버 `bootout` → 운영 worktree `checkout --detach c29c31d`(v4.4.0) → `uv sync --frozen` → 모델 서버 `bootstrap` → 웹앱 `kickstart -k`. `.env`는 그대로 써요.
