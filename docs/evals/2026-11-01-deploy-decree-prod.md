# 기록: 하위법령 첨부 운영 반영 (병합 전 확인용)

- 날짜: 2026-11-01
- 계획: [`docs/archives/deploy-decree-prod.md`](../archives/deploy-decree-prod.md)
- 운영 worktree: 303f764 (API 1, 법률 415) → **6eda75b** (`chore/cleanup` 끝 = PR #26 + #27)
- 운영 `.env`: 첨부 설정 5줄 추가 (`.env.example`과 같은 값). 백업은 레포 밖 (권한 600). 개발 `.env`: 첨부 설정 4줄 추가 (`.env.dev.example`과 같음)

## 결과

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | `uv sync --frozen` | 변경 없음 | OK (uv.lock 303f764와 같음) |
| 2 | `check_deploy` (서비스 내린 상태) | 모델 서버 접속 외 FAIL 0 | **OK**. 법률 415 · 전체 812, `v4-decree` 매니페스트 sha256·법령별·종류별 일치, 첨부 τ 0.9 · 최대 2개 · 후보 10 · 모델 pro, 법률 조문이 `articles_v4.csv`와 같음. FAIL은 모델 서버 미실행 1건(의도) |
| 3 | 모델 서버 `ready` | api_version 2, decree 켜짐 | **OK**. 불러오기 44.4초 (이전 27.1초), 조문 인덱스 36.3초 (법률 415 + 하위법령 397, max_seq 3072), `/info` decree `{tau 0.9, k 2, candidates 10, models [pro], n_articles 397}`, MLX active 17.4GB |
| 4 | 운영 웹앱 시작 | 서버 설정 확인 통과 | **OK**. 시작 거부 없음, 질문마다 `/search`·`/rerank`가 법률·하위법령 두 번씩 호출됨 |
| 5 | 사용자 화면 확인 (Pro, 질문 3개) | 계획 3절 | 사용자 확인 대기 |

## 되돌리기
계획 4절: 두 서비스 `bootout` → 운영 worktree `checkout --detach 303f764` + `.env` 백업 복원, 개발 `.env` 4줄 삭제 → `bootstrap`.
