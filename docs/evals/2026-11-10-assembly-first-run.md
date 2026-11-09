# 국회 수집기 첫 정기 실행 (2026-11-10)

운영 `v5.2.1` (`8b523f0`)의 06:30 정기 실행이다. 시각은 KST이며 운영 결과는 작업 요청에 제공된 로그와 DB 대조 기록을 옮겼다. [수정 계획](../plans/calendar-assembly-redetect.md)

## 실행 기록

| 항목 | 결과 |
| --- | --- |
| 수집 (06:30:05–06:30:41) | `collectors=4 ok=4 failed=0 events=25` (법률안 9건, 입법예고 16건) |
| 캘린더 점검 | `calendar sync imported=0 unchanged=0 failed=1 skipped=0 assembly_bill_imported=9 assembly_bill_unchanged=0 assembly_bill_failed=0 assembly_notice_failed=1` |
| 조문 점검 | `articles updated=no same=yes changed=0 pending=159 next=2026-11-27 new=0` |
| 요약 (06:32:28 시작) | `summaries/2026-11-10.md` 국회 칸 25건. 조세특례제한법 다수, 고용보험법·소득세법·보험료징수법·지방세법·국민건강보험법·국민연금법·부가가치세법 |
| monitor 종료 코드 | `1` |
| 웹 `/` | HTTP 200 |

## 캘린더 점검 실패 원인

- 11월 9일 `assembly_notice` bootstrap은 진행 중인 입법예고를 revision 0으로 기록했다. 11월 10일 첫 정기 실행에서 16건 모두 `assembly_notice_observed`(revision 1)로 발행됐다.
- 그중 10건은 11월 6~7일 일괄 이관으로 `assembly_snapshots`에 같은 `event_id`로 저장돼 있었다. 제공된 읽기 전용 DB 대조 기록에 따르면 10건 모두 저장된 `event_json`과 새 이벤트의 차이는 `detected_at` 하나였다.
- `import_assembly`에서 `Registered Assembly snapshot was altered`가 발생해 입법예고 쓰기 트랜잭션 전체가 롤백됐다. 신규 스냅숏 저장과 `synchronize_notice_content`가 모두 막혔다.
- `sync.registrations`는 커서 없이 매일 등록된 이벤트를 다시 읽는다. 동일한 10건 때문에 수정 전까지 입법예고 import가 반복 실패한다.

## PR #106 리뷰의 DB 사본 재현

리뷰에서 제공된 재현 결과에 따르면 같은 `event_id`의 10건은 초기 수정의 비교를 통과했다. 다른 `event_id` 3건은 `assembly_snapshots`의 `UNIQUE(source, upstream_id, revision)` 제약에 걸려 전체 import가 다시 롤백됐다. 이 3건은 이관 행과 소스·항목·revision이 같았다. 국회 수집기의 해시 입력인 `target.committee_id`만 새로 채워져 ID가 바뀌었다. 이 필드는 캘린더 스냅숏에 저장하지 않으므로 검증·저장 대상 내용은 같았다. [PR 리뷰](https://github.com/bhsong/lawbot/pull/106#pullrequestreview-5833664892)

| 첫 정기 실행의 입법예고 16건 | 건수 | 수정 후 기대 집계 |
| --- | ---: | --- |
| 같은 `event_id`, `detected_at`만 다름 | 10 | `unchanged` |
| 다른 `event_id`, 같은 소스·항목·revision과 저장 필드 | 3 | `unchanged` |
| 새 스냅숏 | 3 | `imported` |

기존 16건 기준으로 신규 3건과 변경 없음 13건을 기대한다. 기존 스냅숏 행을 보존하며, 두 재관측 경로에서 저장 내용 차이가 있으면 해당 소스 트랜잭션을 롤백한다.

## 개발 검증

- 최초 수정 전에는 관측 시각만 달라진 법률안·입법예고에서 `ValueError`를 재현했다. 초기 PR에서는 다른 ID로 재관측된 동일 항목·revision의 법률안·입법예고와 sync 시험에서 `IntegrityError`를 재현했다.
- 리뷰 반영 후 관련 시험 48개가 통과했다. 두 재관측 경로의 원본 행 보존과 저장 필드 불일치 시 소스 전체 롤백을 확인했다. sync 시험에서 `failed=0`, `assembly_notice_imported=1`, `assembly_notice_unchanged=2`와 새 예고 본문의 DB 저장을 확인했다.
- 기본 테스트 1,229개가 통과했다. 단위·계약 시험과 통합 9개, 대역 E2E 36개를 포함한다. 문법·Ruff 린트 및 포맷·Pyright·캘린더 스키마 검사와 두 프런트엔드 타입 검사·단위 시험·빌드도 통과했다.
- `e2e_live`, `models`, 품질 평가는 각각 실행 안 함.

## 수정 후 결과

운영 반영 후 확인 예정
