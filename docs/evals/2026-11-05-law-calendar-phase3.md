# 캘린더 Phase 3 오프라인 연결·검토 CLI 검증

- 기준: PR #69 병합 main `a59ea74`, 작업 브랜치 `codex/law-calendar-phase3-20261105`.
- `services/law-calendar` 전용 코드가 monitor의 저장된 `legalize_law` JSON·old/new.file을 읽는다. 기존 수집기·상태 DB·daily·챗봇·모델 서버는 수정하거나 실행하지 않았다. 외부 수집과 실제 모델 호출, 운영 DB 변경·배포·재시작은 없다.
- source snapshot은 원천 JSON·전체 파일·파서 버전·감지/입력 시각을 보존한다. 법령/조문 원문 버전 및 변경과의 연결을 저장하며 원문 공백·줄바꿈을 유지한다. Git 버전의 효력 구간을 자동 판정하지 않는다.
- import는 조문별 시행일을 비워 두고 비교 미검증·검토 대기로 시작한다. 공포일·기본 시행일·감지일을 공개 날짜로 대체하지 않는다. 기본 요약은 규칙 기반 확인 필요 문구이며 LLM을 사용하지 않는다.
- review는 현재 snapshot, 검토자·검토 사유, 공식 출처·조문별 시행일 근거, 날짜/부칙 검토 및 비교 검증 표시와 별도 한 줄 요약을 요구한다. 검토한 조문 일부만 승인할 수 있고 나머지는 비공개다. 감사 이력에 검토 입력·snapshot을 연결한다. 반려·철회와 원천 정정 시 승인 해제를 기록한다.
- 중복 입력은 무변경이며 SQLite 쓰기 트랜잭션으로 동시 import를 직렬화한다. 일부 파일 실패는 격리·기록하고 재입력으로 재시도한다. 실패가 있으면 성공 수집 시각을 갱신하지 않고 CLI는 종료 코드 1을 반환한다. 공개 수집 시각은 원천 감지 시각이며 실행 시각으로 신선도를 부풀리지 않는다.
- SQLite v1→v2는 기존 DB를 `.pre-v2.backup`에 보존한 뒤 순방향 변경한다. 기존 테이블·공개 view·API 응답 계약은 유지하고 공개 서버의 읽기 전용 경계도 유지한다. 명시적 마이그레이션 전 v1 DB는 새 서버가 503을 반환한다.

## 검사 및 실제 결과

- 신규 워크플로 단위·통합 23개 통과: 재입력/동시 입력, 원문 보존, 조문 신설·삭제, 전체 법령 신설·폐지, 부분 승인·미확정일 비노출, 다조문 날짜·집계, 공식 URL 위장·잘못된 날짜·미검증·오래된 snapshot 거절, 정정/반려/철회 이력, 실패 격리·재시도, 경로 탈출/심볼릭 링크 차단, v1 백업·업그레이드, CLI 실행. 같은 원천 이벤트의 다른 법령 ID 재할당도 거절한다.
- 브라우저는 합성 monitor 파일→어댑터→DB→관리 CLI→독립 API→production 화면에서 승인 전 0건, 승인 후 건수·원문 비교, 철회 후 0건을 확인한다. 실제 모델 E2E·모델 스모크·품질 평가는 미실행이며 대역 E2E와 구분한다.
- 두 frontend의 typecheck/test/build 통과: 캘린더 Vitest 14개, 기존 챗봇 19개.
- 전체 기본 pytest 최종: **798 통과 / 기본 제외 12개 (119.99초)**. 독립 캘린더 API 20개·워크플로 23개·대역 브라우저 E2E 6개를 포함한다. 이전 1차는 797 통과였으며 법령 ID 재할당 거절을 추가한 뒤 전체를 다시 실행했다.
- ruff check/format(205 파일), 독립 API pyright, 전체 pyright, compileall, API 타입 생성 최신성, 루트/독립 lock check 모두 통과했다. 챗봇·모델 서버·기존 monitor·frontend 소스·루트 lock/설정/README·CI의 main 대비 변경 0건을 확인했다. 새 PR의 macOS CI는 커밋 생성 뒤 별도 확인한다.

TDD에서 미구현 import 모듈의 부재와 법령 ID 재할당을 허용하던 동작을 먼저 확인하고 수정했다. 신규 브라우저 검사 초안은 삭제 조문을 첫 행으로 선택해 비교 문구 검사가 실패했으며 제1조를 이름으로 선택하도록 고쳤다. Linux lock 검사는 기본 uv 캐시·인터프리터 경로가 읽기 전용이어서 실패한 뒤 이번 작업의 `/tmp` 환경을 명시해 통과했다. 이를 애플리케이션 실패나 실제 모델 검증으로 기록하지 않는다.

## 재현과 남은 범위

두 frontend에서 각각 `npm run typecheck`, `npm test`, `npm run build` 후 루트의 기본 `uv run pytest`를 실행한다. 별도로 `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pyright services/law-calendar/src`, `uv run python -m law_calendar.schema --check`, `uv run python -m compileall -q services packages tools`, `uv lock --check`, `uv lock --project services/law-calendar --check`를 사용한다. 독립 API 패키지는 CI와 같이 검사 환경에 editable로 설치한다.

로컬은 Node 26·Python 3.14.8 Linux 임시 환경이며 루트 lock 버전과 CPU torch/MLX를 사용했다. 실제 모델 가중치·호출 없이 기본 테스트만 실행하고 macOS CI는 PR의 최종 head로 별도 확인한다.

입법예고는 기존 수집기·인증·응답 계약이 없어 미구현이며 행정규칙·서식도 이번 어댑터의 지원 범위 밖이다. 실제 법령 데이터의 공식 시행 직전·직후 원문 추적, 조항별 효력 구간·경과조치 자동 검증, 원천 이용 조건·공개 범위 확정, 실제 수집 스케줄과 운영 배포는 남아 있다. CLI 검토 표시 자체가 법률 검증을 수행하지 않으며 관리자의 공식 원문 확인을 대신하지 않는다. Phase 3 전체 실데이터 완료로 표시하지 않는다.
