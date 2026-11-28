# 캘린더 오래된 이력 테스트의 CI 간헐 실패 수정

## 문제

`services/law-calendar/tests/test_calendar_ui.py::test_date_sheet_skips_stale_date_in_both_history_directions`가 CI에서 연속 두 번 실패했다(PR #208 첫 실행, PR #209). 로컬에서는 단독 30회 모두 통과했다. CI의 실제 주소는 기대한 `…#after`가 아니라 시작 주소 `…?fixture=calendar#calendar`였다.

## 원인

날짜 시트와 상세 이력은 `pushState`로 같은 주소에 쌓인다. 그래서 시작 이력, 시트, 상세 이력의 주소가 모두 `#calendar`로 같다. 테스트는 뒤로 가기 뒤 `expect(page).to_have_url(base)`로 시작 이력에 도착했다고 보지만, 이 확인은 화면이 오래된 시트·상세 이력을 `history.go(-1)`로 건너뛰는 도중에도 바로 통과한다. 건너뛰기가 끝나기 전에 `go_forward`를 부르면 진행 중인 이동과 겹쳐 시작 이력에 머문다. 화면 코드의 문제가 아니라 테스트가 "건너뛰기 완료"를 기다리지 않는 문제다. 같은 확인 방식을 쓰는 `test_date_sheet_back_skips_stale_layers_from_unpositioned_hash`(339줄)도 같은 위험이 있다.

## 변경

- 두 테스트에서 주소 대신 이력 상태로 도착을 확인한다. 시작 이력에는 캘린더 이력 표식이 없으므로 `page.wait_for_function("() => !history.state?.lawCalendar", timeout=3000)`처럼 기다린다(Navigation API가 없는 변형에서도 동작). 기존 주소·대화상자 확인은 유지한다.
- 같은 파일에서 주소가 같은 이력 사이를 오가며 주소만으로 도착을 판단하는 다른 곳이 있으면 같은 방식으로 고친다. 화면 코드는 바꾸지 않는다.

## 검증

- Red: 수정 전 테스트가 실패하는 것을 재현한다. 커밋하지 않는 임시 방법(예: CDP `Emulation.setCPUThrottlingRate`로 느리게 하거나 반복 실행)으로 재현하고, 수정 후 같은 조건에서 통과하는지 확인한다. 재현하지 못하면 그 사실과 시도한 방법을 기록한다.
- 캘린더 브라우저 테스트 반복 실행, 전체 기본 테스트, 린트, pyright.

## 실행 기록

착수 시 Navigation API를 끈 변형에는 이미 시작 URL과 `history.state === null`을 함께 기다리는 코드가 있었다. 두 테스트 모두 시작 URL과 `history.state.lawCalendar` 표식의 부재를 기다리도록 맞췄다. 기존 화면 확인과 URL 단언은 유지했다.

[Red 재현과 검증 결과](../evals/2026-11-28-calendar-stale-history-flake.md)
