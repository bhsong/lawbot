# 조문 데이터 자동 갱신 확인 (2026-11-02)

계획: [plans/articles-auto-update.md](../plans/articles-auto-update.md). 원문: Legalize KR `8c8917412e48`. 개발 worktree에서 운영 모델 서버로 (운영 웹앱·데이터는 건드리지 않음).

| 항목 | 기대 | 실제 |
| --- | --- | --- |
| `uv run pytest` | 통과 | 643 passed, 9 deselected (갱신 12 · monitor 점검 8 · check_deploy 1 추가) · ruff · pyright 0 errors |
| `--init` | data/v4 → data/live | CSV 2 + 매니페스트 2 복사, `data/live`는 git 밖 (`git status` 깨끗) |
| `--asof 2026-11-27` 갱신 (재시작 없이) | 남녀고용평등법 6조문 갱신 | `articles updated=yes asof=2026-11-27 changed=6 archive=2026-10-20 restart=no`, 67초. `archive/*_2026-10-20.*` 4개, 새 매니페스트 `live-2026-11-27`·`source.ref 8c891741`. 법률 임베딩 새로, 하위법령은 본문이 같아 캐시 그대로. 확인(바뀐 조문 6개 검색) 통과 |
| 갱신된 데이터로 개발 웹앱 | 새 본문으로 답변 | 조문 인덱스 0.1초(갱신이 만든 캐시 사용). "난임치료휴가는 며칠이고 그중 유급은?" → "연간 6일 이내 … 최초 4일은 유급" (갱신 전 본문은 2일), 제18조의3 임베딩 1위·리랭커 1.000 |
| `--rollback --restart` (개발 worktree) | 되돌리고, 운영 웹앱은 재시작 안 함 | `restored=2026-10-20 archived=2026-11-27 restart=skipped(com.lawbot.web은 다른 폴더(/Users/bhsong/Work/lawbot-prod)의 웹앱)`. `data/live` = data/v4 (바이트 같음), 운영 웹앱 PID 63369 그대로 |
| 개발 웹앱 → 운영 모델 서버 질문 3개 | 정상, 이전과 같음 | 3개 정상, 완료 17.6·19.1·26.2초, 답변·인용 조문 점수가 지난 기록과 글자까지 같음 |
