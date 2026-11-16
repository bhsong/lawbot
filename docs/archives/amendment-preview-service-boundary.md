# PR #158 개정 미리보기 서비스 경계 복원

- 2026-11-16 KST. PR #158 `ec8c507`, 기준 `main` `a7786a4`. [이전 리뷰 반영](../archives/scope-expansion-baseline-review.md)
- monitor의 캘린더 패키지 의존성과 루트 lockfile 변경을 되돌린다. monitor와 캘린더는 이벤트 JSON과 상대 경로의 신·구 원문을 교환한다.

## Red → Green

1. `law-monitor replay-amendment --dry-run --output-dir`에 임시 폴더를 지정한다. 기존 `data/replay/amendments/<부모>_<커밋>` 규칙으로 이벤트와 원문을 자체 포함하는 폴더에 내보내고, 이벤트 경로와 실제 법제처 요청 수만 출력한다. monitor home의 상태·원문·잠금 파일은 별도로 검증한다.
2. 독립 캘린더 CLI의 `preview <이벤트 JSON>`에서 기존 `preview_changes`로 조문별 시행일·공개 여부·근거를 계산한다. preview에는 DB가 필요 없고, DB를 쓰는 기존 하위 명령은 `--database`를 요구한다.
3. monitor CLI 결과를 캘린더 CLI 프로세스에 넘겨 타법 부칙의 공포일 시행과 잘못된 날짜의 보류를 실제 import와 대조한다. 기존 로컬 Legalize 객체와 법제처 공식 본문 API로 11월 10일 공포 개정 2건을 다시 미리 보고 PR 표와 비교한다.
4. 기본 pytest 전체, Ruff, Pyright, 캘린더 프론트 검사와 빌드를 실행한다. monitor README와 운영 문서에 두 단계 사용법을 남기고 같은 PR을 갱신한다.

내보낸 이벤트의 `old.file`·`new.file`은 이벤트 JSON이 있는 폴더를 기준으로 읽는다. 폴더 구조를 유지하고 캘린더 CLI에 이벤트 경로를 넘긴다. [명령 사용법](../operations.md#법령-개정-재생성-미리보기) · [Red·Green 및 실제 조문 대조](../evals/2026-11-16-amendment-preview-service-boundary.md)
