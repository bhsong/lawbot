# 법령 캘린더 모바일 날짜 시트 검증

- 대상: [`calendar-mobile-sheet.md`](../archives/calendar-mobile-sheet.md) R1–R6. Red `7bb794ed45`, Green `6a471f371b`.
- 데이터: 기존 `demo.ts`, Vitest 합성 스냅샷 및 대역 E2E 합성 DB의 `[예시]` 조문만 사용했다. 브라우저는 로컬 테스트 서버에만 연결했다.

## Red

| 명령 | 결과 |
| --- | --- |
| 캘린더 프런트에서 `npm run typecheck && npm test -- --reporter=dot` | 타입 검사 통과. Vitest 49건 통과, 4건 실패(파일 8개 통과, 1개 실패). |
| 루트에서 `UV_NO_ENV_FILE=1 uv run pytest -q --tb=short services/law-calendar/tests/test_calendar_ui.py -k 'date_sheet or desktop_date_click or date_selection_and_dialog_focus'` | 대역 E2E 3건 통과, 3건 실패, 14건 제외. 기존 390px·1280px 날짜 선택과 상세 흐름 및 1280px 새 회귀 테스트는 통과. |

첫 실패 문구:

- Vitest: `TestingLibraryElementError: Unable to find an accessible element with the role "dialog" and name "2026년 11월 5일"`. 세무일정만 있는 날짜의 실패는 같은 문구에서 날짜가 `2026년 11월 30일`이다.
- 대역 E2E: `AssertionError: Locator expected to be visible` / `Actual value: None` / `Error: element(s) not found` (`get_by_role("dialog", name="2026년 11월 5일")`). 닫기 입력 테스트의 첫 문구는 `AssertionError: Locator expected to be focused`다.

최종 Red 실패는 모두 시트나 시트 버튼이 없는 데 대한 단언이다. 첫 Green 실행에서 상세 비교문의 문자열이 변경 강조 태그로 분리된 것을 확인했다. 상세 비교 영역의 전체 텍스트를 검증하도록 Red 커밋만 보정한 뒤, 보정된 테스트를 기능 구현이 없는 임시 소스 트리에서 재실행해 같은 4건 실패를 확인했다. Green 커밋은 테스트 파일을 변경하지 않았다.

## Green

| 명령 | 결과 |
| --- | --- |
| `cd services/law-calendar/frontend && npm run typecheck && npm test && npm run build` | 타입 검사·빌드 통과. Vitest 53건(9개 파일) 통과. |
| `cd services/chatbot/frontend && npm run typecheck && npm test && npm run build` | 타입 검사·빌드 통과. Vitest 55건(5개 파일) 통과. 기존 Vite `fingerprint` 확장자 경고만 출력. |
| `UV_NO_ENV_FILE=1 uv run pytest` | 기본 전체 2,979건 통과, 10건 제외(193.86초). 단위·계약·통합·대역 E2E 포함. |
| `UV_NO_ENV_FILE=1 uv run pytest -q --tb=short services/law-calendar/tests/test_calendar_ui.py -k 'date_sheet or desktop_date_click or date_selection_and_dialog_focus'` | Green 대상 대역 E2E 6건 통과, 14건 제외. |
| `UV_NO_ENV_FILE=1 uv run pytest -q services/law-calendar/tests/test_calendar_ui.py` | 오류 안내를 고정 헤더에 배치한 뒤 캘린더 브라우저 20건 모두 통과(11.41초). |
| `UV_NO_ENV_FILE=1 uv run python -m compileall -q services packages tools` | 통과. |

CI 정적 검사는 루트에서 아래 명령으로 실행했다. Ruff 린트 통과, 포맷 626개 파일 변경 불필요, Pyright 4회 모두 오류·경고·정보 0건, 캘린더 스키마 일치.

```sh
export UV_NO_ENV_FILE=1 PYRIGHT_PYTHON_IGNORE_WARNINGS=1
uv run ruff check --output-format=github && uv run ruff format --check && uv run pyright && uv run pyright services/law-calendar/src && uv run python -m law_calendar.schema --check && uv run pyright services/erp-updates/src && uv run pyright services/source-hub/src
```

- 실제 모델 E2E(`e2e_live`): 실행 안 함.
- 모델 스모크(`models`): 실행 안 함.
- 품질 평가: 실행 안 함.
- Astra 모델 리뷰, 실기기 iPhone 확인, 푸시·PR·운영 반영: 실행 안 함.

## 화면 확인

`UV_NO_ENV_FILE=1 uv run python -`의 인라인 검증에서 기존 `calendar_ui_support.serve(..., development=True)`로 예시 페이지를 열었다. Playwright Chromium으로 날짜를 탭·클릭한 뒤 스크린샷을 저장했다. 루트는 `document.documentElement.scrollWidth - innerWidth`, 시트는 `scrollWidth - clientWidth`를 측정했다.

| 뷰포트 | 스크린샷(`results/e2e_stub/calendar-mobile-sheet/`, git 무시) | 관찰 |
| --- | --- | --- |
| 390×844, 터치 | `sheet-390.png` | 어두워진 배경 위 뷰포트 하단에 날짜 제목·닫기 버튼·예시 표시·조문 4건. 시트 하단 844px, 높이 674.14px. 루트·시트 수평 넘침 각각 **0px**. 모션 감소 설정에서 시트 애니메이션 없음. |
| 1280×844 | `calendar-1280.png` | 시트 없이 기존 달력과 달력 아래 조문 4건을 전체 페이지 캡처로 확인. 루트 수평 넘침 **0px**. |

## R6 콘텐츠 점검

- `git diff --unified=0 main...HEAD` 추가 줄을 `grep -Ei`로 검사했다. 이메일, 전화번호, 토큰·개인 키 형태, 절대 홈 경로, 주소·설정의 포트 번호, 개인 식별번호 후보 모두 0건. 화면 문구와 합성 픽스처에 실제 개인정보·사용자 대화·비밀값이 없다.
- `git diff --name-only main...HEAD`에 의존성 매니페스트·잠금 파일, 폰트·이미지, `licenses.txt`, 공용 UI, 챗봇 변경이 없다. `git diff --check main...HEAD` 통과.
- `main` 캘린더 프런트를 임시 위치에서 같은 의존성으로 빌드한 뒤 `cmp`로 `dist/licenses.txt`가 바이트 단위로 같고, 폰트·이미지 빌드 파일명 92개가 같은 것을 확인했다. 출처 문구와 공식 출처·라이선스 링크는 변경하지 않았다.

## 리뷰 반영

Astra 지적 1–6을 반영했다. Red `d15face8a6`는 브라우저 테스트만, Green `6cc3c39274`는 캘린더 프런트만 수정했다.

| 지적 | 반영 및 검증 |
| --- | --- |
| 1. 새로고침·ERP 전환 후 앞으로 가기 | 저장된 날짜와 조문 ID를 해당 월 데이터로 검증하고 유효한 시트·상세를 복원한다. 상세와 시트 각각의 새로고침 및 ERP 탭에서 새 날짜를 누르지 않고 앞으로 간다. 무효 날짜는 진입 방향으로 건너뛴다. 앞으로 갈 유효 항목이 없으면 뒤쪽의 유효 위치로 돌아온다. Navigation API를 끈 Chromium에서도 삭제된 조문을 건너뛰고 캘린더↔시트 이력을 유지했다. |
| 2. 지연 상세 이력과 오류 | 항목 탭에서 상세 이력을 먼저 추가한다. 데모가 아닌 로컬 내보내기에서 응답을 보류해 Back 시 요청 취소를 확인했다. Forward에서 재요청한 상세가 실패하면 시트로 돌아가 오류를 표시하고, 다음 Back은 시트를 닫는다. |
| 3. 상세 복원 후 항목 포커스 | 시트 이력에 마지막 항목 ID, 표시 범위, 스크롤 위치를 저장한다. 17번째 항목에서 Back 두 번→Forward 두 번→Back으로 해당 항목의 포커스와 스크롤 위치 복원을 확인했다. |
| 4. 더 보기 포커스 | 키보드로 8→16건과 16→17건을 펼쳤을 때 각각 첫 새 항목으로 포커스를 옮긴다. |
| 5. 배경 스크롤 검증 | 같은 마우스 휠 입력으로 시트를 열기 전에는 배경이 스크롤되고, 시트를 연 뒤에는 같은 시작 위치에서 움직이지 않음을 Chromium에서 확인했다. 대기에 명시적 상한을 두고 무한정 기다릴 수 있는 `requestAnimationFrame` Promise를 제거했다. iOS 터치 잠금은 이 검사로 입증할 수 없으며 실기기에서만 확인할 수 있다. |
| 6. UUID API 없는 PC | 마운트 UUID를 제거했다. `crypto.randomUUID`가 없는 1280px Chromium에서 시트 없이 인라인 목록이 표시된다. |

| 명령 | 리뷰 반영 결과 |
| --- | --- |
| `UV_NO_ENV_FILE=1 uv run pytest -q services/law-calendar/tests/test_calendar_ui.py -k 'date_sheet or desktop_date_click or date_selection_and_dialog_focus'` | Red: 4건 통과, 8건 단언 실패, 14건 선택 제외. Green: 12건 통과, 14건 선택 제외(8.40초). Green에서는 테스트 변경 없음. |
| 캘린더·챗봇 프런트에서 각각 `npm run typecheck && npm test && npm run build` | 두 프런트 모두 타입 검사·빌드 통과. Vitest 캘린더 53건, 챗봇 55건 통과. 챗봇에는 기존 Vite `fingerprint` 확장자 경고가 남아 있다. |
| 루트에서 `UV_NO_ENV_FILE=1 uv run pytest` | 기본 전체 2,985건 통과, 10건 선택 제외(196.32초). 단위·계약·통합·대역 E2E 포함. 캘린더 UI 브라우저 26건 통과. |
| 위 CI 정적 검사 명령 및 `UV_NO_ENV_FILE=1 uv run python -m compileall -q services packages tools` | Ruff 린트 및 627개 파일 포맷 검사 통과. Pyright 4회 모두 오류·경고·정보 0건. 스키마·문법 검사 통과. |
| Chromium 수평 넘침 재측정 | 390×844: 문서·시트 각각 0px. 1280×844: 문서 0px, 시트 없이 인라인 목록 표시. |
| R6: `main` 공통 조상 기준 `git diff --check` 및 추가 줄·변경 파일 grep | 추가 줄의 diff 표식을 빼고 이메일·전화·개인 식별번호·키/토큰·홈 경로·포트 번호 후보 0건. 공용 UI·챗봇 소스, 의존성·폰트·이미지·라이선스 파일 변경 0건. 리뷰 수정분에서 공식 출처·라이선스 링크 변경 없음. 실제 사용자 대화는 사용하지 않았다. |

iPhone Safari 실기기 확인은 실행하지 않았다. 다음 항목을 실제 기기에서 확인한다.

1. 상세 응답을 지연시킨 뒤 iOS 뒤로 스와이프로 시트에 복귀하고, 늦은 응답이 상세를 다시 열지 않는지 확인.
2. 주소 막대가 접힌 상태에서 시트 바깥 배경을 손가락으로 드래그해도 문서 스크롤 위치가 유지되는지 확인.
3. 시트 안쪽을 손가락으로 드래그하면 목록만 스크롤되고, 끝에서 배경으로 스크롤이 이어지지 않는지 확인.

실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않았다. 운영 트리·운영 설정을 건드리지 않았으며 푸시·PR·배포는 하지 않았다.

## Codex 리뷰 반영

PR #203: Navigation API가 없으면 양쪽 이력 항목에 앱이 기록한 위치가 있고 새 위치가 더 클 때만 앞으로 간 것으로 본다. 그 외에는 뒤로 간 것으로 보므로 일반 해시에서 Back으로 진입한 만료 상세·시트를 뒤로 건너뛴다.

- Red `0dc4ced4a8`: `UV_NO_ENV_FILE=1 uv run pytest -q --tb=short services/law-calendar/tests/test_calendar_ui.py -k 'date_sheet_history_matches or date_sheet_forward_from_erp or date_sheet_skips_stale_date or date_sheet_back_skips_stale_layers'` — 6건 통과, 1건 실패, 23건 선택 제외. Navigation API를 끈 Chromium에서 `#after`는 이전 캘린더 진입 대기 3초를 초과했다. `#erp`는 탭 전환 후 캘린더를 다시 마운트하는 이 경로에서 이미 통과했다.
- Green `c68450132f`: 테스트 변경 없음. `UV_NO_ENV_FILE=1 uv run pytest` 2,989건 통과, 10건 선택 제외(198.41초). 단위·계약·통합·대역 E2E를 포함하며, 캘린더 UI 브라우저 30건에서 `#erp`·`#after` 모두 원래 캘린더로 돌아가고 방금 떠난 해시를 다시 방문하지 않았다.
- 두 프런트의 `npm run typecheck && npm test && npm run build` 통과. Vitest 캘린더 53건, 챗봇 55건 통과. 기존 챗봇 Vite `fingerprint` 경고만 남음. 위 CI 정적 검사와 문법 검사 통과(Ruff 포맷 627개, Pyright 4회 오류·경고·정보 0건).
- R6: `main` 공통 조상 기준 `git diff --check` 통과. 캘린더 프런트·테스트 추가 줄을 검색한 결과 이메일·전화·개인 식별번호·토큰·키·홈 경로·포트 후보 0건. 공용 UI·챗봇 소스, 의존성·폰트·이미지·라이선스 파일 및 이번 수정의 출처·링크 변경 없음. Red 진단 파일은 삭제했다.
- 실제 모델 E2E·모델 스모크·품질 평가·iPhone 실기기 확인은 실행하지 않았다. 푸시·PR 변경·운영 반영 없음.
