# 작업 계획: ruff 린트·포맷터 도입

- 작성·실행: 2026-11-01 (결과: 이 문서 끝 "결과") · 작업 브랜치: `chore/ruff` (`main` 1116ccc에서 분기). PR은 `main` 대상, 병합은 사용자
- 원칙: **프로그램 동작은 바꾸지 않는다.** 기본 테스트 수(405 passed, 3 deselected)가 그대로여야 한다
- 되돌리기: 커밋을 나눠 둔다 (설정 / 수동 수정 / 자동 수정 / 포맷터). PR은 merge commit으로 병합되므로
  전체는 `git revert -m 1 <병합 커밋>`, 일부는 해당 커밋만 `git revert`
- 타입 검사(pyright 등)는 이번에 하지 않는다: FastAPI 모델 서버 전환 때 `model_server`·`remote`·공용 스키마 범위에만 도입

## 1단계: 린트 (`ruff check`)

- `pyproject.toml`: dev 그룹에 `ruff==0.17.3` (버전 고정: 버전에 따라 기본 규칙 범위가 달라짐)
- 대상: 저장소 전체. `data_gd/` 등은 `.gitignore`에 있어 ruff가 자동으로 건너뜀
- 규칙: `E4`·`E7`·`E9`·`F`(pyflakes) + `I`(import 정렬) + `B`(bugbear) + `UP`(pyupgrade) + `RUF`
- 끄는 규칙과 이유

| 규칙 | 이유 |
| --- | --- |
| RUF001·002·003 | 한국어 문장의 전각 기호(【】·（） 등)를 헷갈리는 문자로 오탐 |
| B905 `zip(strict=)` | 길이가 다른 zip을 쓰는 곳을 하나씩 판단해야 함. 동작이 바뀔 수 있어 이번 범위 밖 |
| B007 루프 변수 미사용 | `for stage, hits, raw, st in ask(...): pass` 뒤 **마지막 값을 루프 밖에서 씀** (bench_models·eval_concise·eval_v4). `_`로 바꾸면 깨짐 |
| E741 변수 이름 `l` | `for l, a in ...` 같은 짧은 컴프리헨션 변수. 글꼴 문제일 뿐 |
| E731 lambda 대입 | 짧은 함수·테스트 대역에 일부러 사용 |
| RUF005 `[*a, b]` 권장 | 표기 취향 |
| tests: E402 | 가짜 모듈을 `sys.modules`에 넣은 뒤 import하거나, 절마다 import를 두는 구성이 의도된 것 |
| tests: RUF012 | 테스트 대역 클래스의 공유 기록용 클래스 속성 (의도) |
| tests: RUF059 | 튜플 풀기로 반환 구조를 보여 주는 테스트 |

- 손으로 고치는 것 (자동 수정 전에)
  - `eval_v4.py`의 `LAW_ALIASES`·`cite_section` import는 eval_v4 안에서 안 쓰지만 `eval_decree.py`가 `eval_v4`에서 가져감 →
    `eval_decree`가 `citation`에서 직접 import하도록 바꾼 뒤 eval_v4의 두 이름 제거 (자동 수정으로 지우면 eval_decree가 ImportError,
    테스트가 eval_decree를 import하지 않아 pytest로는 안 잡힘)
  - `build_decree_ctx.py`의 쓰지 않는 `aid` 제거
  - `pytest.raises(match=...)`의 `.` 등 정규식 문자가 든 문자열 3곳 → `r'...'` (같은 정규식, 의미 그대로)
- 자동 수정 (`ruff check --fix`, 안전한 수정만): import 정렬·한 줄 여러 import 분리·쓰지 않는 import 제거·`a and b or c` 괄호(우선순위 그대로)·
  `# -*- coding -*-`·`.encode('utf-8')` 정리
- CI: `uv run ruff check` 단계 추가 (테스트 전)

## 2단계: 포맷터 (`ruff format`)

- 설정: `line-length = 120`, `quote-style = 'single'` (지금 코드가 작은따옴표), 나머지 기본값
- 예상 변경: 약 4,800줄 (대부분 최상위 정의 사이 빈 줄 2줄, 긴 호출 줄바꿈). 동작 변화 없음
- 포맷 커밋은 별도 커밋으로, 해시를 `.git-blame-ignore-revs`에 기록 (GitHub blame이 자동으로 건너뜀. 로컬은
  `git config blame.ignoreRevsFile .git-blame-ignore-revs`)
- CI: `uv run ruff format --check` 추가

## 확인

- `uv run ruff check`·`uv run ruff format --check` 통과
- `uv run pytest` 405 passed, 3 deselected (전과 같음)
- `uv run python -m compileall -q src tests`
- 테스트가 import하지 않는 스크립트(eval_decree 등)는 `python -c "import lawbot.<모듈>"`로 전부 import 확인

## 결과 (2026-11-01)

- 1단계: 154건(기본 규칙) 중 선택 규칙 기준 84건 → 0건. 수동 4곳, 자동 수정 78건
  - 계획과 다른 점: eval_v4의 `LAW_ALIASES`·`cite_section`은 **지우지 않음**. `test_citation`이 `ev.LAW_ALIASES is ci.LAW_ALIASES`로
    재노출을 검사하는 의도된 이름이라 `# noqa: F401`로 표시. eval_decree는 `citation`에서 직접 import하도록 바꿈
- 2단계: 63개 파일 재포맷 (+3,972 / −1,569줄). 바뀐 63개 파일 모두 포맷 전후 `ast.dump`가 같음 (동작 변화 없음)
- 확인: `ruff check`·`ruff format --check` 통과, pytest 405 passed·3 deselected (전과 같음), integration 75 passed,
  `compileall` 통과, lawbot 모듈 전체 import 성공 (`eval_oos`·`eval_refusal`은 import 때 v3 모델을 불러오는 구조라 원래부터 모델 없이는 실패 → 제외)
