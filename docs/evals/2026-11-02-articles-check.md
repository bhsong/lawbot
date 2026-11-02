# 조문 데이터 점검 확인 (2026-11-02)

계획: [archives/monitor-article-check.md](../archives/monitor-article-check.md) (2.1 점검 명령. monitor 연결은 [자동 갱신 계획](../plans/articles-auto-update.md)으로 이어서). 원문: Legalize KR `8c8917412e48`.

| 항목 | 기대 | 실제 |
| --- | --- | --- |
| `lawbot-articles-check --asof 2026-11-02` | 같음 (종료 0) | `articles same=yes changed=0 pending=30 next=2026-11-27 new=0`. 시행 예정 10건이 provenance 3절 표와 같음 (제36751호 포함) |
| `--asof 2026-11-27` | 남녀고용평등법 제21700호 6조문 다름 (종료 1) | `same=no changed=6` — 제2·12·13·18조의3·37·39조 |
| `--asof 2026-12-08` | 3절 표의 12-08 시행분까지 다름 | 21조문: 근로기준법 10(제102조 등 4개는 삭제됨), 최저임금법 3, 남녀 6, 기간제법 1, 임금채권보장법 1 |
| 클론 없음·생성 실패 | 종료 2, 한 줄 | `articles error=legalize-kr 클론이 없습니다: …` |
| `uv run pytest` | 통과 | 623 passed, 9 deselected · 합격 테스트(`-m network`) 2개 통과 · ruff · pyright 0 errors |
| monitor 연결 (시험 구현 후 되돌림) | — | 설계대로 구현했을 때 monitor 테스트 73개 통과, 실제 클론으로 `law-monitor check`가 `이상 없음` 줄을 냄. 사용자 결정으로 되돌리고 설계만 계획 2.2절에 남김 |
| 개발 웹앱 → 운영 모델 서버 질문 3개 | 정상, 이전과 같음 | 3개 정상, 완료 17.6·19.2·26.2초. 답변·인용 조문 점수가 [생성기 확인 기록](2026-11-02-article-generator.md)의 답변과 글자까지 같음 (웹앱 코드·데이터 변경 없음) |
