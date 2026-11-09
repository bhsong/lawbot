# 법령 변경 캘린더

로그인 없이 별도 서브도메인의 루트 `/`에서 제공하는 독립 웹앱이다. 챗봇은 다른 서브도메인의 로그인 서비스로 유지한다.

## 구성

```text
공개 캘린더 서브도메인 → 정적 호스팅 → frontend/dist
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

`frontend/dist/` 전체를 캘린더 호스트의 루트에서 정적 파일로 제공한다. production 빌드는 개발 예시를 제외한다.

## 독립 브라우저 검사

아래 명령은 저장소 루트에서 실행한다. 먼저 위 frontend를 빌드한다.

```fish
uv run --no-project --python 3.14 --with-editable ./services/law-calendar --with pytest==9.1.1 --with httpx==0.28.1 --with playwright==1.64.1 playwright install chromium
uv run --no-project --python 3.14 --with-editable ./services/law-calendar --with pytest==9.1.1 --with httpx==0.28.1 --with playwright==1.64.1 python -m pytest -c pyproject.toml services/law-calendar/tests
```

설계와 구현 계획은 [LAW_CALENDAR_PLAN.md](../../docs/plans/LAW_CALENDAR_PLAN.md)를 참고한다.

## 조회 API

독립 API의 Python 버전·의존성은 이 폴더의 pyproject.toml·uv.lock을 따른다. DB 경로는 직접 지정한다.

```fish
uv sync --project services/law-calendar --locked
uv run --project services/law-calendar python -m law_calendar.db services/law-calendar/data/calendar.db
uv run --project services/law-calendar law-calendar --database services/law-calendar/data/calendar.db --port $CALENDAR_API_PORT
```

정적 파일도 같은 공개 서버에서 제공하려면 `--frontend services/law-calendar/frontend/dist`를 추가한다. 기존 DB는 초기화 명령으로 덮어쓰지 않는다. 운영 DB 변경 전에는 SQLite backup으로 사본을 보관한다.

개발 화면에서 API 데이터를 사용하려면 `LAW_CALENDAR_API_URL`을 로컬 API 주소로 설정하고 Vite 주소의 `/?source=api`로 접속한다. 기본 개발 모드는 합성 예시다. production은 같은 origin의 공개 API를 읽으며, API에 연결할 수 없으면 오류를 표시한다. 빈 DB에는 공개 항목이 없고 예시나 미승인 항목을 자동 삽입하지 않는다.

조회 경로는 `/api/law-calendar?month=YYYY-MM`, `/api/law-events?date=YYYY-MM-DD`, `/api/law-changes/{id}`, `/api/law-calendar/status`다. API의 OpenAPI 계약은 `/openapi.json`에서 확인할 수 있다.

## 저장된 모니터링 결과와 공개 검토

기존 monitor의 `legalize_law` 이벤트 JSON과 원문 사본을 오프라인으로 가져온다. DB 초기화 명령은 새 DB와 기존 v1~v6 DB를 v7로 준비한다. v6에서 올릴 때는 SQLite backup으로 `.pre-v7.backup` 사본을 만든다. 이전 버전의 백업 규칙도 유지한다. v4~v6에서 기존 일정 조회와 검토를 지원한다. 최신 수집 코드의 예고 자동 공개와 첨부 처리 상태 저장은 v7이 필요하다. [v7 반영 절차](../../docs/operations.md#국회-입법예고-자동-공개용-캘린더-db-v7)

import는 명확한 최신 부칙의 절대 날짜·공포일 시행 또는 조문 전체의 구조화 시행 메타데이터를 자동 판정한다. 날짜가 확정되면 규칙 기반 요약과 함께 달력에 표시한다. 위임 표현·복잡한 부칙·부분 시행·상충·근거 부족은 보류한다. 자동 확인은 시행일에 한정되며 Git 구판·신판의 법적 효력 비교는 미검증으로 표시한다.

화면 상단의 **보류 목록** 버튼은 날짜와 무관한 보류 조문·사유·공식 원문 링크를 읽기 전용 dialog로 표시한다. 보류는 달력 건수에 포함되지 않는다. 빈 목록·로딩·오류 재시도·페이지 이동을 지원하며 수정·확정 기능은 제공하지 않는다. 조회 API는 `/api/law-holds?page=1&page_size=20`이다.

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

`decision`은 `approve`, `reject`, `withdraw`다. 반려·철회에는 현재 `event_id`, `snapshot_id`, `note`, `decision`만 있는 JSON을 사용할 수 있다. 수정한 원천을 재입력하면 새 snapshot으로 다시 판정하며 이전 snapshot의 검토 파일은 거절된다. 명시적으로 반려·철회한 이벤트는 재입력으로 자동 공개하지 않는다. 검토 이력과 원문 버전은 DB에 보존한다. 승인 도구는 신뢰하는 관리자의 로컬 DB 접근 권한으로만 실행하며 공개 HTTP 쓰기 API는 제공하지 않는다.

동일 파일 재입력은 중복을 만들지 않는다. import 실패가 하나라도 있으면 종료 코드 1을 반환하고 `runs`에 원인을 기록한다. 수정 후 같은 명령을 다시 실행할 수 있다. 수집 시각은 가져온 원천의 감지 시각이며 import 실행 시각으로 갱신하지 않는다. 입법예고·행정규칙·서식 이벤트는 이 어댑터가 지원하지 않는다.

## 06:30 모니터링과 정기 import

기존 모니터링의 `daily`는 수집 직후 첫 점검으로 캘린더 sync를 실행하고, 기존 챗봇 점검·모델 요약으로 이어진다. 독립 API 환경과 DB를 사전에 준비하고 monitor launchd 템플릿의 `LAW_MONITOR_UV`·`LAW_CALENDAR_DB`를 지정한다. 같은 06:30 작업 안에서 순차 실행하므로 별도 캘린더 launchd 작업을 만들지 않는다. 템플릿은 Mac 시스템 시간대가 KST인 환경을 기준으로 한다.

```fish
uv sync --project services/law-calendar --locked
uv run --project services/law-calendar python -m law_calendar.db $CALENDAR_DB
uv run --project services/law-calendar --frozen --no-sync python -m law_calendar.sync --monitor-home $MONITOR_HOME --database $CALENDAR_DB
```

sync는 monitor state.db의 최신 수집 결과와 등록된 이벤트 파일을 읽기 전용으로 가져온다. 법령은 공통 법령 범위의 Legalize KR 폴더를, 국회 의안과 입법예고는 정규화한 제목 키워드를 대조해 입력을 고른다. 이 필터는 정기 sync에 적용된다. 기존 일정과 스냅숏을 보존하며 명시적으로 지정한 이벤트의 오프라인 import도 지원한다. [공통 법령 범위](../../packages/lawbot-common/src/lawbot_common/law_scope.json)

법령·의안·예고 출처별로 검증하고 변경이 0건인 정상 실행도 각 마지막 수집 시각을 기록한다. 실패한 출처는 시각과 기존 데이터를 보존하고 다른 출처의 입력은 진행한다. 입력 실패는 종료 코드 2, 입법예고 검토 대기나 PDF 재시도는 종료 코드 1로 점검 결과에 표시된다.

### 국회 이벤트 공개 검토

국회 입법예고는 수집이 성공한 정기 sync에서 공식 상세의 의안번호와 예고 시작·종료일이 일치하면 자동 공개한다. 기간이 비어 있거나 불일치하면 검토 대기로 남는다. 수동 반려·철회 상태는 정정이 들어와도 유지된다. 의안은 수동 검토 대상이다.

캘린더의 **보류 목록 → 입법예고 검토 대기**에서 건수, 의안번호, 대기 사유와 공식 원문을 확인할 수 있다. 이 목록은 공개 승인 전 확인용으로 날짜별 캘린더 집계와 분리된다.

`assembly-list --notice-sample 5`로 최근 자동 공개 예고 최대 5건을 골라 의안번호와 검토 근거를 확인할 수 있다. `assembly-show`로 각 공식 링크와 날짜 근거를 확인한다. 전체 목록은 `--notice-sample`을 생략한다.

```bash
uv run --project services/law-calendar python -m law_calendar.manage --database "$CALENDAR_DB" assembly-list
uv run --project services/law-calendar python -m law_calendar.manage --database "$CALENDAR_DB" assembly-show "$SNAPSHOT_ID"
uv run --project services/law-calendar python -m law_calendar.manage --database "$CALENDAR_DB" assembly-review "$SNAPSHOT_ID" approve --reviewer "$REVIEWER_ID" --note '공식 원문과 날짜 확인'
```

같은 명령에서 `reject` 또는 `withdraw`로 반려·철회한다. 공개 화면은 공식 예고 시작·종료일과 법률안 발의·의결일을 구분하며, 같은 예고의 시작과 종료가 같은 날이면 한 건으로 집계한다.

### 입법예고 주요 변경 내용과 공식 대비표

정기 sync는 한 번에 최대 24건을 대조해 본문과 대비표를 함께 저장한다. 일시적인 PDF 조회 오류는 다음 정기 sync에서 재시도한다. PDF가 150쪽 또는 대비표 PNG 한 장이 2MiB를 초과하면 상세창에서 공식 PDF로 안내하고, 기존 이미지는 이전에 저장한 대비표로 표시한다. 한도 초과 건은 새 스냅샷을 수집하거나 `--refresh`를 실행할 때 다시 확인한다. 이미 공개한 자료를 별도로 보강하려면 캘린더 DB를 v7로 올린 뒤 다음 명령을 실행한다.

```bash
uv sync --project services/law-calendar --locked
uv run --project services/law-calendar python -m law_calendar.db "$CALENDAR_DB"
uv run --project services/law-calendar python -m law_calendar.manage --database "$CALENDAR_DB" notice-content
```

명령은 국회 상세의 의안번호·예고기간과 저장된 현재 snapshot을 대조한다. 주요내용을 평문으로 저장하고, 국회가 제안이유와 주요내용을 한 문단으로 제공하면 그 문단을 보존한다. 공식 PDF에서 확인한 신·구조문대비표는 원본 페이지 이미지와 PDF 원문 링크로 제공한다. 상세창에서 대비표를 펼치면 같은 화면에서 확인할 수 있다.

수동 명령은 현재 승인 snapshot 중 보강이나 PDF 재시도가 필요한 자료를 최대 150건 읽으며, `--limit`으로 배치 크기를 줄일 수 있다. 이미 보강한 자료를 다시 대조할 때는 `--refresh`를 사용한다. 재조회에 실패하면 기존 대비표를 보존한다. PDF는 메모리에서 처리하고, 공개 API는 SQLite에 저장한 본문과 PNG 데이터를 읽는다.

`--refresh` 출력에 `nextAfterSnapshot` 값이 있으면 다음 실행에 `--refresh --after-snapshot "$NEXT_SNAPSHOT"`을 넣어 이어서 처리한다. 값이 `null`이면 마지막 배치까지 도달한 것이다. 실패한 항목은 각 배치의 `failed`와 `pdfErrors`, 한도 초과는 `pdfLimits`에 기록된다.
