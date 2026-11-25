# 법령 변경 캘린더

로그인 없이 별도 서브도메인의 루트 `/`에서 제공하는 독립 웹앱이다. 챗봇은 다른 서브도메인의 로그인 서비스로 유지한다.

외부 브라우저 접속은 Cloudflare Tunnel + Access로 제공하며, Cloudflare를 거친 요청의 `/api/admin` 경로는 서비스 토큰과 관계없이 404다. 관리자 검토는 챗봇의 루프백 프록시로만 한다. [운영 절차](../../docs/operations.md#상시-운영과-개발-동시-실행)

## 구성

```text
공개 캘린더 화면 → 정적 호스팅 → 내보낸 사이트 폴더(화면·data·헤더·라이선스)
비공개 챗봇 서브도메인 → 기존 챗봇 서버 → Mac mini 모델 서버
```

캘린더는 별도의 Vite + React 앱이며 챗봇·모델 서버가 실행되지 않아도 동작한다.

## 개발 및 빌드

Node 버전은 `frontend/.nvmrc`, 패키지 버전은 `frontend/package-lock.json`을 따른다. 챗봇의 `.env`나 인증·모델 설정은 필요하지 않다.

```fish
cd services/law-calendar/frontend
npm ci
npm run dev
```

Vite가 표시하는 로컬 개발 주소의 `/`로 접속한다. 개발 모드에만 합성 예시가 있고 한국 표준시 오늘에 배치된다. 모든 예시는 실제 법령이 아니라고 표시한다.

```fish
npm run typecheck
npm test
npm run build
```

`frontend/dist/`에는 화면과 `licenses.txt`가 생성되며 운영 빌드에는 합성 예시가 들어가지 않는다. 배포 또는 정적 미리보기에는 빌드만 복사하지 말고 저장소 루트에서 개발 DB와 ERP 공개 일일 스냅샷으로 사이트를 내보낸다.

```fish
uv run --project services/law-calendar law-calendar export --database $CALENDAR_DB \
  --erp-daily-feed $ERP_DAILY_FEED --frontend-dist services/law-calendar/frontend/dist \
  --out services/law-calendar/data/site
```

내보내기는 포트를 열지 않고 같은 공개 조회 경로를 프로세스 안에서 호출한다. `index.html`, `404.html`, `licenses.txt`와 허용된 `assets/` 파일만 빌드에서 복사하고, 검증한 `data/`와 `_headers`를 추가한다. `--out`은 아직 없거나 이번 형식의 `.law-calendar-site` 표식이 있는 디렉터리만 지정할 수 있다. 이전에 표식 없이 내보낸 폴더는 덮어쓰지 않으므로 새 경로를 지정한다.

내보내기와 게시는 서비스 비공개 디렉터리의 같은 잠금을 사용한다. 독립 내보내기는 잠금을 최대 30초 기다리며, DB·일일 스냅숏 백업부터 설치까지 잠금을 유지한다. 검사가 실패하면 논리 파일 이름, 필드 위치, 검사 코드만 출력하고 종료 코드 1로 끝난다. 기존 사이트는 그대로 남는다. 교체에는 macOS 또는 Linux의 원자적 디렉터리 교환 지원이 필요하다.

Vite 주소에서 `/?source=data`로 접속하면 `services/law-calendar/data/site/`의 정적 데이터를 읽는다. 다른 폴더는 `LAW_CALENDAR_SITE`에 절대 경로로 지정한다. 별도 API 서버는 필요 없다.

## Pages 게시

`law-calendar publish`는 서비스 `.env`에 고정한 입력으로 로컬 사이트를 갱신한다. 프로젝트를 설정하지 않으면 `skipped`, 프로젝트가 활성화됐지만 전용 `pages.env`가 없거나 올바르지 않으면 실패한다. 게시기는 고유 빌드 표식과 운영 배포 ID를 확인한 뒤 배포 이력을 정리한다. daily가 호출하며, 관리자·로컬 검토는 고정 DB의 변경만 예약한다. [환경 변수·운영 전환·수동 게시와 철회](../../docs/operations.md#법령-캘린더-정적-사이트-게시)

## 독립 브라우저 검사

아래 명령은 저장소 루트에서 실행한다. 먼저 위 frontend를 빌드한다.

```fish
uv run --no-project --python 3.14 --with-editable ./services/law-calendar --with pytest==9.1.1 --with httpx==0.28.1 --with playwright==1.64.1 playwright install chromium
uv run --no-project --python 3.14 --with-editable ./services/law-calendar --with pytest==9.1.1 --with httpx==0.28.1 --with playwright==1.64.1 python -m pytest -c pyproject.toml services/law-calendar/tests
```

CI와 같은 의존성이 저장소 가상 환경에 이미 설치되어 있으면 오프라인에서 `uv run --offline --no-sync pytest -c pyproject.toml services/law-calendar/tests`로 실행할 수 있다. 대역 E2E는 내보낸 폴더를 `_headers`가 적용된 정적 파일 서버에서 열며 API 서버를 띄우지 않는다.

설계는 [캘린더 계획](../../docs/plans/LAW_CALENDAR_PLAN.md)과 [정적 사이트 전환 계획](../../docs/plans/calendar-pages-static.md)을 참고한다.

## 조회 API

독립 API의 Python 버전·의존성은 이 폴더의 pyproject.toml·uv.lock을 따른다. DB 경로는 직접 지정한다.

캘린더는 조문별 시행일 기준의 법령 변경만 제공한다. 월별 건수는 시행 조문 수이며, 목록·상세에는 공포일과 시행일을 표시한다. v9 DB는 이벤트 종류 없이 시행 조문을 저장하고, 검토 입력도 공포일·조문별 시행일과 원문 근거를 받는다.

```fish
uv sync --project services/law-calendar --locked
uv run --project services/law-calendar python -m law_calendar.db services/law-calendar/data/calendar.db
uv run --project services/law-calendar law-calendar --database services/law-calendar/data/calendar.db --port $CALENDAR_API_PORT
```

조회 서버에서 정적 화면도 확인하려면 `--frontend services/law-calendar/data/site`를 추가한다. 이 옵션은 개발·전환용이며 별도 정적 호스팅과 달리 `_headers`를 적용하지 않는다. 기존 DB는 초기화 명령으로 덮어쓰지 않는다. 운영 DB 변경 전에는 SQLite backup으로 사본을 보관한다.

브라우저 화면은 조회 API가 아닌 같은 출처의 `/data/` 파일만 읽는다. `data/law-calendar/months.json`에 없는 달에는 변경과 세무일정을 0건으로 표시한다. 빈 DB에는 예시나 미승인 항목을 자동 삽입하지 않는다.

조회 및 내보내기 원천 경로는 `/api/law-calendar?month=YYYY-MM`, `/api/law-events?date=YYYY-MM-DD`, `/api/law-changes/{id}`, `/api/law-calendar/status`, `/api/law-calendar/months`다. 월 색인은 공개 시행일·세무일정이 있는 모든 달과 내보낸 달 전후 한 달, ERP 기준일 파일 목록을 반환한다. API의 OpenAPI 계약은 `/openapi.json`에서 확인할 수 있다.

국세청 세무일정이 통합 스냅샷에 있으면 날짜에 빨간색 `세무 N` 배지를 표시한다. 날짜를 선택하면 공식 표에서 확인한 원천세·지급명세서·연말정산 기한과 대상 기간, 국세청 원문 링크를 볼 수 있다. `GET /api/tax-schedule?month=YYYY-MM`은 해당 월의 일정과 마지막 확인 시각을 반환한다. 법령 변경 일정 집계는 기존 API에서 유지된다.

## ERP 자료 탭

화면 위의 탭에서 `법령 캘린더`와 `ERP 자료`를 고른다. `/#erp`로 접속하면 ERP 자료 탭이 바로 열린다. ERP 코드는 `frontend/src/erp/`에만 있고, 법령 달력 그리드와 챗봇 관리 화면에는 ERP가 나오지 않는다.

- 데이터: [`services/erp-updates`](../erp-updates/README.md)가 승인한 항목만 담아 만든 공개 피드 파일이다. 서버는 `--erp-feed <파일>` 또는 서비스 `.env`(`services/law-calendar/.env`)의 `LAWBOT_CALENDAR_ERP_FEED=<절대 경로>`로 그 파일을 읽기만 한다. 캘린더 DB에는 쓰지 않는다.
- `GET /api/erp-updates`: `state`가 `ok`이면 `items`·`relatedLaws`·`history`를 돌려준다. 파일이 없거나 설정하지 않았으면 `missing`, 형식이 맞지 않으면 `invalid`이고 둘 다 항목은 0건이다. 기존 조회 API의 응답은 바뀌지 않는다.
- 화면 구성:
  - **현재 적용 중**: 시행일이 오늘(KST) 이전인 항목과 현행 법령본 기준 항목
  - **적용 예정**: 시행일이 오늘 이후인 항목
  - **관련 법령 일정**: `relatedLaws`에 있는 법령만 골라 내보낸 시행 조문을 월별로 보여 준다
  - **수집 이력** 버튼: 어떤 항목을, 언제, 어느 원천(저장소 커밋이나 등록한 PDF의 SHA-256)에서 가져와 승인했는지 표로 보여 준다

## 기관별 최신 소식

ERP 탭의 기관 버튼에서 검토된 소식을 게시판별로 최신 최대 10건씩 볼 수 있고, 게시판별 마지막 수집 상태도 함께 표시합니다. 고시·법령·행정규칙 게시판이 먼저, 공지·보도자료 게시판이 마지막에 나오며 게시일이 없는 글은 원래 게시판 목록 순서를 따릅니다. 홈택스는 공식 홈과 자료실 이동 경로를 표시합니다. 국가법령정보센터는 추적 법령 범위의 승인 공포 기록을 읽으며, 일일 스냅숏에 남아 있더라도 현재 공개 조문이 없는 이벤트는 제외합니다. 모든 조문의 승인 시행일이 같을 때 수집 시점의 시행일도 표시합니다.

Source Hub가 내보낸 공개 파일을 `LAWBOT_CALENDAR_NEWS_FEED` 또는 `law-calendar --news-feed`로 지정합니다. 캘린더에 비공개 수집 DB를 연결할 필요가 없습니다. 파일이 준비되지 않으면 드로어에 대기 상태를 표시합니다. `GET /api/erp-news/latest?agency=moel&limit=10`으로 확인할 수 있습니다. [검토 및 내보내기](../source-hub/README.md#기관별-최신-소식-검토와-내보내기)

## ERP 적용 기준 v2

`erp-updates export-rules`로 만든 공개 파일을 `LAWBOT_CALENDAR_ERP_RULES_FEED` 또는 `law-calendar --erp-rules-feed`로 지정합니다. 기존 `GET /api/erp-updates`는 계속 v1 자료와 이력을 읽습니다. `GET /api/erp-updates/v2`는 업무 항목의 적용 범위별 현행판과 확정 예정판을 반환합니다.

선택 매개변수 `asOf`는 한국 표준시 기준일, `targetOn`은 지급·귀속·보험료 등에 적용할 업무 대상일입니다. 둘 다 `YYYY-MM-DD` 형식입니다. `asOf`를 생략하면 오늘, `targetOn`을 생략하면 기준일을 사용합니다. 철회된 내용은 공개 응답에서 빠지고 기존 결정은 ERP 내부 이력에서 조회할 수 있습니다. [검토 및 내보내기](../erp-updates/README.md#적용-기준-버전과-피드-v2)

ERP 탭은 v2가 설정되면 항목마다 한 카드에서 적용 범위별 현행판과 첫 확정 변경을 함께 표시합니다. 이후 변경과 기존 v1 원문·첨부는 같은 카드에서 펼쳐 볼 수 있습니다. v2 파일이 없거나 손상된 경우에는 경고와 함께 v1 자료를 원문 참고로 표시합니다. v2를 설정하지 않은 환경에서는 기존 ERP 화면을 사용합니다.

정적 사이트는 내보내는 날의 한국 표준시 기준 전날부터 이틀 뒤까지 ERP 기준일 파일을 담는다. 오늘 파일이 없거나 JSON이 아니어서 과거 파일을 사용하면 기준일과 갱신 지연을 표시한다. 캘린더 수집 지연(48시간)과 기관 소식 만료는 화면이 데이터를 읽을 때의 브라우저 시각을 기준으로 표시한다.

## ERP 일일 스냅샷

통합 실행에서 만든 파일을 `LAWBOT_CALENDAR_ERP_DAILY_FEED` 또는 `--erp-daily-feed`로 지정합니다. ERP v1·v2·기관 소식이 같은 파일을 읽고 응답에 `X-ERP-Snapshot`을 표시합니다. `GET /api/erp-updates/status`는 스냅샷 해시, 기준일·시각, 수집 상태를 제공합니다. 기존 단독 피드 설정은 통합 파일을 설정하지 않은 환경에서 사용합니다.

## 저장된 모니터링 결과와 공개 검토

기존 monitor의 `legalize_law` 이벤트 JSON과 원문 사본을 오프라인으로 가져온다. DB 초기화 명령은 새 DB를 v9로 만들고 기존 DB를 순차 마이그레이션한다. v8에서 올릴 때는 SQLite backup으로 `.pre-v9.backup` 사본을 만든다. 이전 버전의 백업 규칙도 유지한다. [v9 반영 절차](../../docs/operations.md#캘린더-db-v9-반영)

import는 명확한 최신 부칙의 절대 날짜·공포일 시행 또는 조문 전체의 구조화 시행 메타데이터를 자동 판정한다. 날짜가 확정되면 규칙 기반 요약과 함께 달력에 표시한다. 위임 표현·복잡한 부칙·부분 시행·상충·근거 부족은 보류한다. 자동 확인은 시행일에 한정되며 Git 구판·신판의 법적 효력 비교는 미검증으로 표시한다.

공개 화면의 **보류 목록**은 날짜와 무관한 보류 조문·사유·공식 원문 링크를 표시한다. 보류는 달력 건수에 포함되지 않는다. 조회 API는 `/api/law-holds?page=1&page_size=20`이다.

웹앱의 **피드백 & 법령 관리 → 법령 캘린더 → 보류 목록**에서는 저장 원문과 공식 근거를 대조하고 시행일 보류를 승인할 수 있다. 같은 이벤트에 저장된 공포일이 있으면 다음 조문의 검토 양식에 그 값을 채운다. 시행일 이벤트 전체의 반려·철회는 아래 CLI에서 처리한다.

관리자 검토와 실패 조회를 사용하려면 웹앱의 레포 최상위 `.env`와 `services/law-calendar/.env` 두 곳의 `LAWBOT_CALENDAR_ADMIN_TOKEN`에 사용자가 같은 값을 직접 입력한다. 두 파일은 소유자만 읽고 쓸 수 있게 한다. 캘린더 서버는 `law-calendar` 실행 파일을 직접 호출해도 서비스 폴더의 `.env`를 시작할 때 읽는다. 프로세스 환경에 같은 변수가 있으면 그 값을 우선 사용한다.

아래 변수는 캘린더 전용 개발 DB, 저장된 monitor home과 이벤트 JSON 파일, 검토자 식별자로 직접 지정한다. monitor home은 `old/new.file` 상대 경로의 기준 디렉터리다. import는 원천 파일을 읽기만 하며 외부 수집이나 모델 호출을 하지 않는다.

```fish
uv run --project services/law-calendar python -m law_calendar.manage --database $CALENDAR_DB import --monitor-home $MONITOR_HOME $EVENT_JSON
uv run --project services/law-calendar python -m law_calendar.manage --database $CALENDAR_DB list
uv run --project services/law-calendar python -m law_calendar.manage --database $CALENDAR_DB show $EVENT_ID
uv run --project services/law-calendar python -m law_calendar.manage --database $CALENDAR_DB show $EVENT_ID --template
uv run --project services/law-calendar python -m law_calendar.manage --database $CALENDAR_DB runs
```

`show`는 원천 이벤트·전체 원문·변경 후보를 표시한다. `--template` 출력 내용을 새 검토 JSON 파일에 저장하고, 공식 원문과 부칙을 확인한 관리자가 수정한다. `note`, `source_url`, `promulgated_on`, `dates_verified: true` 및 공개할 `changes`의 `effective_on`, `summary`, `evidence_url`, `comparison_verified: true`가 필요하다. summary는 원문과 별도로 저장하는 한 줄 요약이다. 시행일을 특정하지 못한 조문은 changes에서 제외해 검토 대기로 남긴다. Git 구판·신판이 실제 시행 전후 조문인지 확인할 수 없으면 승인하지 않는다.

```fish
uv run --project services/law-calendar python -m law_calendar.manage --database $CALENDAR_DB review $REVIEW_JSON --reviewer $REVIEWER_ID
```

`decision`은 `approve`, `reject`, `withdraw`다. 반려·철회에는 현재 `event_id`, `snapshot_id`, `note`, `decision`만 있는 JSON을 사용할 수 있다. 수정한 원천을 재입력하면 새 snapshot으로 다시 판정하며 이전 snapshot의 검토 파일은 거절된다. 명시적으로 반려·철회한 이벤트는 재입력으로 자동 공개하지 않는다. 검토 이력과 원문 버전은 DB에 보존한다.

동일 파일 재입력은 중복을 만들지 않는다. import 실패가 하나라도 있으면 종료 코드 1을 반환하고 `runs`에 원인을 기록한다. 수정 후 같은 명령을 다시 실행할 수 있다. 수집 시각은 가져온 원천의 감지 시각이며 import 실행 시각으로 갱신하지 않는다. 행정규칙·서식 이벤트는 이 어댑터가 지원하지 않는다.

## 06:30 모니터링과 정기 import

기존 모니터링의 `daily`는 수집 직후 첫 점검으로 캘린더 sync를 실행하고, 기존 챗봇 점검·모델 요약으로 이어진다. 독립 API 환경과 DB를 사전에 준비하고 monitor launchd 템플릿의 `LAW_MONITOR_UV`·`LAW_CALENDAR_DB`를 지정한다. 같은 06:30 작업 안에서 순차 실행하므로 별도 캘린더 launchd 작업을 만들지 않는다. 템플릿은 Mac 시스템 시간대가 KST인 환경을 기준으로 한다.

```fish
uv sync --project services/law-calendar --locked
uv run --project services/law-calendar python -m law_calendar.db $CALENDAR_DB
uv run --project services/law-calendar --frozen --no-sync python -m law_calendar.sync --monitor-home $MONITOR_HOME --database $CALENDAR_DB
```

sync는 monitor state.db의 최신 수집 결과를 읽고, 캘린더에 기록된 이벤트 ID는 JSON을 열기 전에 건너뛴다. 미등록 이벤트는 공통 법령 범위의 Legalize KR 폴더를 대조해 가져온다. 기존 일정과 스냅숏을 보존하며 명시적으로 지정한 이벤트의 오프라인 import도 지원한다. [공통 법령 범위](../../packages/lawbot-common/src/lawbot_common/law_scope.json)

법령 수집을 검증하고 변경이 0건인 정상 실행도 마지막 수집 시각을 기록한다. 수집 실패 시 시각과 기존 데이터를 보존한다. 다음 실행에서 실패한 이벤트를 재시도하며 웹앱 관리자 캘린더의 **가져오기 실패**에서 실패 기록을 확인할 수 있다. 입력 실패는 종료 코드 2로 점검 결과에 표시된다.

