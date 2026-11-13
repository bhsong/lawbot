# 고용노동부 공식 RSS 수집 검증

2026-11-13 KST. 기준 `b14d666`. [작업 계획](../plans/source-hub-moel-rss.md)

## 공식 경로와 실제 응답

고용노동부 RSS 안내에서 세 채널 주소를 확인했다. 실제 `LawbotSourceHub/1.0` 요청에서 `robots.txt`는 HTTP 200과 `text/plain`으로 응답했다. 세 피드 URL은 해당 사용자 에이전트의 제한 경로에 해당하지 않았다. 저작권 정책의 공공누리 표시와 별도 협의 조건을 확인해 내부 RSS 메타데이터와 공식 게시물 링크까지만 저장했다. 상세 페이지와 첨부는 요청하지 않았다. [RSS 안내](https://www.moel.go.kr/site/rss/rssList.do) · [robots.txt](https://www.moel.go.kr/robots.txt) · [저작권 정책](https://www.moel.go.kr/site/copyright/copyrightList.do)

| 수집원 | 공식 RSS 경로 | HTTP / 형식 | 저장 | 게시일 / GUID | ON 요청 | OFF 요청 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `moel-rss-notice` | `/rss/notice.do` | 200 / `text/xml` | 10 | 10 / 0 | 2 | 0 |
| `moel-rss-policy` | `/rss/policy.do` | 200 / `text/xml` | 10 | 10 / 0 | 2 | 0 |
| `moel-rss-lawinfo` | `/rss/lawinfo.do` | 200 / `text/xml` | 10 | 10 / 0 | 2 | 0 |

수집 실행에서 채널별로 `robots.txt` 1회와 RSS 1회를 요청했다. 처음 받은 30건은 모두 기준선으로 기록했고 신규로 분류한 건은 0이다. 세 피드 모두 게시일은 `dc:date`, 문서 식별자는 공식 게시물 링크의 `bbs_seq`에서 확인했다. 임시 DB에서 다시 OFF로 전환한 뒤 저장한 자료가 유지되는지 확인했다. 실측은 게시판 전체 범위와 신규 게시물의 실제 발생을 포함하지 않는다.

## 대역 검증

- `pytest -q services/source-hub/tests/test_moel_rss.py`에서 13건을 통과했다. 출처·기관·그룹 OFF 요청 0건, 간격 전 재실행 요청 0건, OFF 뒤 재수집, 신규 1건·수정 1건, 동일 응답 재수집 0건, 금지·확인 실패 시 피드 미요청, 공식 목록 밖 경로 차단, 리디렉션 거부, XML 구조 이상과 DB 분리, 수집 기록 쓰기 실패 뒤 재조회 없는 복구를 확인했다.
- `pytest -q --tb=short` 기본 전체 결과는 `1904 passed, 13 deselected in 151.09s`다. 단위, 모델 대역 계약, 통합, Chromium 대역 E2E를 포함한다.
- Ruff, 포맷 검사, Pyright, compileall, 캘린더 스키마와 잠금 파일 검사를 통과했다. 챗봇 55건과 캘린더 26건의 Vitest, 양쪽 typecheck와 빌드를 통과했다.
- 운영 스케줄, 조문 갱신, 모델 추론, 실제 발송은 실행하지 않았다. 게시판 대조·다른 기관·공개 API는 후속 범위다.

재현 명령은 [수집기 실행 안내](../../services/source-hub/README.md#고용노동부-rss-수동-수집)를 따른다. 임시 경로에 설정 DB와 자료 DB를 분리하고 채널별 `collect`, `documents`, `runs`를 실행한다. 외부 요청 없이 검증하려면 아래 명령을 사용한다.

```fish
uv pip install --no-deps -e services/source-hub
uv --no-cache run --no-sync pytest -q services/source-hub/tests/test_moel_rss.py
uv --no-cache run --no-sync pytest -q --tb=short
```
