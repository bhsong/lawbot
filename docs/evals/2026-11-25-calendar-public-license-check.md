# 캘린더 전면 공개 전 데이터·이용 조건·침입 경로 재확인 (2026-11-25)

법령 캘린더를 Cloudflare Tunnel + Access(본인만)에서 비밀번호 없는 Cloudflare Pages 정적 사이트로 옮기기 전에 확인했다. 공개 범위는 현재 캘린더 화면이 보여 주는 데이터와 같다. 법률 자문 결과가 아니다. [계획](../archives/calendar-pages-static.md) · [11-19 확인](2026-11-19-external-access-copyright-check.md)

## 결론

- 현재 공개 화면 데이터에는 공개하면 안 되는 내부 정보(검토자, 가져오기 기록, 원천 스냅숏, 이메일, 로컬 경로, IP)가 없다. 공개 API는 DB 공개 뷰 3개(`public_calendar_events` 70건, `public_changes` 70건, `public_holds` 0건)와 ERP 공개 스냅숏만 읽는다.
- 이용 조건 위반은 없다. 다만 공개 전에 아래 네 가지를 해야 한다.
  1. 화면에 "법령 정보 출처: 법제처 국가법령정보센터"를 글로 명기한다. 법제처 Open API 가입 주의사항은 출처 미표기를 형사처벌 대상 행위로 적는다. 지금은 "공식 출처" 링크만 있다.
  2. Pretendard(OFL-1.1) 저작권 고지와 라이선스 전문, React·React DOM·scheduler(MIT) 고지를 함께 배포한다.
  3. 정적 내보내기에서 외부 링크를 `https`와 허용 공식 도메인으로 다시 검사한다.
  4. 새 배포마다 이전 배포를 지운다. Pages는 배포마다 고유 해시 주소를 만들고, 30일이 지나도 최근 100개는 지우지 않는다. 철회한 항목이 예전 해시 주소에 남는다.
- 기관 소식(고용노동부·재정경제부·국세청)은 제목·게시일·공식 링크만 계속 보여 준다. 샘플 게시물에는 개별 공공누리 표시가 없다. 그래서 본문·첨부를 가져오는 것은 기관 정책상 사전 협의 대상이다.
- 정적 Pages(Functions 없음)에서는 맥 미니로 들어오는 네트워크 경로가 없다.

## 이전 보고에서 넘긴 항목의 재확인

| 이전 판단 | 재확인 결과 |
| --- | --- |
| 재정경제부·국세청 저작권 정책은 11-19 기록에 기댐 | 두 정책 원문을 오늘 전문으로 다시 읽었다. 11-19 요지와 같다. 공공누리 표시 자료는 유형별 조건 안에서 자유 이용하고, 출처(기관명·누리집 URL)를 반드시 표시한다. 미표시 자료는 사전 협의한다. 링크·제목 게시나 자동 수집을 금지하는 조항은 없다. 11-19에 요약으로만 본 국세청 뒷부분도 원문으로 확인했다(공공누리 유형 설명뿐). |
| 고용노동부 정책 | 다시 읽었고 변경 없음. 다른 사이트에서 직접 링크할 때 저작권 정책도 함께 링크해 달라는 요청은 화면에 이미 반영돼 있다. |
| 세 기관 모두 "제목 목록 게시 허락" 명시 문구 없음 | 여전히 없다. 샘플 13개 게시물(고용노동부 3, 재정경제부 4, 국세청 6)에서 개별 공공누리 표시를 찾았다. 재정경제부 페이지 하단의 공공누리 로고는 사이트 공통 표시이고 유형이 없다. 나머지 두 기관 페이지에는 표시가 없다. 따라서 현재 범위(제목·게시일·공식 링크)를 유지한다. 근거는 금지 조항이 없다는 점, 짧은 제목은 일반적으로 저작물로 보호되지 않는다는 점, 정상 게시물에 대한 링크라는 점이다. 국세청 세무일정은 기한 날짜·세목 제목·대상 기간이라는 사실 정보다. |
| 국가법령정보센터 Open API 이용약관 미확인, "우리는 API를 직접 쓰지 않는다" | **이전 보고가 틀렸다.** 운영 감시 서비스(`services/monitor`)가 인증키로 법제처 Open API(`DRF/lawService.do`)를 호출한다. 받은 개정문·부칙·시행일 정보는 캘린더 시행일 판정에 쓰인다. 공개 화면에 나가는 것은 판정한 날짜뿐이고 조문 원문은 legalize-kr에서 온다. legalize-kr·admrule-kr도 원천이 같은 Open API다. 공동활용 가입 주의사항(원문 오늘 확인)의 요지는 다음과 같다. 개인·기관 홈페이지에서 자유롭게 검색·활용·연결할 수 있다. 위조·변조해 제공하거나, 출처를 표기하지 않거나, 법제처가 아닌 기관을 출처로 적는 행위는 형사처벌 대상일 수 있다. 짧은 시간의 과도한 호출은 제한될 수 있다. 보급 수단이 바뀌는지는 이용 기관이 수시로 확인해야 한다. 법적효력·저작권 안내는 영리 목적을 포함한 자유 활용을 보장하고, 법령정보는 법적 효력이 없는 참고자료라고 적는다. → 출처 명기를 필수 작업으로 넣었다. |
| Pages Git 연동에서 포크 PR 빌드 여부 미확인 | Pages 알려진 문제 문서에 "포크 저장소의 커밋·PR은 미리보기를 만들지 않는다"고 적혀 있다. 계획은 Git 연동 대신 Direct Upload를 쓴다. Direct Upload 프로젝트는 나중에 Git 연동으로 바꿀 수 없다. 레포 PR이 배포를 만들 경로가 없다. |
| 정적 Pages는 Functions가 없다 | 조건이 있다. `wrangler pages deploy`는 명령을 실행한 위치에 `functions` 폴더가 있으면 함께 올린다. 출력 폴더의 `_worker.js`도 Functions로 동작한다. → 빈 스테이징 폴더에서 배포하고 두 파일이 없는지 검사한다. |
| 철회 시 재배포하면 사라진다 | 일부만 맞다. 운영 주소는 새 배포로 바뀐다. 이전 배포는 고유 해시 주소로 계속 열린다. 미리보기 Access 정책이 보호한다고 문서에 적힌 것은 미리보기 주소뿐이다. → 배포 뒤 이전 배포를 지우는 단계를 넣었다. |
| 글꼴·라이브러리 라이선스 | Pretendard 1.3.9는 OFL-1.1이다. npm 패키지의 `dist/LICENSE.txt`에는 Pretendard의 저작권·예약 글꼴 이름 고지와 라이선스 전문이 있다. Source·Inter·M PLUS 1의 고지는 원 저장소의 현재 LICENSE에 있다. 배포하는 subset 파일은 원작자가 만든 그대로이며 이름만 해시가 붙는다. 런타임 의존성은 `react`·`react-dom`·`scheduler`(MIT)와 `pretendard`뿐이다. |
| legalize-kr·admrule-kr 메타데이터 MIT | 두 README를 오늘 다시 읽었다. "원문은 공공저작물, 저장소 구조·메타데이터는 MIT"로 같다. GitHub에서 인식한 LICENSE 파일은 없다. 고지는 README 라이선스 절 링크로 한다. |

## 확인한 공식 문서

- 고용노동부 [저작권정책](https://www.moel.go.kr/site/copyright/copyrightList.do) · 재정경제부 [저작권정책](https://mofe.go.kr/mn/siteguide/drmp.do?menuNo=2030000) · 국세청 [저작권정책](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=6791&cntntsId=8169)
- 국가법령정보센터 [법적효력 및 저작권](https://www.law.go.kr/LSW/legalCopyright.do?menuId=803&subMenuId=79) · 국가법령정보 공동활용 [이용안내](https://open.law.go.kr/LSO/information/service.do) · [사용자 가입 주의사항](https://open.law.go.kr/LSO/usrJoin.do)
- [legalize-kr](https://github.com/legalize-kr/legalize-kr#라이선스) · [admrule-kr](https://github.com/legalize-kr/admrule-kr#라이선스) · [Pretendard LICENSE](https://github.com/orioncactus/pretendard/blob/main/LICENSE)
- Cloudflare Pages: [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/) · [CI에서 Direct Upload(토큰 권한)](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/) · [미리보기 배포](https://developers.cloudflare.com/pages/configuration/preview-deployments/) · [롤백·배포 보관](https://developers.cloudflare.com/pages/configuration/rollbacks/) · [헤더](https://developers.cloudflare.com/pages/configuration/headers/) · [알려진 문제](https://developers.cloudflare.com/pages/platform/known-issues/) · [wrangler pages 명령](https://developers.cloudflare.com/workers/wrangler/commands/pages/)

요청은 기관·법제처 정책·안내 페이지 12개, 샘플 게시물 13개, Cloudflare 문서 9개였다. User-Agent에 `lawbot-policy-check`를 표시했고 요청 간격은 1–2초였다. 운영 데이터는 읽기 전용으로만 열었다.
