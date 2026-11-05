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

기존 monitor의 `legalize_law` 이벤트 JSON과 원문 사본을 오프라인으로 가져온다. DB 초기화 명령은 새 DB 생성 및 기존 v1 DB의 v2 업그레이드를 지원한다. 기존 DB는 `.pre-v2.backup`으로 백업하며 같은 이름의 백업을 덮어쓰지 않는다. 공개 서버를 새 코드로 실행하기 전에 마이그레이션을 수행한다.

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

`decision`은 `approve`, `reject`, `withdraw`다. 반려·철회에는 현재 `event_id`, `snapshot_id`, `note`, `decision`만 있는 JSON을 사용할 수 있다. 수정한 원천을 재입력하면 기존 공개 승인이 해제되고 이전 snapshot의 검토 파일은 거절된다. 검토 이력과 원문 버전은 DB에 보존한다. 승인 도구는 신뢰하는 관리자의 로컬 DB 접근 권한으로만 실행하며 공개 HTTP 쓰기 API는 제공하지 않는다.

동일 파일 재입력은 중복을 만들지 않는다. import 실패가 하나라도 있으면 종료 코드 1을 반환하고 `runs`에 원인을 기록한다. 수정 후 같은 명령을 다시 실행할 수 있다. 수집 시각은 가져온 원천의 감지 시각이며 import 실행 시각으로 갱신하지 않는다. 입법예고·행정규칙·서식 이벤트는 이 어댑터가 지원하지 않는다.
