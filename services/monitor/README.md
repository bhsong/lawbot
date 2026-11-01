# 법령 변경 수집기 (law-monitor, 1단계)

매일 한 번 추적 대상 법령·행정규칙을 확인해 **지난 실행과 달라진 것만** 변경 이벤트(JSON)로 저장합니다. 요약(2단계)과 전송(3단계)은 이 이벤트만 읽으면 됩니다.

- 설계: [docs/plans/monitor-collector.md](../../docs/plans/monitor-collector.md) (원 계획서: [monitor-collector-spec.md](../../docs/plans/monitor-collector-spec.md))
- 지금 있는 수집기: `legalize_law`([legalize-kr](https://github.com/legalize-kr/legalize-kr), 법률·시행령·시행규칙), `legalize_admrule`([admrule-kr](https://github.com/legalize-kr/admrule-kr), 고시 등). 둘 다 공개 git 저장소라 키가 필요 없습니다.
- 아직 없는 것: 별표·서식 파일 비교와 개정이유(법제처 API, PR 2), 입법예고·국회 법안(PR 3).

## 설치

레포 최상위에서 실행합니다 (uv workspace 멤버라 다른 서비스와 같이 설치됨).

```bash
uv sync
cp services/monitor/.env.example services/monitor/.env   # PR 1은 키를 쓰지 않음
```

실행 데이터는 `services/monitor/data/`(저장소 클론, `state.db`, 이벤트)와 `services/monitor/logs/`에 생기고 git에 올라가지 않습니다. 다른 곳에 두려면 `LAW_MONITOR_HOME` 또는 `--home`을 씁니다.

## 실행

```bash
uv run law-monitor bootstrap              # 첫 실행: 저장소 부분 클론 + 현재 상태를 기준점으로 (이벤트 없음)
uv run law-monitor collect                # 평소 실행: 기준점 이후 변경만 이벤트로
uv run law-monitor collect --only legalize_law
uv run law-monitor replay --repo legalize-kr --from <커밋> --to <커밋>   # 과거 구간 → data/replay/ (상태 안 바뀜)
```

- 마지막 줄이 실행 요약입니다: `collectors=2 ok=2 failed=0 events=1`. 같은 줄이 `logs/collector-YYYYMMDD.log` 끝에도 남습니다.
- 종료 코드: 0 성공, 1 수집기 일부 실패(나머지는 저장됨), 2 실행 전 거부(설정 오류, bootstrap 전 collect 등).
- 한 수집기가 3번 연속 실패하면 `collector_failing` 이벤트가 한 번 생깁니다.
- `bootstrap --force`는 기준점을 다시 잡습니다. 그 사이 변경은 이벤트가 생기지 않으니, 대상 설정을 바꿔서 새 행정규칙을 처음부터 추적할 때만 씁니다 (설정만 바꾸면 이미 있는 행정규칙은 다음 개정부터 잡힘).

## 이벤트

`data/events/YYYY-MM-DD/<event_id>.json` (+ 본문 diff `<event_id>.diff`). `event_id`는 `sha1(수집기|항목|새 내용 blob)`이라 같은 변경은 몇 번 실행해도 한 번만 저장되고, `state.db`의 `events` 테이블에도 한 줄 들어갑니다 (2단계는 `processed_at`이 빈 것을 처리).

| 필드 | 내용 |
| --- | --- |
| `change_type` | `law_amended` · `law_new` · `law_repealed` · `admrule_amended` · `admrule_new` · `collector_failing`. 행정규칙 삭제는 `admrule_amended` + `removed: true` |
| `category` · `categories` | 한눈에 보기 번호 (행정규칙은 여러 규칙에 맞을 수 있어 목록도 둠) |
| `target` | 법령: `law_name`·`item_id`(저장소 경로)·`law_id`·`status`. 행정규칙: `law_name`·`item_id`(행정규칙ID)·`agency` |
| `dates` | `promulgation`(법령 공포일자, 행정규칙 발령일자) · `effective`(시행일자) |
| `old` · `new` | `version_key`(blob 해시), `file`(전체 내용 사본 `data/files/<blob>.md`), `path`(저장소 경로) |
| `diff` | `path`(본문 unified diff, 머리말 제외), `chunks`(조문·부칙 블록 단위 diff: `article`, `kind` added/changed/removed) |
| `meta_changes` | 머리말에서 바뀐 값 (시행일자·발령번호 등, 첨부 제외) |
| `attachments` | 첨부(별표·서식) 목록의 추가·삭제·제목 변경. 링크(flSeq)는 개정마다 전부 바뀌므로 비교하지 않음 |
| `context` | `source_url`, `commit`(sha·subject·date, 커밋 날짜는 공포일), `addenda_added`(새 부칙 원문: 조문별 시행일), `fallback`, `amendment_text`·`amendment_reason`(PR 2 전에는 `null`) |

같은 실행에 개정 커밋이 둘 들어오면 이벤트도 둘입니다 (공포일·시행일이 섞이지 않게).

## 추적 대상 바꾸기

`config/targets.yaml`만 고칩니다.

- `laws`: legalize-kr `kr/<폴더>/`의 폴더명 (띄어쓰기 없음). 폴더 안 파일(법률·시행령·시행규칙)을 모두 추적합니다.
- `admrules.names`: 소관부처명 + 행정규칙명 정규식. 최저임금처럼 이름에 연도가 들어가는 고시는 `^\d{4}년 적용 최저임금(안)? 고시$`처럼 씁니다.
- `admrules.keyword_rules`: 소관부처명 + 이름 키워드 (하나라도 들어 있으면 대상).
- `agency`는 파일 머리말 `소관부처명`과 정확히 같아야 합니다 (국세청 고시는 경로가 `재정경제부/국세청/...`이지만 소관부처명은 `국세청`).

## launchd 등록 (매일 06:30)

`deploy/com.lawbot.monitor.plist.example`의 `__UV__`·`__PROD_DIR__`·`__LOG_DIR__`을 채워 `~/Library/LaunchAgents/com.lawbot.monitor.plist`로 복사한 뒤:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lawbot.monitor.plist
launchctl kickstart gui/$(id -u)/com.lawbot.monitor     # 바로 한 번 실행해 보기
launchctl bootout gui/$(id -u)/com.lawbot.monitor       # 해제
```

등록 전에 같은 worktree에서 `uv run law-monitor bootstrap`을 한 번 실행해야 합니다 (안 하면 collect가 종료 코드 2로 거부).

## 테스트·린트

```bash
uv run pytest services/monitor          # 임시 git 저장소만 사용 (네트워크 없음)
uv run ruff check && uv run ruff format --check
```

설정(ruff·pytest)은 레포 최상위 `pyproject.toml`을 따릅니다. 실제 외부 저장소·API에 접속하는 테스트는 `@pytest.mark.network`를 붙이고 `uv run pytest -m network services/monitor`로 따로 실행합니다. 테스트 도우미는 `tests/mon_helpers.py`에 있습니다 (다른 서비스의 `tests/conftest.py`와 이름이 겹치지 않게 conftest를 두지 않음).
