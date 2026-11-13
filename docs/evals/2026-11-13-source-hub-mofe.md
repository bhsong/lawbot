# 재정경제부 공식 수집 검증

2026-11-13 KST. PR #132. [작업 계획](../plans/source-hub-mofe.md)

재정경제부는 RSS 안내에서 보도·참고, 설명·반박, 공지, 정책게시판, 최근경제동향 채널을 직접 제공한다. 확인한 공개 API 안내와 공공데이터 목록에서 이 게시물을 계속 제공하는 동일 범위의 API를 찾지 못했다. 해당 기관의 저작권 정책은 공공누리 유형별 조건과 출처 표시를 요구하고 미표시 저작물 이용 시 사전 협의를 안내한다. 이번 수집은 제목·게시일·공식 링크의 내부 기록에 한정했다. 본문·첨부·작성자·원본 응답을 저장하거나 공개하지 않았다. [공식 RSS](https://mofe.go.kr/mn/siteguide/rssService.do?menuNo=2060000) · [저작권 정책](https://mofe.go.kr/mn/siteguide/drmp.do?menuNo=2030000) · [robots.txt](https://mofe.go.kr/robots.txt)

## 실제 응답

| 수집원 | RSS / 목록 | 목록 대조 | 기준선 | ON / OFF 요청 |
| --- | --- | --- | ---: | --- |
| `mofe-rss-press` · 보도·참고 | RSS 40, 목록 20 | 2쪽, 목록 전용 0 | 40 | 4 / 0 |
| `mofe-rss-explanation` · 설명·반박 | RSS 40, 목록 20 | 2쪽, 목록 전용 0 | 40 | 4 / 0 |
| `mofe-rss-notice` · 공지 | RSS 40, 목록 20 | 2쪽, 목록 전용 0 | 40 | 4 / 0 |
| `mofe-rss-policy` · 정책게시판 | RSS 40, 목록 20 | 2쪽, 목록 전용 0 | 40 | 4 / 0 |
| `mofe-rss-economy` · 최근경제동향 | RSS 40 | 대응 목록 미연결 | 40 | 2 / 0 |
| `mofe-laws` · 법령자료실 | 목록 10 | 첫 쪽 | 10 | 2 / 0 |
| `mofe-notice` · 고시·공고·지침 | 목록 10 | 첫 쪽 | 10 | 2 / 0 |
| `mofe-tax-reform` · 입법·행정예고 | 목록 10 | 첫 쪽 | 10 | 2 / 0 |

수집 HTTP는 모두 200이었다. 임시 DB에서 소스별 230개 기준선과 OFF 요청 0건을 확인했다. 입력 RSS의 HTTP 문서 링크는 고정된 공식 HTTPS 주소로 정규화했다. 입법예고 목록에는 예고 기간만 제공돼 게시일을 비웠다. 세제개편안 본편과 문답은 보도·참고 채널의 게시물에서 발견할 수 있다. 별도 캠페인 페이지, 발간물 전용 목록과 문답 첨부는 자동 수집 상태로 전환하지 않았다. [세제개편안 공식 발표](https://mofe.go.kr/nw/nes/detailNesDtaView.do?menuNo=4010100&searchNttId1=MOSF_000000000078809)

같은 실행에서 고용노동부 세 채널의 기존 결과인 기준선 68건, RSS 응답에 없던 목록 게시물 38건과 OFF 요청 0건을 재확인했다.

## 대역 검증

- `pytest -q services/source-hub/tests/test_mofe.py`에서 공식 RSS와 목록 중복 제거, HTTP 링크의 안전한 HTTPS 정규화, 공식 목록 밖 링크 거부, 이전 관측 보존과 OFF 무요청을 검증했다.
- 기본 pytest는 `1917 passed, 13 deselected in 149.01s`였다. 단위·계약·통합·브라우저 대역 E2E를 포함한다. Ruff, Pyright, compileall, 스키마, Source Hub 잠금 검사와 두 프런트엔드 typecheck·Vitest 81건·빌드를 통과했다. 최초 제한 환경의 브라우저 대역은 루프백 바인딩 거부로 실행하지 못해 루프백이 허용된 전체 실행으로 재검증했다.
- 실제 모델 E2E·모델 스모크·품질 평가, 운영 배포와 스케줄 변경은 실행 안 함.
