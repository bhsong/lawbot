# 외부 접속 반영: Cloudflare Tunnel + Access (2026-11-19)

Tailscale을 설치할 수 없는 브라우저(회사 PC)에서 운영 챗봇과 법령 캘린더를 쓰도록 Cloudflare Tunnel과 Access를 붙였다. 접속자는 사용자 본인 하나이고, 관리자 기능은 Tailscale·로컬에서만 쓴다. [계획](../archives/cloudflare-tunnel-access.md) · [운영 절차](../operations.md#상시-운영과-개발-동시-실행) · [출처 이용 조건](2026-11-19-external-access-copyright-check.md)

호스트명·이메일·터널 ID·포트는 기록하지 않는다.

## 구성

| 항목 | 내용 |
| --- | --- |
| Access | Self-hosted 앱 1개에 챗봇·캘린더 public hostname 2개. 로그인 방법은 One-time PIN만(모든 IdP 허용 끔), 정책은 Allow + Include Emails 1개, 세션 24시간 |
| 터널 | 이름 있는 터널 1개, 두 호스트 DNS CNAME. `~/.cloudflared/config.yml`(600)·인증 파일(400)·`cert.pem`(600)은 레포 밖 |
| ingress | 챗봇·캘린더 → 각 운영 루프백 포트, 두 호스트의 `^/api/admin` → 404, 그 밖의 호스트 → 404 (`ingress validate` OK, `ingress rule`로 경로별 확인) |
| 상시 실행 | 사용자 LaunchAgent `com.cloudflare.cloudflared`. `cloudflared service install`이 만든 plist의 실행 인자가 `cloudflared` 하나뿐이라 터널이 뜨지 않았고(`use cloudflared tunnel run` 안내 후 종료), 인자를 `tunnel --config <설정> run`으로 고쳐 다시 등록했다 |
| 운영 서비스 | 웹앱·캘린더·모델 서버·생성 서버는 터널 설정 때문에 재시작하지 않았다. `cloudflared` 버전 2026.11.1 |

## 확인

| 확인 | 결과 |
| --- | --- |
| 공개 주소(인증 없음) `/`, `/api/...` | 모두 Access 로그인으로 302 |
| 터널 연결 | Cloudflare 에지 연결 2개 등록 |
| 외부 브라우저: Access OTP → 챗봇 | 접속·답변 확인(사용자) |
| 외부 `/api/admin/...` | 오류(404) 확인(사용자). 루프백 확인: 웹·캘린더 모두 `CF-Connecting-IP` 헤더가 있으면 404 |
| 외부 `/admin/...` | 화면 앱의 같은 `index.html`(관리자 화면 아님, 사용자 확인) |
| tailnet 챗봇·캘린더 | 200 유지 |

## 발견한 문제와 수정

외부 브라우저에서 캘린더 화면은 열렸지만 법령 시행·ERP·기관 소식 데이터가 비어 있었다. 캘린더 화면이 API를 `credentials: 'omit'`으로 요청해 Access 인증 쿠키가 빠졌고, Access가 데이터 요청을 로그인으로 돌려보냈다. PR #187에서 네 곳을 `same-origin`으로 바꾸고 테스트를 고쳐(변경 전 6개 실패 → 통과, 기본 pytest 2,776개 통과) v6.4.1로 반영했다(캘린더 화면 빌드·캘린더 재시작만). 반영 뒤 루프백·tailnet 캘린더 API 200, 공개 주소의 인증 없는 요청은 여전히 302.

실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않았다(모델 경로 변경 없음). 다음 날 06:30 실행 점검에 터널 서비스 상태와 로그를 포함했다.
