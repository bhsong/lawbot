# 승인 소식이 있는 ERP 공개 스냅샷 검증

2026-11-13 KST. 기준 `0e8de61`. [수정 계획](../plans/erp-snapshot-news-serialization.md)

| 확인 | 결과 |
| --- | --- |
| Red | 임시 캘린더 DB의 승인 법령과 기관 소식이 들어간 캡처 CLI에서 `TypeError: Object of type NewsItem is not JSON serializable` 재현. |
| Green | 캡처 CLI가 JSON 파일을 만들고 두 소식의 날짜와 공식 링크를 보존했다. 관련 캘린더·통합 수집 테스트 17건 통과. |
| 운영 데이터 읽기 전용 재현 | 운영 ERP 사본·캘린더 조회에서 공개 ERP 6건의 ID와 해시 일치, v2 기준 항목 6건, 법령 소식 10건을 캡처했다. 운영 공개 파일·DB·일정에 쓰지 않았다. |
| 기본 전체 | `1976 passed, 13 deselected in 156.40s`. 단위·계약·통합·브라우저 대역을 포함한다. |
| 정적 검사 | Ruff 검사·포맷, 루트 및 독립 캘린더·Source Hub Pyright, compileall, 캘린더 스키마 통과. |
| 실제 호출 | 새 모델 추론·Gmail 발송·기관 실수집은 실행하지 않았다. |

```fish
uv run pytest -q services/law-calendar/tests/test_calendar_news.py services/law-calendar/tests/test_erp_capture_summary.py services/source-hub/tests/test_daily.py
uv run pytest -q --tb=short
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pyright services/law-calendar/src services/source-hub/src
uv run python -m law_calendar.schema --check
```
