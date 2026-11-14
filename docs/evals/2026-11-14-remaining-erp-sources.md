# 비활성 ERP 수집원 중복 제거 및 공식 경로 점검

2026-11-14 KST. 운영 설정 기준 활성 24개, 비활성 18개. 기존 수집기의 소스 매핑, 운영 상태 DB와 공식 기관 안내를 대조했다. [비활성 등록부 전체 기록](2026-11-14-lawgo-assembly-inactive-sources.md)

## 이미 수집 중인 3개

- `moel-policy`와 같은 정책자료실을 `moel-rss-policy`가 RSS 및 공식 게시판으로 수집·대조한다. [기존 검증](2026-11-13-source-hub-moel-reconcile.md)
- `mofe-rss`는 안내 페이지의 5개 개별 채널을 가리키고, `mofe-press`는 그중 보도·참고자료 및 설명·반박자료에 해당한다. 개별 채널 5개가 이미 켜져 있다. [공식 RSS 안내](https://mofe.go.kr/mn/siteguide/rssService.do?menuNo=2060000) · [기존 검증](2026-11-13-source-hub-mofe.md)

## 나머지 15개

| 수집원 | 확인 결과와 남은 작업 |
| --- | --- |
| `lawgo-forms` | 공식 목록 API가 문서화돼 있다. 기존 법령 본문 API도 추적 중인 법령의 별표·서식을 제공한다. ERP가 필요로 하는 추가 범위가 확인되면 동일 서식의 중복을 막는 방식으로 연결할 수 있다. [공식 API 목록](https://open.law.go.kr/LSO/openApi/guideList.do) · [본문 API 활용 안내](https://open.law.go.kr/LSO/openApi/openApiManual.do) |
| `lawgo-history` | 공식 법령 변경이력 목록 API가 문서화돼 있다. 기존 Git 추적 범위에 같은 공포·개정 이력이 있는지 대조가 필요하다. 두 법제처 후보 모두 확인 시점에 공식 호스트가 점검 화면으로 이동했다. [공식 변경이력 API](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsChgListGuide) |
| `moel-rules` | 공식 목록에 고시·훈령 번호, 제목, 등록일, 상세 링크가 있다. 현재 운영 행정규칙 등록 36건에서 최신 목록 표본인 `사업장 위험성평가에 관한 지침`, `노동감독관 수사규칙` 등의 제목을 찾지 못했다. 별도의 게시물 발견 범위가 있다. 동일 번호가 기존 법령 수집에 들어오면 중복을 제거하고 게시일과 시행일을 구분해야 한다. [공식 목록](https://www.moel.go.kr/info/lawinfo/instruction/list.do) |
| `moel-interpretations` | 공식 질의회시 목록에 퇴직연금·퇴직급여 관련 제목, 날짜, 상세 링크가 있다. 확인한 첫 화면의 가장 최근 표시 게시물은 2022-08-03으로, 과거 참고자료 성격이 강하다. 제목·날짜·공식 링크와 본문 이용 범위를 각각 확인해야 한다. [공식 목록](https://www.moel.go.kr/info/publicdata/qnrinfo/qnrInfoList.do) |
| `moel-publications` | 주요발간자료는 독립 목록이다. 첫 화면에 고용영향평가·일생활 균형 등 보고서가 확인됐다. 급여·보험 업무편람의 수록 범위와 정책자료 RSS에 교차 게시된 문서를 대조해야 한다. [공식 목록](https://www.moel.go.kr/info/publicdata/majorpublish/majorPublishList.do) |
| `moel-press` | 11월 13일자 노동·근로시간 보도자료가 있는 독립 목록을 확인했다. 활성 고용노동부 RSS 3개에는 보도자료 채널이 없다. 목록에서 제목·날짜·공식 링크를 검증하는 어댑터가 필요하다. [공식 보도자료](https://www.moel.go.kr/news/enews/report/enewsList.do) · [공식 RSS 안내](https://www.moel.go.kr/site/rss/rssList.do) |
| `mofe-qa` | 세제개편 발표 상세에 문답자료 PDF·HWPX의 공식 링크가 있다. 해당 발표를 발견하는 활성 RSS가 있으므로 별도 소스보다는 기존 발표에 자료 링크를 보강하는 방식이 맞다. 별도 문답 피드는 확인되지 않았다. [공식 세제개편 발표](https://mofe.go.kr/nw/nes/detailNesDtaView.do?menuNo=4010100&searchNttId1=MOSF_000000000078809) |
| `mofe-publications` | `최근경제동향`은 활성 RSS와 중복된다. `간추린 개정세법`은 별도 게시판(`PUB05`)이며 2025년판을 확인했다. 이 목록을 별도 수집 후보로 볼 수 있다. 세법해석 신청 안내와 발간물은 다른 경로다. [개정세법 발간 목록](https://mofe.go.kr/pl/policydta/pblictn/ComtBalbbsList.do?bbsId=MOSFBBS_000000000004&menuNo=5020300&policyCategory=PUB05) · [세법해석 신청 안내](https://mofe.go.kr/cv/taxlaw/taxlawGuidance.do?menuNo=8120100) |
| `nts-forms` | 공식 세무서식 페이지가 법제처 제공 서식을 안내한다. 같은 법령·서식은 법제처와 중복으로 등록할 필요가 없다. 국세청 화면의 분류에 추가 업무 가치가 있는지는 별도로 확인해야 한다. [공식 세무서식](https://www.nts.go.kr/nts/ad/nf/nltFormatApiList.do?mi=40178) |
| `nts-calendar` | 월별 공식 표에서 지급명세서 제출일과 대상 지급월을 함께 확인했다. 게시일을 기준으로 하는 자료실 파서와 다른 일정 파서가 필요하다. 조사 범위에서 같은 일정을 제공하는 공식 API를 찾지 못했다. [공식 월별 일정](https://www.nts.go.kr/nts/ad/taxSchdul/selectList.do?mi=135747) · [국세청 Open API 목록](https://www.nts.go.kr/nts/pdoc/openApi/list.do?mi=6409) |
| `nts-payment-reports` | 소득 유형별 제출 시기와 방법을 제공하는 상시 안내 페이지다. 일정과 겹치는 제출기한을 중복 알림으로 만들지 않도록 대상과 제출 방식의 변경을 구별해야 한다. [공식 제출 안내](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=12242&cntntsId=8631) |
| `hometax-media` | 국세청 통합검색에 2026-11-05 등록 전산매체 제출요령이 노출된다. 활성 국세청 프로그램 게시판의 과거 자료와 일부 유형이 겹치지만 홈택스 신규 게시물 전체 범위는 확인되지 않는다. [국세청 통합검색](https://www.nts.go.kr/search/search.jsp) · [국세청 프로그램](https://www.nts.go.kr/nts/na/ntt/selectNttList.do?bbsId=1099&mi=2544) |
| `hometax-specs` | 공식 자료실 이용 경로는 확인됐다. 전자신고 파일설명서를 계속 나열하는 공개 공식 API·RSS를 찾지 못했다. [자료실 안내](https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350699) |
| `hometax-developers` | 국세청이 별도로 게시한 개발자 설명회는 활성 채널에 있다. 홈택스 고유 공지를 나열하는 공식 자동 수집 경로는 확인되지 않았다. [기존 검증](2026-11-13-source-hub-nts-hometax.md) |
| `hometax-errata` | 공식 자료실 안내에서 정오표·변경공지의 별도 목록이나 공식 피드를 확인하지 못했다. [자료실 안내](https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350699) |

08:25 KST에 고용노동부·재정경제부·국세청·홈택스의 `robots.txt`는 HTTP 200을 반환했다. 확인한 목록 주소는 해당 호스트의 명시 금지 대상에 없었고 홈택스 `/search/`는 금지였다. `www.law.go.kr/robots.txt`는 점검 페이지로 HTTP 302 이동했다. 기관별 저작권 정책은 자료별 공공누리와 이용 범위 확인을 요구한다. 공식 목록 열람, 경로 및 정책 확인을 수행했다. 신규 어댑터를 통한 시험 수집, 첨부 다운로드, 운영 설정 변경, 활성화는 각 0회다. [고용노동부 저작권 정책](https://www.moel.go.kr/site/copyright/copyrightList.do) · [재정경제부 저작권 정책](https://mofe.go.kr/mn/siteguide/drmp.do?menuNo=2030000) · [국세청 저작권 정책](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=6791&cntntsId=8169) · [홈택스 robots.txt](https://www.hometax.go.kr/robots.txt)
