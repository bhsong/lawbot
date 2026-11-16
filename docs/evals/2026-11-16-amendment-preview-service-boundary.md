# PR #158 개정 미리보기 서비스 경계 검증

- 2026-11-16 KST. PR #158 `ec8c507` 이후 변경을 검증했다. [계획](../archives/amendment-preview-service-boundary.md)
- Red에서 기존 monitor CLI가 `--output-dir`을 지원하지 않아 5개 테스트가 실패했다. Green에서 캘린더 CLI를 별도 프로세스로 실행해 타법 부칙의 공포일 시행과 잘못된 날짜의 `conflict` 보류가 미리보기와 실제 import에서 일치하는지 확인했다. monitor DB·원문·파일 목록이 그대로였고, 재시도 요청 수와 같은 임시 폴더를 사용하는 재실행도 확인했다.
- 루트 `pyproject.toml`, monitor `pyproject.toml`, `uv.lock`이 `main` `a7786a4`와 일치했다. 캘린더 import를 차단한 Python 프로세스에서 monitor CLI가 시작됐다. `uv sync --locked --offline --inexact`가 성공했다.

## 11월 10일 공포 개정 재검증

개발용 monitor·캘린더 DB를 각각 임시 폴더에 복사했다. 기존 로컬 Legalize 객체로 monitor에서 이벤트와 원문을 내보내고, 독립 캘린더 가상 환경에서 판정했다.

```sh
.venv/bin/law-monitor --home "$MONITOR_HOME" replay-amendment --repo-path "$LEGALIZE_REPO" \
  --path "$LAW_PATH" --commit "$COMMIT" --dry-run --output-dir "$PREVIEW_DIR"
services/law-calendar/.venv/bin/python -m law_calendar.manage preview "$EVENT_JSON"
```

| 법령 | 커밋 | 조문 | 시행일 | 판정 | 근거 |
| --- | --- | --- | --- | --- | --- |
| 남녀고용평등법 시행령 | `9b89703510` | 제3조·제15조·제22조 | 2026-11-27 | 모두 자동 공개 | `simple_addendum` |
| 임금채권보장법 시행령 | `22b6bd0ee3` | 제13조·제16조 | 2026-11-10 | 모두 자동 공개 | `simple_addendum` |

조문 5개의 결과가 `ec8c507`에 게시된 PR 표와 일치했다. 기존 법제처 본문 API는 각각 1회, 총 2회 호출했다. DB 사본 두 개의 전후 해시가 일치했고 monitor home에는 추가 파일이 없었다. [공식 경로와 허용 범위 확인](2026-11-16-scope-expansion-baseline-review.md#11월-10일-공포-개정-dry-run)

## 전체 검증

| 항목 | 결과 |
| --- | --- |
| 단위·계약·통합·대역 E2E | `uv run --no-sync pytest` 2,071개 통과, 기본 설정 제외 13개. 통합 7개, 대역 E2E 53개 포함 |
| 정적 검사 | Ruff 린트·포맷, Pyright 및 독립 서비스 타입 검사, `compileall`, 캘린더 스키마 검사 통과 |
| 프론트 | 캘린더 31개, 챗봇 55개 통과. 두 서비스의 타입 검사와 빌드 통과 |
| 실제 모델 E2E | 실행 안 함 |
| 모델 스모크 | 실행 안 함 |
| 품질 평가 | 실행 안 함 |
