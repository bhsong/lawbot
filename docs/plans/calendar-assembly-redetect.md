# 국회 이벤트 재관측으로 인한 캘린더 import 실패 수정

2026-11-10 KST · `fix/calendar-assembly-redetect` · 운영 반영 버전 `v5.2.2` (Z, 버그 수정)

## 배경과 목표

11월 10일 06:30 수집은 국회 이벤트 25건을 기록했다. 전날 bootstrap에서 revision 0으로 등록한 진행 중 입법예고 16건이 첫 정기 실행에서 revision 1로 발행됐다. 11월 6~7일 일괄 이관에 있던 같은 `event_id` 10건은 `detected_at`만 달랐다. 이 충돌로 입법예고 트랜잭션 전체가 롤백됐고 본문 동기화도 건너뛰었다. `sync.registrations`는 등록 이벤트 전체를 매번 읽으므로 다음 정기 실행에서도 같은 충돌이 발생한다. [첫 실행 기록](../evals/2026-11-10-assembly-first-run.md)

PR #106 리뷰에서 초기 수정으로 운영 DB 사본을 재현한 결과, 남은 6건 중 3건은 다른 `event_id`로 이미 이관된 동일 항목·revision이었고 3건은 신규였다. 국회 수집기의 해시 입력에 들어가는 `target.committee_id`가 새로 채워지면서 ID가 달라졌다. 이 필드는 캘린더의 검증·저장 대상에 없으므로 저장 스냅숏의 내용은 같았다. 초기 수정은 다른 ID를 삽입하려다 `assembly_snapshots`의 `UNIQUE(source, upstream_id, revision)` 제약에 걸렸다.

## 구현 및 검증

1. 기존 `scheduled` 픽스처와 국회 이벤트 등록 헬퍼로 입법예고와 법률안의 재관측을 재현한다. 같은 ID에 `detected_at`만 바뀐 경우와 다른 ID에 동일 항목·revision의 저장 필드가 같은 경우를 각각 검증한다. 재입력은 `imported=0, unchanged=1`이며 기존 ID, `collected_at`, `event_json`을 보존해야 한다.
2. 다른 ID의 동일 항목·revision 재관측이 기존 코드의 유일성 제약 오류를 내는지 먼저 확인한다. 같은 ID와 다른 ID 두 경로에서 `dates.notice_end`처럼 저장 필드가 달라지면 `ValueError`가 나야 한다. 같은 소스 트랜잭션에서 먼저 처리한 새 스냅숏까지 롤백되는지 확인한다.
3. `assembly_ingest.import_assembly`는 ID로 기존 행을 먼저 찾는다. 이 경로에서는 최상위 `detected_at`만 비교에서 제외한다. ID로 찾지 못한 경우에만 `(source, upstream_id, revision)`으로 조회한다. 이 경로에서는 최상위 `event_id`와 `detected_at`을 제외한 저장 스냅숏의 모든 필드를 비교한다. 일치하면 기존 행을 보존하고 변경 없음으로 집계한다. 기존 입력 검증, 소스별 트랜잭션, sync 결과 키를 유지한다.
4. 임시 monitor home의 `state.db`와 이벤트 JSON, 임시 캘린더 DB로 두 재관측 경로와 새 예고가 섞인 `synchronize`를 검증한다. 새 예고의 import, 기존 예고의 unchanged 집계, `failed=0` 및 입법예고 본문 저장을 확인한다. 본문 단계의 외부 I/O는 대역으로 대체한다.
5. 관련 회귀 시험과 기본 테스트 전체(단위·계약·통합·대역 E2E), 린트·타입·빌드 등 CI 검사 항목을 검증한다. `e2e_live`, `models`, 품질 평가는 실행 안 함으로 기록한다.

## 운영 반영 시 확인

개발 체크아웃의 Git 객체로 운영 기준 `8b523f0`부터 PR까지의 변경 파일과 서비스 시작점의 import 경로를 검토하고 PR에 재시작 판단을 기록한다. 이번 작업의 검증은 격리 개발 체크아웃과 임시 DB에서 진행한다. 운영 반영 시에는 코드만 배포하고 기존 등록 이벤트를 사용한다. 다음 06:30 정기 실행에서 첫 실행의 입법예고 16건을 기준으로 신규 3건 import와 변경 없음 13건, 본문 동기화와 캘린더 점검의 “이상 없음”을 확인한다.
