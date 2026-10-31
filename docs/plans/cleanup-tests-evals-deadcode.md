# 작업 계획: 불필요한 테스트·평가·실행되지 않는 코드 정리

- 작성: 2026-10-31 (계획만. 실행은 하위법령 평가(PR #26)를 마무리한 뒤)
- 작업 브랜치: `chore/cleanup` (`feat/decree-attach`에서 분기 → PR 기준 브랜치도 `feat/decree-attach`. #26이 병합되면 GitHub가 기준을 `main`으로 바꿈)
- 원칙: **프로그램 동작은 바꾸지 않는다.** 웹앱·모델 서버·`lawbot-eval-v4`의 결과(검색 순위·프롬프트·답변·채점)가 그대로여야 한다. 지우는 것은 아무것도 지키지 않는 테스트, 더 이상 쓰지 않는 평가 스크립트, 실행될 수 없는 코드뿐이다.
- 조사 방법: 파일 읽기와 grep만 사용 (평가 실행 중이라 pytest·모델 실행 안 함). 테스트 22개 파일과 `src/lawbot` 전체를 대상으로 했고, 아래 항목은 근거 줄을 직접 확인했다.

---

## 1. 불필요한 테스트 (무조건 통과)

| # | 테스트 | 문제 | 조치 |
| --- | --- | --- | --- |
| T1 | `test_scoring.py::test_recorded_citation_edge_cases` | 저장된 CSV 값만 읽어 상수와 비교한다. 채점 코드(`score_one`)를 부르지 않아, 채점이 바뀌어도 실패하지 않음 | **고침**: 같은 5개 사례를 `score_one(정답 행, 저장된 답변)`으로 다시 채점해 비교한다. 경계 사례(거절 + 제공 조문 목록, 헷갈리는 조문 함께 인용) 문서 역할은 유지 |
| T2 | `test_eval_v4.py::test_refused` | `test_citation.py::test_refused`와 같은 함수(`ev.refused is ci.refused`는 따로 검사), 같은 5가지 경우 | **삭제** (`citation` 쪽 테스트가 기준) |
| T3 | `test_webapp.py::test_pro_only_app`의 `assert FakeLLM.created == 1 or list(a.models.loaded) == ['pro']` | 뒤쪽 조건은 같은 테스트에서 이미 검사했고, 앞쪽 카운터는 테스트 사이에 초기화되지 않아 이 줄 혼자서는 실패할 수 없음 | **그 줄만 삭제** |
| T4 | `test_citation.py::test_citations_reads_cite_section`의 `assert ev.citations(ans) == ci.citations(ans)` | 같은 파일에서 `ev.citations is ci.citations`를 검사하므로 함수를 자기 자신과 비교하는 셈 | **그 줄만 삭제** |
| T5 | `test_webapp.py::test_status_steps`의 `assert all(s[0].startswith('⏳') or s[0] for s in steps)` | `or s[0]` 때문에 비어 있지 않으면 통과해 `⏳` 검사가 의미 없음 | **고침**: 의도대로 "생성 중 단계(마지막 제외)의 본문은 비어 있지 않고, 처리 중 문구면 `⏳`로 시작"을 검사. 고친 뒤 일부러 틀린 값으로 실패하는지 확인 |
| T6 | `test_auth_integration.py`의 `free_port()` | `conftest.free_port`를 복사한 것 | **conftest 것을 import** (테스트 수 변화 없음) |
| T7 | `test_pipeline.py`의 `load_models`·`answer_stream` 테스트 5개 | 테스트 자체는 진짜 검사지만, 대상 함수가 테스트 말고는 쓰이지 않음 (3절 C6) | **C6과 함께 삭제** |

남기는 것 (조사에서 후보였으나 의미 있음):
- `test_diff_answers.py::test_saved_results_equal_themselves`: 자기 자신과 비교하지만, 빈 값(NaN)·줄바꿈이 섞인 **실제 결과 파일**을 쓰므로 비교 코드가 NaN을 다르게 보면 실패한다. 행 수(200·122·200)로 `load`도 검사한다.
- `test_api_versions_match`, 노트북 문자열 고정 테스트, `⏳` 상수 테스트, 데이터 스키마·보안(포트·비밀 값 없음)·gitignore 테스트: 서로 맞아야 하는 약속이나 보안 속성을 지킨다.
- `-m models` 테스트 3개: 기본 실행에서 일부러 빼 둔 실제 모델 테스트.

예상: 383 → 377 passed (T2 −5, T7 −5, T1·T3~T6 수 변화 없음. 실행 전 숫자를 다시 센다).

## 2. 불필요한 평가

| # | 대상 | 상태 | 권장 |
| --- | --- | --- | --- |
| E1 | `diag_fail.py`, `diag_oracle.py`, `diag_pos.py` | v1(근로기준법 130조문, 4B) 때의 일회성 진단. 어디서도 import·실행 안 함. 결과는 `docs/evals/2026-10-18-v1.md`에 기록됨. 이 맥에는 v1 모델이 없어 실행도 불가 | **삭제**. 결과 CSV(`results/scored_oracle_4bit.csv` 등)는 테스트 기준 데이터라 남김 |
| E2 | `summ_dev.py` | `eval_dev` 결과의 거절 지표 요약. 같은 지표를 `report.py`가 계산함 (`--scored`) | **삭제**, README 명령 줄 정리 |
| E3 | `eval_dev`(`lawbot-eval`)·`eval_oos`·`eval_refusal`·`report` | v1 기준선·개선 전후 비교 체인. `report.METRICS`는 `eval_v4.compare`가 씀. v1 프로필이 아직 코드에 있음 | **유지** (v1 전체를 없앨지는 별도 결정. 아래 결정 사항 D1) |
| E4 | `decree_filter`·`build_decree_ctx`·`mlx_decree_scores`·`eval_decree` | 하위법령 실험. `decree_filter`·`eval_decree`는 `test_decree`의 기준, 나머지는 `docs/evals` 실험 수치를 다시 만드는 도구 | **유지** |
| E5 | `eval_concise` | `LAWBOT_EARLY_STOP`의 val 검증이 아직 남음 | **유지** |
| E6 | `bench_models`·`bench_ui`·`verify_reranker`·`verify_decree_emb`·`smoke_decree`·`versions` | 운영·검증 도구 | **유지** |

## 3. 로직상 실행되지 않는 코드

바로 지울 것 (근거 확인함):

| # | 위치 | 근거 | 조치 |
| --- | --- | --- | --- |
| C1 | `decree.py` `KIND_LABELS` | 레포 어디에서도 읽지 않음 | 삭제 |
| C2 | `eval_v4.py` import의 `REFUSAL` | 모듈에서 안 쓰고, `ev.REFUSAL`을 읽는 곳도 없음 (테스트의 `REFUSAL`은 각 파일의 자체 상수) | import에서 제거 |
| C3 | `llm.py` `LLM.__init__(temp=0.0)` | 호출하는 곳(service·eval_*·diag·테스트) 어디도 `temp`를 넘기지 않음 | 인자 제거, `make_sampler(temp=0.0)` 고정 (동작 동일) |
| C4 | `retriever.py` `self.max_seq_length`·`self.decree_max_seq` | 저장만 하고 읽지 않음 (지역 변수는 그대로 씀) | 속성 저장 줄 제거 |
| C5 | `remote.py` `ModelServerError.kind` | 저장만 하고 읽지 않음 (메시지에는 들어감). `.status`·`.base`만 읽음 | 속성 제거 (메시지 그대로) |
| C6 | `service.py` `load_models()`·`answer_stream()` (+ 이것만 쓰는 `LLM_4BIT` import) | src·README·docs에서 부르지 않음. docstring의 "평가·기존 호출용"과 달리 평가 스크립트는 `Retriever()`·`LLM()`을 직접 만듦 | 함수와 테스트 5개 삭제 (T7) |
| C7 | `check_deploy.py` `sha256()` | `manifest.sha256()`과 같은 코드 | `manifest.sha256` 사용 |
| C8 | `retriever.py` `LAW_KIND` | `decree.LAW_KIND`와 같은 값 (decree는 모델 라이브러리 import 없음) | `decree`에서 import |
| C9 | `remote.py` `RemoteLoadError`·`RemoteValueError` docstring | "app.py가 OSError로 받아" → 지금은 `service.error_type`이 단계(`load`)로 판정 | docstring만 사실대로 고침 (클래스는 오류 메시지·테스트에 쓰임) |

남기는 것:
- `ask(k=TOP_K)`, `retrieve(model_key=None)` 기본값: 항상 같은 값이 들어가지만 공개 함수의 인자라 해롭지 않고, 바꾸면 호출부만 늘어난다.
- `getattr(…, 'n_law' / 'decree' / 'kinds' …)` 대체값: 실제 검색기에서는 쓰이지 않지만 테스트의 가짜 검색기(`fakes.FakeRetriever`, `model_fakes.Ret`)가 이 경로를 탄다. 지우려면 가짜도 함께 고쳐야 해서 이번에는 남긴다.
- `answer_format.render`의 이중 거절 검사, `remote.GEN_FIELDS` 중복(서버 모듈을 import하지 않으려는 의도), `service._LAZY['SYSTEM']`: 동작에 영향 없고 바꿀 이득이 작다.

## 4. 진행 순서 (작업 방식: 계획 → 테스트 → 구현)

1. 하위법령 평가를 마무리한다 (PR #26 표·기록·README 한 줄, 운영 복구).
2. `chore/cleanup` 브랜치를 만들고, 바꾸기 전의 `uv run pytest -q` 통과 수를 기록한다.
3. **테스트 먼저**: T1·T5를 고친다. 고친 테스트가 실제로 실패할 수 있는지 일부러 틀린 값으로 한 번씩 확인하고 되돌린다. 그다음 T2~T4·T6을 정리한다.
4. **구현**: C1~C9, E1·E2, T7. README의 프로젝트 구조·평가 명령에서 지운 스크립트를 뺀다(한국어·영어).
5. 확인:
   - `uv run python -m compileall -q src tests`, `uv run pytest -q` (예상 377 passed)
   - 지운 이름이 남아 있지 않은지 `git grep`
   - 동작 동일: 바뀌는 실행 코드는 C3(같은 temp 0.0)·C4·C5·C7·C8(같은 값)뿐이라 결과에 영향이 없다. 실제 모델 재실행은 필요 없다고 보지만, 원하면 `lawbot.smoke_decree --compare`로 PR #26 결과와 답변을 대조할 수 있다(약 3분, 실행 전 승인).
6. 평가 기록: `docs/evals/`에 정리 전후 테스트 수만 짧게 남긴다(평가 결과가 아니므로 README 줄은 추가하지 않음).
7. PR 생성(기준 `feat/decree-attach`) → 보고(확인할 사항, 병합·main 복귀 방법).
8. 끝나면 이 계획서를 `docs/archives/`로 옮긴다.

## 5. 결정 (2026-10-31 사용자)

- **D1. v1 평가 체인**: 계획대로 **남긴다** (E3). v1 프로필 제거는 이번 범위 밖.
- **D2. E1(`diag_*` 3개)·E2(`summ_dev`)**: **삭제한다.**
