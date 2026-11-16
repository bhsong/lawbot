# PR #158 리뷰 반영 검증

- 2026-11-16 KST. 사용자 결정에 따라 실제 제정(`law_new`)과 개정 중 신설 조문에 기존 자동 승인 규칙을 적용했다. 캘린더 규칙과 파서 버전은 `effective-date-v7`과 `monitor-law-v7`이다. 새 감시 파일의 이벤트 생성 방지는 monitor에서 담당한다. [계획](../archives/scope-expansion-baseline-review.md)
- `d9e43e6`의 Red에서 강제 푸시와 범위 확장 동시 발생, `state.db` 없는 home의 ID 충돌, 법제처 context 누락, 미리보기 판정·날짜 오류 사유 누락, 제정·신설 자동 공개 제한으로 신규·수정 테스트 7개가 실패했다.
- Green에서 이전 감시 범위의 개정·제정은 대체 비교로 수집되고 새 감시 경로 1개는 기준점으로 저장됐다. 실제 제정 및 개정의 신설 조문은 부칙 시행일에 자동 공개됐다. 철회·거절 상태는 동일 이벤트와 정정 원천을 다시 넣어도 유지됐다.
- 재생성의 법제처 context가 일일 수집과 같았다. 타법 부칙의 공포일 시행은 미리보기와 import에서 같은 날짜로 공개됐고, 존재하지 않는 날짜는 양쪽에서 `conflict`로 보류됐다. 요청 수에 재시도도 포함되는지 확인했다.

## 11월 10일 공포 개정 dry-run

`.venv/bin/law-monitor --home "$MONITOR_HOME" replay-amendment --repo-path "$LEGALIZE_REPO" --path "$LAW_PATH" --commit "$COMMIT" --dry-run`을 두 건에 실행했다. 기존 로컬 Legalize 객체와 개발용 monitor·캘린더 DB 사본을 사용했다. 두 DB의 전후 해시가 일치했고 monitor home에 추가 파일은 0개였다.

| 법령 | 커밋 | 조문 | 시행일 | 판정 | 근거 |
| --- | --- | --- | --- | --- | --- |
| 남녀고용평등법 시행령 | `9b89703510` | 제3조 | 2026-11-27 | 자동 공개 | `simple_addendum` |
| 남녀고용평등법 시행령 | `9b89703510` | 제15조 | 2026-11-27 | 자동 공개 | `simple_addendum` |
| 남녀고용평등법 시행령 | `9b89703510` | 제22조 | 2026-11-27 | 자동 공개 | `simple_addendum` |
| 임금채권보장법 시행령 | `22b6bd0ee3` | 제13조 | 2026-11-10 | 자동 공개 | `simple_addendum` |
| 임금채권보장법 시행령 | `22b6bd0ee3` | 제16조 | 2026-11-10 | 자동 공개 | `simple_addendum` |

2026-11-16에 기존 공식 API의 법령 본문 제공 범위와 두 공식 호스트의 robots.txt를 확인했다. 일일 수집에 쓰는 `GET /DRF/lawService.do` 본문 요청을 건당 1회씩 순서대로 보내 실제 요청은 총 2회였다. 첨부 다운로드·재시도·리디렉션은 0회였다. [공식 API 안내](https://open.law.go.kr/LSO/openApi/guideList.do) · [이용 조건](https://open.law.go.kr/LSO/usrJoin.do) · [www 호스트 robots.txt](https://www.law.go.kr/robots.txt) · [기본 호스트 robots.txt](https://law.go.kr/robots.txt)

## 전체 검증

| 항목 | 결과 |
| --- | --- |
| 단위·계약·통합·대역 E2E | `uv run --no-sync pytest` 2,070개 통과, 기본 설정 제외 13개. 통합 7개, 대역 E2E 53개 포함 |
| 정적 검사 | `ruff check`, `ruff format --check`, `pyright` 및 독립 서비스 타입 검사, `compileall`, 캘린더 스키마 검사 통과 |
| 환경 | `uv sync --locked --offline --inexact` 성공. monitor에서 캘린더 판정 모듈을 사용하는 의존성을 lockfile로 관리 |
| 프론트 | 캘린더 31개, 챗봇 55개 통과. 두 서비스의 타입 검사와 빌드 통과 |
| 실제 모델 E2E | 실행 안 함 |
| 모델 스모크 | 실행 안 함 |
| 품질 평가 | 실행 안 함 |
