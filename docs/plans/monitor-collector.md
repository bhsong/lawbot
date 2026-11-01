# 계획: 법령 변경 수집기 (services/monitor, 1단계)

- 작성일: 2026-11-01
- 원 계획서: `~/Downloads/1단계_법령수집_구현계획.md` (이하 "원 계획서"). PR 1 첫 커밋에서 `docs/plans/monitor-collector-spec.md`로 레포에 넣는다.
- 이 문서는 원 계획서를 이 레포(uv workspace 모노레포)에 맞춘 것이다. **원 계획서와 다르게 하는 것**, **미리 확인한 사실(스파이크 일부)**, **PR 나누기와 테스트 목록**만 적는다. 나머지(이벤트 스키마, 상태 테이블, 실행 모드, 안정성 규칙, 완료 기준)는 원 계획서를 따른다.

## 1. 원 계획서와 다르게 하는 것

| 항목 | 원 계획서 | 이 레포에서 | 이유 |
| --- | --- | --- | --- |
| 위치 | 독립 프로젝트 `law-monitor/` | `services/monitor/` (workspace 멤버). 패키지 이름 `law-monitor`, 모듈 `law_monitor`, 명령 `uv run law-monitor` | 사용자 지시. 기존 `services/chatbot`, `services/model-server`와 같은 구조 |
| `uv.lock` | 자체 잠금 파일 | 루트 `uv.lock` 하나 (`uv add --package law-monitor ...`) | workspace 멤버는 잠금 파일을 따로 가질 수 없다 |
| Python 고정 | `uv python pin 3.14` | 루트 `.python-version`(3.14) 그대로 | 이미 3.14 |
| ruff | 100자, 큰따옴표, `SIM`·`W` 규칙 | **루트 설정**(120자, 작은따옴표, 루트 규칙). 멤버 pyproject에 `[tool.ruff]`를 두지 않음 | 사용자 결정 (2026-11-01): 레포 전체 스타일 하나 |
| pytest | 멤버 안 `[tool.pytest.ini_options]` | 루트 `testpaths`에 `services/monitor/tests` 추가, 마커 `network` 추가, `addopts`를 `-m 'not models and not network'`로 | 루트에서 한 번에 실행, CI 그대로 |
| `pytest-cov`, `pre-commit` | 추가 | 넣지 않음 | 레포에서 쓰지 않음 (원 계획서도 선택) |
| git 호출 | `GitPython` 또는 `subprocess` | `subprocess` | 의존성 하나 줄임. 필요한 명령이 몇 개뿐 |
| 의존성 추가 시점 | 처음에 전부 | 쓰는 PR에서 추가. PR 1은 `pyyaml`만. `httpx`·`tenacity`·`lxml`·`pdfplumber`는 PR 2 | PR 1은 HTTP·PDF를 쓰지 않음 |
| 실행 데이터 | `law-monitor/data`, `logs` | `services/monitor/data/`, `services/monitor/logs/` (git 제외). 환경변수 `LAW_MONITOR_HOME`으로 바꿀 수 있음 | 사용자 지시(폴더 안에서 구현). 루트 `.gitignore`의 `logs/`는 이미 모든 위치에 적용, `services/monitor/data/`만 추가 |
| `.env` | 프로젝트 루트 | `services/monitor/.env` (+ `.env.example`). 루트 `.gitignore`의 `.env`가 이미 막음 | 웹앱 `.env`와 섞지 않음 |
| launchd | 템플릿 위치 미정 | `services/monitor/deploy/com.lawbot.monitor.plist.example` | 폴더 안에서 구현. 실제 등록은 운영 작업이라 별도 승인 |
| `admrules.exact` | 정확한 이름 목록 | `admrules.names`: `{agency, pattern(정규식), category}` | 2절: 연도가 이름에 들어가 해마다 폴더가 새로 생김 |
| 이벤트 단위 | 파일마다 `last_commit..HEAD` diff 하나 | **대상 파일을 건드린 커밋 하나 = 이벤트 하나** | legalize-kr은 커밋 하나가 개정 하나. 한 실행에 개정 둘이 들어오면 공포일·시행일이 섞이지 않게 |
| `version_key` (git) | 미정 | 그 커밋에서의 파일 blob 해시 | 내용 기반이라 강제 푸시로 커밋 해시가 바뀌어도 같은 이벤트가 다시 생기지 않음 |
| `replay` 결과 | 미정 | `data/replay/<from>_<to>/`에 저장, `events` 테이블·커서는 건드리지 않음 | 2단계 처리 대기열(`processed_at` 빈 이벤트)에 테스트 데이터가 섞이지 않게 |

## 2. 미리 확인한 사실 (2026-11-01, 읽기만 함)

원 계획서 7장 스파이크 중 git 저장소 부분은 공개 저장소라 키 없이 확인했다.

### 2.1 legalize-kr (`docs/data-provenance.md` 4절 + GitHub API)

- 폴더 `kr/<법령명(띄어쓰기 없음)>/` 안에 `법률.md`, `시행령.md`, `시행규칙.md`. **근로기준법은 `법률(법률).md`가 현행이고 `법률.md`는 1997년 폐지된 옛 법**이다. → 머리말 `상태`(시행/폐지)를 이벤트에 넣고, 폐지 상태 파일은 변하지 않는 한 이벤트가 생기지 않으므로 따로 거르지 않는다.
- 머리말(YAML) 필드: `제목`, `법령MST`, `법령ID`, `법령구분`, `소관부처`(목록), `공포일자`, `공포번호`, `시행일자`, `상태`, `출처`, `첨부파일`. 본문 조문은 `##### 제N조 (제목)`, 부칙은 `## 부칙`.
- **공포 시점에 들어온다** (시행 전 개정도 들어 있음). → 원 계획서 7장 2-2의 "시행예정 법령 조회로 보완"은 필요 없다. 다만 부칙에서 조문별 시행일이 다르면 머리말 `시행일자` 하나로는 부족하다 → 이벤트 `context`에 부칙 원문을 넣는다.
- **커밋의 author·committer 날짜가 모두 공포일**이다 (예: `51fea869` 2026-05-22T03:00:00Z). → 원 계획서대로 날짜 필터 없이 커밋 해시 범위만 쓴다.
- **별표·서식은 본문이 아니라 머리말 `첨부파일` 목록**에 있다: `별표번호`, `별표가지번호`, `별표구분`(별표/서식), `제목`, `파일링크`(HWP), `PDF링크`. 소득세법 시행규칙은 별표 7개 + 서식 247개.
- **개정 한 번마다 모든 첨부의 링크(`flSeq`)가 바뀐다.** 소득세법 시행규칙 `4220f9dc` → `51fea869`에서 링크 508개 중 같은 것이 0개였다. → 링크·개정일로는 어느 서식이 바뀌었는지 알 수 없다. PR 1은 링크를 뺀 목록(구분·번호·가지번호·제목)만 비교하고, 실제 서식 변경은 PR 2에서 파일을 받아 **추출 텍스트의 해시**로 판단한다 (PDF 바이트는 다시 만들어지며 바뀔 수 있어 쓰지 않음).

### 2.2 admrule-kr (부분 클론으로 확인)

- 경로: `{기관경로...}/{종류}/{행정규칙명}/본문.md`. 국세청은 `재정경제부/국세청/고시/...`, 고용노동부·보건복지부는 `<부처>/_본부/고시/...`.
- 머리말 필드: `행정규칙ID`, `행정규칙일련번호`, `행정규칙명`, `행정규칙종류`, `상위기관명`, `소관부처명`, `발령번호`, `발령일자`, `시행일자`, `제개정구분`, `현행여부`, `출처`, **`첨부파일`**(별표번호·구분·제목·`파일링크`, 일부 `PDF링크`).
  → 부처 필터는 경로가 아니라 `소관부처명`으로 한다. 같은 규칙인지는 `행정규칙ID`로 판단한다 (부처 이름이 바뀌면 경로가 바뀜: 기획재정부 → 재정경제부).
- README 공지: **파이프라인 개선 시 force-push로 모든 커밋 해시가 바뀔 수 있다.** → 원 계획서 5.4 A-5(커서가 사라졌을 때 내용 해시로 대체)는 필수이고 테스트로 고정한다.
- 이력에 `현행 스냅샷: ...` 커밋(현행 목록으로 맞추는 일괄 커밋)과 `고시: <이름> (<발령번호>)` 커밋이 섞여 있다.
- 추적 대상 실제 이름 (원 계획서 7장 5번):

| 대상 | admrule-kr 경로 (HEAD) | 비고 |
| --- | --- | --- |
| 국민연금 기준소득월액 상·하한 | `보건복지부/_본부/고시/국민연금 기준소득월액 하한액과 상한액` | 이름 고정, 해마다 개정 커밋 (2024-6, 2025-24, 2026-31) |
| 건강보험료 상·하한 | `보건복지부/_본부/고시/월별 건강보험료액의 상한과 하한에 관한 고시` | |
| 최저임금 | `고용노동부/_본부/고시/2026년 적용 최저임금 고시` | **연도가 이름에 있음**. 같은 경로 이력에 `2027년 적용 최저임금안 고시` 커밋도 있음 → 패턴 `^\d{4}년 적용 최저임금(안)? 고시$` |
| 사업종류별 산재보험료율 | `고용노동부/_본부/고시/2026년도 사업종류별 산재보험료율` | 연도 포함 → `^\d{4}년도 사업종류별 산재보험료율$` |
| 지급명세서 전산매체 제출요령 | **찾지 못함** | 행정규칙(고시)이 아닐 수 있다 (국세청 안내 자료). PR 2 스파이크에서 법제처 API로 다시 찾고, 없으면 5번은 사용자와 소스를 다시 정한다 |
| 부가세 전자신고 관련 | `부가가치세 전자신고 시 제출기한을 연장하는 서류 고시` 등 | 레이아웃 고시는 없음. `전자(세금)계산서 겸용서식 사용 사업자의 전산매체 제출 절차에 관한 고시`는 별지 첨부가 있음 (5번 후보) |

## 3. PR 나누기

| PR | 내용 | 키 | 원 계획서 6장 |
| --- | --- | --- | --- |
| **PR 1** | 뼈대(workspace 멤버, 설정·상태·이벤트), `legalize_git`(법령·행정규칙, 첨부 목록 비교), 실행기(실패 격리·요약·`collector_failing`), CLI(`bootstrap`/`collect --only`/`replay`), 로그, launchd 템플릿, README | 없음 | 1, 2, 3, 7 |
| **PR 2** | 법제처 OC 스파이크 → `utils/http.py`, `pdf_text.py`. 별표·서식·고시 첨부 파일 받기 → 텍스트 해시 비교 → diff 이벤트(`attachment_*`). 법령 이벤트에 개정문·제·개정 이유 채우기. 5번 소스 확정 | `LAW_OC` (있음) | 0(법제처), 4, 5 |
| **PR 3** | 국민참여입법센터(`lawmaking`, OC로 되는지 스파이크) + 국회(`assembly`) | `LAW_OC`, `DATA_GO_KR_KEY` (아직 없음 → 키를 받은 뒤) | 0(나머지), 6 |

PR 2의 첨부 수집은 원 계획서의 `lawgo_forms`(목록을 API로 조회)와 달리 **목록은 git 저장소 머리말에서 읽고, 파일만 법제처에서 받는다**. 법제처 별표서식 목록 API에 서식별 개정일이 있으면(스파이크로 확인) 그것으로 받을 파일을 줄인다. 없으면 개정 커밋이 들어온 법령의 첨부를 모두 받는다 (소득세법 시행규칙 기준 약 250개, 0.5초 간격으로 약 3~5분, 개정 때만).

## 4. PR 1 설계

### 4.1 파일

```
services/monitor/
  pyproject.toml            # law-monitor, 의존성 pyyaml, 명령 law-monitor = law_monitor.cli:main
  README.md
  .env.example              # LAW_OC=, DATA_GO_KR_KEY= (PR 1은 읽지 않음, 자리만)
  config/targets.yaml       # 원 계획서 4장 + admrules.names (2.2 표)
  deploy/com.lawbot.monitor.plist.example
  src/law_monitor/
    paths.py                # HOME = LAW_MONITOR_HOME 또는 services/monitor (parents로 계산, 테스트로 고정)
    config.py  state.py  events.py  runner.py  cli.py  logs.py
    gitrepo.py              # subprocess git: clone(--filter=blob:none)·fetch·ff-only·rev-list·show·diff·cat-file
    frontmatter.py          # 머리말 YAML / 본문 분리, 첨부 목록 정규화(링크 제외)
    articles.py             # 본문을 ##### 제N조 단위로 나눠 diff.chunks 만들기
    collectors/base.py  collectors/legalize_git.py
  tests/ (+ conftest: 임시 git 저장소를 만드는 도우미)
```

`services/monitor/` 밖의 변경 (**구현을 시작하기 전에 이 목록으로 따로 승인을 받는다**):

| 파일 | 바꿀 내용 | 이유 |
| --- | --- | --- |
| 루트 `pyproject.toml` | `tool.uv.sources`에 `law-monitor`, dev 그룹에 `law-monitor`, `testpaths`에 `services/monitor/tests`, `network` 마커, `addopts`에 `not network` | workspace 멤버 등록, 루트에서 테스트 실행 (1절, 사용자 결정) |
| `uv.lock` | `uv lock`이 다시 만든 결과 (`law-monitor`, `pyyaml`) | workspace 잠금 파일은 하나 |
| `.gitignore` | `services/monitor/data/` 한 줄 | 클론·상태 DB·이벤트를 커밋하지 않게 |
| `README.md` | 모니터 자리 설명을 `services/monitor/README.md`로 가는 안내 한 줄로 | 레포 문서 규칙 |
| `docs/plans/monitor-collector-spec.md` | 원 계획서 원문 복사 | 근거 문서를 레포에 둠 |
| `docs/evals/<날짜>-monitor-collector.md` | 6절 확인 결과 | 레포 기록 규칙 |

이 목록에 없는 바깥 파일을 고쳐야 하면 그때마다 다시 묻는다.

### 4.2 `legalize_git` 동작

1. 저장소 없으면 `git clone --filter=blob:none`, 있으면 `fetch` + `merge --ff-only`. ff 실패(강제 푸시)면 `reset --hard origin/<기본 브랜치>` 후 5번 대체 경로.
2. `git rev-list --reverse <cursor>..HEAD -- <대상 경로들>`로 커밋을 뽑고, 커밋마다 `diff-tree -M --name-status`로 바뀐 대상 파일을 찾는다.
   - 법령: 대상 경로 = `kr/<laws[].name>/`.
   - 행정규칙: 경로가 미리 정해지지 않으므로 커밋이 바꾼 `본문.md`마다 새 머리말을 읽어 `names`(소관부처명 + 이름 정규식) 또는 `keyword_rules`(소관부처명 + 이름 키워드)에 맞는지 본다. 지워진 파일은 지우기 전 내용의 머리말로 판단한다.
3. 파일마다 이벤트를 만든다.
   - `change_type`: 추가 → `law_new`/`admrule_new`, 삭제 또는 `상태: 폐지` → `law_repealed` (행정규칙 삭제는 `admrule_amended`에 `removed: true`, 원 계획서 목록에 행정규칙 폐지 값이 없으므로), 그 밖 → `*_amended`. 경로만 바뀌고 `행정규칙ID`·본문이 같으면 이벤트를 만들지 않는다.
   - `target`: 법령 `{law_name: 제목, item_id: 경로, law_id: 법령ID}`, 행정규칙 `{law_name: 행정규칙명, item_id: 행정규칙ID, agency: 소관부처명}`.
   - `dates`: 공포일자/발령일자, 시행일자. `old`/`new`: `{version_key: blob, file: data/files/<blob>.md}`.
   - `diff`: 본문 unified diff를 `data/events/<날짜>/<id>.diff`로, `chunks`는 조문별 diff 목록 (원 계획서 9장 선택 항목, 쉬워서 PR 1에 넣음).
   - `attachments`: 링크를 뺀 첨부 목록의 추가·삭제·제목 변경. 링크만 바뀐 경우는 비어 있음.
   - `context`: `source_url`(출처), 커밋 메시지(제개정구분 포함), 부칙 원문. `amendment_text`·`amendment_reason`은 PR 1에서 `null` (PR 2가 채움).
4. `event_id = sha1(source|item_id|new blob)`. 이미 있는 id면 건너뛴다.
5. 커서 커밋이 저장소에 없으면 경고 로그 + 대상 파일마다 HEAD blob을 `items`의 blob과 비교하고, 이전 내용은 `data/files/<blob>.md`(마지막 내용 사본)로 diff한다.
6. 상태(커서, `items`, 이벤트 행)는 수집기가 반환한 뒤 실행기가 한 트랜잭션으로 저장한다. 이벤트 JSON 파일을 먼저 쓰고 DB를 커밋하며, DB 커밋이 실패하면 다음 실행에서 같은 id로 다시 써진다 (덮어써도 내용이 같음).

### 4.3 실행기·CLI

- `bootstrap`: 클론, 커서를 HEAD로, 대상 파일 사본·`items` 저장, 이벤트 0건. 이미 기준점이 있으면 `--force` 없이는 거부.
- `collect [--only <name>]`: 기준점이 없으면 "먼저 bootstrap" 오류로 끝난다. 수집기 예외는 잡아서 `runs.summary_json`에 남기고 다음으로 넘어간다. 마지막 줄 `collectors=2 ok=1 failed=1(legalize_admrule) events=3`. 연속 3회 실패 시 `collector_failing` 이벤트 1건 (같은 실패 연속 구간에서 한 번만).
- `replay --repo legalize-kr|admrule-kr --from A --to B`: 2·3의 같은 로직으로 `data/replay/<A>_<B>/`에 이벤트를 쓴다. 상태 변경 없음.
- 로그: `logs/collector-YYYYMMDD.log`, 시간대 KST.
- launchd 템플릿: `StartCalendarInterval` 06:30 (잠자기 중 놓친 실행은 깨어난 뒤 실행됨, 전원이 꺼져 있던 경우는 안 됨), `ProgramArguments` = uv 절대 경로 + `run --project <레포> law-monitor collect`.

## 5. PR 1 테스트 (구현보다 먼저)

네트워크 없이 테스트 안에서 만든 임시 git 저장소(머리말·조문이 있는 작은 md 파일)만 쓴다.

| 파일 | 테스트 |
| --- | --- |
| `test_paths.py` | `HOME`이 `services/monitor`, `LAW_MONITOR_HOME`으로 바뀜 |
| `test_config.py` | 레포의 `targets.yaml`이 읽힘. 알 수 없는 category, 빈 이름, 중복 법령, 잘못된 정규식 거부 |
| `test_events.py` | 같은 입력 → 같은 `event_id`. 두 번 저장 → 파일 1개·행 1개. JSON에 원 계획서 5.2의 키가 모두 있음 |
| `test_state.py` | 테이블 생성. 저장 중 예외 → 커서·`items` 그대로 |
| `test_frontmatter.py` | 머리말·본문 분리. 첨부 목록 정규화가 링크를 버림 |
| `test_articles.py` | 조문 단위 chunks: 바뀐 조문만, 신설·삭제 조문 표시 |
| `test_legalize_law.py` | bootstrap → 0건 · 커서=HEAD. 대상 개정 커밋 → `law_amended` 1건(diff·공포일·시행일·chunks), 재실행 → 0건. 대상 밖 폴더 → 0건. **커서보다 과거 날짜의 새 커밋도 잡힘**. 범위에 개정 2개 → 2건(각자 날짜). 새 파일 → `law_new`. `상태: 폐지` → `law_repealed`. 링크만 바뀐 첨부 → `attachments` 비어 있음. 서식 제목 추가 → `attachments`에 표시 |
| `test_legalize_admrule.py` | `keyword_rules`: 부처 맞고 키워드 맞음/부처 다름/키워드 없음. `names` 정규식: `2027년 적용 최저임금 고시` 신설이 `admrule_new`로 잡힘. 경로만 바뀐 같은 `행정규칙ID` → 0건 |
| `test_force_push.py` | 원격 이력을 새로 써서 커서가 사라짐 → 경고 + 내용 비교로 바뀐 파일만 1건, 이미 낸 변경은 다시 안 나옴 |
| `test_runner.py` | 수집기 하나가 예외 → 나머지는 실행, 요약 줄, `runs` 행. 3회 연속 실패 → `collector_failing` 1건, 4회째 0건 |
| `test_cli.py` | bootstrap 전 collect → 오류. `--only`. `replay`가 상태를 바꾸지 않음 |

## 6. PR 1 확인 (맥미니 실제 저장소, 실행 전에 승인)

모델·운영 서비스와 무관하다. 레포 작업 트리에서 `LAW_MONITOR_HOME`을 임시 폴더로 두고 실행한다.

| # | 항목 | 기대값 |
| --- | --- | --- |
| 1 | `uv sync --locked`, ruff check·format --check, pytest | 모두 통과 |
| 2 | `law-monitor bootstrap` (두 저장소 부분 클론) | 이벤트 0건, 커서 = 각 HEAD, 디스크 사용량 기록 |
| 3 | 곧바로 `collect` | 이벤트 0건, 요약 줄 `ok=2` |
| 4 | `replay --repo legalize-kr --from 4220f9dc --to 51fea869` | 소득세법 시행규칙 `law_amended` 1건, 공포일 2026-05-22, 서식 링크만 바뀐 것은 `attachments`에 없음 |
| 5 | `replay --repo admrule-kr` (최저임금 `70a4db47c5c`의 부모 → 그 커밋) | 2027년 적용 최저임금안 고시 1건 |

결과는 `docs/evals/<날짜>-monitor-collector.md`에 기록하고 README에는 한 줄만 쓴다. launchd 실제 등록은 병합 뒤 운영 worktree(`~/Work/lawbot-prod`)에서 따로 승인을 받아 한다.

## 7. PR 2·3 스파이크 목록 (원 계획서 7장 중 남은 것)

- 법제처 (PR 2): 별표서식 목록 API에 서식별 개정일·개정구분이 있는지 / 법령 본문 조회의 개정문·제개정이유 필드 / 행정규칙 상세의 첨부 목록 (admrule-kr 머리말과 같은지) / `flDownload.do` 링크를 OC 없이 받을 수 있는지 / 맥미니 IP에서 OC 호출 / `lxml`·`pdfplumber`의 3.14 휠 (`uv lock` 결과) / 지급명세서 전산매체 제출요령의 실제 소스.
- 국민참여입법센터·국회 (PR 3): 목록 엔드포인트, OC로 되는지, 게시물 ID·제목·의견제출 기한 필드, 서비스키 발급.
- 실제 호출은 각 PR에서 요청 URL(키는 가린 채)과 응답 견본을 `services/monitor/tests/fixtures/`에, 확인 결과를 `docs/evals/<날짜>-monitor-spike.md`에 남긴다 (원 계획서의 `docs/spike-notes.md` 대신 레포 규칙).

## 8. 정하지 않은 것 (PR 1 리뷰 때 다시 볼 것)

- 원 계획서 `change_type`에는 행정규칙 폐지 값이 없다. PR 1은 `admrule_amended` + `removed: true`로 두고, 2단계에서 따로 필요하면 `admrule_repealed`를 추가한다.
- `현행 스냅샷` 일괄 커밋이 대상 고시를 건드리면 개정이 없어도 이벤트가 생길 수 있다 (예: 현행 선택만 바뀐 경우). 본문·머리말 주요 필드가 같으면 건너뛰는 규칙을 PR 1에 넣고, 실제 replay 결과로 맞는지 본다.
