# 캘린더 만료 이력 테스트 간헐 실패 검증

[보관된 계획](../archives/calendar-stale-history-flake.md)

`#calendar`는 시작 이력과 만료된 날짜 시트·상세 이력이 공유한다. 만료 이력에서도 대화상자가 사라지므로 URL과 대화상자 개수만으로는 건너뛰기가 끝났는지 알 수 없다. 두 테스트에서 시작 URL과 `history.state.lawCalendar` 표식 부재를 함께 기다린다. 기존 화면·URL 단언은 유지했고 화면 소스는 변경하지 않았다.

## Red 재현

대상: `services/law-calendar/tests/test_calendar_ui.py::test_date_sheet_skips_stale_date_in_both_history_directions`. 레포 밖 임시 pytest 플러그인으로 시행 횟수를 매개변수화하고, 각 Chromium 페이지에 CDP `Emulation.setCPUThrottlingRate`를 적용했다.

| 조건 | 실패 | 통과 | 관찰 |
| --- | ---: | ---: | --- |
| Chromium CPU 8배 감속만 | 3 | 9 | 기존 단언에서 `#after`를 기대했으나 `?fixture=calendar#calendar`에 머물렀다. CI와 같은 증상. |
| 8배 감속 + 만료 이력의 `history.go(-1)`을 250ms 지연 + `go_forward()` 진입 시 표식 확인 | 8 | 0 | 8회 모두 이전 이력을 다 건너뛰기 전에 `go_forward()`를 요청했다. |

지연 스크립트를 선언만 하고 실행하지 않은 초기 계측은 실패 0회·통과 8회였다. 지연이 적용되지 않아 유효한 지연 실험 집계에서 제외했다.

## 수정 후 검증

| 명령·조건 | 결과 |
| --- | --- |
| `uv run pytest -p flake_probe`로 Red 대상 재실행, CPU 8배 감속만 | 12회 통과, 실패 0회 |
| 같은 명령, CPU 8배 감속 + 250ms 지연 + `go_forward()` 진입 시 표식 확인 | 8회 통과, 실패 0회 |
| `uv run pytest -p flake_probe -q services/law-calendar/tests/test_calendar_ui.py`를 새 pytest 프로세스로 20회 실행 | 일반 19회, CPU 4배 감속 1회. 매회 30개, 총 600개 통과. 실패 0회 |
| `uv run pytest` | 기본 테스트 3,046개 통과, 10개 선택 제외. 단위·계약·통합·대역 E2E 포함 |
| `uv run ruff check` | 통과 |
| `uv run ruff format --check` | 636개 파일 통과 |
| `uv run pyright` | 오류 0, 경고 0 |

실제 모델 E2E, 모델 스모크, 품질 평가는 실행하지 않았다. 운영 트리와 `.env`는 변경하지 않았다. 레포 밖 임시 플러그인, 스크립트, 로그와 Red 진단 자료를 삭제했다.
