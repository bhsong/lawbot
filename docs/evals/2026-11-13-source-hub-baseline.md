# 통합 수집기와 ERP v1.1 착수 기준선

- 확인일 2026-11-13 KST. 개발 HEAD와 원격 `main` 모두 `d34c74987f5bd29a7c46387e9c31414cf943e95c`. 운영 HEAD는 detached `6aae4f657fee521598b9cb218d0e8f95d47464e8`이며 기준선의 조상이다. 두 커밋 사이의 변경은 운영 검증 문서 2개다.
- 개발·운영의 기존 미추적 항목은 보존했다. 테스트는 개발 트리의 기존 패키지와 합성 자료를 사용했다. 실제 추론, 기관 수집, 첨부 다운로드, 외부 발송은 각각 0회다. [구현 계획](../plans/source-hub-erp-v1.1.md)

## 표 A. 원천별 접근

| 원천 | 이번 검증 | 남은 실측 |
| --- | --- | --- |
| 기존 Legalize KR·국회 | 합성 Git·API 응답과 실행 기록으로 수집 및 캘린더 sync 계약 확인 | 현재 운영 소스별 수집 시각·범위 및 실조회 |
| 기존 ERP 6종 | 합성 사본에서 자료 선택, 읽기 전용, 동일판, 승인, 파일 등록·해시, 공개 계약 확인 | 운영 기록은 2026-11-12 별도 기록 참조 |
| 국가법령정보센터 | 첨부의 공식 문서 경로와 기존 ERP 원천 경계 확인 | 변경이력 API 범위·인증·실제 목록·최신 10건 |
| 고용노동부 | 첨부의 RSS·게시판 후보 검토 | 정책·RSS XML·ID 매핑·목록 대조·첨부 |
| 재정경제부 | 첨부의 RSS·게시판 후보 검토 | 정책·세션 없는 RSS URL·목록 대조·첨부 |
| 국세청 | 첨부의 공개 게시판 후보 검토 | 정책·목록/상세·연말정산 안내책자·첨부 |
| 홈택스 | 첨부의 공개 검색/목록 폴백 검토 | 정책·비로그인 메타·개별 링크·최신성 |

과거 운영 결과 [ERP 원천 및 PDF 확인](2026-11-12-erp-sources.md) · [ERP 탭 운영 반영](2026-11-12-deploy-v5.7.0.md)

## 표 B. 기능 검증

| 시험 | 실제 결과 | 상태 |
| --- | --- | --- |
| 관련 Python 테스트 | `108 passed in 8.60s`. monitor 점검·요약·실행, 기존 ERP 피드, 캘린더 sync와 법령·ERP API. 테스트 모델과 외부 API는 대역 사용. | PASS |
| 캘린더 TypeScript·Vitest | 타입 검사 종료 코드 0, `Test Files 7 passed (7)`, `Tests 26 passed (26)`. | PASS |
| 기존 ERP 브라우저 대역 E2E | 기본 샌드박스에서 Chromium Mach port 등록 권한 오류로 설정 단계 2건 오류. 브라우저 실행 권한으로 같은 대역을 재실행해 `2 passed in 1.64s`. 개발 루프백과 합성 DB·피드를 사용. | PASS (재실행) |
| 전체 기본 pytest·CI 검사 | 위의 대상 시험만 실행했다. 전체 기본 pytest, 전체 정적 검사, 양쪽 프런트엔드 새 빌드, 챗봇 대역 E2E는 실행 안 함. | NOT_RUN |
| 신규 ON/OFF·등록부·수집·피드·알림·분리 | v1.1 구현 승인 이후 시험. | NOT_RUN |
| 실제 모델 E2E·모델 스모크·품질 평가 | 실행 안 함. | NOT_RUN |

실행한 테스트의 명령은 아래와 같다. 초기 Python 실행에서 기본 uv 캐시 쓰기 권한 오류가 발생해 `--no-cache`로 재실행했다. fish 호환 명령이며 이번 실행 셸은 zsh였다.

```fish
uv --no-cache run --no-sync pytest -q services/monitor/tests/test_mon_checks.py services/monitor/tests/test_mon_summary.py services/monitor/tests/test_mon_runner.py services/erp-updates/tests/test_erp_updates.py services/law-calendar/tests/test_calendar_sync.py services/law-calendar/tests/test_calendar_api.py services/law-calendar/tests/test_calendar_erp.py
uv --no-cache run --no-sync pytest -q --tb=short -m e2e_stub services/law-calendar/tests/test_calendar_erp_ui.py
pushd services/law-calendar/frontend
npm run typecheck
npm test
popd
```

## 표 C. 일일 배포 실측

신규 운영 일일 실행, 공개 스냅샷 비교 및 시험·실수신자 발송은 실행 안 함.

## 표 D. v1.1 카드·드로어

| 시험 | 실제 결과 | 상태 |
| --- | --- | --- |
| 기존 ERP 탭·수집 이력·관련 일정과 피드 장애 격리 | 브라우저 대역 2건 통과. | PASS (기존 계약) |
| 현행과 미래 확정판, 복수 시행·정정, 초안 구분 | 구현 전. | NOT_RUN |
| 기관별 최신 10건, 원문, 호버·모바일·키보드 | 구현 전. | NOT_RUN |
| 채널별 미래 변경과 화면 스냅샷 일치 | 구현 전. | NOT_RUN |
