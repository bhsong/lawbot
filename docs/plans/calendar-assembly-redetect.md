# 국회 이벤트 재관측으로 인한 캘린더 import 실패 수정

2026-11-10 KST · `fix/calendar-assembly-redetect` · 운영 반영 버전 `v5.2.2` (Z, 버그 수정)

## 배경과 목표

11월 10일 06:30 수집은 25건을 기록했지만 입법예고 import가 실패했다. 전날 bootstrap에서 revision 0으로 등록한 진행 중 예고가 첫 정기 실행에 revision 1로 발행됐다. 그중 10건은 11월 6~7일 일괄 이관과 `event_id`가 같았고 `detected_at`만 달랐다. 스냅숏 불일치 오류로 입법예고 트랜잭션 전체가 롤백돼 새 예고 6건의 저장과 본문 동기화가 막혔다. `sync.registrations`는 등록된 이벤트 전체를 매번 읽으므로 다음 정기 실행에서도 같은 충돌이 발생한다. [첫 실행 기록](../evals/2026-11-10-assembly-first-run.md)

## 구현 및 검증

1. 기존 `scheduled` 픽스처와 국회 이벤트 등록 헬퍼를 재사용한다. 입법예고와 법률안을 각각 두 번 가져오고, 두 번째 입력에서 `detected_at`만 바꾼다. 두 번째 결과가 `imported=0, unchanged=1`이고 첫 행의 `collected_at`과 `event_json`이 보존되는지 확인한다. 두 소스에서 기존 코드의 예외를 재현한다.
2. 같은 `event_id`의 `dates.notice_end` 변경은 여전히 `ValueError`를 내고, 동일 트랜잭션에서 먼저 처리한 새 이벤트까지 롤백하는지 확인한다.
3. 임시 monitor home의 `state.db`와 이벤트 JSON, 임시 캘린더 DB를 사용한다. 이관된 예고의 재관측과 새 예고 1건을 `synchronize`에 넘겨 `failed=0`, `assembly_notice_imported=1`, `assembly_notice_unchanged=1` 및 입법예고 본문 동기화 호출을 검증한다. 본문 단계의 외부 I/O는 대역으로 대체한다.
4. `assembly_ingest.import_assembly`의 기존 행 비교에서 최상위 `detected_at`만 제외한다. 일치하면 기존 행을 보존하고 변경 없음으로 집계한다. 기존의 입력 검증, 소스별 트랜잭션, sync 결과 키를 유지한다.
5. 관련 회귀 시험과 기본 테스트 전체(단위·계약·통합·대역 E2E), 린트·타입·빌드 등 CI 검사 항목을 통과한 뒤 PR을 연다. `e2e_live`, `models`, 품질 평가는 실행 안 함으로 기록한다.

## 운영 반영 시 확인

개발 체크아웃의 Git 객체로 운영 기준 `8b523f0`부터 PR까지의 변경 파일과 서비스 시작점의 import 경로를 검토하고 PR에 재시작 판단을 기록한다. 이번 작업의 검증은 격리 개발 체크아웃과 임시 DB에서 진행한다. 운영 반영 시에는 코드만 배포하고 기존 등록 이벤트를 사용한다. 다음 06:30 정기 실행에서 새 입법예고 6건의 import와 캘린더 점검의 “이상 없음”을 확인한다.
