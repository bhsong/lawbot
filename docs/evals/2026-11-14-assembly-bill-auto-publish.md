# 국회 법률안 자동 공개 검증

2026-11-14 KST. 기준 `main` `16c2533`. [구현 계획](../plans/assembly-bill-auto-publish.md)

- 공식 의안 API의 저장 관측값으로 의안 식별자·원문 링크·발의 또는 의결 날짜·처리 상태를 검증한다. 통과한 기록은 공개 캘린더의 법률안 유형으로 표시되며 `bill-auto` 검토 이력을 남긴다. 날짜나 상태 근거가 부족한 기록은 보류되고 수동 반려·철회는 새 판에도 유지된다.
- 운영 캘린더 DB를 읽기 전용으로 열어 임시 사본에서 재현했다. 대기 62건이 공개 승인됐으며 11월 14일 수집한 26건이 모두 포함됐다. 발의와 의결이 다른 날인 의안 1건을 포함해 공개 날짜 행은 63개였다. 같은 동기화 두 번으로 자동 승인 이력은 총 62건이었다. 운영 원본은 대기 62건이었다.
- 법제처 `www.law.go.kr`과 `open.law.go.kr`의 `robots.txt`는 08:14 KST에 각각 점검 페이지로 HTTP 302를 반환했다. 06:30의 비JSON 응답도 점검 영향일 가능성이 높다. 당시 응답 본문이 없어 직접 확인할 수 없다. 기존 법제처 코드가 실패 때 Git 커서를 보존하고 정상 응답이 돌아오면 다음 수집에서 재처리하는 것을 대역으로 확인했다. 인증 API 재호출과 운영 작업 재실행은 0회다. [법제처 개편 발표](https://www.korea.kr/briefing/pressReleaseView.do?newsId=156789202)
- 기존 ERP 확장 회귀 테스트에서 과거 기준일을 조회하면서 실행 당일을 검토 시각으로 사용해 발생한 날짜 의존 실패를 재현했다. 합성 테스트 검토 시각을 고정했다.

| 검사 | 결과 |
| --- | --- |
| 법률안 자동 공개 및 기존 법제처 재처리 계약 | 20건 통과 |
| 기본 pytest 전체 | 1,982건 통과, 기본 제외 13건, 157.76초. 단위·계약·통합·Chromium 대역 E2E 포함 |
| Ruff 검사·포맷, Pyright, 캘린더 스키마, compileall | 통과 |
| 캘린더·챗봇 프런트엔드 typecheck, Vitest, 빌드 | 통과. 캘린더 30건, 챗봇 55건 |
| 실제 모델 E2E, 모델 스모크, 품질 평가 | 실행 안 함 |

```fish
.venv/bin/pytest -q --tb=short services/monitor/tests/test_mon_lawgo.py::test_retry_then_success_and_failure_keeps_cursor services/law-calendar/tests/test_calendar_assembly.py
.venv/bin/pytest -q --tb=short
```
