# 계획: SQLite 데이터 기준 저장소 · 관리자 검수 · 스냅샷 · 코퍼스 릴리스 전환

- 작성일: 2026-11-07
- 원 명세서: [sqlite-admin-pipeline-spec.md](sqlite-admin-pipeline-spec.md) (이하 "명세서"). 명세서와 이 계획이 다르면 **이 계획을 따른다**. 다르게 하는 이유는 1절에 있다.
- 대상 독자: 이 계획을 구현할 코딩 에이전트. 각 PR은 아래 체크리스트를 위에서부터 차례로 처리한다.
- 진행 방식: **PR 하나씩**. 각 PR은 앞 PR이 병합된 뒤 사용자 확인을 받고 시작한다. 운영 반영은 PR과 별개로 사용자가 요청할 때만 한다.

---

## 0. 구현 에이전트가 지킬 것 (모든 PR 공통)

- [ ] 시작 전에 `AGENTS.md` 전체를 읽는다. 이 계획은 AGENTS.md를 대신하지 않는다.
- [ ] PR마다 1절(요청 확인과 계획)을 다시 한다: `git status`, 브랜치, HEAD, `git worktree list`, `gh pr list`. 운영 트리(`~/Work/lawbot-prod`)는 **읽기만** 한다.
- [ ] 이 계획서에 적힌 사실(행 수, 열, 경로)을 PR 시작 때 다시 확인한다. 다르면 구현하지 말고 사용자에게 보고한다.
- [ ] TDD: 체크리스트의 "Red" 항목 테스트를 먼저 쓰고 **의도한 이유로** 실패하는지 확인한 뒤 구현한다.
- [ ] 새 코드 식별자·주석·docstring은 영어, 보고·문서는 한국어.
- [ ] 비밀값(비밀번호, 해시, 토큰, 포트 번호)을 코드·문서·테스트 출력·커밋에 남기지 않는다. 테스트는 가짜 값만 쓴다.
- [ ] 질문 원문이 든 파일(DB, 백업, 스냅샷, 이관 보고서의 표본)은 레포 밖 또는 `.gitignore`에 걸리는 경로에만 쓴다. 기존 `lawbot.feedback.check_private`로 검사한다.
- [ ] 테스트는 `tmp_path`의 DB만 쓴다. 레포의 `data/db/`, 운영 트리, 실제 모델 서버에 쓰지 않는다.
- [ ] 실제 모델 호출(e2e_live·models·평가)은 이 계획의 어느 PR에서도 **기본으로 하지 않는다**. 필요하면 AGENTS.md 절차로 승인받는다.
- [ ] PR마다 "전체 Green 조건"(AGENTS.md)을 지킨다: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pyright services/law-calendar/src`, `uv run pytest`(기본, e2e_stub 포함), 두 프론트엔드의 `npm run typecheck && npm test && npm run build`.
- [ ] 끝난 계획 단계는 이 문서의 체크박스를 채워 같은 PR에 커밋한다. 마지막 PR이 병합되면 이 문서와 명세서를 `docs/archives/`로 옮긴다.
- [ ] 막히면(사실이 다름, 테스트가 설계상 통과 불가, 범위 확대 필요) 멈추고 PR을 먼저 만든 뒤 질문한다.

---

## 1. 명세서와 다르게 하는 것 (검토 결과)

| # | 명세서 | 이 계획 | 근거 (2026-11-07 확인) |
|---|---|---|---|
| 1 | 운영 기준이 v4 | **운영은 v5**. 이관·전환의 기준은 v5 live(`data/live/v5`) | 운영 `.env`: `LAWBOT_PROFILE=v5`, `LAWBOT_V5_*ARTICLES=data/live/v5/…`. v5.0.0이 2026-11-07 운영 반영됨. v4는 기록용 릴리스로만 이관 |
| 2 | "테스트 650개를 포함한 고정 Test" | 고정 Test는 **v4 2,075**(A_lsa 650 + B_newlaw 1,425), **v5 3,420**, **하위법령 질문 v4 test 1,880**. 650은 v4 근로기준법(A_lsa) 부분집합 | `split_v4.csv`·`split_tax_v5.csv`·`decree_exp/split_decree_v4.csv` 집계. 두 파일 모두 `split_emb`의 test 집합 = `split_sllm`의 test 집합 |
| 3 | 기존 피드백 JSONL 전체 이관 | 이관 도구는 만들되 **현재 실데이터 0건** | 운영 트리에 `feedback/` 폴더 없음, 개발 `feedback/`는 빈 폴더. 테스트는 합성 JSONL로 한다 |
| 4 | `feedback_review`/`feedback_export` 재사용 | **먼저 v5를 지원하게 고친다**(PR 3) | `feedback_review.eval_sets()`는 v4 test/val·하위법령 질문만 본다(v5 test 3,420·val 2,052·`qa_reasoning_val` 누락). `feedback_export`는 `profile != 'v4'` 기록을 모두 버리고 v4 조문 CSV만 읽는다 → 지금 운영(v5) 피드백은 내보내기에서 전부 빠진다 |
| 5 | 피드백에 모델·프롬프트·코퍼스 버전 | **턴 기록에 필드를 추가**한다 | 현재 `turn_record`에는 `dataset_version`·`commit`·`profile`·system 전문만 있고 조문 파일 해시, 임베딩·리랭커 모델 id, system 해시가 없다 |
| 6 | DB 스키마·접근 계약을 `packages/lawbot-common`에 | **`services/chatbot/src/lawbot/db/`**(런타임 패키지)에 둔다 | DB를 쓰는 곳은 챗봇 런타임과 `lawbot_tools`(이미 `lawbot`을 import)뿐이다. 공통 패키지에 넣으면 모델 서버·monitor가 쓰지 않는 의존을 갖게 된다 |
| 7 | 변경 조문만 재임베딩 | **전체 재임베딩 유지**(지금과 같음). 부분 재임베딩은 비목표 | `retriever.py` 주석: 묶음(batch) 구성·패딩이 바뀌면 fp16 벡터 값이 달라진다. 부분 재임베딩 행렬은 전체 임베딩과 비트 단위로 다르고, 현재 캐시 검증(확인용 벡터)·재현성과 충돌한다 |
| 8 | 새 릴리스 무중단 로딩·진행 중 요청 일관성 | **재시작으로 활성화**(지금과 같음). 웹앱은 시작할 때 활성 릴리스 하나를 읽어 프로세스가 끝날 때까지 바꾸지 않는다 | 요청 일관성이 구조적으로 보장되고 코드가 가장 단순하다. 지금 `articles_update`도 `launchctl kickstart`로 재시작한다. 핫 스왑은 비목표 |
| 9 | 캘린더 프록시는 "선택" | **필요**. 챗봇 서버가 캘린더 공개 GET만 허용 목록으로 중계 | 캘린더는 별도 포트·별도 출처이고 CORS가 없다. 공개 캘린더를 고치지 않으려면 같은 출처 프록시가 유일하다 |
| 10 | `packages/lawbot-ui`를 npm workspace로 | **소스 공유(Vite alias + tsconfig paths)**, npm workspace 안 씀 | 두 프론트엔드가 각자 `package-lock.json`과 CI 단계를 갖는다. workspace로 바꾸면 lockfile·CI·`npm ci` 위치가 모두 바뀐다. 결정 D2 |
| 11 | 챗봇 화면 빌드 | 공유 UI 소스도 **화면 지문에 포함** | `web.require_build`는 `frontend/` 안 파일만 지문으로 본다. 공유 소스를 빼면 공유 UI만 바뀌었을 때 옛 화면이 그대로 실행된다 |
| 12 | 결과 파일 `plan.md` | `docs/plans/sqlite-admin-pipeline.md`(이 문서) | AGENTS.md: 계획서는 `docs/plans/`에 둔다 |
| 13 | CSRF 보호 신설 | 기존 방어(SameSite=Strict 쿠키 + JSON만 받는 POST)를 유지하고 **관리자 POST에 Origin 검사를 추가** | `web.py`의 `require_json`, `set_cookie(samesite='strict')` |
| 14 | 새 환경 변수 이름 자유 | **`LAWBOT_` 접두사만** | `lawbot_common.envfile.parse`는 `.env`에서 `LAWBOT_*`만 읽는다 |
| 15 | `data/lawbot.db` | 기본 경로 **`data/db/lawbot.db`**, `.gitignore`에 `data/db/` 추가, `LAWBOT_DB`로 지정 | 지금 `.gitignore`는 `data/*.db`를 막지 않는다. 백업도 같은 폴더(`data/db/backups/`) |
| 16 | QA ID를 그대로 PK로 | **(데이터셋, 원래 id)로 네임스페이스** | `scope_controls`·`refusal_hard_cases`·`labor_eval_keywords`가 v4의 `Q…` id를 다시 쓰고, `refusal_hard_cases`는 id가 중복(같은 문항 여러 행)이다 |
| 17 | 법률·하위법령 조합을 각각 보존 | 릴리스마다 **조합 CSV 한 목록**만 저장하고 법률 CSV는 거기서 만든다 | v4·v5 모두 `articles_*.csv` = `law_articles_*_decree.csv`에서 `kind == '법률'`인 행(같은 순서, 같은 값, 열은 앞 9개)임을 확인 |
| 18 | 원본 대비 "표본/전체 내용 대조" | **바이트 단위 왕복 대조**를 완료 조건으로 | 조문 CSV 4개와 QA·분할·정답 키 CSV 9개 모두 `pd.read_csv(dtype=str, keep_default_na=False)` → `to_csv(index=False, lineterminator=…)`로 원본과 바이트가 같음을 확인(`qa_reasoning_val.csv`만 `\r\n`) |
| 19 | 목적별 분할(embedding/sllm/reranker) | 기존 데이터에는 **emb·sllm 두 목적만** 있다. reranker 분할은 만들지 않는다 | 분할 CSV 열: `split_emb`, `split_sllm` |
| 20 | 사용자 질문 원문 보존·삭제 정책 | 이번 범위는 **권한(600/700)과 Git 제외**까지. 보존 기간·삭제 명령은 결정 D3 이후 | 명세서에 기간 값이 없음 |

참고로 확인한 것: v4·v5의 기존 분할에서 정규화(공백·문장부호 제거) 완전 일치 질문이 서로 다른 분할에 걸친 경우는 **0건**이다(`split_emb`·`split_sllm` 모두). 유사도(0.9) 기준 교차 검사는 PR 4에서 보고서로만 낸다(기존 분할은 고치지 않는다).

---

## 2. 확인한 현재 상태 (2026-11-07)

### 2.1 레포

| 항목 | 값 |
|---|---|
| 개발 트리 | `~/Work/lawbot`, 이 계획 브랜치 `docs/sqlite-admin-pipeline-plan` (main `042d4bd`에서 분기) |
| 운영 트리 | `~/Work/lawbot-prod`, detached HEAD `69a2dd3` (v5.0.0) |
| 열린 PR | 없음 (PR #82 병합됨) |
| 개발 트리 미커밋 | 다른 작업의 문서(`docs/evals/README.md` 수정, 캘린더 백필·v5 배포 기록 md, `results/v5/emb_v5b_train.json`). **이 계획과 무관, 건드리지 않는다** |
| uv workspace | 루트 `pyproject.toml`. `services/law-calendar`는 workspace에서 제외된 독립 프로젝트(자체 `uv.lock`) |
| 패키지 방향 | `lawbot_tools` → `lawbot` 단방향 (`services/chatbot/tests/test_layers.py`가 검사) |
| CI | `.github/workflows/ci.yml`: compileall, 캘린더 pyright·스키마, ruff, format, pyright, 두 프론트엔드 typecheck·test·build, pytest(e2e_stub 포함) |

### 2.2 데이터 (모두 UTF-8 BOM, 모든 값을 문자열로 읽음)

| 파일 | 행 | 열 |
|---|---|---|
| `data/v5/law_articles_v5_decree.csv` | 1,525 (비삭제 1,497 = 법률 705 + 하위법령 792) | law, article_id, article_no, title, chapter, deleted, text, effective_date, related_ids, kind, parent_law, parent_ids, child_ids |
| `data/v5/articles_v5.csv` | 713 (비삭제 705) | 위의 앞 9개 열 |
| `data/v4/law_articles_v4_decree.csv` | 840 (비삭제 812) | v5 조합과 같은 13열 |
| `data/v4/articles_v4.csv` | 423 (비삭제 415) | 앞 9개 열 |
| `data/v5/qa_tax_v5.csv` | 13,680 | id(T…), question, law, article_id, title, question_type, confusable_with, eval_cat(D_tax), answer_key, rid |
| `data/v5/split_tax_v5.csv` | 13,680 | id, eval_cat, split_emb(train 8,208 · val 2,052 · test 3,420), split_sllm(train 9,234 · val 1,026 · test 3,420) |
| `data/v5/answer_key_tax_v5.csv` | 1,745 (answer_key 유일) | answer_key, law, article_id, answer, must_include(JSON 목록의 목록), must_not_include, must_not_in_conclusion |
| `data/v4/qa_full_v4.csv` / `split_v4.csv` | 7,618 | qa: id(Q…), question, law, article_id, title, question_type, confusable_with, eval_cat(A_lsa 2,602 · B_newlaw 5,016), answer_key. split_emb test 2,075 · val 1,110 · train 4,433 |
| `data/v4/answer_key_v4.csv` | 751 | v5와 같은 열 |
| `data/decree_exp/qa_decree_v4.csv` / `split_decree_v4.csv` | 4,096 | 하위법령 질문(D…). test 1,880 · val 444. 원본 `answer_key` 고유 값은 567개이며 별도 정답 키 CSV는 없다. |
| `data/v5/qa_reasoning_val.csv` | 30 | id(ERR…), error_type, question, answer, cited_articles, article_ids (UTF-8 BOM, 줄바꿈 `\r\n`) |
| `data/oos_eval.csv` 102 · `data/scope_controls.csv` 20 · `data/refusal_hard_cases.csv` 86 · `data/labor_eval_keywords.csv` 650 | | 평가 전용. `oos_eval`을 뺀 셋은 id가 v4와 겹치거나 중복 |
| 매니페스트 | `data/v5/manifest.json`, `data/v5/manifest_law_articles_v5_decree.json`, `data/v4/manifest.json`, `data/v4/manifest_decree.json` | dataset_version, reference_date, collected_date, source{name,url,ref}, articles_file, articles_sha256, articles_total, articles_by_law, articles_by_kind, trained_on |
| 운영 live | `lawbot-prod/data/live/v5/`(v5 CSV 2개 + 매니페스트 2개, `archive/` 아직 없음), `lawbot-prod/data/live/`(v4 live와 `archive/deploy-v5.0.0/`) | |
| 운영 임베딩 캐시 | `lawbot-prod/cache/embeddings/*.npz` 4개 | |

- 키: 조합 CSV에서 `article_id`(법령명 포함, 예: `근로기준법 제1조`) 유일, `(law, article_no)` 유일.
- 법률 CSV = 조합 CSV의 `kind == '법률'` 행, 같은 순서·같은 값(v4·v5 확인).

### 2.3 코드 계약 (바꾸지 않을 것)

| 대상 | 계약 |
|---|---|
| `lawbot.retriever.doc_text` | `law + ' ' + article_id + '(' + title + ')\n' + text` (`article_no`가 있으면 그 값을 `article_id`로 씀). 바꾸지 않는다 |
| `Retriever` | 비삭제 행(`deleted == 0`)을 법률·하위법령으로 나누고, 각 그룹 안에서 CSV 행 순서를 유지해 법률 먼저 붙인다. 법률·하위법령을 따로 인코딩 |
| `embed_cache.cache_key` | sha256(모델 id, max_seq, 문서 문자열 전체)[:32]. 문서 문자열이 같으면 같은 캐시 파일 |
| `manifest.check` | CSV sha256·비삭제 조문 수·법령별·종류별 수가 매니페스트와 다르면 시작 거부 |
| `/api/login` | JSON `{user, password}` → 쿠키 `lawbot_session`(HttpOnly, SameSite=Strict, https일 때 Secure, path `/`). 실패 401 + 1초 지연 |
| `/api/me` | 지금 `{user}` → `{user, role}`로 확장 |
| `/api/feedback` | JSON `{turn_id, label, reason}` → `{saved}`. label `like`·`flag`·`cleared`, flag reason `retrieval`·`answer`·`false_refusal`·`other`. 2초 안 같은 버튼은 저장 안 함 |
| 피드백 줄 | `feedback_id, turn_id, ts('%Y-%m-%dT%H:%M:%S%z'), label, reason` + 턴 기록(question, hits[], system, answer_raw, answer_shown, model, fmt, refused, n_cited, citations, timing, decree, n_decree, instance, mode, profile, dataset_version, commit, request_id) |
| 최신 상태 규칙 | `feedback_review.latest`: `ts` 문자열 오름차순 정렬 뒤 turn_id마다 마지막 줄, `cleared`면 제외 |
| 누출 검사 규칙 | `feedback_review.Overlap`: 정규화(`[\W_]+` 제거, 소문자) 완전 일치 → difflib `SequenceMatcher(autojunk=False)` 비율 ≥ 0.9 |
| `lawbot-articles-update` | 종료 코드 0(변화 없음)·1(갱신)·2(실패), 마지막 줄 `articles updated=…`(monitor가 마지막 줄과 종료 코드를 요약에 씀, `law_monitor/checks.py`). `.update.lock`, 아카이브 → 전환 → 재시작 → 실패 시 복구 |
| 공개 캘린더 | `services/law-calendar`, 공개 GET만: `/api/law-calendar`, `/api/law-events`, `/api/law-changes/{id}`, `/api/law-changes/{id}/notice-comparison/{page}`, `/api/law-calendar/status`, `/api/law-holds` |
| 캘린더 컴포넌트 | `MonthCalendar.tsx`(dates·types만 import), `EventList.tsx`(types), `ChangeDialog.tsx`(types). CSS 접두사 `lc-` (`calendar.css`) |

---

## 3. 설계 결정

### 3.1 모듈 경계

```
services/chatbot/src/lawbot/
  db/
    __init__.py
    connection.py     # connect(path, write=False) context manager, PRAGMAs, BEGIN IMMEDIATE helper
    migrate.py        # apply migrations, schema version check, init (new file only)
    backup.py         # online backup via sqlite3 backup API, integrity check, restore-to-new-path
    cli.py            # `lawbot-db` command: init, migrate, status, backup, restore, export-release
    migrations/0001_init.sql (and later 000N_*.sql)
  corpus_store.py     # corpus releases: import rows, build CSV bytes, active release, events
  feedback_store.py   # append feedback rows, latest-state queries, reviews, promotions
  dataset_store.py    # qa items, answer keys, splits, preferences, dataset releases
  qa_rules.py         # norm, Overlap, latest_rows, leak check (moved out of lawbot_tools.feedback_review)
  auth.py             # password hashing (scrypt), admin credential config, login rate limiter
  admin.py            # FastAPI APIRouter for /api/admin/* incl. read-only calendar proxy
tools/lawbot-tools/src/lawbot_tools/
  migrate_csv_to_sqlite.py
  export_dataset_snapshot.py
  assign_splits.py
packages/lawbot-ui/src/   # shared TSX/TS/CSS source (no own package.json build)
```

- API·도메인 코드는 `*_store.py`의 함수만 부른다. `sqlite3`는 `db/`와 `*_store.py` 밖에서 import하지 않는다(테스트로 검사).
- `lawbot.db`·`*_store.py`·`qa_rules.py`는 FastAPI를 import하지 않는다.
- 연결: 작업마다 짧게 연다(`with connect(path) as conn:`). 스레드 간 연결 공유 금지.

### 3.2 DB 파일과 설정

| 변수 | 필수 | 용도 |
|---|---|---|
| `LAWBOT_DB` | 피드백은 선택. 관리자·DB 코퍼스는 필수 | SQLite 파일 경로. 상대 경로는 레포 최상위 기준. 레포 안이면 `.gitignore`에 걸려야 함(`check_private(path, folder=False)`). 지정한 파일이 없으면 시작 거부(자동 생성 안 함) |
| `LAWBOT_CORPUS` | 아니요 | `csv`(기본, 지금과 같음) / `db`(활성 릴리스에서 조문을 읽음, PR 11) |
| `LAWBOT_ADMIN_USER` | 아니요 | 관리자 사용자명. `LAWBOT_USER`와 같으면 설정 오류 |
| `LAWBOT_ADMIN_PW_HASH` | 관리자 사용 시 | `scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>`. 평문 비밀번호 변수는 만들지 않음. 둘 중 하나만 있으면 설정 오류 |
| `LAWBOT_CALENDAR_API_URL` | 아니요 | 캘린더 API 주소. `http://127.0.0.1:<포트>`만(`security.model_server_url`과 같은 검사). 없으면 관리자 화면에 "캘린더 연결 안 됨" |

- 모든 설정은 `lawbot.settings.load`에서 한꺼번에 검사한다. PR 5 착수 요청에 따라 `LAWBOT_DB`가 비어 있으면 기존 `LAWBOT_FEEDBACK_DIR`와 JSONL 저장 동작을 유지한다. `LAWBOT_DB`를 지정하면 JSONL에 이중 저장하지 않는다. 두 경로를 모두 지정하면 설정 오류로 `migrate_csv_to_sqlite feedback` 이관 명령을 안내한다.
- 파일 권한: DB 폴더 700, DB·`-wal`·`-shm`·백업 600. `connect`가 처음 만들 때(`init`만) 설정하고, 시작할 때 권한이 넓으면 경고한다.
- `conftest.py`의 `ENV_KEYS`에 새 변수를 추가해 테스트 간 누수를 막는다.

### 3.3 연결·마이그레이션·백업

- PRAGMA: `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`. 연결마다 `foreign_keys`를 켜고 꺼져 있으면 예외.
- DB와 보조 파일의 Git 제외 여부는 설정을 읽거나 관리 명령을 실행할 때 확인한다. `connect()`에는 확인한 경로를 넘긴다. 권한 경고는 프로세스에서 경로별 한 번 표시한다.
- 쓰기: `BEGIN IMMEDIATE` … `COMMIT`. 트랜잭션 안에서 HTTP·모델 서버·파일 대량 I/O 금지(리뷰 체크 항목).
- 마이그레이션: `migrations/NNNN_name.sql`을 번호순으로 하나씩 트랜잭션 적용, `schema_migrations(version, name, checksum, applied_at)` 기록. 이미 적용된 파일의 checksum이 다르면 실패.
- 웹앱 시작: `LAWBOT_DB`를 지정했을 때 파일이 없으면 거부("`uv run lawbot-db init --db …`"), 스키마 버전이 코드보다 낮으면 거부("`uv run lawbot-db migrate`"), 높으면 거부. 시작할 때 생성·마이그레이션하지 않는다.
- `lawbot-db init --db PATH`: 파일이 이미 있으면 거부. 새로 만들고 모든 마이그레이션 적용.
- `lawbot-db backup --db PATH --out FILE`: `sqlite3.Connection.backup()`으로 임시 파일에 복사 → `PRAGMA integrity_check`가 `ok`인지 확인 → `os.link`로 FILE 공개. 검사 후 동시에 생긴 파일도 덮어쓰지 않는다. 권한 600.
- `lawbot-db restore --from BACKUP --to NEWPATH`: 새 경로에만 복원(덮어쓰기 없음). 무결성·스키마 버전 확인 후 경로 출력. 운영 교체는 운영 절차(7절)에서 사람이 한다.

### 3.4 DDL (`0001_init.sql`)

구현 에이전트는 아래를 그대로 쓰고, 바꿔야 하면 PR 설명에 이유를 적는다.

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
);

-- ---------- corpus ----------
CREATE TABLE laws (
  law_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,                 -- CSV `law`, e.g. '근로기준법 시행령'
  kind TEXT NOT NULL CHECK (kind IN ('법률','시행령','시행규칙')),
  parent_name TEXT,                          -- CSV `parent_law` for decrees; NULL for statutes
  source TEXT NOT NULL                       -- e.g. 'Legalize KR'
);

-- One row per distinct content of one article. All CSV values kept as exact strings.
CREATE TABLE article_versions (
  version_id INTEGER PRIMARY KEY,
  law_id INTEGER NOT NULL REFERENCES laws(law_id),
  article_no TEXT NOT NULL,
  article_id TEXT NOT NULL, title TEXT NOT NULL, chapter TEXT NOT NULL,
  deleted TEXT NOT NULL CHECK (deleted IN ('0','1')),
  text TEXT NOT NULL, effective_date TEXT NOT NULL, related_ids TEXT NOT NULL,
  kind TEXT NOT NULL, parent_law TEXT NOT NULL, parent_ids TEXT NOT NULL, child_ids TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,              -- sha256 of the 13 values joined with '\x1f'
  UNIQUE (law_id, article_no, content_sha256)
);
CREATE INDEX article_versions_law_no ON article_versions(law_id, article_no);

CREATE TABLE corpus_releases (
  release_id TEXT PRIMARY KEY,               -- e.g. 'v5', 'v5-live-2026-11-08', 'v4-decree'
  profile TEXT NOT NULL CHECK (profile IN ('v4','v5')),
  dataset_version TEXT NOT NULL,
  reference_date TEXT NOT NULL, collected_date TEXT,
  source_name TEXT, source_url TEXT, source_ref TEXT,
  law_csv_name TEXT NOT NULL, combined_csv_name TEXT NOT NULL,
  law_columns TEXT NOT NULL, combined_columns TEXT NOT NULL,   -- JSON arrays (column order)
  law_csv_sha256 TEXT NOT NULL, combined_csv_sha256 TEXT NOT NULL,
  law_manifest TEXT NOT NULL, combined_manifest TEXT NOT NULL, -- manifest JSON text, byte-exact
  law_manifest_name TEXT NOT NULL, combined_manifest_name TEXT NOT NULL,
  origin TEXT NOT NULL,                      -- where it came from: path or 'articles-update'
  status TEXT NOT NULL CHECK (status IN ('staged','validated','failed','archived')),
  created_at TEXT NOT NULL, validated_at TEXT, note TEXT
);

CREATE TABLE corpus_release_articles (
  release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  position INTEGER NOT NULL,                 -- 0-based row order in the combined CSV
  version_id INTEGER NOT NULL REFERENCES article_versions(version_id),
  PRIMARY KEY (release_id, position),
  UNIQUE (release_id, version_id)
);
-- searchable = (deleted = '0'); in law CSV = (kind = '법률'). Both derived, not stored.

CREATE TABLE active_corpus (
  profile TEXT PRIMARY KEY CHECK (profile IN ('v4','v5')),
  release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  activated_at TEXT NOT NULL, activated_by TEXT NOT NULL
);

CREATE TABLE corpus_events (
  event_id INTEGER PRIMARY KEY,
  release_id TEXT REFERENCES corpus_releases(release_id),
  profile TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('imported','staged','validated','failed','activated','rolled_back','restart_failed')),
  previous_release_id TEXT, at TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT  -- detail: JSON
);

CREATE TABLE embedding_sets (              -- metadata of .npz caches (vectors stay in cache/embeddings)
  cache_key TEXT PRIMARY KEY, model_id TEXT NOT NULL, model_revision TEXT,
  max_seq INTEGER NOT NULL, dim INTEGER NOT NULL, n_docs INTEGER NOT NULL,
  docs_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE release_embedding_sets (
  release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  part TEXT NOT NULL CHECK (part IN ('law','decree')),
  cache_key TEXT NOT NULL REFERENCES embedding_sets(cache_key),
  PRIMARY KEY (release_id, part, cache_key)
);

-- ---------- feedback ----------
CREATE TABLE feedbacks (
  feedback_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL, ts TEXT NOT NULL,
  label TEXT NOT NULL CHECK (label IN ('like','flag','cleared')),
  reason TEXT CHECK (reason IS NULL OR reason IN ('retrieval','answer','false_refusal','other')),
  question TEXT NOT NULL, answer_raw TEXT, answer_shown TEXT,
  model TEXT, profile TEXT, dataset_version TEXT, corpus_release_id TEXT, articles_sha256 TEXT,
  commit_sha TEXT, request_id TEXT, instance TEXT,
  record TEXT NOT NULL,                      -- the full original row as JSON (lossless)
  origin TEXT NOT NULL,                      -- 'web' or 'import:<file name>:<line no>'
  CHECK ((label = 'flag') = (reason IS NOT NULL))
);
CREATE INDEX feedbacks_turn ON feedbacks(turn_id, ts);
CREATE INDEX feedbacks_ts ON feedbacks(ts);
CREATE TRIGGER feedbacks_append_only BEFORE UPDATE ON feedbacks
BEGIN SELECT RAISE(ABORT, 'feedbacks are append-only'); END;

CREATE TABLE feedback_reviews (            -- append-only history; the latest row per feedback wins
  review_id INTEGER PRIMARY KEY,
  feedback_id TEXT NOT NULL REFERENCES feedbacks(feedback_id),
  reviewer TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('correct','incorrect','uncertain')),
  decision TEXT NOT NULL CHECK (decision IN ('approved','hold','rejected')),
  scope TEXT CHECK (scope IS NULL OR scope IN ('in','out')),
  gold_articles TEXT NOT NULL,               -- JSON [{"law": "...", "article_no": "제N조"}]
  corrected_question TEXT, corrected_answer TEXT, note TEXT,
  created_at TEXT NOT NULL
);
CREATE TRIGGER feedback_reviews_append_only BEFORE UPDATE ON feedback_reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;

CREATE TABLE feedback_promotions (
  promotion_id INTEGER PRIMARY KEY,
  feedback_id TEXT NOT NULL REFERENCES feedbacks(feedback_id),
  review_id INTEGER NOT NULL REFERENCES feedback_reviews(review_id),
  kind TEXT NOT NULL CHECK (kind IN ('up','down')),
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  preference_id INTEGER REFERENCES qa_preferences(preference_id),
  approver TEXT NOT NULL, created_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active','revoked')),
  revoked_at TEXT, revoked_by TEXT, revoke_note TEXT,
  snapshot_version TEXT                      -- dataset release that first included it
);
CREATE UNIQUE INDEX one_active_promotion ON feedback_promotions(feedback_id) WHERE state = 'active';

CREATE TABLE idempotency_keys (
  actor TEXT NOT NULL, endpoint TEXT NOT NULL, key TEXT NOT NULL,
  request_sha256 TEXT NOT NULL, status_code INTEGER NOT NULL, response TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (actor, endpoint, key)
);

CREATE TABLE audit_events (
  event_id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL,
  action TEXT NOT NULL,                      -- 'login_ok','login_fail','review','promote_up','promote_down','revoke',...
  target_type TEXT, target_id TEXT,
  summary TEXT                               -- JSON; never question/answer text or secrets
);

-- ---------- QA datasets ----------
CREATE TABLE answer_keys (
  key_set TEXT NOT NULL,                     -- 'v4','v5','feedback'
  answer_key TEXT NOT NULL,
  law TEXT NOT NULL, article_id TEXT NOT NULL, answer TEXT NOT NULL,
  must_include TEXT NOT NULL, must_not_include TEXT NOT NULL, must_not_in_conclusion TEXT NOT NULL,
  row_no INTEGER,                            -- source CSV order (NULL for feedback)
  PRIMARY KEY (key_set, answer_key)
);

CREATE TABLE qa_items (
  qa_uid TEXT PRIMARY KEY,                   -- '<dataset>:<id>' or '<dataset>:#<row_no>' if ids are not unique
  dataset TEXT NOT NULL,                     -- 'v4','v5','decree_v4','reasoning_v5','oos','scope','refusal_hard','labor_eval','feedback'
  source_id TEXT NOT NULL, row_no INTEGER,
  question TEXT NOT NULL, question_norm TEXT NOT NULL,
  law TEXT, article_id TEXT, question_type TEXT, eval_cat TEXT,
  key_set TEXT, answer_key TEXT,
  row TEXT NOT NULL,                         -- full original row JSON (column order kept)
  origin TEXT NOT NULL,                      -- 'import:data/v5/qa_tax_v5.csv' or 'feedback:<feedback_id>'
  status TEXT NOT NULL CHECK (status IN ('active','retired')),
  created_at TEXT NOT NULL,
  FOREIGN KEY (key_set, answer_key) REFERENCES answer_keys(key_set, answer_key)
);
CREATE INDEX qa_items_norm ON qa_items(question_norm);
CREATE UNIQUE INDEX qa_items_row ON qa_items(dataset, row_no) WHERE row_no IS NOT NULL;

CREATE TABLE qa_gold_articles (            -- for feedback items (imported sets keep law/article_id)
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  law TEXT NOT NULL, article_no TEXT NOT NULL,
  PRIMARY KEY (qa_uid, law, article_no)
);

CREATE TABLE split_sets (
  split_set TEXT PRIMARY KEY,                -- 'v4','v5','decree_v4','feedback'
  frozen INTEGER NOT NULL CHECK (frozen IN (0,1)),
  policy TEXT NOT NULL                       -- JSON: rule, seed, group key, source file + sha256
);

CREATE TABLE qa_splits (
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  split_set TEXT NOT NULL REFERENCES split_sets(split_set),
  purpose TEXT NOT NULL CHECK (purpose IN ('emb','sllm','eval')),
  split TEXT NOT NULL CHECK (split IN ('train','val','test','unassigned')),
  group_key TEXT NOT NULL,
  row_no INTEGER,                            -- source split CSV order
  assigned_at TEXT NOT NULL,
  PRIMARY KEY (qa_uid, split_set, purpose)
);
CREATE TRIGGER qa_splits_frozen_insert BEFORE INSERT ON qa_splits
WHEN (SELECT frozen FROM split_sets WHERE split_set = NEW.split_set) = 1
BEGIN SELECT RAISE(ABORT, 'split set is frozen'); END;
CREATE TRIGGER qa_splits_frozen_update BEFORE UPDATE ON qa_splits
WHEN OLD.split <> 'unassigned' OR EXISTS (
  SELECT 1 FROM split_sets WHERE frozen = 1 AND split_set IN (OLD.split_set, NEW.split_set)
)
BEGIN SELECT RAISE(ABORT, 'assigned or frozen split cannot change'); END;
CREATE TRIGGER qa_splits_no_delete BEFORE DELETE ON qa_splits
BEGIN SELECT RAISE(ABORT, 'splits are never deleted'); END;
CREATE TRIGGER split_sets_no_unfreeze BEFORE UPDATE OF frozen ON split_sets
WHEN OLD.frozen = 1 BEGIN SELECT RAISE(ABORT, 'cannot unfreeze'); END;

CREATE TABLE qa_preferences (
  preference_id INTEGER PRIMARY KEY,
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  chosen TEXT NOT NULL, rejected TEXT NOT NULL,
  rejected_feedback_id TEXT NOT NULL REFERENCES feedbacks(feedback_id),
  verdict TEXT NOT NULL CHECK (verdict = 'incorrect'),
  tasks TEXT NOT NULL,                       -- JSON, e.g. ["dpo","kto"]; never embedding negatives
  created_at TEXT NOT NULL
);

CREATE TABLE dataset_releases (
  version TEXT PRIMARY KEY,                  -- e.g. 'v6'
  corpus_release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  created_at TEXT NOT NULL,
  manifest TEXT NOT NULL,                    -- the manifest.json text written with the snapshot
  manifest_sha256 TEXT NOT NULL
);
```

- `0002_corpus_csv_format.sql`은 `ALTER TABLE corpus_releases ADD COLUMN csv_format TEXT`로 복원 형식 전용 열을 추가한다. CSV별 BOM·줄바꿈·마지막 줄바꿈과 매니페스트 해시를 기록한다. 이전 이관 코드가 `note`에 저장한 형식은 전용 열로 옮기고 나머지 메모는 보존한다.
- `feedback_promotions`는 뒤에 선언된 `qa_items`와 `qa_preferences`를 참조한다. 테스트로 FK가 실제 동작하는지 확인한다.
- 기존 분할의 `test`는 `split_sets.frozen=1`로 넣은 뒤 바꿀 수 없다(트리거). 피드백 분할 세트 `feedback`은 `frozen=0`, `unassigned`에서 한 번만 `train`/`val`로 바뀐다.
- 기존 데이터셋 분할: `v4`·`v5`·`decree_v4`는 purpose `emb`·`sllm` 두 줄씩. 평가 전용 세트(`reasoning_v5`, `oos`, `scope`, `refusal_hard`, `labor_eval`)는 purpose `eval`, split `test`(누출 검사에서 test와 같이 취급).
- `group_key` 규칙(모든 문항에 같은 규칙): 기존 세트는 `question_norm`. 피드백 문항은 PR 10의 그룹 규칙.

### 3.5 피드백 상태

관리자 화면의 상태는 저장하지 않고 계산한다(한 곳: `feedback_store.status_of`).

| 상태 | 조건 (위에서부터 먼저 맞는 것) |
|---|---|
| `superseded` | 같은 turn_id에 더 늦은 피드백 줄이 있음(목록에는 기본으로 안 보임) |
| `promoted` | 활성(`active`) 승격이 있음 |
| `quarantined` | 누출 검사에서 test·val·평가 전용 문항과 겹침 |
| `rejected` / `hold` | 최신 검수의 decision |
| `reviewed` | 최신 검수 decision이 `approved` |
| `pending` | 검수 없음 |

- 목록은 `latest` 규칙(ts 오름차순 마지막, `cleared` 제외)을 그대로 쓴다: `qa_rules.latest_rows`를 `feedback_review`와 함께 쓴다.
- 검수는 feedback_id에 붙는다. 사용자가 나중에 같은 답의 버튼을 바꾸면(새 줄) 새 feedback_id는 `pending`이고, 이전 검수는 상세에 "이전 라벨의 검수"로 보인다.

### 3.6 승격 규칙

| 규칙 | 좋아요(up) | 문제 신고(down) |
|---|---|---|
| 최신 줄 label | `like` | `flag` (reason 보존) |
| 선행 검수 | 최신 검수 `decision=approved`, `verdict=correct` 필요 | 요청 안에서 검수를 함께 기록. `verdict=uncertain`이면 거부 |
| 질문 | `corrected_question` 있으면 그것, 없으면 원 질문 | `corrected_question` 필수 |
| 정답 | `corrected_answer` 있으면 그것, 없으면 `answer_raw` | `corrected_answer` 필수 |
| 근거 조문 | `gold_articles` 1개 이상, 모두 **현재 웹앱이 읽은 조문**에 있어야 함 | 같음 |
| 비선호 쌍 | 만들지 않음 | `verdict=incorrect`일 때만 `qa_preferences(chosen=교정 답, rejected=answer_raw)`. `correct`(답은 맞고 검색·표현 문제)면 만들지 않음 |
| 누출 검사 | 질문(교정본이 있으면 교정본과 원본 둘 다)을 test·val·평가 전용 문항과 `Overlap`으로 대조. 겹치면 `leak_test`/`leak_val`/`leak_eval` 실패, 승격하지 않음 | 같음 |
| QA 항목 | `qa_items(dataset='feedback', qa_uid='feedback:FB-<feedback_id>', key_set='feedback', answer_key='FB-<feedback_id>')`, `answer_keys`(answer=정답, must_* = `'[]'`), `qa_gold_articles` | 같음 |
| 분할 | `qa_splits(split_set='feedback', purpose='emb'/'sllm', split='unassigned')` 두 줄 | 같음 |
| 중복 | 활성 승격이 있으면 `already_promoted` | 같음 |
| 트랜잭션 | 검사(읽기)를 트랜잭션 밖에서 한 뒤, `BEGIN IMMEDIATE`에서 상태를 **다시 확인**하고 QA·정답·분할·(선호)·승격·감사를 한 번에 쓴다 | 검수 줄까지 같은 트랜잭션 |
| 취소 | `POST /api/admin/promotions/{id}/revoke`: 승격 `revoked`, QA 항목 `retired`. 분할 줄은 지우지 않음(스냅샷이 `retired`를 뺌) | 같음 |

- 좋아요만으로 승격되는 경로는 없다(검수 필수). 문제 신고만으로 오답(선호 쌍)이 되는 경로도 없다(`verdict=incorrect` 필수).
- 같은 규칙을 `lawbot_tools.feedback_export`도 쓴다: 검수 CSV의 `h_ok=맞음` ↔ `correct`, `틀림` ↔ `incorrect`, `모름` ↔ `uncertain`. 매핑은 `qa_rules`에 상수로 둔다.

### 3.7 관리자 API

공통: 모든 `/api/admin/*`는 `require_admin`(401 로그인 없음, 403 관리자 아님). POST는 `require_json` + `require_same_origin` + 본문 64 KiB 상한(413) + 세션당 분당 30회(429) + 멱등 키.

멱등: 본문의 `idempotency_key`(1–64자, `[A-Za-z0-9_-]`). `(actor, endpoint, key)`가 있고 본문 sha256이 같으면 저장된 응답을 그대로, 다르면 409 `idempotency_conflict`. 응답 저장은 처리 트랜잭션 안에서 한다.

오류 본문: `{"detail": "<한국어 안내>", "code": "<영문 코드>"}`.

| API | 요청 | 응답 |
|---|---|---|
| `GET /api/admin/feedbacks` | `status`(`pending`·`reviewed`·`hold`·`rejected`·`quarantined`·`promoted`·`all`, 기본 `pending`), `label`(`like`·`flag`), `reason`, `q`(질문 부분 일치, 100자 이하), `limit`(1–200, 기본 50), `cursor` | `{items: [{feedback_id, turn_id, ts, label, reason, status, question_preview(120자), model, profile, dataset_version, corpus_release_id, latest_review: {verdict, decision, reviewer, created_at} \| null, warnings: [code]}], next_cursor}`. 정렬 `ts DESC, feedback_id DESC`. cursor = base64url JSON `{ts, id}` |
| `GET /api/admin/feedbacks/{id}` | | `{feedback: {저장 필드 전체 + hits, citations, timing, system}, reviews: [...], promotions: [...], checks: {overlap: {qa_uid, split_set, split, how} \| null, corpus: {feedback_dataset_version, feedback_articles_sha256, current_dataset_version, current_articles_sha256, changed_hits: [{law, article_id, status: 'same'\|'changed'\|'missing'}]}}}` |
| `POST /api/admin/feedbacks/{id}/review` | `{idempotency_key, verdict, decision, scope?, gold_articles: [{law, article_no}] (0–10), corrected_question? (≤2000자), corrected_answer? (≤8000자), note? (≤2000자)}` | 201 `{review_id, status}`. 규칙: `decision=approved`면 `verdict≠uncertain`, `verdict=incorrect`+`approved`면 `corrected_answer` 필수, gold 조문은 현재 조문에 있어야 함(422 `unknown_article`) |
| `POST /api/admin/feedbacks/promote-up` | `{idempotency_key, feedback_ids: [1–50개, 중복 없음]}` | 200 `{results: [{feedback_id, ok: true, promotion_id, qa_uid} \| {feedback_id, ok: false, code, detail}]}`. 항목마다 따로 트랜잭션. 일부 실패여도 200. 코드: `not_found`, `superseded`, `not_like`, `not_reviewed`, `not_approved`, `verdict_not_correct`, `missing_gold`, `unknown_article`, `leak_test`, `leak_val`, `leak_eval`, `already_promoted` |
| `POST /api/admin/feedbacks/{id}/promote-down` | `{idempotency_key, corrected_question, corrected_answer, gold_articles (1–10), review_verdict: 'correct'\|'incorrect', review_note?, scope?}` | 201 `{review_id, promotion_id, qa_uid, preference_id \| null}`. 실패는 409/422 + 위 코드 |
| `POST /api/admin/promotions/{promotion_id}/revoke` | `{idempotency_key, note}` | 200 `{promotion_id, state: 'revoked'}` |
| `GET /api/admin/articles?q=` | 법령명·조문 번호 부분 일치(근거 조문 입력 자동완성) | `{items: [{law, article_no, title, kind}]}` 최대 20개 |
| `GET /api/admin/calendar/{path}` | 허용: `law-calendar`, `law-calendar/status`, `law-events`, `law-changes/{id}`, `law-changes/{id}/notice-comparison/{page}`. 쿼리는 그대로 전달 | 캘린더 응답의 상태·본문·`content-type`을 그대로. 미설정 503 `calendar_not_configured`, 연결 실패 502, 10초 시간 초과 504. 허용 밖 경로 404 |

### 3.8 인증

- 비밀번호 해시: 표준 라이브러리 `hashlib.scrypt`(n=2**14, r=8, p=1, 16바이트 salt, 32바이트 결과). 비교는 `hmac.compare_digest`. 새 의존성 없음.
- 해시 만들기: `uv run python -m lawbot.auth hash` → `getpass`로 두 번 입력받아 한 줄 출력. 사용자가 `.env`에 붙여 넣는다. `$`가 있으므로 값을 작은따옴표로 감싸라고 안내한다(envfile의 `$` 처리는 PR 6에서 테스트로 확인).
- 로그인: 기존 `/api/login` 하나. 일반 계정(기존 평문 비교, 바꾸지 않음) 또는 관리자 계정(사용자명 비교 + scrypt). 사용자명이 관리자와 다르면 가짜 해시로 scrypt를 한 번 돌려 시간 차이를 줄인다.
- 세션: `Sessions` 값에 role 추가 `(user, sid, created, role)`. 쿠키·TTL·최대 수는 그대로. 클라이언트 입력의 role은 읽지 않는다.
- 로그인 제한: 사용자명마다 15분에 실패 5회면 15분 동안 429(메모리). 기존 1초 지연 유지. 관리자 로그인 성공·실패는 `audit_events`(DB가 있을 때).
- Origin 검사(관리자 POST): `Origin` 헤더가 있어야 하고 `f'{request.url.scheme}://{request.headers["host"]}'`와 같아야 한다. 없거나 다르면 403 `bad_origin`. Tailscale 경유 실제 값은 운영 반영 때 확인(7절).
- 일반 사용자 비밀번호 해시 전환은 이번 범위 밖(명세서도 별도 마이그레이션).

### 3.9 공유 UI (`packages/lawbot-ui`)

- 소스만 둔다: `packages/lawbot-ui/src/{MonthCalendar,EventList,ChangeView,Modal}.tsx`, `dates.ts`, `types.ts`, `calendar.css`, 테스트. 자체 `package.json`·빌드 없음(D2).
- 소비: 두 프론트엔드의 `vite.config.ts`에 `resolve.alias['@lawbot/ui'] = <repo>/packages/lawbot-ui/src`, `resolve.dedupe = ['react','react-dom']`, `tsconfig.json`의 `paths`·`include`에 같은 경로. React 등 의존성은 각 프론트엔드의 `node_modules`를 쓴다.
- 테스트: 공유 컴포넌트 테스트는 `packages/lawbot-ui/src/*.test.tsx`에 두고 **캘린더 프론트엔드의 Vitest**가 `test.include`로 함께 돌린다(CI 명령 그대로).
- 지문: `services/chatbot/frontend/fingerprint.ts`와 `lawbot/web.py`의 `SOURCES`에 공유 소스 폴더를 넣는다(두 구현이 같은 규칙). 레포 최상위 기준 상대 경로를 쓰도록 지문 규칙을 확장하고 기존 지문 테스트를 고친다.
- CSS: `lc-` 접두사 클래스 그대로 공유 `calendar.css`로 옮긴다. 캘린더 화면 전용 스타일은 `calendar.css`에 남긴다.
- `ChangeDialog`: 표시 부분(`ChangeView`)과 모달 틀(`Modal`)을 나눠 공유하고, 공개 캘린더의 `ChangeDialog`는 둘을 조합만 한다. `LawCalendarPage`, `data.ts`, `HoldDialog`, `api-types.ts`는 옮기지 않는다.

### 3.10 코퍼스 런타임 (PR 11)

- `LAWBOT_CORPUS=db`면 웹앱 시작 때 `corpus_store.active_release(conn, profile)` → 법률·조합 CSV **바이트**를 DB에서 만든다(`to_csv` 규칙: BOM, `index=False`, `lineterminator='\n'`, 저장한 열 순서). 그 sha256이 릴리스의 `*_csv_sha256`과 다르면 시작 거부.
- `Retriever`와 `manifest.verify`는 경로 대신 바이트도 받게 한다(`pd.read_csv(io.BytesIO(data), encoding='utf-8-sig')`). 읽는 코드 경로가 CSV 파일과 같으므로 문서 문자열·순서·dtype이 같고, 임베딩 캐시 키도 같다 → **같은 .npz를 그대로 쓴다**(모델 호출 없이 검색 동등성 증명).
- 웹앱이 쓰는 릴리스 id·데이터 버전·sha를 `LawbotApp`에 두고 요청 로그·피드백·`check_deploy` 출력에 남긴다. 실행 중에는 바뀌지 않는다.
- 평가 도구(`eval_v4` 등)는 지금처럼 CSV를 읽는다. DB 릴리스를 평가하려면 `lawbot-db export-release --release <id> --out <폴더>`로 CSV를 만든다(바이트가 원본과 같음).

### 3.11 조문 갱신 (PR 12)

`LAWBOT_CORPUS=db`일 때 `lawbot-articles-update`의 흐름:

1. 잠금: `<DB 폴더>/.articles-update.lock`(기존 `.update.lock`과 같은 방식).
2. 현재 활성 릴리스 바이트로 기존 `articles_check.check_with_files`를 돌린다(경로 대신 바이트를 받도록 최소 수정). 같으면 종료 0.
3. 다르면 생성 결과를 `corpus_releases(status='staged')`로 저장(짧은 트랜잭션). 매니페스트는 기존 `reindex.new_manifest`로 만든다. 릴리스 id `v5-live-<기준일>`(같은 날 두 번이면 `_2`, `_3`).
4. 트랜잭션 밖에서 `reindex.validate`와 `gate`(기존 검사)를 돌린다. 임베딩 캐시도 이때 만들어진다. 실패하면 `status='failed'` + 이벤트 + 종료 2.
5. 성공하면 `validated` → 한 트랜잭션에서 `active_corpus`를 바꾸고 `activated` 이벤트(이전 릴리스 id 포함).
6. `--restart`면 기존 `do_restart`. 웹앱이 응답하지 않으면 `active_corpus`를 이전 릴리스로 되돌리고(`rolled_back` 이벤트) 다시 재시작, 종료 2.
7. `--rollback`: `activated` 이벤트의 `previous_release_id`로 되돌림 + 재시작.
8. 마지막 줄 형식과 종료 코드는 그대로(`archive=` 자리에 이전 릴리스 id). 보고서 md도 그대로 쓴다.

`LAWBOT_CORPUS=csv`(기본)면 지금 코드 경로를 그대로 탄다. 두 경로가 같은 데이터를 동시에 갱신하는 일은 없다(설정 하나가 고른다).

---

## 4. PR별 체크리스트

### PR 1 — 계획 문서 (이 PR, 브랜치 `docs/sqlite-admin-pipeline-plan`)

- [x] 명세서 원문 `docs/plans/sqlite-admin-pipeline-spec.md`
- [x] 이 계획서
- [x] 사용자 결정 D1–D7(8절) 답변 반영: 2026-11-07 전부 권장안

### PR 2 — DB 기반 (`feat/sqlite-db-foundation`)

목표: 빈 DB를 만들고 마이그레이션·백업·복원을 할 수 있다. 웹앱 동작은 바뀌지 않는다.

- [x] `.gitignore`에 `data/db/` 추가(주석: 질문 원문 포함, `lawbot.db`). `git check-ignore -v data/db/lawbot.db`로 확인.
- [x] Red `services/chatbot/tests/test_db.py`:
  - [x] `init`이 새 파일을 만들고 `schema_migrations`에 0001이 있다. 파일 권한 600, 폴더 700.
  - [x] 이미 있는 파일에 `init`하면 실패하고 파일이 그대로다(내용 sha 비교).
  - [x] `connect`가 `foreign_keys=1`, `journal_mode=wal`, `busy_timeout=5000`을 돌려준다.
  - [x] FK 위반 INSERT가 `sqlite3.IntegrityError`.
  - [x] 적용된 마이그레이션 파일의 checksum이 바뀌면 `migrate`가 실패.
  - [x] 스키마 버전 검사: 낮음·높음·없음이 각각 다른 오류 문구.
  - [x] 트리거: `feedbacks` UPDATE 거부, frozen 세트 INSERT·UPDATE 거부, `qa_splits` DELETE 거부, `unassigned`→`train` 한 번만 허용, `frozen` 해제 거부, `one_active_promotion` 중복 거부.
  - [x] 백업: 쓰기 연결이 열린(WAL에 커밋 안 된 체크포인트가 있는) 상태에서 백업 → 복원 → 행 수·내용 sha 동일, `integrity_check == 'ok'`. 대상 파일이 있으면 거부.
  - [x] `restore`는 새 경로에만 쓰고 기존 파일 덮어쓰기를 거부.
- [x] Green: `lawbot/db/{connection,migrate,backup,cli}.py`, `migrations/0001_init.sql`(3.4 DDL).
- [x] `services/chatbot/pyproject.toml`에 `lawbot-db = "lawbot.db.cli:main"`.
- [x] `lawbot.settings`에 `LAWBOT_DB` 읽기(아직 아무도 필수로 요구하지 않음)와 `check_private`. `test_settings.py`에 경로 검사 테스트. `conftest.ENV_KEYS`에 새 변수.
- [x] `test_layers.py`: `lawbot.db`가 fastapi·lawbot_tools를 import하지 않음. `sqlite3` import는 `lawbot/db/`·`*_store.py`만(소스 grep 테스트).
- [x] `docs/operations.md`에 "데이터 DB" 절: 변수, init·migrate·backup·restore 명령, 권한, 복구는 새 경로 복원 후 사람이 교체.
- [x] 완료 기준: 기본 테스트·린트·타입 Green. 웹앱 시작 경로 변화 없음(`LAWBOT_DB` 미설정 시 지금과 같음).

### PR 3 — 공통 검증 규칙 추출 + 기존 도구 v5 지원 (`fix/feedback-tools-v5`)

목표: 관리자 API와 검수 도구가 같은 규칙을 쓰고, v5 피드백이 빠지지 않는다. DB는 아직 안 씀.

- [x] Red `services/chatbot/tests/test_qa_rules.py`: 기존 `feedback_review`의 `norm`, `Overlap.match`, `latest` 테스트를 `lawbot.qa_rules`로 옮겨 같은 기대값으로 통과(동작 불변).
- [x] Red `test_feedback.py`(또는 기존 도구 테스트 파일):
  - [x] `eval_sets()`가 v5 test 3,420 + v5 val 2,052 + `qa_reasoning_val` 30을 포함한다(개수 단언).
  - [x] v5 test 질문과 같은 합성 피드백이 `review()`에서 빠진다.
  - [x] `feedback_export`가 `profile='v5'` 기록을 내보내고, 조문은 v5 CSV(기본: 기록의 profile에 맞는 `deploy.article_paths`)에서 찾는다. `profile`이 v4·v5가 아니면 빼고 건수 출력.
- [x] Green: `lawbot/qa_rules.py`(`norm`, `Overlap`, `latest_rows`, `OK_TO_VERDICT` 매핑, `LEAK_SPLITS=('val','test')`). `feedback_review`·`feedback_export`는 여기서 import(복붙 금지). `eval_sets`에 v5 세트 추가, `feedback_export`의 v4 고정 제거.
- [x] `docs/` 의 피드백 검수 안내(있으면 `operations.md`)에 v5 반영.
- [x] 완료 기준: 기존 피드백 도구 테스트 기대값 불변 + 새 테스트 Green.

### PR 4 — CSV·JSONL → SQLite 이관 도구 (`feat/migrate-csv-to-sqlite`)

목표: 원본과 바이트 단위로 같은 내용을 DB에 넣고 되돌려 만들 수 있다. 운영 데이터는 건드리지 않는다.

- [x] 도구 `lawbot_tools/migrate_csv_to_sqlite.py`, 하위 명령:
  - `corpus --db PATH --dir <CSV·매니페스트 폴더> --profile v4|v5 --release-id ID [--activate]`
  - `qa --db PATH --set v4|v5|decree_v4|reasoning_v5|oos|scope|refusal_hard|labor_eval` (파일 경로는 세트별 상수, `--file`로 덮어쓰기)
  - `feedback --db PATH <폴더·파일>...`
  - `verify --db PATH` (DB의 복원 해시와 기록된 원본 파일 상태를 각각 확인)
  - 공통: `--report <md 경로>`(질문 원문 없음: 행 수·키·sha만), `--dry-run`(DB 열지 않고 검사만)
  - CLI는 인자 처리와 보고를 맡긴다. SQL은 `lawbot/db/` 또는 `lawbot/*_store.py`에 두고 `lawbot_tools`의 `sqlite3` import를 막는 `test_layers.py` 규칙을 유지한다.
- [x] 동작 규칙:
  - [x] 입력 검사: 파일 존재, UTF-8 인코딩, 기대 열, 행 수, id 유일성(세트별: 유일하지 않으면 `#row_no` 규칙), 정답 키 CSV가 있는 세트의 참조 무결성, 분할 값(`train`·`val`·`test`).
  - [x] 파일의 실제 바이트에서 LF·CRLF와 BOM 여부를 검출한다. 열 순서와 함께 `split_sets.policy` 또는 릴리스의 메타데이터에 기록한다.
  - [x] 정답 키 CSV가 있는 `v4`·`v5`는 `qa_items.key_set`·`answer_key`를 모두 채워 FK를 강제한다. 원본의 모든 키가 정답 키 CSV에 있어야 한다. `decree_v4`와 평가 전용 세트는 두 열을 모두 NULL로 두고 원본 행 JSON에 값을 보존한다. 이 규칙을 세트 정책에 기록한다.
  - [x] 한 세트·한 릴리스 = 한 트랜잭션. 실패하면 롤백해 DB가 전과 같다.
  - [x] 멱등: 같은 입력을 다시 넣으면 변경 0건으로 끝난다. 같은 id에 다른 내용이면 실패(덮어쓰지 않음).
  - [x] 분할 세트 `v4`·`v5`·`decree_v4`와 평가 전용 세트는 넣은 직후 `frozen=1`.
  - [x] 피드백: 깨진 JSON이나 유효하지 않은 줄은 건수·줄 번호만 보고하고 건너뛴다. `record`에 원본 줄의 dict 전체를 보존한다. 건너뛴 줄이 있으면 유효한 줄을 저장하고 종료 코드 1을 반환한다. 배치 전체 실패는 2다.
  - [x] `--db`는 필수이고 기본값이 없다. 쓰기 전에 대상 DB 경로와 넣을 행 수를 출력한다. 운영 DB 이관은 7절 절차로만 한다(`--dry-run` 먼저).
- [x] Red `services/chatbot/tests/test_migrate_csv_to_sqlite.py` (레포의 실제 `data/` 파일을 읽고 `tmp_path` DB에 쓴다):
  - [x] v5 릴리스(`data/v5`): 법률·조합 CSV를 DB에서 다시 만든 바이트가 원본과 같다(sha256). 매니페스트 텍스트도 같다.
  - [x] v4 릴리스(`data/v4`, 매니페스트 `manifest.json`·`manifest_decree.json`)도 같다.
  - [x] 두 릴리스에 같은 내용 조문은 `article_versions`를 공유한다(버전 수 < 두 릴리스 조문 수 합).
  - [x] 모든 비삭제 조문의 `doc_text`가 CSV에서 만든 것과 DB에서 만든 것이 같다.
  - [x] QA 세트 8종(`v4`, `v5`, `decree_v4`, `reasoning_v5`, `oos`, `scope`, `refusal_hard`, `labor_eval`)과 각 세트에 딸린 split·answer_key CSV: 원본 바이트 왕복 동일, 행 수, id·answer_key 참조, 분할 집계(2.2 표의 숫자) 일치.
  - [x] `decree_v4`의 관계형 정답 키 두 열은 NULL이며 원본 키 문자열은 `row` JSON과 복원 CSV에 그대로 남는다.
  - [x] frozen 세트 test id 집합이 원본 split CSV의 test id 집합과 같다(v4 2,075 · v5 3,420 · decree 1,880).
  - [x] 두 번 실행해도 행 수 변화 없음. 원본 한 칸을 바꾼 사본을 넣으면 실패하고 DB 그대로.
  - [x] 합성 피드백 JSONL(like·flag 4종·cleared·깨진 줄·같은 turn 두 번): 모든 키 손실 없음(`record` JSON == 원본 dict), `latest_rows` 결과가 JSONL 기준과 DB 기준이 같다.
  - [x] 보고서에 질문 원문이 없다(합성 질문 문자열이 보고서에 없음을 단언).
- [x] 유사도 교차 보고(테스트 아님, 1회 실행). 승인 후 10분 상한으로 v4·v5의 기존 분할을 비교했다. [쌍 수와 ID 기록](../evals/2026-11-08-split-similarity.md)
- [x] `docs/operations.md`: 이관 명령과 검증 명령.
- [x] 완료 기준: 위 테스트 Green, `verify`가 모든 세트 OK. 운영 트리·`data/` 원본 변경 없음(`git status`).

### PR 5 — 피드백 저장을 SQLite로 + 턴 기록 보강 (`feat/feedback-sqlite`)

- [x] Red `test_feedback.py`·`test_web.py`·`test_settings.py`(DB 저장을 확인하고 기존 관찰 동작을 유지):
  - [x] `/api/feedback` 계약(요청·응답·400·404·2초 중복 무시) 그대로.
  - [x] `record` JSON에 기존 JSONL의 모든 키와 새 키를 저장한다.
  - [x] 새 턴 기록 필드 `articles_sha256`(웹앱이 읽은 조문 CSV의 SHA-256), `emb_model`, `rerank_model`, `chat_model`(모델 ID), `system_sha256`, `corpus_release_id`(CSV 모드면 null).
  - [x] DB 쓰기 실패는 답변을 막지 않고 경고 로그만 남긴다. 응답 `saved`는 중복 여부를 뜻한다(결정 D1).
  - [x] `LAWBOT_DB`가 비어 있으면 기존 JSONL 설정과 저장을 유지한다. 지정한 DB가 없거나 스키마가 맞지 않으면 시작 오류, `LAWBOT_FEEDBACK_DIR`를 함께 지정하면 이관 안내를 반환한다.
  - [x] 피드백 모듈이 fastapi를 import하지 않음(기존 테스트 유지).
- [x] Green `feedback_store.append(conn, row)`를 추가하고 `Feedback.write`가 DB에 쓰도록 한다. 메모리 턴 기록·turn_id 규칙은 유지한다.
- [x] `feedback_review`에 `--db PATH` 입력을 추가한다. JSONL과 동일한 최신 상태 규칙과 CSV 형식을 사용한다.
- [x] e2e_stub에서 로그인 → 질문 → 피드백 버튼 → tmp DB 저장을 확인한다(`e2e_support`의 환경에 `LAWBOT_DB`를 tmp 경로로, 테스트 시작 때 `lawbot-db init`).
- [x] `.env.example`·`.env.dev.example`·`operations.md`에 `LAWBOT_DB` 설명과 DB를 쓸 때 `LAWBOT_FEEDBACK_DIR`를 비우도록 안내한다.
- [x] 기본 테스트 Green. 운영 반영 시 DB 생성이 먼저 필요하다는 점을 PR 설명의 "병합 전 확인"에 적었다. [검증 기록](../evals/2026-11-08-feedback-sqlite.md)

### PR 6 — 관리자 인증 (`feat/admin-auth`)

- [ ] Red `services/chatbot/tests/test_auth.py`:
  - [ ] `hash_password` → `verify_password` 참, 다른 비밀번호 거짓, 형식 틀린 해시는 `ValueError`.
  - [ ] 설정: 관리자 변수 하나만 있음·사용자명이 일반 계정과 같음·`LAWBOT_DB` 없음 → 각각 설정 오류(값은 출력 안 함).
  - [ ] `.env`에 작은따옴표로 감싼 해시(`$` 포함)가 그대로 읽힌다(`lawbot_common.envfile`).
- [ ] Red `test_web.py` 확장:
  - [ ] 일반 로그인 → `/api/me` = `{user, role: 'user'}`. 관리자 로그인 → `role: 'admin'`.
  - [ ] 요청 본문에 `role: 'admin'`을 넣어도 일반 계정은 `user`.
  - [ ] 로그인 실패 5회 → 6번째는 비밀번호가 맞아도 429, 시간이 지나면(시계 주입) 풀림.
  - [ ] 관리자 로그인 성공·실패가 `audit_events`에 남고 비밀번호·해시가 없다.
  - [ ] 쿠키 속성(HttpOnly, SameSite=Strict, http에서 Secure 없음) 그대로.
- [ ] Green: `lawbot/auth.py`, `web.create_app`에 관리자 계정·DB 경로 전달, `Sessions` role, `/api/me` 확장. `require_admin`, `require_same_origin`, 본문 크기·분당 제한 의존성(아직 쓰는 라우트 없음 → 테스트용 더미 라우트로 검증하지 말고 PR 7과 함께 쓰이도록 함수 단위 테스트).
- [ ] 프론트 `api.me` 타입을 `{user, role}`로(화면 변화 없음), `App.test.tsx` 갱신.
- [ ] `operations.md`: 관리자 계정 만들기(`python -m lawbot.auth hash`), 변수 표.
- [ ] 완료 기준: 기본 테스트 Green, 기존 로그인 E2E 그대로 통과.

### PR 7 — 관리자 API (`feat/admin-feedback-api`)

- [ ] Red `services/chatbot/tests/test_admin_api.py`(TestClient + tmp DB + 합성 피드백·합성 조문 일부):
  - [ ] 모든 `/api/admin/*`: 로그인 없음 401, 일반 사용자 403, 관리자 200.
  - [ ] POST: `Origin` 없음·다름 403, `content-type` JSON 아님 415, 64 KiB 초과 413, 분당 31번째 429.
  - [ ] 목록: 상태·label·reason·q 필터, 정렬, cursor로 넘겨도 중복·누락 없음(합성 120건), `superseded`·`cleared`는 기본 목록에 없음.
  - [ ] 상세: hits·citations·검수 이력·승격 이력, `checks.corpus.changed_hits`(합성 조문 하나를 바꿔 `changed` 확인).
  - [ ] review: 규칙 위반 422(각 코드), 정상 201, 이력 append-only(두 번 저장하면 두 줄).
  - [ ] promote-up: 검수 없음·uncertain·rejected·hold·gold 없음·없는 조문·flag 줄 → 각 실패 코드. 정상 → `qa_items`·`answer_keys`·`qa_gold_articles`·`qa_splits`(emb·sllm `unassigned`)·`feedback_promotions`·`audit_events` 한 번씩.
  - [ ] promote-up 배치: 성공·실패 섞임 → 200, 항목별 결과. 같은 키로 재요청 → 같은 응답, 행 수 그대로. 같은 키·다른 본문 → 409. 새 키로 재요청 → `already_promoted`.
  - [ ] 누출: v5 test 질문(DB에 이관된 세트)과 같은/유사(0.9 이상) 질문 → `leak_test`, val → `leak_val`, 평가 전용 → `leak_eval`, 상태 `quarantined`. **교정 질문만 다르고 원 질문이 test와 같아도 차단.**
  - [ ] promote-down: `verdict=incorrect` → `qa_preferences` 1줄(chosen=교정 답, rejected=answer_raw). `verdict=correct` → 선호 없음. `uncertain` → 422. flag reason 보존(원본 줄 불변).
  - [ ] 동시성: 같은 피드백을 두 스레드에서 동시에 promote → 하나만 성공, 다른 하나 `already_promoted`(유니크 인덱스).
  - [ ] revoke: 승격 `revoked`, QA `retired`, 다시 승격 가능.
  - [ ] 트랜잭션 원자성: QA 쓰기 뒤 승격 INSERT에서 예외를 주입 → 아무것도 남지 않음.
  - [ ] 캘린더 프록시(httpx `MockTransport`): 허용 경로 전달·쿼리 전달·상태 코드 전달, 허용 밖 404, 미설정 503, 연결 실패 502, 시간 초과 504. 쓰기 메서드(POST 등)는 405.
- [ ] Green: `lawbot/admin.py`(APIRouter), `feedback_store`·`dataset_store`의 조회·쓰기 함수, `qa_rules`의 누출 검사(평가·val·test 문항은 DB `qa_items`+`qa_splits`에서 읽어 앱 시작 때 한 번 `Overlap` 생성, 피드백 세트는 제외).
- [ ] 웹앱 시작: 관리자 설정이 있으면 DB의 이관 세트(v4·v5·decree_v4·평가 전용)가 있는지 확인, 없으면 시작 거부("migrate_csv_to_sqlite qa …를 먼저").
- [ ] `integration` 테스트(`test_web_integration.py` 방식): 실제 웹앱 프로세스에서 관리자 로그인 → 목록 → review → promote-up 한 번.
- [ ] `docs/architecture.md`·`operations.md`에 관리자 API 표.
- [ ] 완료 기준: 명세서 Phase 2 완료 조건(401/403, Origin, 중복 승격, 검수 이력, 누출 차단) 전부 테스트로 있음.

### PR 8 — 공유 UI 추출 (`refactor/lawbot-ui-package`)

목표: 공개 캘린더 동작·화면·테스트가 그대로이고 컴포넌트만 공유 위치로 옮겨진다.

- [ ] 옮기기 전 기준: 캘린더 프론트엔드 `npm test` 결과(테스트 이름 목록)와 `npm run build` 산출물 크기를 PR 설명에 기록.
- [ ] `packages/lawbot-ui/src/`로 `MonthCalendar.tsx`, `EventList.tsx`, `dates.ts`, 컴포넌트가 쓰는 타입(`types.ts`에서 해당 부분), `ChangeView.tsx`(ChangeDialog의 본문 표시부), `Modal.tsx`(포커스 가두기·Esc·라벨 연결), 공유 CSS. 각 테스트(`dates.test.ts`, `ChangeDialog.test.tsx`의 표시부)도 함께.
- [ ] 캘린더 쪽은 `@lawbot/ui`에서 import. `LawCalendarPage`, `data.ts`, `HoldDialog`, `api-types.ts`, `demo.ts`는 그대로.
- [ ] 두 프론트엔드 `vite.config.ts`·`tsconfig.json`에 alias·dedupe·paths. 캘린더 Vitest `test.include`에 공유 테스트 경로.
- [ ] 챗봇 화면 지문(`fingerprint.ts`, `web.py SOURCES`)에 공유 폴더 포함 + 테스트: 공유 파일 한 글자 변경 → `require_build`가 거부.
- [ ] Red→Green: 기존 캘린더 테스트 전부 통과(이름 목록 동일, 옮긴 테스트는 공유 위치에서 실행됨), 캘린더 e2e_stub 통과.
- [ ] CI 파일은 바꾸지 않아도 되는지 확인(공유 소스는 각 프론트엔드 빌드에 포함됨). 바꿔야 하면 이유를 PR에 적는다.
- [ ] 완료 기준: 두 프론트엔드 typecheck·test·build Green, 공개 캘린더 화면 차이 없음(e2e_stub 스크린샷 비교는 하지 않고 기존 E2E 단언으로 판단).

### PR 9 — 관리자 화면 (`feat/admin-frontend`)

- [ ] `services/chatbot/frontend/src/admin/`:
  - `AdminPage.tsx`: 위 캘린더 영역, 아래 좌(좋아요)·우(문제 신고) 두 패널. 휴대폰 폭에서는 세로로 쌓기.
  - `CalendarPanel.tsx`: `@lawbot/ui`의 `MonthCalendar`·`EventList`·`ChangeView`+`Modal`, 데이터는 `/api/admin/calendar/...`. 503이면 "캘린더 연결 안 됨" 안내.
  - `FeedbackList.tsx`: 상태 필터, 검색, 더 보기(cursor), 다중 선택(좋아요 패널만), 경고 배지(`quarantined`, 조문 버전 다름, 라벨 바뀜).
  - `FeedbackDetail.tsx`: 질문, answer_raw·answer_shown(둘이 다를 때 둘 다), 참고 조문, 인용 판정, 검수·승격 이력.
  - `FeedbackCorrectionDialog.tsx`: 명세서 5.2의 7개 요구사항. 원 질문은 읽기 전용으로 따로 표시, "기존 답변을 초안으로 복사" 버튼(복사해도 판정은 비어 있음), 근거 조문 추가(`/api/admin/articles` 자동완성)·삭제, 당시 검색 조문과 현재 조문의 `changed_hits` 표시, 판정 라디오(정답·오답·판정 보류), 메모, 버튼 [검수 저장] [승격] [보류] [반려]. 승격 버튼은 필수 입력이 비었거나 `quarantined`면 비활성 + 이유 문구.
  - `adminApi.ts`: 멱등 키를 버튼 한 번 누를 때 하나 만들고(`crypto.randomUUID()`), 같은 동작 재시도에 같은 키를 쓴다.
- [ ] `App.tsx`: `role === 'admin'`일 때만 [채팅] / [피드백 & 법령 관리] 전환(`Segmented` 재사용). 일반 사용자는 메뉴 없음.
- [ ] Red Vitest:
  - [ ] 일반 사용자에게 전환 메뉴 없음, 관리자에게 있음.
  - [ ] 목록 로딩·빈 목록·오류(401이면 로그인 화면, 403이면 권한 안내).
  - [ ] 교정 다이얼로그: Tab 순서·Esc 닫기·첫 입력 포커스·라벨 연결, 필수 누락 시 승격 비활성, 오류 응답 코드별 문구(`leak_test` 등), 저장 중 버튼 비활성, 같은 동작 재시도 시 같은 멱등 키.
  - [ ] 배치 승격 결과의 성공·실패 항목 표시.
- [ ] e2e_stub(`test_e2e_stub.py` 확장): 관리자 로그인 → 관리 화면 → 좋아요 하나 검수 저장 → 승격 → 상태 `promoted`. 일반 사용자 로그인 → 메뉴 없음 + `/api/admin/feedbacks` 직접 호출 403. 캘린더 영역은 `LAWBOT_CALENDAR_API_URL` 미설정 안내를 확인(캘린더 서버까지 띄우는 E2E는 기존 캘린더 e2e_stub 픽스처가 재사용 가능할 때만 추가).
- [ ] 화면 요소는 역할·라벨·`data-testid`로 찾는다(AGENTS.md).
- [ ] 완료 기준: 명세서 Phase 3 완료 조건.

### PR 10 — 스냅샷 내보내기와 분할 배정 (`feat/dataset-snapshot`)

- [ ] `lawbot_tools/assign_splits.py --db PATH --split-set feedback --val-frac 0.1 --seed 42`:
  - 그룹: 활성 피드백 문항(이미 배정된 문항 포함)을 `Overlap`(0.9)으로 묶는다(합집합-찾기). 그룹이 기존 v4·v5 **train** 문항과 겹치면 기록만(분할은 피드백 세트 안에서만 정함).
  - 그룹 키는 **한 번 정하면 바뀌지 않는다**. 목적(emb·sllm)마다 따로 판단:
    - 그룹에 이미 배정된 문항이 있고 그 분할이 모두 같으면: 새 문항은 그 분할과 그 문항들의 `group_key`를 **상속**한다(해시를 다시 계산하지 않음).
    - 그룹에 배정된 문항의 분할이 서로 다르면(새 문항이 train 그룹과 val 그룹을 이어 붙인 경우): 그 그룹의 새 문항은 배정하지 않고 `unassigned`로 남긴 뒤 충돌로 보고하고 종료 코드 1. 기존 배정은 바꾸지 않는다(사람이 해당 피드백 승격을 취소하거나 보류).
    - 그룹 전체가 `unassigned`일 때만 새 키 = 그룹 안 가장 작은 `qa_uid`, `sha256(f'{seed}:{group_key}')`를 [0,1)로 바꿔 `< val_frac`이면 val, 아니면 train. 목적별 frac은 옵션.
  - 이미 배정된 줄은 바꾸지 않는다(트리거가 막음). 결과(배정·상속·충돌 수)와 정책을 `split_sets.policy`에 추가 기록.
  - 같은 이유로 PR 7의 승격 단계에서도, 새 문항이 이미 배정된 피드백 문항과 유사하면 상세의 `checks.overlap`에 그 문항과 분할을 경고로 보여 준다(차단은 하지 않음, 배정은 위 규칙이 정함).
- [ ] `lawbot_tools/export_dataset_snapshot.py --db PATH --version v6 --corpus-release ID --out data/v6/`:
  - [ ] 한 읽기 트랜잭션(`BEGIN` … 읽기만)에서 모두 읽는다.
  - [ ] 출력(이름은 기존 도구 계약에 맞춤): `articles_v6.csv`·`law_articles_v6_decree.csv`(릴리스 CSV 바이트 그대로, 이름만 버전) + 매니페스트 2개(`manifest.json`·`manifest_law_articles_v6_decree.json`). 매니페스트는 릴리스의 JSON에서 **`articles_file`만 새 CSV 이름으로 바꾸고** 나머지 값(dataset_version·sha256·조문 수 등)은 그대로, 기존 `reindex`와 같은 형식(`json.dumps(ensure_ascii=False, indent=2) + '\n'`)으로 쓴다. 원래 매니페스트의 sha256은 스냅샷 `manifest.json`에 기록. 이름을 바꾸지 않는 `lawbot-db export-release`는 매니페스트도 바이트 그대로, `qa_<name>.csv`, `split_<name>.csv`(`id, eval_cat, split_emb, split_sllm`), `answer_key_<name>.csv`(기존 열), 피드백 산출물 `fb_questions.csv`·`fb_emb_pairs.csv`·`fb_sft.csv`·`fb_kto.csv`(기존 `feedback_export` 열과 같음) + `fb_preferences.jsonl`(`prompt, chosen, rejected`).
  - [ ] `fb_emb_pairs.negative`는 검수자가 지정한 오답 조문만(이번 범위에는 지정 UI가 없으므로 빈 값). **`rejected` 답변을 negative로 쓰지 않는다.**
  - [ ] test·평가 전용 문항은 학습 산출물(`fb_*`, split의 train/val)에 나오지 않는다.
  - [ ] `manifest.json`: DB 경로 이름(전체 경로 아님), 스키마 버전, 코퍼스 릴리스 id·sha, 분할 정책·시드, 그룹 수, 모델 id·system sha(피드백 기록에서), 생성 시각, 파일마다 행 수·sha256.
  - [ ] 출력 폴더가 있으면: 같은 버전이 `dataset_releases`에 있고 새로 만든 파일 해시가 모두 같으면 "같음"으로 끝(0), 다르면 실패(덮어쓰지 않음). `--new-version`은 만들지 않는다(사용자가 다른 버전 이름을 준다).
  - [ ] 출력 폴더가 레포 안이면 `check_private`(질문 원문). `data/v6/`는 `.gitignore`에 추가(결정 D5).
- [ ] Red `test_export_dataset_snapshot.py`:
  - [ ] DB에 이관한 v5 세트를 v5 이름으로 내보내면 `qa_tax_v5.csv`·`split_tax_v5.csv`·`answer_key_tax_v5.csv`가 원본과 바이트가 같다(기존 도구 입력 계약 증명).
  - [ ] `eval_v4.load_tax`·`keywords('v5')`·`score_answers`가 내보낸 파일을 그대로 읽는다(모델 없이, 함수 호출만).
  - [ ] 같은 그룹이 train·val에 동시에 없음(합성 유사 질문 3개 → 같은 그룹).
  - [ ] 증분 배정: 1차 실행 뒤 기존 그룹에 더 작은 `qa_uid`의 유사 문항을 추가하고 2차 실행 → 새 문항은 기존 분할·`group_key`를 상속(해시가 다른 분할을 가리키는 경우를 골라 만든다).
  - [ ] 증분 배정 충돌: train 그룹과 val 그룹 둘 다와 유사한 새 문항 → 배정 안 됨(`unassigned`), 충돌 보고, 종료 코드 1, 기존 배정 불변.
  - [ ] 이름을 바꾼 스냅샷: `manifest.load(out/'articles_v6.csv')`·`manifest.load(out/'law_articles_v6_decree.csv')`가 각각 매니페스트를 찾고 `manifest.check`가 통과(sha·조문 수 일치).
  - [ ] 재실행 해시 동일, DB에 피드백 하나 추가 후 같은 버전 → 실패.
  - [ ] revoked/retired 문항 제외, 선호 쌍은 `verdict=incorrect`만.
- [ ] 완료 기준: 명세서 Phase 4 완료 조건. 기존 고정 Test 불변은 frozen 트리거 + 바이트 동일 테스트로 증명.

### PR 11 — 웹앱 코퍼스를 DB 활성 릴리스에서 읽기 (`feat/corpus-from-db`)

- [ ] Red:
  - [ ] `LAWBOT_CORPUS=db` + v5 릴리스 활성 → `load_app`(가짜 모델 서버)의 `Retriever.docs`·`ids`·`laws`·`kinds`·순서가 CSV 모드와 같다.
  - [ ] 같은 조건에서 `EmbedCache`가 CSV 모드가 만든 .npz를 그대로 쓴다(가짜 인코더 호출 수가 확인용 1개뿐).
  - [ ] 릴리스 바이트 sha가 기록과 다르면(DB 행을 테스트에서 직접 고침 — 트리거 없는 `article_versions`) 시작 거부.
  - [ ] 활성 릴리스 없음·DB 없음·스키마 낮음 → 각각 시작 거부 문구.
  - [ ] 실행 중 DB의 활성 릴리스를 바꿔도 같은 프로세스의 검색 결과·피드백의 `corpus_release_id`가 그대로(요청 일관성).
  - [ ] `check_deploy`가 DB 모드에서 릴리스 id·데이터 버전·sha·조문 수를 PASS/FAIL로 출력.
  - [ ] `lawbot-db export-release`가 원본과 같은 CSV·매니페스트 바이트를 쓴다.
  - [ ] 요청 로그에 `corpus_release_id`.
- [ ] Green: `corpus_store.release_bytes`, `Retriever`·`manifest.verify` 바이트 입력, `service.load_app` 분기, `embed_cache`가 `.npz`에 `meta`(JSON: model_id, max_seq, dim, n_docs, docs_sha256, model_revision) 추가 저장 + `embedding_sets`·`release_embedding_sets` 기록(DB 모드에서만). 차원은 실제 결과 배열에서 읽는다(상수 1024 금지). `meta`가 없는 기존 .npz도 읽는다.
- [ ] 모델 revision은 모델 서버 `/v1/models`의 `revision`을 읽어 기록만 한다(캐시 재사용 판단은 기존 확인용 벡터 그대로).
- [ ] e2e_stub: DB 모드로 한 번(질문 → 답변·조문 카드 3개). 기존 CSV 모드 E2E는 그대로.
- [ ] 완료 기준: CSV 모드 기존 테스트 전부 그대로 Green + DB 모드 테스트 Green.

### PR 12 — 조문 자동 갱신을 릴리스와 연동 (`feat/articles-update-db`)

- [ ] Red `test_articles_update.py` 확장(기존 테스트는 CSV 모드로 그대로):
  - [ ] DB 모드 변화 없음 → 종료 0, 릴리스 수 그대로, 마지막 줄 형식 동일.
  - [ ] 변화 있음 → `staged`→`validated`→`activated` 이벤트, 활성 릴리스 바뀜, 종료 1, 마지막 줄 키(`updated`, `asof`, `changed`, `archive`, `restart`) 그대로.
  - [ ] 검사 실패(`reindex.validate` 오류, gate 실패) → 릴리스 `failed`, 활성 그대로, 종료 2.
  - [ ] 재시작 실패(가짜 `restart_web`/`wait_web`) → 활성 이전 릴리스로 되돌림, `rolled_back` 이벤트, 종료 2.
  - [ ] `--rollback` → 직전 활성 릴리스로, 재시작.
  - [ ] 동시 실행 → 잠금으로 두 번째가 실패(종료 2, 기존 문구).
  - [ ] 쓰기 트랜잭션 안에서 임베딩(가짜 인코더)이 불리지 않음: 가짜 인코더가 호출될 때 DB에 쓰기 잠금이 없음을 확인(`BEGIN IMMEDIATE`가 다른 연결에서 바로 성공).
  - [ ] 같은 날 두 번 갱신 → 릴리스 id `_2`.
  - [ ] `--init`(DB 모드): 프로필 기본 데이터로 첫 릴리스를 만들고 활성화(이미 있으면 거부).
  - [ ] monitor 계약: `law_monitor` 쪽 테스트(있으면)가 그대로 통과.
- [ ] Green: 3.11 흐름. 기존 보호 조건(웹앱 CSV 경로 확인, 매니페스트 확인, 잠금, 복구)은 DB 모드에 맞는 같은 의미의 검사로 바꾸고 하나도 빼지 않는다(PR 설명에 기존 조건 ↔ 새 조건 대응표).
- [ ] 통합 테스트: tmp DB + 가짜 legalize-kr 저장소(기존 테스트 픽스처 재사용) + 가짜 모델 서버로 생성 → 검증 → 활성화 → 롤백 한 바퀴.
- [ ] `docs/operations.md`·`docs/archives/articles-auto-update.md` 링크 갱신, DB 모드 운영 절차.
- [ ] 완료 기준: 명세서 Phase 5의 코드 부분. 운영 전환은 7절(별도 요청).

---

## 5. 테스트 층 정리

| 층 | 이번 작업에서 추가 | 실행 |
|---|---|---|
| 단위 | db·migrate·backup·qa_rules·auth·stores·이관·스냅샷·분할 | 기본·CI |
| 통합(`integration`) | 관리자 로그인 → 검수 → 승격(실제 웹앱 프로세스, 가짜 모델) | 기본·CI |
| 대역 E2E(`e2e_stub`) | 관리자 화면 흐름, 일반 사용자 차단, DB 코퍼스 모드 질문 | 기본·CI |
| 실제 모델 E2E(`e2e_live`) | 추가 없음. **운영 전환 후** 기존 3개 시나리오를 승인받아 실행 | 승인 후 |
| 모델 스모크·품질 평가 | 없음 | — |

모든 새 테스트는 `tmp_path` DB를 쓰고 시간 상한이 있는 대기만 쓴다.

---

## 6. 기준선과 측정

- 검색 동등성(모델 호출 없음): CSV 모드와 DB 모드의 문서 문자열·순서·캐시 키가 같음 → 같은 .npz → 같은 검색 결과. 이것을 PR 11의 테스트와 운영 전환 기록에 남긴다.
- 운영 전환 기록(`docs/evals/YYYY-MM-DD-sqlite-cutover.md`): 전환 전후 릴리스 id, CSV·매니페스트 sha, 조문 수, 캐시 키, `check_deploy` 결과, 웹앱 시작 시간(캐시 사용 로그), e2e_live 3개(승인 시) 결과와 AGENTS.md "실행 구성 기록" 항목.
- 품질 지표(검색 Recall, 인용 정확도, 거절률)는 **바뀌지 않아야 하므로 재평가하지 않는다**. 재평가가 필요하다고 판단되면 val로만, 승인받아 실행한다. test는 선택에 쓰지 않는다.
- 측정하지 않은 성능(예: 관리자 API 응답 시간)은 기록에 "측정 안 함"으로 적는다.

---

## 7. 운영 반영 순서와 복구 (각 단계는 사용자가 요청할 때만)

운영 반영은 AGENTS.md 5절을 따른다. 아래는 PR 병합 뒤 반영 단위다.

### 7.1 피드백 DB 반영 (PR 2–5 병합 후)

1. 운영 HEAD와 반영 커밋 diff 확인, 재시작할 서비스 판단(웹앱만).
2. `cd ~/Work/lawbot-prod && uv run lawbot-db init --db data/db/lawbot.db` → 권한 확인.
3. 이관: `migrate_csv_to_sqlite qa`(v4·v5·decree_v4·평가 전용) → `verify`. 피드백 JSONL이 생겼으면 `feedback`도.
4. 사용자가 운영 `.env`에 `LAWBOT_DB=data/db/lawbot.db` 추가(`LAWBOT_FEEDBACK_DIR` 줄 삭제).
5. 웹앱 재시작(승인) → `check_deploy` → 휴대폰에서 👍 한 번 → DB에 한 줄 확인(질문 원문은 출력하지 않고 건수만).
6. 백업: `lawbot-db backup --out data/db/backups/lawbot-<날짜>.db`.
- 복구: `.env`에서 `LAWBOT_DB`·`LAWBOT_FEEDBACK=1`을 빼고 이전 커밋으로 되돌려 재시작. DB 파일은 지우지 않고 보존.

### 7.2 관리자 반영 (PR 6–9 병합 후)

1. 사용자가 해시를 만들어 `.env`에 `LAWBOT_ADMIN_USER`·`LAWBOT_ADMIN_PW_HASH`(작은따옴표), 필요하면 `LAWBOT_CALENDAR_API_URL`.
2. 프론트엔드 빌드 → 웹앱 재시작 → Tailscale https로 관리자 로그인, 관리 화면에서 검수 저장 1회(감사 로그 확인), **Origin 검사가 Tailscale 경유에서 통과하는지 확인**(실패하면 반영 중단·보고).
3. 일반 계정으로 관리 메뉴 없음 확인.
- 복구: 관리자 변수 제거 후 재시작(관리자 기능만 꺼짐).

### 7.3 코퍼스 DB 전환 (PR 10–12 병합 후)

1. 06:20–06:50(조문 자동 갱신) 밖에서 진행.
2. 백업: DB 백업 + `data/live/v5/` 사본(`archive/` 아래 날짜 폴더).
3. `migrate_csv_to_sqlite corpus --dir data/live/v5 --profile v5 --release-id v5-live-<기준일> --activate` → `verify` → `export-release`로 만든 CSV sha가 `data/live/v5`의 sha와 같은지 확인.
4. 사용자가 `.env`에 `LAWBOT_CORPUS=db`.
5. 웹앱 재시작 → 로그에 "임베딩 캐시 사용" 확인(같은 .npz) → `check_deploy` PASS → e2e_live 3개(승인 시).
6. 다음 06:30 monitor 실행 결과(종료 코드·마지막 줄) 확인.
- 복구: `lawbot-db export-release --release <활성 릴리스> --out data/live/v5`(최신 법령 본문으로 CSV 갱신; 기존 파일은 2단계 사본으로 보존) → `.env`에서 `LAWBOT_CORPUS` 제거 → 재시작. DB의 릴리스·이벤트는 지우지 않는다.

---

## 8. 사용자 결정 (2026-11-07 확정: 모두 권장안)

| # | 질문 | 확정 (권장안) | 고르지 않은 선택 |
|---|---|---|---|
| D1 | 피드백 저장을 JSONL에서 DB로 **바꿀지**(이중 저장 안 함) | `LAWBOT_DB`를 지정하면 DB로 교체. 미설정이면 JSONL 유지(PR 5 착수 요청). 쓰기 실패는 경고 로그만 남기고 답변·응답 형식을 유지 | JSONL과 DB 둘 다 쓰기(명세서 "이중 관리 금지"와 충돌) |
| D2 | 공유 UI 연결 방식 | 소스 공유(Vite alias), npm workspace 안 씀 | 루트 npm workspace(lockfile·CI 개편) |
| D3 | 질문 원문 보존 기간과 삭제 | 이번 범위는 권한·Git 제외만, 기간은 나중에 정함 | 기간(예: N개월)을 정하고 삭제 명령까지 이번에 |
| D4 | DB 파일 위치 | 운영 트리 `data/db/lawbot.db`(gitignore) | 레포 밖 경로(예: `~/Library/Application Support/lawbot/`) |
| D5 | 스냅샷 출력 `data/v6/`를 Git에 올릴지 | 피드백 문항이 있으므로 **올리지 않음**(`.gitignore`) | 피드백 없는 부분만 커밋 |
| D6 | 누출 검사 범위 | test·val·평가 전용과 겹치면 차단(지금 `feedback_review` 규칙), train과 겹침은 허용·기록 | train과 겹쳐도 보류 |
| D7 | 조문 갱신 반영 방식 | 재시작으로 활성화(무중단 아님), 전체 재임베딩 | 핫 스왑·부분 재임베딩(복잡도·재현성 문제, 1절 7·8) |

---

## 9. 하지 않는 것

- 모델 재학습, 모델 서버 코드·설정 변경, 모델 추가 로딩.
- 캘린더 DB 통합, 공개 캘린더에 관리자·쓰기 기능 추가, 공개 캘린더에 CORS 추가.
- sqlite-vec, 핫 스왑, 부분 재임베딩.
- 기존 고정 Test·Val 재분할, 기존 분할 파일 수정.
- 피드백 자동 정답·오답 판정.
- 일반 사용자 비밀번호 해시 전환(별도 작업).
- 검색 Hard Negative 조문 지정 UI(스냅샷에서 negative는 비워 둠).
