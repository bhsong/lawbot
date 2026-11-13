# 통합 일일 실행과 공개 ERP 스냅샷 검증

2026-11-13 KST. 기준 `2f7094b`. [작업 계획](../plans/source-hub-daily.md)

| 항목 | 결과 |
| --- | --- |
| 통합 실행 | 합성 등록부와 가짜 기관 응답으로 활성화·비활성·차단·정상 0건을 구분했다. 같은 날 재실행에서 추가 수집 0회, 발행 실패 후 완료 수집 단계 재실행 0회, 잠금 경합 시 `busy`를 확인했다. |
| 법령과 챗봇 계약 | 가짜 monitor와 로컬 Git 사본으로 단일 정상 실행 기록, 조문 입력 갱신·캘린더 import·조문 점검·요약 순서, 법령 OFF 후 재활성화, 중단 중 마지막 정상 캘린더 시각 보존을 확인했다. |
| 공개 경로 | 독립 ERP 내보내기와 캘린더 캡처를 임시 파일로 실행했다. Chromium에서 v1·v2·기관 소식의 `X-ERP-Snapshot`이 동일했다. 변조 파일은 `invalid`를 반환했다. |
| 변경과 재발행 | 승인된 원천에서 최신 10건 밖의 신규를 검출하고 기준선과 중복을 제외했다. 당일 재발행은 수집 없이 새 해시를 생성했다. |
| 기본 전체 | `1948 passed, 13 deselected in 153.31s`. 단위, 계약, 통합, Chromium 대역을 포함한다. |
| 프런트엔드 | 챗봇 Vitest 55건, 캘린더 29건 및 양쪽 typecheck·빌드 통과. |
| 정적 검사 | Ruff 검사·포맷, Pyright, compileall, 생성 스키마, uv 잠금 파일 4개와 launchd 템플릿 검사 통과. |
| 외부·운영 | 실제 사이트 수집, 운영 전환, 실제 모델 E2E, 모델 스모크, 품질 평가, 이메일 발송은 실행 안 함. |

운영용 06:30 launchd 예시는 기존 작업과 같은 라벨을 사용한다. 자료가 공개된 뒤 검토 결정이 바뀌면 `daily --republish`를 실행한다. 기존 일일 실행은 보존하며, 최신 해시를 기준으로 공개 파일을 교체한다. [실행 및 전환 방법](../../services/source-hub/README.md#통합-일일-실행)

```fish
uv run pytest -q services/source-hub/tests/test_daily.py services/monitor/tests/test_mon_managed_daily.py services/law-calendar/tests/test_calendar_sync.py
uv run pytest -q services/law-calendar/tests/test_calendar_erp_ui.py -k daily
uv run pytest -q --tb=short
```
