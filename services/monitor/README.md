# 법령 변경 수집기 (law-monitor, 1단계)

매일 한 번 추적 대상 법령·행정규칙을 확인해 **지난 실행과 달라진 것만** 변경 이벤트(JSON)로 저장합니다. 요약(2단계)과 전송(3단계)은 이 이벤트만 읽으면 됩니다.

- 설계: [docs/plans/monitor-collector.md](../../docs/plans/monitor-collector.md) (원 계획서: [monitor-collector-spec.md](../../docs/plans/monitor-collector-spec.md))
- 지금 있는 수집기: `legalize_law`([legalize-kr](https://github.com/legalize-kr/legalize-kr), 법률·시행령·시행규칙), `legalize_admrule`([admrule-kr](https://github.com/legalize-kr/admrule-kr), 고시 등). 변경은 공개 git 저장소에서 찾고, 이벤트마다 법제처 Open API로 개정문·제개정이유를 채웁니다 ([계획](../../docs/archives/monitor-lawgo.md)).
- 별표·서식: `forms` 대상 법령과 추적 행정규칙은 신·구판의 별표·서식 텍스트를 비교해 바뀐 것만 `lawgo_forms`·`lawgo_admrul` 이벤트로 만들고, 그 서식의 PDF·HWP 원본을 받습니다.
- 요약(2단계): 요약하지 않은 이벤트를 운영 모델 서버의 `qwen3.8-27b-4bit`로 요약해 `data/summaries/YYYY-MM-DD.md`를 만듭니다 ([계획](../../docs/archives/monitor-summary.md)).
- 점검: `config/targets.yaml`의 `checks`에 지정된 캘린더 sync와 챗봇 조문 데이터 자동 갱신을 `daily`가 순차 실행합니다.
- 국회 법률안과 입법예고는 아래 절의 선택 수집기로 연결합니다. 전자신고 파일 레이아웃(지급명세서 전산매체 제출요령)은 3단계 연례 리마인드 대상입니다 ([결정](../../docs/plans/monitor-collector.md) 9절).

## 설치

레포 최상위에서 실행합니다 (uv workspace 멤버라 다른 서비스와 같이 설치됨).

```bash
uv sync
cp services/monitor/.env.example services/monitor/.env   # LAW_OC(법제처 Open API 인증키)를 채움
```

실행 데이터는 `services/monitor/data/`(저장소 클론, `state.db`, 이벤트)와 `services/monitor/logs/`에 생기고 git에 올라가지 않습니다. 다른 곳에 두려면 `LAW_MONITOR_HOME` 또는 `--home`을 씁니다.

## 실행

```bash
uv run law-monitor bootstrap              # 첫 실행: 저장소 부분 클론 + 현재 상태를 기준점으로 (이벤트 없음)
uv run law-monitor collect                # 평소 실행: 기준점 이후 변경만 이벤트로
uv run law-monitor collect --only legalize_law
uv run law-monitor replay --repo legalize-kr --from <커밋> --to <커밋>   # 과거 구간 → data/replay/ (상태 안 바뀜)
uv run law-monitor summarize              # 대기 이벤트 → 모델 요약 → data/summaries/YYYY-MM-DD.md
uv run law-monitor summarize --events-dir data/replay/<구간>   # replay 결과 요약 → 그 폴더의 summary.md (상태 안 바뀜)
uv run law-monitor check                  # 점검(checks)만: 결과 줄 출력 (요약·상태 안 바뀜)
uv run law-monitor daily                  # launchd가 매일 실행: collect → 점검 → summarize
```

- 마지막 줄이 실행 요약입니다: `collectors=2 ok=2 failed=0 events=1`. 같은 줄이 `logs/collector-YYYYMMDD.log` 끝에도 남습니다.
- 종료 코드: 0 성공, 1 수집기 일부 실패(나머지는 저장됨), 2 실행 전 거부(설정 오류, bootstrap 전 collect, `LAW_OC` 없음, 다른 실행 중 등).
- 같은 home(`data/`)으로 두 실행이 겹치지 않게 `data/.lock`에 잠금을 겁니다. launchd의 `daily`가 도는 동안 수동으로 실행하면 "다른 law-monitor 실행 중"으로 바로 끝납니다(종료 코드 2). 프로세스가 죽으면 잠금은 저절로 풀립니다.
- `LAW_OC`는 환경변수가 먼저이고, 없으면 `services/monitor/.env`에서 읽습니다. `bootstrap`은 법제처를 부르지 않아 키 없이도 됩니다. 로그·이벤트·오류 메시지에는 `OC=***`로만 남습니다.
- 법제처 호출이 실패하면(재시도 3회 뒤) 그 수집기가 실패로 끝나고 커서가 그대로라, 다음 실행에서 같은 변경을 다시 처리합니다. 원문이 빠진 이벤트는 저장하지 않습니다.
- 한 수집기가 3번 연속 실패하면 `collector_failing` 이벤트가 한 번 생깁니다.
- Git 수집에서 `bootstrap --force`를 실행하면 현재 저장소를 새 기준점으로 삼습니다. 새 행정규칙을 현재 상태부터 추적해야 할 때 사용합니다. 설정만 바꾸면 기존 행정규칙은 다음 개정부터 잡힙니다.

## 국회 법률안과 입법예고

`services/monitor/.env`의 `LAW_ASSEMBLY_KEY`를 사용합니다. 공통 법령 범위의 `keywords`와 `config/targets.yaml`의 `prelegislation.assembly.age`에 따라 의안검색·진행 중·종료 예고를 읽습니다. 제목과 키워드를 정규화한 뒤 포함 여부를 검사합니다. [공통 법령 범위](../../packages/lawbot-common/src/lawbot_common/law_scope.json)

배포 전에는 임시 home에서 아래 명령을 사용합니다.

```bash
uv run law-monitor --home "$MONITOR_HOME" bootstrap --only assembly_bill
uv run law-monitor --home "$MONITOR_HOME" bootstrap --only assembly_notice
uv run law-monitor --home "$MONITOR_HOME" collect --only assembly_notice
```

기준선을 잡은 뒤 첫 정상 예고 수집에서 해당 키워드의 진행 중 예고를 검토 후보로 발행합니다. 이후 의안·예고 변경을 이어서 추적합니다. 공통 키워드나 국회 대수를 바꿀 때는 다음 정기 실행 전에 `assembly_bill`과 `assembly_notice` 각각에 `bootstrap --only <수집기> --force`를 실행해 기준을 갱신합니다.

강제 bootstrap은 기존 관측 순번을 유지하면서 비교 기준을 갱신합니다. 기준에 흡수된 변경은 이벤트 발행 대상에서 제외됩니다. 정상 수집에서 들어온 변경 예고는 캘린더 sync에서 공식 상세의 의안번호와 기간이 일치하면 자동 공개됩니다. 의안 공개는 수동 검토를 거칩니다.

일일 수집과 예고 자동 공개를 사용하려면 캘린더 DB v7과 두 국회 기준선이 준비된 뒤 Monitor 환경의 `LAW_ASSEMBLY_ENABLED=1`을 사용합니다. 이 값은 YAML 활성화 설정에 우선합니다. 기존 06:30 작업에서 각 국회 수집기 실패를 별도로 기록합니다. 국회 이벤트는 공식 날짜와 상태로 별도 요약하므로 모델 호출 수를 늘리지 않습니다. [캘린더 공개 검토 절차](../law-calendar/README.md#국회-이벤트-공개-검토) · [실제 API 검증 방법](../../docs/evals/2026-11-05-assembly-api-validation-guide.md)

## 요약 (2단계)

- 대상: `state.db`의 `events`에서 `processed_at`이 빈 것. 요약이 끝나면 `processed_at`을 적습니다.
- 법령·행정규칙 이벤트 하나에 모델 호출 하나입니다. 같은 개정의 별표서식 이벤트(`parent_event_id`)는 그 호출에 함께 넣습니다. 입력은 호출당 약 12,000자이고, 넘치면 뒤를 자르고 내용을 못 넣은 서식은 제목만 넣습니다.
- 모델 설정은 웹앱과 같은 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`입니다. 환경변수 → `services/monitor/.env` → **레포 최상위 `.env`** 순서로 읽어서, 운영 worktree에서는 따로 넣을 것이 없습니다. 모델 id는 `LAW_MONITOR_SUMMARY_MODEL`(기본 `qwen3.8-27b-4bit`, 환경변수나 `services/monitor/.env`에만: 레포 최상위 `.env`에는 `LAWBOT_*`만 적을 수 있음). `.env` 형식은 웹앱과 같은 규칙입니다(`export`·따옴표·줄 끝 ` # 주석` 허용, `lawbot_common.envfile`). `services/monitor/.env`에는 `LAW_*`·`DATA_GO_KR_*`·`LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`만 적을 수 있고, 다른 변수가 있으면 실행을 거부합니다. 생성 설정은 temperature 0, 최대 800토큰, thinking 끔.
- md 구성: 머리(실행 시각·수집 요약 줄·이벤트 수) → 한눈에 보기 번호별 절 → 법령마다 모델 요약 + 바뀐 별표서식 목록 + 원문 이벤트 경로·법제처 링크 → 경고(수집기 연속 실패). 새 이벤트가 없으면 "새 변경 없음".
- 파일 날짜는 **요약을 실행한 날**입니다. 내용은 "지난 요약 실행(처음이면 bootstrap) ~ 이번 실행" 사이에 저장소에 새로 들어와 감지된 변경이고, md 머리의 `대상:` 줄에 그 구간을 적습니다. 공포일은 항목마다 따로 나옵니다 (legalize-kr은 커밋 날짜가 공포일이라 오늘 들어온 개정이 과거 공포일일 수 있음).
- 같은 날 두 번 실행하면 두 번째 파일은 `YYYY-MM-DD-HHMMSS.md`입니다 (앞 파일을 덮지 않음).
- 모델 서버가 실패하면(시도 3번) md에 "요약 실패, 대기 이벤트 N건"을 쓰고 그 이벤트는 대기로 둡니다. 다음 실행에서 밀린 것까지 요약합니다.
- 요약은 참고용입니다. md 머리에도 "시행일·금액·서식 내용은 원문으로 확인"이라고 씁니다.

## 점검 (checks)

`config/targets.yaml`의 `checks`에 이름과 명령을 적으면 `daily`가 collect 다음에 차례로 실행합니다. monitor는 그 명령을 import하지 않고 하위 프로세스로 부릅니다.

- 자리표시: `{bin}`(law-monitor와 같은 venv의 bin 폴더), `{home}`, `{data}`, `{repos}`
- 종료 코드 약속: 0 이상 없음, 1 확인 필요, 그 밖·시간 초과(15분)·명령 없음은 점검 실패. 결과 줄은 표준 출력의 마지막 줄입니다
- 요약 md 끝 '점검' 절에 `- 이름 — 상태: \`결과 줄\``로 적습니다. 점검 실패가 있으면 `daily` 종료 코드가 1입니다 (요약은 그대로 씀)
- 챗봇 조문 데이터: monitor가 갱신한 `data/repos/legalize-kr`로 그날 시행 중인 조문을 만들어 웹앱 설정에 따라 v4 `data/live` 또는 v5 `data/live/v5`와 비교하고, 다르면 새 CSV·임베딩·확인·보관·전환·웹앱 재시작까지 합니다 (`lawbot-articles-update --restart`, 약 1~2분). 보고서는 `data/articles/<날짜>.md`. 결과 줄 예: `articles updated=no same=yes changed=0 pending=30 next=2026-11-27 new=0 → …`, `articles updated=yes asof=2026-11-27 changed=6 archive=2026-10-20 restart=ok → …`

## 이벤트

`data/events/YYYY-MM-DD/<event_id>.json` (+ 본문 diff `<event_id>.diff`). `event_id`는 `sha1(수집기|항목|새 내용 blob)`이라 같은 변경은 몇 번 실행해도 한 번만 저장되고, `state.db`의 `events` 테이블에도 한 줄 들어갑니다 (2단계는 `processed_at`이 빈 것을 처리).

| 필드 | 내용 |
| --- | --- |
| `change_type` | `law_amended` · `law_new` · `law_repealed` · `admrule_amended` · `admrule_new` · `attachment_changed` · `attachment_added` · `attachment_removed` · `collector_failing`. 행정규칙 삭제는 `admrule_amended` + `removed: true` |
| `category` · `categories` | 한눈에 보기 번호 (행정규칙은 여러 규칙에 맞을 수 있어 목록도 둠) |
| `target` | 법령: `law_name`·`item_id`(저장소 경로)·`law_id`·`status`. 행정규칙: `law_name`·`item_id`(행정규칙ID)·`agency` |
| `dates` | `promulgation`(법령 공포일자, 행정규칙 발령일자) · `effective`(시행일자) |
| `old` · `new` | `version_key`(blob 해시), `file`(전체 내용 사본 `data/files/<blob>.md`), `path`(저장소 경로) |
| `diff` | `path`(본문 unified diff, 머리말 제외), `chunks`(조문·부칙 블록 단위 diff: `article`, `kind` added/changed/removed) |
| `meta_changes` | 머리말에서 바뀐 값 (시행일자·발령번호 등, 첨부 제외) |
| `attachments` | 첨부(별표·서식) 목록의 추가·삭제·제목 변경. 링크(flSeq)는 개정마다 전부 바뀌므로 비교하지 않음 |
| `context` | `amendment_text`(개정문)·`amendment_reason`(제·개정 이유, 법제처), `source_url`, `commit`(sha·subject·date, 커밋 날짜는 공포일), `addenda_added`(새 부칙 원문: 조문별 시행일), `fallback`, `lawgo`(법령: `mst_old`·`mst_new`·`attachment_effective`(서식별 시행일 원문), 행정규칙: 일련번호·`files`(고시 원문·개정 이유서 링크)), 행정규칙은 `addenda`(부칙) |

같은 실행에 개정 커밋이 둘 들어오면 이벤트도 둘입니다 (공포일·시행일이 섞이지 않게).

별표·서식 이벤트(`lawgo_forms`·`lawgo_admrul`)는 부모 이벤트와 같은 개정에서 나옵니다.

| 필드 | 내용 |
| --- | --- |
| `target` | `law_name`, `item_name`(별표 제목), `item_id`(`서식 0021-00` = 구분·번호·가지번호), `kind` |
| `dates` | `promulgation`(부모와 같음), `effective`(그 서식의 시행일, 본문의 별표시행일자문자열. 없으면 부모 시행일), `effective_dates` |
| `old` · `new` | `version_key`(텍스트 sha256), `text`(`data/files/<sha256>.txt`). 신판에만 `file`(PDF)·`hwp` 원본 |
| `diff.path` | 서식 텍스트 unified diff (표는 상자 그리기 문자 그대로) |
| `context` | `parent_event_id`, 부모와 같은 `amendment_text`·`amendment_reason`, `source_url`(PDF 링크) |

서식은 PDF 바이트가 아니라 법제처 본문 API의 서식 텍스트로 비교합니다. 바뀌지 않은 서식도 PDF는 판마다 다시 만들어지기 때문입니다 ([스파이크](../../docs/evals/2026-11-01-monitor-spike.md)). 그래서 개정 때 바뀐 서식의 파일만 받습니다.

## 추적 대상 바꾸기

법률·시행령·시행규칙 수집은 공통 법령 범위의 `folder`에 해당하는 Legalize KR `kr/<폴더>/`를 추적합니다. `category`는 수집 분류이며 `keywords`는 국회 제목 필터에 사용합니다. [공통 법령 범위](../../packages/lawbot-common/src/lawbot_common/law_scope.json)

별표·서식, 행정규칙, 수집기 설정과 점검 명령은 `config/targets.yaml` 또는 `--config`로 지정한 YAML에서 관리합니다. 이전 설정의 `laws`와 `prelegislation.keywords`는 실행 오류가 되므로 YAML에서 제거하고 공통 법령 범위에 반영합니다. 부가가치세법은 법령과 별표·서식 수집 범위 및 국회 제목 키워드에서 제외했습니다. 행정규칙은 아래 규칙으로 별도 추적합니다.

- `admrules.names`: 소관부처명 + 행정규칙명 정규식. 최저임금처럼 이름에 연도가 들어가는 고시는 `^\d{4}년 적용 최저임금(안)? 고시$`처럼 씁니다.
- `admrules.keyword_rules`: 소관부처명 + 이름 키워드 (하나라도 들어 있으면 대상).
- `agency`는 파일 머리말 `소관부처명`과 정확히 같아야 합니다 (국세청 고시는 경로가 `재정경제부/국세청/...`이지만 소관부처명은 `국세청`).
- `forms`: 별표·서식을 비교할 법령(머리말 `제목`, 띄어쓰기 있음)과 구분(`별표`·`서식`). 추적 행정규칙의 별지·별표는 설정 없이 모두 비교합니다.

## launchd 등록 (매일 06:30)

템플릿은 `law-monitor daily`(수집 → 캘린더 import → 기존 챗봇 점검 → 요약)를 실행합니다. 캘린더는 독립 Python 환경의 하위 프로세스로 실행하며 monitor가 calendar 패키지를 import하지 않습니다. 기본 설정에서 캘린더 점검이 실패해도 뒤 점검과 요약은 이어집니다.

등록 전에 캘린더 환경을 `uv sync --project services/law-calendar --locked`로 준비하고, [캘린더 사용법](../law-calendar/README.md)에 따라 별도 DB를 초기화/마이그레이션합니다. `deploy/com.lawbot.monitor.plist.example`의 `__UV__`·`__PROD_DIR__`·`__LOG_DIR__`·`__CALENDAR_DB__`를 채워 `~/Library/LaunchAgents/com.lawbot.monitor.plist`로 복사합니다. `__CALENDAR_DB__`는 캘린더 DB 절대 경로이고, `__UV__`는 `LAW_MONITOR_UV` 환경변수에도 사용합니다. 스케줄은 Mac 시스템 시간대가 KST일 때 매일 06:30입니다.

직접 점검하는 경우에도 프로세스 환경에 `LAW_CALENDAR_DB`를 지정합니다. `{uv}`는 `LAW_MONITOR_UV` 또는 PATH에서 찾은 uv를 사용하고 `{root}`는 저장소 루트입니다. 스케줄은 사전 설치된 캘린더 환경을 `--no-sync`로 실행합니다. 아래 launchctl 명령은 실제 운영 등록/실행/해제 작업입니다.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.monitor.plist
launchctl kickstart gui/$(id -u)/com.lawbot.monitor     # 바로 한 번 실행해 보기
launchctl bootout gui/$(id -u)/com.lawbot.monitor       # 해제
```

등록 전에 같은 worktree에서 `uv run law-monitor bootstrap`을 한 번 실행해야 합니다 (안 하면 collect가 종료 코드 2로 거부).

## 테스트·린트

```bash
uv run pytest services/monitor          # 임시 git 저장소 + 가짜 법제처(tests/fixtures/lawgo, 스파이크 응답을 줄인 것)
uv run pytest -m network services/monitor   # 실제 법제처 1회 (LAW_OC 필요)
uv run ruff check && uv run ruff format --check
```

설정(ruff·pytest)은 레포 최상위 `pyproject.toml`을 따릅니다. 실제 외부 저장소·API에 접속하는 테스트는 `@pytest.mark.network`를 붙이고 `uv run pytest -m network services/monitor`로 따로 실행합니다. 테스트 도우미는 `tests/mon_helpers.py`에 있습니다 (다른 서비스의 `tests/conftest.py`와 이름이 겹치지 않게 conftest를 두지 않음).
