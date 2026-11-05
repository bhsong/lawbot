# 국회 수집기 강제 bootstrap 회귀 검증

- 실행일 2026-11-05 KST · PR #73 리뷰 후속 · 수정 전 `bbfdf4f`
- 기존 의안·예고를 갱신하지 않은 분기를 수정했다. 강제 bootstrap은 관측 순번을 유지하고 현재 비교 스냅샷을 저장한다. 이전에 발행한 예고는 같은 수집 과정에서 공식 상세 기간을 확인하거나 종료일이 같은 기존 증거를 보존한다.

| 검사 | 실행과 결과 |
| --- | --- |
| 수정 전 재현 | `pytest -q --tb=short services/monitor/tests/test_mon_assembly.py -k force_bootstrap`. 기존 의안 정정, 진행 예고 기간 정정, 종료 예고 기간 정정 세 건에서 다음 동일 수집이 이벤트 한 건씩을 발행해 실패 |
| 수정 후 회귀 | `pytest -q services/monitor/tests/test_mon_assembly.py`. 9개 통과. 강제 bootstrap과 그 직후 같은 응답에서 이벤트 0건, 이후 의안 정정에서 순번 2의 이벤트 1건과 갱신된 이전 스냅샷 확인 |
| 기본 전체 | `pytest -q --tb=short`. 단위·계약·통합·대역 E2E 포함 852개 통과, 선택 실행 13개 제외, 68.84초 |
| 정적 검사 | Ruff 린트·포맷, compileall, 루트·캘린더 Pyright, 생성 스키마, 두 uv 잠금 검사 통과 |
| 국회 실제 API·실제 모델 E2E·모델 스모크·품질 평가 | 후속 실행 안 함. 원천 계약과 화면 연결의 [기존 검증](2026-11-05-assembly-law-calendar.md)은 최초 PR 구현 기준 |

공식 상세 응답이 강제 bootstrap 때 확인되지 않고 이후에 확인되면, 그때 새로 검증한 시작일은 정상 수집에서 이벤트가 될 수 있다. 초기 진행 예고의 첫 관측 발행은 기존과 같다.
