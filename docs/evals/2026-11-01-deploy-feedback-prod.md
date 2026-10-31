# 기록: 답변 피드백(B6) 운영 반영

- 날짜: 2026-11-01
- PR: #29 (#25 대체), 계획 [`docs/archives/feedback-on-decree-main.md`](../archives/feedback-on-decree-main.md)
- 운영 worktree: c8dbd3e → **8ca96ff** (main, #29 병합)
- 운영 `.env`: `LAWBOT_FEEDBACK=1`, `LAWBOT_FEEDBACK_DIR`(레포 밖 절대 경로, 폴더 700) 추가. 백업은 레포 밖 (권한 600)
- 질문 원문은 이 기록에 남기지 않는다

## 결과

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | 운영 코드와 main 차이 (c8dbd3e → 8ca96ff) | 웹앱 쪽만 | **OK**. 실행 코드는 `app.py` + 새 `feedback*.py` 4개. `model_server.py`·`llm.py`·`retriever.py`·`reranker.py`·`uv.lock` 같음 → `uv sync`·모델 서버 재시작 없음 |
| 2 | 웹앱 재시작 (`kickstart -k com.lawbot.web`) | 시작 거부 없음 | **OK**. 저장 폴더 확인 통과, 웹앱 running |
| 3 | 화면 | 답 아래 👍·깃발 | **OK** (사용자 확인) |
| 4 | 👍 저장 (Pro, 간결형) | 한 줄, 권한 600 | **OK**. 파일 `-rw-------`, 폴더 `drwx------`, `label` like |
| 5 | 기록 내용 | 운영·원격·v4·첨부 설정 | **OK**. `instance` 운영, `mode` remote, `profile` v4, `dataset_version` v4-decree, `commit` 8ca96ff, `request_id` 있음, system 289자 |
| 6 | 하위법령 첨부 기록 | `decree`·`kind` | **OK**. `decree` {τ 0.9, k 2}, `n_decree` 2. hits = 법률 3 + 근로기준법 시행규칙 1·시행령 1 (`kind` 표시), 법률 hit에는 `kind` 없음 |
| 7 | 인용 판정·측정값 | 기록됨 | **OK**. 근로기준법 제26조 ok, 거절 아님. search 0.20 · rerank 2.56 · decree_search 0.07 · decree_rerank 3.78 · ttft 20.1 · 전체 36.8초, 99토큰 |

## 확인하지 않은 것
- 깃발 이유 4종, 선택 해제(cleared), 휴대폰 깃발 탭, 운영·개발 두 탭 동시 사용 (#29 본문의 맥 확인 순서)
- 피드백을 켜기 전과 같은 질문의 응답 시간 비교 (피드백은 답이 끝난 뒤 기록을 메모리에 두는 것만 함)

## 되돌리기
- 피드백만 끄기: 운영 `.env`의 `LAWBOT_FEEDBACK=0` → 웹앱 재시작 (버튼 없음, 화면·동작은 이전과 같음)
- 코드까지: `git -C ~/Work/lawbot-prod checkout --detach c8dbd3e`, `.env` 백업 복원 → 웹앱 재시작
