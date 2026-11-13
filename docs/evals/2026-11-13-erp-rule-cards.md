# ERP 현행·예정 카드 검증

2026-11-13 KST. 버전 판정과 API PR #136의 `996f1d3` 기준. [작업 계획](../plans/erp-rule-versions.md)

| 항목 | 결과 |
| --- | --- |
| Vitest | 캘린더 28건, 챗봇 55건 통과. 추가 사례는 현행·예정 동시 카드, 동일 업무 항목 중복 방지, 복수 변경·부분 적용·검토 중 링크, v2 오류 시 기존 원문·이력 보존을 확인했다. |
| Chromium 대역 | ERP 브라우저 테스트 4건 통과. 실제 캘린더 서버와 합성 파일로 모바일 390px, 키보드 타임라인 펼치기, 기존 PDF 원문 링크, 법령 탭 전환을 확인했다. |
| 기본 전체 | `1938 passed, 13 deselected in 152.89s`. 단위, 계약, 통합, Chromium 대역을 포함한다. |
| 정적 검사 | 양쪽 typecheck·빌드, Ruff 검사·포맷, Pyright, compileall, 생성 스키마 검증 통과. |
| 실제 자료·운영 | 합성 자료만 사용했다. 실제 적용판 등록, 운영 반영, 실제 모델 E2E, 모델 스모크, 품질 평가, 발송은 실행 안 함. |

v2를 구성하면 확인된 적용판만 현행·예정으로 표시하고 기존 자료는 공식 원문 참고로 제공한다. 미구성 환경에는 기존 화면을 제공한다. [캘린더 설정](../../services/law-calendar/README.md#erp-적용-기준-v2)

```fish
uv run pytest -q services/law-calendar/tests/test_calendar_erp_ui.py --tb=short
uv run pytest -q --tb=short
npm --prefix services/law-calendar/frontend run typecheck
npm --prefix services/law-calendar/frontend test
npm --prefix services/law-calendar/frontend run build
```
