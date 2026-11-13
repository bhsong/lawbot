# ERP 적용 기준 버전과 API 검증

2026-11-13 KST. 기준 `12a1347`. [작업 계획](../archives/erp-rule-versions.md)

| 항목 | 결과 |
| --- | --- |
| ERP 검토·API 계약 | 신규 테스트 6건 통과. 승인된 공식 원천 리비전과 연결, 미승인판·유사 호스트 거부, 두 범위의 부분 시행, 복수 일정, 초안 분리, v1 바이트 일치를 확인했다. |
| 시점 판정 | 시행일 당일 재확인 전에는 기존 판을 유지하고, 확인 후 새 판을 선택했다. 이전 대상월, 연기·철회, 후속판 재확인, 한시 판 종료와 누락·오류 파일을 검증했다. |
| 설정 확장 | 추가 회귀 2건 통과. 일곱 번째 업무 키에 기존 서식을 공유해 수집, 원문 승인, PDF 대상 지정과 해시, 현행·예정 검토 및 공개 API를 확인했다. 기존 ID와 승인 상태를 보존하고 중복 설정 키를 거부했다. |
| 기본 전체 | `1939 passed, 13 deselected in 155.38s`. 단위, 계약, 통합, Chromium 대역을 포함한다. |
| 프런트엔드와 정적 검사 | 챗봇 Vitest 55건, 캘린더 26건. 양쪽 typecheck·빌드, Ruff 검사·포맷, Pyright, compileall, 생성 스키마와 잠금 파일 확인 통과. |
| 실제 자료·운영 | 합성 자료만 사용했다. 실제 기관 요청, 실제 적용판 등록, 운영 반영, 실제 모델 E2E, 모델 스모크, 품질 평가, 발송은 실행 안 함. |

공개 조회의 과거 기준일은 내보낸 승인 이력에 적용한다. 철회판의 내용은 비공개 ERP 이력에서만 확인한다. 검증된 구조화 수치 비교 대신 각 판의 공식 원문을 제공한다.

```fish
uv run pytest -q services/erp-updates/tests services/law-calendar/tests/test_calendar_erp.py --tb=short
uv run pytest -q --tb=short
uv run python -m law_calendar.schema --check
```
