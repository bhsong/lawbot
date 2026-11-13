# 고용노동부 공식 게시판 대조 검증

2026-11-13 KST. 기준 `788def5`. [작업 계획](../plans/source-hub-moel-reconcile.md)

## 실측

공식 메뉴의 공지·정책자료·입법·행정예고 게시판과 RSS 안내를 대조했다. 동일 호스트의 저작권 정책과 `robots.txt`를 확인하고, 일반 게시판 목록에서 노출된 2페이지 링크를 확인했다. 실행은 각 URL의 경로와 쿼리에 규칙을 적용했다. 목록의 제목·게시일·공식 링크를 내부 임시 DB에 기록했고, 상세나 첨부에는 요청하지 않았다. [고용노동부 안내지도](https://www.moel.go.kr/site/sitemap/sitemapList.do) · [RSS 안내](https://www.moel.go.kr/site/rss/rssList.do) · [저작권 정책](https://www.moel.go.kr/site/copyright/copyrightList.do) · [robots.txt](https://www.moel.go.kr/robots.txt)

| 채널 | 공식 게시판 | RSS / 목록 응답 | 목록 게시물 | 해당 RSS에 없는 목록 게시물 | 합계 기준선 | ON / OFF 요청 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 공지 | `/news/notice/noticeList.do` | 모두 HTTP 200 | 20 | 10 | 20 | 4 / 0 |
| 정책자료 | `/policy/policydata/list.do` | 모두 HTTP 200 | 20 | 18 | 28 | 4 / 0 |
| 입법·행정예고 | `/info/lawinfo/lawmaking/list.do` | 모두 HTTP 200 | 20 | 10 | 20 | 4 / 0 |

ON은 `robots.txt`, RSS, 목록 1·2페이지에 각 한 요청을 보냈다. 임시 DB에 최초 기록한 68건은 전부 기준선이며 신규·수정은 각 0건이다. 목록과 RSS의 차이는 그 시점에 대조한 범위에서 계산했다. 더 오래된 페이지나 실제 정정 사례는 실측하지 않았다.

## 대역 검증

- `pytest -q services/source-hub/tests/test_moel_reconcile.py services/source-hub/tests/test_moel_rss.py`에서 25건 통과. RSS와 목록의 중복 제거, 최초 기준선, 후속 신규·제목 수정, 구버전 SQLite 및 GUID 보존, 대조 주기와 OFF 요청 0건을 확인했다.
- 2페이지 쿼리 금지, 목록 구조 변경, 1페이지 반복 응답을 부분 실패로 기록하고 RSS 데이터는 보존했다.
- `pytest -q --tb=short`는 `1916 passed, 13 deselected in 149.82s`였다. 단위, 모델 대역 계약, 통합, Chromium 대역 E2E를 포함한다. Ruff, Pyright, compileall, 스키마 및 Source Hub 잠금 검사와 두 프런트엔드 typecheck·Vitest 81건·빌드를 통과했다.
- 운영 DB·수집 스케줄과 실제 모델 E2E·모델 스모크·품질 평가에는 손대지 않았다.

실행 방법은 [수집기 안내](../../services/source-hub/README.md#공식-게시판-목록-대조)를 따른다. 외부 요청 없이 아래 명령으로 재현할 수 있다.

```fish
uv --no-cache run --no-sync pytest -q services/source-hub/tests/test_moel_reconcile.py services/source-hub/tests/test_moel_rss.py
uv --no-cache run --no-sync pytest -q --tb=short
```
