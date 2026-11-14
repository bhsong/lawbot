# 검증된 ERP 기관 수집원 운영 활성화

2026-11-13 KST. 운영 `v5.8.0` (`67c4086`). 운영자 요청으로 등록부의 신규 수집원 37개 중 검증된 19개의 `enabled`를 켰다. 운영 등록부 버전은 2에서 21로 변경됐고 감사 이력 19건과 무결성 검사 `ok`를 확인했다. 기존 수집원 5개를 포함해 24개가 실행 가능하다.

| 기관 | 활성화한 공식 경로 | 활성화 전 확인 |
| --- | --- | --- |
| 고용노동부 | `moel-rss-notice`, `moel-rss-policy`, `moel-rss-lawinfo` | [저작권 정책](https://www.moel.go.kr/site/copyright/copyrightList.do) · [robots.txt](https://www.moel.go.kr/robots.txt) · [RSS·목록 실측](2026-11-13-source-hub-moel-reconcile.md) |
| 재정경제부 | `mofe-rss-press`, `mofe-rss-explanation`, `mofe-rss-notice`, `mofe-rss-policy`, `mofe-rss-economy`, `mofe-laws`, `mofe-notice`, `mofe-tax-reform` | [저작권 정책](https://mofe.go.kr/mn/siteguide/drmp.do?menuNo=2030000) · [robots.txt](https://mofe.go.kr/robots.txt) · [RSS·목록 실측](2026-11-13-source-hub-mofe.md) |
| 국세청 | `nts-notice`, `nts-rules`, `nts-tax-references`, `nts-withholding`, `nts-tools`, `nts-tax-02`, `nts-tax-amendments`, `nts-developer-notices` | [저작권 정책](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=6791&cntntsId=8169) · [robots.txt](https://www.nts.go.kr/robots.txt) · [공식 목록·안내 실측](2026-11-13-source-hub-nts-hometax.md) |

세 호스트의 최신 `robots.txt`를 수집기 사용자 에이전트로 조회했다. 등록된 수집 경로 19개와 해당 RSS의 연결 게시판 목록 첫 페이지 경로가 접근 판정을 통과했다. 수집 범위는 제목·게시일·공식 링크의 내부 저장이다. 수동 공개 검토와 첫 수집 기준선 정책을 유지했다. 수집원별 공식 URL은 운영 등록부의 초기 설정과 위 실측 기록에 있다.

공식 주소 또는 연결 어댑터·정책 검증이 남은 18개는 비활성 상태다. 홈택스 4개는 공식 자료실 링크 경로를 유지한다. 운영 등록부의 dry-run은 신규 활성 수집원 19개의 실행 이력 0건을 확인했다. 실제 자료 조회는 다음 06:30 통합 실행에서 진행한다. 변경 전 SQLite 등록부 복구본은 운영 데이터의 `rollback/source-enable/` 아래에 소유자 전용 권한으로 보관했다.
