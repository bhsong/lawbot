# 11월 14일 법제처 응답·국회 공개 상태·비활성 수집원 점검

2026-11-14 KST. 운영 `v5.8.1` (`16c2533`). [06:30 정기 실행 기록](2026-11-14-source-hub-first-operational-run.md)

## 아침 요약과 법제처

- `services/monitor/data/summaries/2026-11-14.md`에 06:32:55 KST 아침 보고서가 있다. 국회 이벤트 38건은 규칙으로 출력했고, 모델 요약 대상인 법령·행정규칙·별표서식 이벤트는 각각 0건이다. 헤더에 모델 설정 `qwen3.8-27b-4bit`이 표시되며 실제 호출은 0회다. 관리형 `daily-180f539c4aa745d0bab9db4136c11351.json`의 `entries`는 비어 있다. 당일 Qwen 출력이 없어 생성 품질을 확인할 수 없다.
- 06:30:07 KST에 법령 Git 비교 과정에서 기간제 및 단시간근로자 보호 등에 관한 법률(`MST=285375`)의 본문 보강을 시도했다. 법제처 공식 `DRF/lawService.do`에 `target=law`, `type=JSON`으로 조회했고 JSON 파싱이 실패했다. 당시 응답의 본문·헤더·최종 URL은 남지 않았다. 해당 수집은 실패로 기록됐고 캘린더 법령 가져오기는 성공 0건·실패 1건이었다. [법제처 본문 API 명세](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwInfoGuide)
- 후속 읽기 전용 확인에서 동일 MST의 공개 상세와 `www.law.go.kr/robots.txt` 모두 점검 페이지로 HTTP 302 이동을 반환했다. 법제처는 11월 14일 국가법령정보센터 개편을 발표했다. 개편 점검 응답이 원인일 가능성이 높으며, 06:30 본문 API 원응답은 확인할 수 없다. 인증된 본문 API를 재호출하지 않았다. [법제처 점검 화면](https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285375) · [법제처 개편 발표](https://www.korea.kr/briefing/pressReleaseView.do?newsId=156789202)

## 국회 법률안과 입법예고

- 운영 캘린더 DB를 읽기 전용으로 확인했다. 당일 법률안은 최초 감지 5건과 기존 의안 상태 변경 21건을 정상 저장했다. 현재 발의·처리 일정 공개 정책은 수동 검토이므로 26건 모두 대기다. 공개용 뷰의 법률안은 0건이다. 승인 단계에서는 의안번호·공식 상세 링크·처리 상태와 발의일·의결일의 근거를 확인한다. [캘린더 국회 공개 절차](../../services/law-calendar/README.md#국회-이벤트-공개-검토)
- 입법예고 12건 중 11건은 국회 상세와 의안번호·예고기간이 일치해 자동 공개됐다. 남은 1건은 근로기준법 일부개정법률안 `2222635`다. 시작일 11월 13일과 종료일 11월 22일은 저장돼 있다. 06:30:59 KST에 공식 상세를 대조했으나 공개하지 못했다. 실행 로그에는 상세 확인 오류 건수 1건만 남아 있어 HTTP 응답 실패인지 상세 문서 형식 또는 값의 불일치인지 구별할 수 없다. 자동 공개에는 PDF 다운로드나 비교 이미지가 필요하지 않다. [국회 공식 상세](https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=PRC_552DA4F1C80A4C278AFA65F9E6867B)

## 비활성 수집원 18개

운영 YAML 기본값과 SQLite 설정을 대조했다. 총 42개 중 24개 활성, 18개 비활성이다. 비활성 중 8개는 `not_tested`, 10개는 `manual_review`다. 모두 해당 수집원 ID의 실행 연결과 출처별 자동 수집 검증이 남아 있다. 공식 안내와 공개 페이지는 열람했고 등록되지 않은 수집 작업은 실행하지 않았다.

| 수집원 ID | 공식 경로 및 확인 결과 |
| --- | --- |
| `lawgo-forms` | 법제처 API 목록에 법령 별표·서식 조회가 있다. 기존 본문 API에도 별표·서식이 포함되므로 신규 목록이 추가할 범위를 확인해야 한다. [법제처 API 목록](https://open.law.go.kr/LSO/openApi/guideList.do) |
| `lawgo-history` | 법제처 법령 변경이력 API가 문서화돼 있다. 기존 Git 변경 추적과 ERP에 필요한 공포·개정 범위를 대조해야 한다. [변경이력 API](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsChgListGuide) |
| `moel-rules` | 훈령·예규·고시 공식 목록을 확인했다. 기존 행정규칙 수집과의 중복 및 게시판이 추가할 범위를 확인해야 한다. [공식 목록](https://www.moel.go.kr/info/lawinfo/instruction/list.do) |
| `moel-interpretations` | 행정해석·질의회시 공식 목록을 확인했다. 자료 이용 범위와 목록 어댑터 검증이 남았다. [공식 목록](https://www.moel.go.kr/info/publicdata/qnrinfo/qnrInfoList.do) |
| `moel-policy` | 같은 정책자료실 목록을 활성 `moel-rss-policy`가 주기적으로 대조한다. [공식 목록](https://www.moel.go.kr/policy/policydata/list.do) · [기존 검증](2026-11-13-source-hub-moel-reconcile.md) |
| `moel-publications` | 주요발간자료 공식 목록을 확인했다. 업무편람의 실제 수록 범위와 자료별 이용 조건 확인이 남았다. [공식 목록](https://www.moel.go.kr/info/publicdata/majorpublish/majorPublishList.do) |
| `moel-press` | 보도자료 공식 목록을 확인했다. 해당 목록의 어댑터 검증이 남았다. [공식 목록](https://www.moel.go.kr/news/enews/report/enewsList.do) |
| `mofe-rss` | 공식 RSS 안내 페이지에 있는 개별 RSS 5개는 이미 활성이다. [공식 RSS 안내](https://mofe.go.kr/mn/siteguide/rssService.do?menuNo=2060000) |
| `mofe-qa` | 활성 보도·참고자료 채널의 세제개편 발표 상세에 문답 PDF·HWPX 링크가 있다. 별도 문답 목록이나 피드 및 첨부 처리 조건은 확인하지 못했다. [공식 발표 상세](https://mofe.go.kr/nw/nes/detailNesDtaView.do?menuNo=4010100&searchNttId1=MOSF_000000000078809) |
| `mofe-press` | 보도·참고자료와 설명·반박자료는 개별 RSS가 활성이다. [기존 검증](2026-11-13-source-hub-mofe.md) |
| `mofe-publications` | 공식 발간물 메뉴를 확인했다. 세법해석 범위, 기존 최근경제동향 RSS와의 중복, 게시판 어댑터 검증이 남았다. [공식 발간물](https://mofe.go.kr/pl/policydta/pblictn.do?menuNo=5020300) |
| `nts-forms` | 공식 세무서식 검색 페이지는 법제처 제공 서식을 안내한다. 국가법령정보센터 목록과 범위를 대조해야 한다. [공식 세무서식](https://www.nts.go.kr/nts/ad/nf/nltFormatApiList.do?mi=40178) |
| `nts-calendar` | 공식 월별 세무일정을 확인했다. 게시물 발견과 별개로 일정과 기한의 변경을 표현할 수 있는 파서 검증이 남았다. [공식 세무일정](https://www.nts.go.kr/nts/ad/taxSchdul/selectList.do?mi=135747) |
| `nts-payment-reports` | 공식 지급명세서 제출 안내 페이지를 확인했다. 안내 페이지 변경의 검출 방식과 관련 자료실 범위를 확인해야 한다. [공식 제출 안내](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=8631&mi=12242) |
| `hometax-media` | 국세청 공개 통합검색에 ‘2026년 귀속 지급명세서 전산매체 제출요령’ 제목과 날짜가 노출된다. 전체 신규 게시물을 제공하는 자동 수집 경로와 이용 조건은 확인되지 않았다. [국세청 통합검색](https://www.nts.go.kr/search/search.jsp) |
| `hometax-specs` | 홈택스 공식 자료실 메뉴가 있다. 전자신고 파일설명서의 공개 자동 수집 경로는 확인되지 않았다. [공식 자료실 경로 안내](https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350699) |
| `hometax-developers` | 국세청이 게시한 개발자 설명회 자료는 활성 국세청 수집원에서 찾을 수 있다. 홈택스의 별도 공개 자동 수집 경로는 확인되지 않았다. [기존 검증](2026-11-13-source-hub-nts-hometax.md) |
| `hometax-errata` | 홈택스 공식 자료실 메뉴가 있다. 정오표·변경공지를 별도로 열거하는 공개 자동 수집 경로는 확인되지 않았다. [공식 자료실 경로 안내](https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350699) |

고용노동부·재정경제부·국세청·홈택스의 `robots.txt`는 HTTP 200이었다. 위에서 확인한 고용노동부·재정경제부 목록과 국세청 3개 주소는 명시 차단 경로에 해당하지 않는다. 홈택스는 `/search/`를 차단하고 있다. 이번 확인은 자동 수집·저장·재배포 범위의 승인으로 기록하지 않는다. [고용노동부](https://www.moel.go.kr/robots.txt) · [재정경제부](https://mofe.go.kr/robots.txt) · [국세청](https://www.nts.go.kr/robots.txt) · [홈택스](https://www.hometax.go.kr/robots.txt)
