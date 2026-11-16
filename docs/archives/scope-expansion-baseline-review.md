# PR #158 리뷰 반영과 공개 규칙 결정

- 2026-11-16 KST. PR #158 `d9e43e6`, 기준 `main` `a7786a4`. [기존 경위](../archives/scope-expansion-baseline.md)
- 사용자 결정. 실제 법령 제정(`law_new`)과 일반 개정 중 조문 신설에도 이전 원문 유무와 관계없이 기존 시행일 판정과 자동 승인을 적용한다. 감시 범위 확장으로 처음 본 파일의 이벤트 생성 방지는 monitor에서 담당한다. 캘린더 규칙과 파서 버전은 v7로 복귀한다.

## Red → Green

1. 강제 푸시와 범위 확장을 겹쳐 재현한다. 이전 관리 범위의 파일 변경은 기존 대체 비교로 이벤트를 만들고, 새 범위 및 미추적 파일만 기준점으로 잡는다. paused 경로의 복구는 기존 방식으로 검증한다.
2. 별도 저장소·home의 replay에도 `amendment:<old_blob>:<new_blob>` ID를 쓴다. 일일 수집기에 쓰는 `default_collectors`와 `LawGoEnricher`를 재사용한다. 외부 Git 갱신 없이 공식 법제처 클라이언트의 기존 URL·인증·캐시·간격·재시도 규칙을 사용한다. 미리보기는 실제 요청 수를 출력하고 파일을 쓰지 않는다.
3. replay 이벤트의 이전·이후 원문과 보강된 context에 캘린더 importer와 같은 조문·시행일 판정을 적용한다. 타법 부칙, 공포일 실행, 날짜 오류 사유, 미리보기와 실제 import 결과 일치를 테스트한다. 이전 원문 없는 조문의 자동 공개와 기존 철회·거절 유지도 확인한다.
4. 기본 pytest 전체, Ruff, Pyright, 캘린더 프론트 검사와 빌드를 돌린다. 지정 시행령 2건의 dry-run 결과를 새 검증 기록과 PR에 남긴다. 리뷰 스레드 8건에 반영 근거를 답한다. 운영 반영 및 운영 replay·import는 각각 승인 후 진행한다.

## 구현 결과

- 캘린더 importer와 같은 판정을 호출하도록 monitor의 로컬 패키지 의존성에 `law-calendar`를 추가했다. 독립 캘린더 lockfile은 그대로 쓴다.
- `state.db`와 무관하게 같은 재생성 ID를 사용한다. 이벤트를 이미 저장했다면 그 원천으로 미리보기를 재현한다. 재생성 ID가 정기 수집 ID와 달라 같은 실제 개정이 이미 있는 경우에는 import 전에 캘린더를 확인한다.
- [Red·Green, 요청 수, 조문별 dry-run 및 전체 검증](../evals/2026-11-16-scope-expansion-baseline-review.md)
