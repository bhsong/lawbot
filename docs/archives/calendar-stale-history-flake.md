# 캘린더 오래된 이력 테스트의 CI 간헐 실패 수정

## 문제

`services/law-calendar/tests/test_calendar_ui.py::test_date_sheet_skips_stale_date_in_both_history_directions`가 CI에서 연속 두 번 실패했다(PR #208 첫 실행, PR #209). 로컬에서는 단독 30회 모두 통과했다. CI의 실제 주소는 기대한 `…#after`가 아니라 시작 주소 `…?fixture=calendar#calendar`였다.

## 원인

날짜 시트와 상세 이력은 `pushState`로 같은 주소에 쌓인다. 그래서 시작 이력, 시트, 상세 이력의 주소가 모두 `#calendar`로 같다. 첫 번째 테스트는 뒤로 가기 뒤 `expect(page).to_have_url(base)`로 시작 이력에 도착했다고 보지만, 이 확인은 화면이 오래된 시트·상세 이력을 `history.go(-1)`로 건너뛰는 도중에도 바로 통과한다. 건너뛰기가 끝나기 전에 `go_forward`를 부르면 진행 중인 이동과 겹쳐 시작 이력에 머문다. CI에서 재현된 원인은 첫 번째 테스트가 건너뛰기 완료를 기다리지 않은 것이다. `test_date_sheet_back_skips_stale_layers_from_unpositioned_hash`는 기준 커밋 `8a8d200`에서 이미 시작 URL과 `history.state === null`을 함께 기다렸으므로 이 원인에 해당하지 않는다.

## 변경

- `test_date_sheet_skips_stale_date_in_both_history_directions`에서 시작 URL과 `history.state.lawCalendar` 표식의 부재를 함께 기다린 뒤 `go_forward()`를 호출한다. 기존 URL·대화상자 단언은 유지한다.
- `test_date_sheet_back_skips_stale_layers_from_unpositioned_hash`는 기준 커밋의 시작 URL과 `history.state === null` 대기 조건을 유지한다. 화면 코드는 바꾸지 않는다.

## 검증

- Red: 수정 전 테스트가 실패하는 것을 재현한다. 커밋하지 않는 임시 방법(예: CDP `Emulation.setCPUThrottlingRate`로 느리게 하거나 반복 실행)으로 재현하고, 수정 후 같은 조건에서 통과하는지 확인한다. 재현하지 못하면 그 사실과 시도한 방법을 기록한다.
- 캘린더 브라우저 테스트 반복 실행, 전체 기본 테스트, 린트, pyright.

[Red 재현과 검증 결과](../evals/2026-11-28-calendar-stale-history-flake.md)
