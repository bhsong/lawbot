# 국회 입법예고 PDF 미리보기 제거 검증

- 2026-11-13 KST. 기준 커밋 `58707fe`, 개발 브랜치 `fix/remove-assembly-pdf-preview`. [구현 계획](../plans/remove-assembly-notice-pdf-preview.md)
- 기존 동작에서 ‘정기 sync 중 PDF 요청 0건’ 시험이 실패했다. 상세 HTML을 읽은 뒤 PDF를 요청하는 기존 호출이 원인이었다. 수정 뒤 정기 sync와 수동 `notice-content --refresh` 모두 상세 HTML만 요청하고 같은 공식 PDF 링크를 제공했다.

| 항목 | 결과 |
| --- | --- |
| 관련 Python 회귀 | `233 passed, 14 deselected in 6.89s`. 캘린더·관리자 API, v5·v6·v7 업그레이드, 이미지 경로 404, 공식 링크 유지, 실패한 마이그레이션의 트랜잭션 복구 포함. |
| 전체 기본 pytest | `1881 passed, 13 deselected in 149.90s`. 단위·계약·통합·대역 E2E 포함. 공개 캘린더에서 390px·1280px 다이얼로그에 공식 링크를 표시하고 열 때 PDF 요청이 없음을 확인했다. |
| 화면 | 챗봇 Vitest 55건과 캘린더 Vitest 26건, 두 화면 타입 검사·빌드 통과. 챗봇 빌드에 기존 Vite 설정 경고가 출력됐다. |
| 정적 검사 | Ruff·포맷, Pyright, 캘린더 스키마, compileall과 루트·캘린더·ERP·수집 설정 잠금 파일 검사 통과. |
| 운영 DB 임시 사본 | v7 → v8에서 이미지 639개 제거. 두 번째 실행은 무변경. 공개 일정 322건, 본문·링크 137건, 검토 기록 144건, 시도 이력 25건의 내용 해시가 사본 작업 전후 일치했다. `quick_check` 정상, 외래 키 오류 0, WAL 체크포인트 정상. 임시 백업에는 이전 이미지 639개가 있음을 확인했다. |
| 실제 모델 E2E·모델 스모크·품질 평가 | 실행 안 함. |

검증에 사용한 운영 원본은 읽기 전용으로 열었다. 운영 원본 스키마 v7과 이미지 639개가 유지됐음을 확인했고, 임시 사본과 검증용 백업을 정리했다. 실제 모델 및 국회 PDF 요청은 0건이다.

## 재현 명령

기존 개발 의존성과 두 화면 빌드를 사용한다. 루트 환경에 독립 수집 설정 서비스가 설치되어 있지 않아 테스트에 소스 경로를 지정했다.

```fish
uv --no-cache run --no-sync pytest -q --tb=short -m 'not e2e_stub' services/law-calendar/tests services/chatbot/tests/test_admin_api.py
env PYTHONPATH=services/source-hub/src uv --no-cache run --no-sync pytest -q --tb=short
uv --no-cache run --no-sync ruff check
uv --no-cache run --no-sync ruff format --check
uv --no-cache run --no-sync pyright
uv --no-cache run --no-sync pyright services/law-calendar/src services/erp-updates/src services/source-hub/src
uv --no-cache run --no-sync python -m compileall -q services packages tools
uv --no-cache run --no-sync python -m law_calendar.schema --check
uv --no-cache lock --check --offline
for project in law-calendar erp-updates source-hub
    uv --no-cache lock --check --offline --project services/$project
end
for frontend in services/chatbot/frontend services/law-calendar/frontend
    pushd $frontend
    npm run typecheck; and npm test; and npm run build
    popd
end
```
