# 기록: 법령 변경 수집기 PR 3 스파이크 (국민참여입법센터·국회)

- 날짜: 2026-11-02
- 목적: 계획서 [monitor-collector.md](../plans/monitor-collector.md) 7절의 PR 3 항목(국민참여입법센터 목록 API가 기존 `LAW_OC`로 되는지, 국회 API 출처)을 확인한다
- 장소: 맥미니(집 IP), `services/monitor/.env`의 `LAW_OC`
- 호출: 활용가이드 페이지 3회, 목록 API GET 4회(그중 2회는 옛 주소라 301만 받음). OC 값은 출력에서 `***`로 가렸고 응답은 세션 임시 폴더에만 두었다
- 레포 변경 없음 (이 기록과 계획서 메모만)
- 결론: **기존 `LAW_OC`로는 안 된다.** 국민참여입법센터와 국회 모두 회원가입 후 별도 키가 필요하다. 2026-11-02 가입이 잘 되지 않아 PR 3은 키를 받은 뒤로 미룬다

## 국민참여입법센터 Open API

활용가이드: `https://opinion.lawmaking.go.kr/api/operationGuide` (상세는 `POST /api/apiGuideInfo`, `type=4` 입법예고, `type=5` 행정예고)

| API | 목록 주소 | 주요 요청변수 (가이드) |
| --- | --- | --- |
| 입법예고 목록 (4) | `https://opinion.lawmaking.go.kr/rest/ogLmPp.{xml\|html}` | `OC`, `lsClsCd`(AA0101 법률 …), `cptOfiOrgCd`(소관부처 코드, 예: 1492000 고용노동부), `diff`(0 진행, 1 종료), `pntcNo`·`pntcNo2`, `stYdFmt`·`edYdFmt`(예고일자), `lsNm`(UTF-8) |
| 행정예고 목록 (5) | `https://opinion.lawmaking.go.kr/rest/ptcpAdmPp.{xml\|html}` | `OC`, `lsClsCd`(BA0103 등), `asndOfiNm`(소관부처명), `admRulNm`, `closing` |

- 가이드의 `OC` 설명: "정보공개 서비스 신청 계정 ID(@ 앞부분)". 국가법령정보 공동활용의 OC가 아니라 **국민참여입법센터에서 정보공개(OPEN API) 신청을 한 계정**이다. 신청은 로그인(회원가입) 후 `/api/applyApi`.
- 가이드 예시의 `www.lawmaking.go.kr/rest/...`는 `opinion.lawmaking.go.kr/rest/...`로 301 이동한다.
- 목록 조회 결과에 기간 표기 외의 상세 필드(게시물 ID, 의견제출 기한)는 키가 없어 확인하지 못했다.

| # | 호출 | 결과 |
| --- | --- | --- |
| 1 | `www.lawmaking.go.kr/rest/ogLmPp.xml?OC=***&diff=0` | 301 → `opinion.lawmaking.go.kr` |
| 2 | `www.lawmaking.go.kr/rest/ptcpAdmPp.xml?OC=***&diff=0` | 301 → `opinion.lawmaking.go.kr` |
| 3 | `opinion.lawmaking.go.kr/rest/ogLmPp.xml?OC=***&diff=0` | HTTP 200, 본문 `<result><retMsg>401</retMsg></result>` |
| 4 | `opinion.lawmaking.go.kr/rest/ptcpAdmPp.xml?OC=***&diff=0` | HTTP 200, 본문 `<result><retMsg>401</retMsg></result>` |

인증 실패가 HTTP 상태가 아니라 본문 `retMsg`로 온다. 구현 때 HTTP 200이어도 `retMsg`를 확인해 실패로 처리해야 한다.

## 국회

- 원 계획서는 공공데이터포털 국회사무처 "진행중 입법예고"(data.go.kr 15126069, 서비스키)를 가정했지만, 사용자 확인(2026-11-02) 결과 국회 자체의 **열린국회정보 Open API**(`open.assembly.go.kr`)를 쓴다.
- 열린국회정보는 회원가입 후 마이페이지에서 인증키를 받고, 요청변수 `KEY`·`Type`·`pIndex`·`pSize`를 쓴다(제3자 튜토리얼 기준, 키 오류는 `ERROR-290`). 진행중 입법예고 API의 정확한 주소와 응답 필드는 확인하지 못했다. 키를 받을 때 확인한다.

## 다음에 할 것 (키를 받은 뒤 PR 3)

1. 국민참여입법센터 회원가입 → 정보공개(OPEN API) 신청 → 받은 계정 ID를 `services/monitor/.env`에 새 변수로 넣는다 (예: `LAW_LAWMAKING_OC`).
2. 열린국회정보 회원가입 → 인증키 → `services/monitor/.env`에 새 변수로 넣는다 (예: `LAW_ASSEMBLY_KEY`). `.env`는 `LAW_*`를 이미 허용하므로 코드 변경 없이 넣을 수 있다. `DATA_GO_KR_KEY`와 `.env.example`의 설명은 PR 3에서 정리한다.
3. 키로 목록 1회씩 다시 호출해 게시물 ID·제목·의견제출 기한 필드를 확인하고 응답 견본을 `services/monitor/tests/fixtures/`에 둔 뒤 TDD로 구현한다.
