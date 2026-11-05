# 06:30 모니터링·캘린더 import 연결 검증

- 기준: 미병합 PR #71 head `e33aaa5`, 신규 브랜치 `codex/law-calendar-daily-import-20261105`. 사용자 지시에 따라 기존 06:30 daily 수집 직후 캘린더 import를 실행한다. 실제 운영 launchd 등록/실행, 외부 수집, 모델 호출은 하지 않았다.
- monitor의 기존 checks에 첫 항목을 추가한다. generic `{root}`·`{uv}` 확장으로 calendar 독립 프로젝트의 `python -m law_calendar.sync`를 실행하고, 기존 챗봇 점검·요약을 이어간다. monitor는 calendar를 import하지 않고 별도 프로세스를 사용한다. 기본 수집기·챗봇·모델 서버 코드는 변경하지 않았다.
- uv 절대 경로와 calendar DB를 launchd 환경에 명시한다. 독립 환경은 운영 반영 전에 사전 sync하며 정기 실행은 --no-sync를 사용한다. DB 자동 생성/마이그레이션·두 번째 launchd 작업을 추가하지 않았다. 기존 06:30 KST 기준 시스템 시각과 monitor 잠금을 유지한다.
- calendar sync는 monitor state.db를 mode=ro/query_only로 열고 최신 normal run의 legalize_law 정상 완료를 확인한다. 등록된 법령 이벤트만 처리하고 모델 요약의 processed_at을 소비 커서로 사용하지 않는다. 다른 수집기 실패와 법령 수집 성공은 구분한다. replay·다른 소스·등록되지 않은 파일은 제외한다. 조문 변경이 없는 제목/메타데이터 이벤트는 skipped로 집계한다.
- 성공한 수집·입력은 변경이 0건인 날에도 원천 수집 완료 시각으로 freshness를 갱신한다. 실패/진행 중/법령 수집 누락·파일 불일치·DB 부재는 성공으로 표시하지 않는다. 캘린더 실패는 점검 실패 코드 2로 기록하고 뒤 점검·요약은 계속 실행한다. 실패 시 모델 요약 처리 여부와 무관하게 다음 sync에서 재시도한다.
- 입법예고 실수집·입력은 추가하지 않았다. 테스트/개발 합성 예시를 제외하면 현재 입력 경로는 effective 이벤트만 생성하므로 왼쪽 입법예고 패널은 0건/빈 상태다.

## 검증 결과

- 신규 sync 15개: 등록된 monitor 파일→calendar DB/API, 원천 DB 불변, 요약 처리된 이벤트 입력, 변경 0건, 수집 실패/진행/누락, 다른 소스·orphan/replay 제외, 경로/ID 불일치, 누락 DB 미생성, 재시도·신선도, 부분 수집 실패, 메타데이터만 바뀐 이벤트 건너뛰기, CLI 실패 코드 2.
- monitor checks 10개: 기존 점검 회귀와 기본 순서·독립 프로젝트/uv/경로 확장, plist 06:30/환경 설정, 실제 임시 Git 수집→sync subprocess→뒤 점검→가짜 요약. 캘린더 DB 부재 때도 뒤 작업이 실행되는 실패 경로를 포함한다. 관련 검사 25개 통과.
- 두 frontend typecheck/test/build 통과: 캘린더 Vitest 16개, 기존 챗봇 19개. 프론트엔드 변경은 없다.
- ruff check/format, 루트·캘린더 pyright, compileall, schema 최신성, 루트·독립 lock check 통과. 기본 pytest 최종 **836 통과 / 제외 12개(118.04초)**. 1차 835 통과(115.48초) 후 메타데이터 이벤트 처리를 보완하고 전체를 재실행했다. macOS CI는 새 PR의 최종 head로 별도 확인한다.

TDD에서 미구현 sync, 기본 점검/환경 설정 부재, 조문 없는 이벤트의 반복 실패를 재현하고 수정했다. 초기 plist 주석에 옵션의 연속 하이픈을 적어 XML 파서 오류가 발생했으며 주석을 고쳐 plistlib 검사로 확인했다. 단위·계약·통합·대역 E2E와 실제 모델/외부 검증을 구분하며, 실제 모델 E2E·스모크·품질 평가는 미실행이다.

## 재현과 운영 경계

두 frontend의 typecheck/test/build 후 루트 기본 uv run pytest를 사용한다. uv run ruff check, uv run ruff format --check, uv run pyright, uv run pyright services/law-calendar/src, uv run python -m compileall -q services packages tools, uv run python -m law_calendar.schema --check 및 두 프로젝트 uv lock --check로 검사한다. calendar 패키지는 CI처럼 검사 환경에 별도로 설치한다. 로컬은 기존 Linux Python 3.14.8·Node 26 및 CPU torch/MLX 임시 환경이며 실제 모델 가중치를 호출하지 않는다.

배포 시 별도 calendar 환경·DB를 준비하고 monitor plist의 __UV__/__CALENDAR_DB__ 등 실제 경로를 입력해야 한다. 이 커밋만으로 현재 등록된 운영 launchd가 바뀌지는 않는다. 운영 서버·DNS·TLS·공개 API 배포는 별도다. 수집/공개 시각은 다음 API 조회부터 반영되며 브라우저 실시간 push 기능은 추가하지 않았다. 새 PR은 #71 후속으로 만들고 CI 확인 뒤 보고하며 사용자 승인 없이 병합하지 않는다.
