# 국세청·홈택스 공식 자료 연동 검증

2026-11-13 KST. PR #132의 공통 실행기를 기준으로 검증했다. [작업 계획](../plans/source-hub-nts-hometax.md)

국세청 공식 메뉴, 오픈 API 안내, `robots.txt`, 저작권 정책을 확인했다. 확인 범위에서 아래 게시판을 제공하는 API 명세를 찾지 못했다. 국세청은 공공누리 유형과 출처 표시를 확인하고 미표시 저작물은 사전에 협의하도록 안내한다. 수집 범위는 내부 제목·게시일·공식 링크다. 자료실 목록에서 게시일을 제공하지 않은 항목은 비워 뒀다. 연말정산 종합 안내에서는 자료명과 안내 페이지 링크만 기록했다. [국세청 공식 메뉴](https://www.nts.go.kr/nts/main.do) · [오픈 API 안내](https://www.nts.go.kr/nts/pdoc/openApi/list.do?mi=6409) · [저작권 정책](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=6791&cntntsId=8169) · [robots.txt](https://www.nts.go.kr/robots.txt)

| 소스 | HTTP | 기준선 | 게시일 확인 | ON / OFF 요청 |
| --- | --- | ---: | ---: | --- |
| `nts-notice` | 200 | 10 | 10 | 2 / 0 |
| `nts-rules` | 200 | 10 | 10 | 2 / 0 |
| `nts-tax-references` | 200 | 10 | 0 | 2 / 0 |
| `nts-withholding` | 200 | 10 | 0 | 2 / 0 |
| `nts-tools` | 200 | 10 | 10 | 2 / 0 |
| `nts-tax-02` | 200 | 1 | 0 | 2 / 0 |
| `nts-tax-amendments` | 200 | 1 | 0 | 2 / 0 |
| `nts-developer-notices` | 200 | 1 | 0 | 2 / 0 |

임시 DB에 총 53건을 저장했다. ON 요청은 출처별 정책과 공식 페이지 각 1건이다. 게시물 상세·본문·첨부 요청은 0건이었다. 세무서식·세무일정·지급명세서 제출 안내는 공식 주소를 등록하고 자동 수집을 비활성으로 뒀다. [연말정산 종합 안내](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=2304&cntntsId=238938)

홈택스 자료실의 자동 수집 허가와 문서화된 공개 게시물 조회 경로를 확인하지 못해 네 수집원을 수동 확인 상태로 뒀다. 국세청이 직접 게시한 개발자 설명회·프로그램 자료는 국세청 출처로 기록한다. 실제 홈택스 게시물은 운영자가 확인한 경우에만 `record-notice`로 내부에 기록할 수 있다. 동일 ID로 다시 등록하면 신규 0건이며 홈택스 HTTP 요청은 0건이다. [홈택스](https://www.hometax.go.kr/) · [홈택스 이용에 관한 규정](https://www.law.go.kr/admRulLsInfoP.do?admRulId=34730&efYd=0&joNo=004400)

- `pytest -q services/source-hub/tests/test_nts.py` 3건 통과. 국세청 날짜·공식 링크·OFF·재수집, TAX-02 안내 주소, 홈택스 수동 등록의 중복 방지와 공식 검색 링크를 확인했다.
- 기본 pytest는 `1920 passed, 13 deselected in 150.00s`였다. 단위·계약·통합·브라우저 대역 E2E를 포함한다. Ruff, Pyright, compileall, 캘린더 스키마, Source Hub 잠금 검사를 통과했다. 프런트엔드는 상위 PR에서 typecheck·Vitest 81건·빌드를 통과했고 이 브랜치의 변경 파일에 포함되지 않는다.
- 운영·실발송·실제 모델 E2E·모델 스모크·품질 평가는 실행 안 함.
