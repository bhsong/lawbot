# ERP 기존 승인 카드 표시 검증

2026-11-13 KST. 기준 `67c4086`. [수정 계획](../plans/erp-approved-card-fallback.md)

- 운영 읽기 전용 확인에서 ERP v1은 승인 자료 6건, v2는 적용 범위와 검토 링크가 없는 원천 키 6개였다. 새 화면에서 기존 자료가 접혀 보이는 상태를 재현했다.
- Vitest에서 기존 승인 자료 본문·일자·PDF 표시, v1과 실제 v2가 섞인 경우의 중복 방지, 부분 적용 재확인·예정·검토 링크 및 v2 오류 경로를 검증했다. 캘린더 30건, 챗봇 55건과 양쪽 typecheck 및 빌드가 통과했다.
- 운영 공개본 사본과 기존 캘린더 백업을 사용한 개발 Chromium 390px에서 기존 승인 카드 6개, 공식 원문 링크 6개, PDF 링크 표시와 빈 현행 경고 0개를 확인했다. 공개 스냅샷 해시는 일치했다.
- 기본 전체 검사 `1976 passed, 13 deselected in 156.30s`. 단위·계약·통합·브라우저 대역을 포함한다.
- Ruff, 루트와 독립 서비스 Pyright, 생성 스키마, compileall, 잠금 파일 4개 검사를 통과했다. 운영 데이터 쓰기, 실제 모델 E2E·모델 스모크·품질 평가, 외부 수집·발송은 실행하지 않았다.

```fish
cd services/law-calendar/frontend
npm run typecheck
npm test
npm run build
cd ../../..
uv run pytest -q services/law-calendar/tests/test_calendar_erp_ui.py
uv run pytest -q --tb=short
```
