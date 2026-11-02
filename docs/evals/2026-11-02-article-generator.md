# 조문 CSV 생성기 확인 (2026-11-02)

계획: [archives/article-generator.md](../archives/article-generator.md). 원문: Legalize KR `8c8917412e48`, 기준일 2026-10-20.

| 항목 | 기대 | 실제 |
| --- | --- | --- |
| 호환판(`d2d32b7`) → 이전 두 CSV | 바이트 단위 일치 | `articles_v4.csv`·`law_articles_v4_decree.csv` 모두 일치, 두 번 실행해도 같음 (34초) |
| 통일판(`f51c7ec`) → 레포 CSV | 바이트 단위 일치 (`--check`, `-m network` 합격 테스트) | 일치 (48초) |
| 통일 전후 데이터 | 런타임 열·행 순서 그대로 | `law`·`article_id`·`title`·`text`·`deleted`·행 순서 같음. 바뀐 칸 `effective_date` 300 · `chapter` 66 · `related_ids` 2 |
| 시행 전 조문 | provenance 3절 표와 같음 | 근로기준법 제21533호 12 · 제21784호 5 · 제21472호 1, 남녀고용평등법 6, 최저임금법 3, 기간제법 1, 임금채권보장법 1 + 시행령 1(제36751호, 새로 들어온 개정) |
| `uv run pytest` | 통과 | 604 passed, 8 deselected (`test_decree`의 실험 재현 포함) · ruff · pyright 0 errors |
| `check_deploy` | FAIL 없음 | 모두 OK (매니페스트 sha256 일치) |
| 개발 웹앱 시작 | 기존 임베딩 캐시 사용 | `조문 임베딩 캐시 사용` 415·397개, 인덱스 0.1초 (본문·순서가 같다는 뜻) |
| 개발 웹앱 → 운영 모델 서버 질문 3개 | 정상 답변 | 3개 정상. 인용 근로기준법 제26조·최저임금법 제6조·남녀고용평등법 제19조, 완료 17.6·19.2·26.2초 |
