# 계획: 법령 캘린더를 Cloudflare Pages 정적 사이트로 전면 공개

- 작성: 2026-11-25 KST. 기준 `main` `3143417`, 운영 v6.6.0(`0c68d5a`).
- 사용자 결정: A 방식(정적 내보내기). Pages Functions와 터널 프록시는 쓰지 않는다.
- 사전 확인: [공개 전 데이터·이용 조건·침입 경로 재확인](../evals/2026-11-25-calendar-public-license-check.md)

## 목표

- 비밀번호 없이 누구나 캘린더(법령 달력, ERP 자료, 기관별 최신 소식)를 본다.
- Pages에서 맥 미니로 들어오는 경로가 없다. 맥 미니는 공개 데이터를 파일로 만들어 올리기만 한다.
- 공개되는 필드는 지금의 공개 API 응답과 같다. 이 범위를 넘는 데이터는 내보내지 않는다.
- 철회한 항목은 다음 배포에서 운영 주소와 이전 배포 주소 모두에서 사라진다.

## 범위 밖

- 챗봇, 관리자 화면, 캘린더 관리자 API는 지금처럼 터널 + Access와 Tailscale에 둔다.
- 캘린더 FastAPI 서버(`com.lawbot.calendar`)는 계속 `127.0.0.1`에서 돈다. 관리자 검토 프록시가 쓰기 때문이다. 공개 경로만 Pages로 옮긴다.
- 새 수집원 추가와 기관 소식 본문·첨부 저장은 하지 않는다.

## 설계

### 1. 정적 내보내기 (`law-calendar export`)

- 같은 `create_app`을 프로세스 안에서 띄우고, httpx ASGI transport로 공개 GET 경로를 호출해 응답 JSON을 파일로 쓴다. 공개 API와 내보내기가 같은 코드·뷰·검증을 쓰므로 별도 허용 목록을 만들지 않는다. HTTP 포트는 열지 않는다.
- 출력 폴더 `data/` 아래 파일:

| 파일 | 내용 (기존 경로) |
| --- | --- |
| `law-calendar/status.json` | `/api/law-calendar/status` |
| `law-calendar/months.json` | `/api/law-calendar/months`: 월 색인(`months`)과 ERP 기준일 파일 색인(`erpRuleDates`) |
| `law-calendar/YYYY-MM.json` | `/api/law-calendar?month=` |
| `law-events/YYYY-MM.json` | `/api/law-events?month=`의 모든 페이지를 합친 목록 |
| `law-changes/<id>.json` | `/api/law-changes/{id}` |
| `law-holds.json` | `/api/law-holds?type=effective` 전 페이지 |
| `tax-schedule/YYYY-MM.json` | `/api/tax-schedule?month=` |
| `erp-updates.json` | `/api/erp-updates` |
| `erp-rules/YYYY-MM-DD.json` | `/api/erp-updates/v2?asOf=`, 내보낸 날의 KST 기준 D-1–D+2 |
| `erp-news/<agency>.json` | `/api/erp-news/latest?agency=` |

- 월 범위: 이벤트·세무일정이 있는 모든 월과 내보낸 KST 월 전후 한 달. 탐색 가능한 기간은 제한하지 않으며, 색인에 없는 월은 화면에서 "0건"으로 처리한다.
- 검사(하나라도 실패하면 출력 폴더를 만들지 않고 종료 코드 1):
  - 모든 외부 URL이 `https`이고, 출처별 허용 호스트(`law.go.kr`, 기관 3곳, `github.com/legalize-kr`)에 속한다. 기존 `official_url`·`CONFIRMATION_HOSTS` 규칙을 재사용한다.
  - 파일마다 해당 응답 모델로 다시 검증한다.
  - 문자열 전체에서 이메일, `localhost`·`127.0.0.1`·사설 IP, 로컬 절대 경로 모양이 없다.
  - 출력에 `_worker.js`, `_routes.json`, `functions/`가 없다.
  - 파일 수 20,000개, 파일당 25 MiB 미만(Pages 한도).

### 2. 화면이 파일을 읽도록 전환

- `law-calendar/data.ts`, `erp/data.ts`, `AgencyNews.tsx`의 `/api/...` 호출을 `/data/...json` 읽기로 바꾼다. 경로 조립은 한 함수에 모은다.
- 월 이동: `months.json`을 먼저 읽고, 없는 월은 빈 달로 보여 준다.
- ERP 적용 기준일: 브라우저의 KST 오늘에 맞는 `erp-rules/<오늘>.json`을 읽는다. 없으면(배포가 하루 넘게 늦음) 가장 가까운 과거 파일을 쓰고 "기준일 YYYY-MM-DD" 표시로 늦었음을 보여 준다.
- 개발 모드의 합성 예시는 그대로 둔다. 기존 `?source=api` 개발 모드는 로컬에서 내보낸 폴더를 읽는 `?source=data`로 바꾼다.
- 게시할 사이트 폴더(화면 빌드 + `data/`)는 운영의 고정 위치(`services/law-calendar/data/site/`, git 제외)에 만든다. 전환 전까지는 캘린더 서버의 `--frontend`가 이 폴더를 가리켜, 터널 화면과 Pages가 같은 파일을 보여 준다. 그래서 공개 경로가 둘이어도 철회가 한쪽에만 반영되지 않는다.

### 3. 출처·라이선스 고지

- 법령 달력과 ERP 탭에 "법령 정보 출처: 법제처 국가법령정보센터(www.law.go.kr)"를 글로 적는다. 기존 링크와 참고용 안내는 유지한다.
- 빌드할 때 `licenses.txt`를 만든다.
  - Pretendard 1.3.9 OFL-1.1 전문과 원 저장소 저작권 고지. 레포에 고정 사본으로 둔다. npm 패키지에 라이선스 파일이 없기 때문이다.
  - `react`·`react-dom`·`scheduler`의 LICENSE: `node_modules`에서 복사한다.
  - legalize-kr·admrule-kr 라이선스 문구와 링크
- 화면 바닥글에 "라이선스" 링크를 둔다. 런타임 의존성이 이 네 개 밖으로 늘면 빌드가 실패하게 한다.

### 4. `_headers`

- 모든 경로:
  - `Content-Security-Policy: default-src 'self'; img-src 'self' data:; font-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `/data/*`: `Cache-Control: public, max-age=0, must-revalidate`
- `/assets/*`: `Cache-Control: public, max-age=31536000, immutable`(파일 이름에 해시가 있음)
- CSP 위반이 없는지는 대역 E2E가 같은 헤더를 붙인 정적 서버로 확인한다.

### 5. 배포 (`law-calendar publish`)

- 순서: `export` → 빈 스테이징 폴더에 `frontend/dist`와 `data/`를 복사 → 검사 → `wrangler pages deploy <스테이징> --project-name <이름> --branch main`.
  - wrangler는 스테이징 폴더를 작업 위치로 삼아 실행한다. 그래야 `functions` 폴더가 섞이지 않는다.
  - wrangler 버전은 `frontend/package-lock.json`에 고정한다. 원격 측정은 끈다.
- 배포 뒤 처리:
  1. 새 배포 주소에서 `status.json`을 받아 내보낸 해시와 같은지 확인한다.
  2. 현재 운영 배포와 그 직전 하나(롤백용)를 뺀 이전 배포를 모두 지운다.
  3. 철회 모드(`--purge-history`)에서는 직전 배포까지 지운다.
- Pages 설정(프로젝트 이름·계정 ID·토큰)이 없으면 사이트 폴더만 갱신하고 게시는 "미설정"으로 건너뛴다. 이 경우 daily 실행은 실패로 보지 않는다.
- 비밀값: Pages 전용 API 토큰(Account › Cloudflare Pages › Edit만)과 계정 ID를 운영 `services/law-calendar/.env`(권한 600)에 둔다. 로그·출력·예외 메시지에 남기지 않는다.
- 실행 시점:
  - 매일 06:30 통합 실행(`source-hub daily`)의 마지막 단계로 호출한다. 수집이 실패해도 마지막 정상 데이터로 게시한다. 게시가 실패해도 수집 결과는 바꾸지 않는다. 게시 결과(성공·실패·배포 ID)는 일일 요약에 남는다.
  - 관리자 승인·철회 뒤에는 운영 절차의 `publish` 명령으로 바로 반영한다. 철회는 `--purge-history`를 쓴다.
- 미리보기 배포는 만들지 않는다(`--branch main`만 사용).

### 6. 전환 순서 (운영 반영 승인 뒤, 각 단계 확인 후 다음 단계)

1. 사용자가 Pages 프로젝트를 만들고 API 토큰을 발급한다. 계정 2단계 인증을 확인하고, 미리보기 배포의 Access 정책을 켠다.
2. 코드를 반영하고 `pages.dev` 주소에서 화면·데이터·헤더·라이선스를 확인한다.
3. 캘린더 호스트 이름을 Pages 사용자 지정 도메인으로 연결한다. 기존 터널 DNS 레코드는 이때 바뀐다.
4. 캘린더 호스트의 Access 애플리케이션과 터널 ingress 규칙을 지우고 cloudflared를 재시작한다. 터널에는 챗봇만 남는다.
5. 외부망에서 캘린더 주소가 인증 없이 열리는지, 챗봇은 여전히 Access를 요구하는지, 예전 캘린더 경로(`/api/...`, `/docs`)가 404인지 확인한다.

- 되돌리기: 사용자 지정 도메인을 떼고 DNS·ingress·Access를 이전 값으로 되돌린다. 이전 값은 반영 전에 운영 기록에 남긴다(비밀값·호스트 이름은 기록하지 않음).

## 테스트 (TDD)

| 층 | 내용 |
| --- | --- |
| 단위 | 내보내기 파일 목록과 내용이 같은 DB의 공개 API 응답과 같음. 미승인 원문·철회 항목은 빠지고 공개 보류 목록에는 공개 API 메타데이터만 남음. 허용 밖 호스트·`http`·`javascript:` URL, 이메일·사설 IP·로컬 경로가 들어가면 실패하고 출력 폴더 없음. `_worker.js`·`_routes.json`·`functions/`가 있으면 실패. 배포 정리가 현재·직전 하나만 남기고, `--purge-history`는 현재만 남김(가짜 wrangler). 토큰이 로그·예외에 없음 |
| 프런트엔드 | 파일 경로 조립, 색인에 없는 월은 빈 달, ERP 기준일 파일 대체와 늦음 표시, 출처 문구·라이선스 링크 |
| 대역 E2E | 테스트 DB에서 내보낸 폴더를 `_headers`를 적용하는 정적 서버로 띄우고 브라우저로 달력 이동, 날짜 상세, ERP 탭, 기관 소식, 세무 배지를 확인. API 서버 없이 동작. CSP 위반 콘솔 오류 0, `/api/` 요청 0 |
| 통합 | `source-hub daily`가 게시 실패를 기록하고 수집 결과는 유지(가짜 게시기) |
| 실제 Pages | 운영 반영 때 1회. 자동 테스트에 넣지 않음 |

## 완료 기준

- 기본 테스트 전체와 프런트엔드 typecheck·test·build가 통과한다.
- 대역 E2E에서 API 없이 화면 전체가 동작하고 CSP 위반이 없다.
- 운영 전환 뒤 외부망 확인 5개 항목이 통과하고, 다음 날 06:30 실행에서 자동 게시가 성공한다.

## 작업 나누기

- PR 1: 내보내기 + 화면 전환 + 출처·라이선스 + `_headers` + 대역 E2E. 운영 서버의 정적 제공 제거는 전환 뒤에 하므로 PR 2로 미룬다.
- PR 2: `publish`(사이트 폴더 갱신, wrangler 게시, 배포 정리, 미설정 건너뛰기), daily 연결, 운영 문서 절차.
- 화면이 `/data/`를 읽게 되므로 PR 1만 운영에 반영하면 터널로 보는 지금 화면이 깨진다. 두 PR을 모두 병합한 뒤 함께 반영한다. 반영 때 캘린더 서버의 `--frontend`를 사이트 폴더로 바꾼다.
- 캘린더 서버의 공개 API와 `--frontend`는 관리자 프록시·개발용으로 남긴다. 터널에서 캘린더 호스트를 빼면 외부에서는 닿지 않는다.
- omp가 구현하고, PR마다 Astra 리뷰 1회를 받아 타당한 지적을 반영한 뒤 PR을 올린다. CI가 통과하면 병합한다.
- 운영 반영(6절)은 별도 승인을 받는다. DNS·Access·터널 변경은 그 승인 범위에 포함해 단계별로 보고한다.

## 사용자 할 일

- Cloudflare에서 Pages 프로젝트(Direct Upload) 만들기와 이름 정하기. 토큰 발급 방법은 대화로 안내한다.
- 계정 2단계 인증 확인과 미리보기 배포 Access 정책 켜기
- (권장) 법제처 Open API 마이페이지의 시스템·활용 목적 정보를 공개 캘린더 용도로 갱신하기. 가입 주의사항에 따라 Open API 변경 공지는 수시로 확인한다.
