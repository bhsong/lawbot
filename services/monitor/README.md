# 법령 변경 수집기 (law-monitor, 1단계)

매일 한 번 추적 대상 법령·행정규칙을 확인해 **지난 실행과 달라진 것만** 변경 이벤트(JSON)로 저장합니다. 요약(2단계)과 전송(3단계)은 이 이벤트만 읽으면 됩니다.

- 설계: [docs/plans/monitor-collector.md](../../docs/plans/monitor-collector.md) (원 계획서: [monitor-collector-spec.md](../../docs/plans/monitor-collector-spec.md))
- 지금 있는 수집기: `legalize_law`([legalize-kr](https://github.com/legalize-kr/legalize-kr), 법률·시행령·시행규칙), `legalize_admrule`([admrule-kr](https://github.com/legalize-kr/admrule-kr), 고시 등). 변경은 공개 git 저장소에서 찾고, 이벤트마다 법제처 Open API로 개정문·제개정이유를 채웁니다 ([계획](../../docs/archives/monitor-lawgo.md)).
- 별표·서식: `forms` 대상 법령과 추적 행정규칙은 신·구판의 별표·서식 텍스트를 비교해 바뀐 것만 `lawgo_forms`·`lawgo_admrul` 이벤트로 만들고, 그 서식의 PDF·HWP 원본을 받습니다.
- 요약(2단계): 요약하지 않은 이벤트를 운영 모델 서버의 `qwen3.8-27b-4bit`로 요약해 `data/summaries/YYYY-MM-DD.md`를 만듭니다 ([계획](../../docs/plans/monitor-summary.md)).
- 아직 없는 것: 입법예고·국회 법안(PR 3). 한눈에 보기 5번 전자신고 파일 레이아웃(지급명세서 전산매체 제출요령)은 수집하지 않고 3단계 연례 리마인드로 합니다 ([결정](../../docs/plans/monitor-collector.md) 9절).

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
uv run law-monitor daily                  # launchd가 매일 실행: collect 다음 summarize
```

- 마지막 줄이 실행 요약입니다: `collectors=2 ok=2 failed=0 events=1`. 같은 줄이 `logs/collector-YYYYMMDD.log` 끝에도 남습니다.
- 종료 코드: 0 성공, 1 수집기 일부 실패(나머지는 저장됨), 2 실행 전 거부(설정 오류, bootstrap 전 collect, `LAW_OC` 없음 등).
- `LAW_OC`는 환경변수가 먼저이고, 없으면 `services/monitor/.env`에서 읽습니다. `bootstrap`은 법제처를 부르지 않아 키 없이도 됩니다. 로그·이벤트·오류 메시지에는 `OC=***`로만 남습니다.
- 법제처 호출이 실패하면(재시도 3회 뒤) 그 수집기가 실패로 끝나고 커서가 그대로라, 다음 실행에서 같은 변경을 다시 처리합니다. 원문이 빠진 이벤트는 저장하지 않습니다.
- 한 수집기가 3번 연속 실패하면 `collector_failing` 이벤트가 한 번 생깁니다.
- `bootstrap --force`는 기준점을 다시 잡습니다. 그 사이 변경은 이벤트가 생기지 않으니, 대상 설정을 바꿔서 새 행정규칙을 처음부터 추적할 때만 씁니다 (설정만 바꾸면 이미 있는 행정규칙은 다음 개정부터 잡힘).

## 요약 (2단계)

- 대상: `state.db`의 `events`에서 `processed_at`이 빈 것. 요약이 끝나면 `processed_at`을 적습니다.
- 법령·행정규칙 이벤트 하나에 모델 호출 하나입니다. 같은 개정의 별표서식 이벤트(`parent_event_id`)는 그 호출에 함께 넣습니다. 입력은 호출당 약 12,000자이고, 넘치면 뒤를 자르고 내용을 못 넣은 서식은 제목만 넣습니다.
- 모델 설정은 웹앱과 같은 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`입니다. 환경변수 → `services/monitor/.env` → **레포 최상위 `.env`** 순서로 읽어서, 운영 worktree에서는 따로 넣을 것이 없습니다. 모델 id는 `LAW_MONITOR_SUMMARY_MODEL`(기본 `qwen3.8-27b-4bit`). 생성 설정은 temperature 0, 최대 800토큰, thinking 끔.
- md 구성: 머리(실행 시각·수집 요약 줄·이벤트 수) → 한눈에 보기 번호별 절 → 법령마다 모델 요약 + 바뀐 별표서식 목록 + 원문 이벤트 경로·법제처 링크 → 경고(수집기 연속 실패). 새 이벤트가 없으면 "새 변경 없음".
- 같은 날 두 번 실행하면 두 번째 파일은 `YYYY-MM-DD-HHMMSS.md`입니다 (앞 파일을 덮지 않음).
- 모델 서버가 실패하면(시도 3번) md에 "요약 실패, 대기 이벤트 N건"을 쓰고 그 이벤트는 대기로 둡니다. 다음 실행에서 밀린 것까지 요약합니다.
- 요약은 참고용입니다. md 머리에도 "시행일·금액·서식 내용은 원문으로 확인"이라고 씁니다.

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

`config/targets.yaml`만 고칩니다.

- `laws`: legalize-kr `kr/<폴더>/`의 폴더명 (띄어쓰기 없음). 폴더 안 파일(법률·시행령·시행규칙)을 모두 추적합니다.
- `admrules.names`: 소관부처명 + 행정규칙명 정규식. 최저임금처럼 이름에 연도가 들어가는 고시는 `^\d{4}년 적용 최저임금(안)? 고시$`처럼 씁니다.
- `admrules.keyword_rules`: 소관부처명 + 이름 키워드 (하나라도 들어 있으면 대상).
- `agency`는 파일 머리말 `소관부처명`과 정확히 같아야 합니다 (국세청 고시는 경로가 `재정경제부/국세청/...`이지만 소관부처명은 `국세청`).
- `forms`: 별표·서식을 비교할 법령(머리말 `제목`, 띄어쓰기 있음)과 구분(`별표`·`서식`). 추적 행정규칙의 별지·별표는 설정 없이 모두 비교합니다.

## launchd 등록 (매일 06:30)

템플릿은 `law-monitor daily`(collect → summarize)를 실행합니다. `deploy/com.lawbot.monitor.plist.example`의 `__UV__`·`__PROD_DIR__`·`__LOG_DIR__`을 채워 `~/Library/LaunchAgents/com.lawbot.monitor.plist`로 복사한 뒤:

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
