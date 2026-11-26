# 법령 캘린더 모바일 날짜 시트 검증

- 대상: [`calendar-mobile-sheet.md`](../plans/calendar-mobile-sheet.md) R1–R6. Red `7bb794ed45`, Green `6a471f371b`.
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
