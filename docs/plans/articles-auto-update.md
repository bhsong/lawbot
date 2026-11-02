# 계획: 조문 데이터 자동 갱신 (monitor 06:30 → 생성 → 아카이브 → 임베딩 → 확인 → 전환 → 재시작)

- 작성일: 2026-11-02
- 사용자 결정 (2026-11-02): 조문 DB(지금은 CSV) 자동화는 Lawbot의 중요한 목표. 지금 레포로 가능하면 미루지 않는다.
  - 위치: 운영 worktree의 **git 밖 `data/live/`**. 바꾸기 전 파일은 날짜를 붙여 `data/live/archive/`에 보관
  - 재시작: **자동**. monitor가 06:30에 돌 때 같이
  - 매일 실행: **monitor와 지금 연결** ([monitor-article-check.md](monitor-article-check.md) 2.2절 설계를 그대로 살림)
- 앞 작업: 생성기 `lawbot-build-articles`(PR #45), 점검 `lawbot-articles-check`(PR #46, 이 PR에 이어서).

## 1. 미리 확인한 사실

- 웹앱은 조문 CSV 경로를 `.env`의 `LAWBOT_V4_DECREE_ARTICLES`(하위법령 켬)·`LAWBOT_V4_ARTICLES`에서 읽고, 매니페스트는 CSV와 같은 폴더에서 찾는다 (`lawbot.manifest`). → `.env`만 `data/live`로 바꾸면 웹앱 코드는 그대로.
- 임베딩 캐시 키 = 모델·max_seq·본문(순서대로). 새 CSV면 새 캐시가 생기고, `lawbot-reindex`의 `build_cache`가 웹앱과 같은 방식으로 미리 만든다 (812조문 약 35초, 모델 서버 상주).
- `check_deploy`는 하위법령 CSV의 법률 행을 **`data/v4/articles_v4.csv`(고정 경로)**와 비교한다 → `data/live`로 바꾸면 FAIL. 설정의 `LAWBOT_V4_ARTICLES`와 비교하도록 고친다.
- `data/v4`와 그 매니페스트는 학습 기준 데이터다 (임베딩·4B LoRA `trained_on`, `data/decree_exp` 실험 재현 테스트). 그대로 둔다.
- 운영 웹앱은 launchd `com.lawbot.web`. monitor도 같은 사용자의 launchd 작업이라 `launchctl kickstart -k gui/<uid>/com.lawbot.web`을 부를 수 있다.
- 개발 worktree와 운영 worktree가 같은 launchd 이름을 본다 → 개발에서 실행한 명령이 운영 웹앱을 재시작하면 안 된다.

## 2. 설계

### 2.1 `lawbot-articles-update` (tools/lawbot-tools)

```
uv run lawbot-articles-update --repo <legalize-kr 클론> [--asof 오늘] [--live-dir data/live] [--report-dir DIR] [--restart]
uv run lawbot-articles-update --init        # data/v4 → data/live (처음 한 번)
uv run lawbot-articles-update --rollback [--restart]   # 바로 전 파일로 되돌림
```

차례 (하나라도 실패하면 그 자리에서 멈추고 지금 파일 그대로, 종료 코드 2):

1. **사전 검사**: 웹앱 설정(`settings.load`)의 조문 CSV가 `--live-dir` 안에 있어야 한다 (아니면 바꿔 봐야 웹앱이 안 읽음). `data/live`가 비어 있으면 `--init`하라고 안내.
2. **점검**: `articles_check`로 기준일(오늘) 생성 결과와 `data/live`를 비교. 같으면 끝 (종료 0, 아무것도 안 씀).
3. **생성·검사**: 생성 결과를 `data/live/.staging/`에 쓰고 `lawbot-reindex`의 검사(열·중복·번호·빈 본문)와 매니페스트(새 `dataset_version` = `live-<기준일>`, `reference_date`·`collected_date` = 기준일, `source.ref` = legalize-kr HEAD, `trained_on`은 이전 값 유지).
4. **임베딩**: 웹앱이 읽을 CSV로 임베딩 캐시를 만든다 (`build_cache`와 같은 Retriever).
5. **확인**: 답변 근거가 바뀐 조문마다 제목+본문 앞부분으로 검색해 그 조문이 상위 10개 안에 나와야 한다 (법률은 `search`, 하위법령은 `search_decrees`). 하나라도 안 나오면 실패.
6. **전환**: 지금 파일(CSV 2개 + 매니페스트 2개)을 `data/live/archive/<이름>_<이전 기준일>.<확장자>`로 옮기고, staging을 제자리로 (`os.replace`). 같은 기준일 아카이브가 이미 있으면 `_2`, `_3`.
7. **재시작** (`--restart`일 때만): `com.lawbot.web`의 작업 폴더가 이 레포일 때만 `launchctl kickstart -k`. 웹앱 포트(`LAWBOT_PORT`)가 3분 안에 응답하면 성공. 응답이 없으면 **아카이브에서 되돌리고 다시 재시작**, 종료 코드 2.
8. **보고서**: `--report-dir/<기준일>.md` (점검 보고서 + 갱신 결과: 아카이브 이름·새 매니페스트·확인 결과·재시작).

종료 코드 (monitor 점검 약속과 같음): 0 바뀐 것 없음, 1 갱신함(확인 필요), 2 실패(지금 데이터 그대로). 마지막 줄: `articles updated=yes|no …`.

### 2.2 monitor 연결

[monitor-article-check.md](monitor-article-check.md) 2.2절 그대로 (`checks` 설정, 하위 프로세스, `law-monitor check`, `daily`: collect → checks → summarize, 요약 md '점검' 절, 15분 상한). `targets.yaml`의 명령만 `lawbot-articles-update ... --restart`로.

### 2.3 그 밖

- `check_deploy`: 법률 행 비교 기준을 설정의 `LAWBOT_V4_ARTICLES`로.
- `.gitignore`에 `data/live/`.
- 운영 반영(사용자 승인 후): 운영 worktree 갱신 → `lawbot-articles-update --init` → 운영 `.env`에 `LAWBOT_V4_ARTICLES=data/live/articles_v4.csv`, `LAWBOT_V4_DECREE_ARTICLES=data/live/law_articles_v4_decree.csv` → `check_deploy` → 웹앱 재시작. monitor launchd는 그대로 `daily`.

## 3. 순서 (커밋, PR #46에 이어서)

1. 이 계획.
2. monitor 점검 연결 (되돌렸던 커밋 다시).
3. `check_deploy` 기준 경로, `.gitignore`.
4. `lawbot-articles-update` + 테스트.
5. `targets.yaml` 명령 교체, 문서 (`operations.md`, `data-provenance.md`, monitor README), 확인 기록.

## 4. 테스트

- 갱신 (가짜 생성기·임베딩·재시작·포트):
  - 같으면 0이고 파일이 그대로다.
  - 다르면 아카이브 이름(날짜), 새 파일·매니페스트(기준일·`source.ref`·sha), 캐시 생성, 재시작이 이 순서로 일어나고 1을 돌려준다.
  - 확인 실패, 포트 무응답(되돌리고 다시 재시작), 설정이 `data/live`가 아닌 경우, `data/live`가 빈 경우는 모두 2다.
  - `--init`과 `--rollback`.
  - 작업 폴더가 다른 launchd 작업이면 재시작하지 않는다.
- monitor: 이전 시험 구현의 테스트 그대로.
- `check_deploy`: `LAWBOT_V4_ARTICLES`를 바꾸면 그 파일과 비교한다.

## 5. 수동 확인 (맥 미니)

- 개발 worktree에서: `--init` → `--asof 2026-11-27`(재시작 없이)로 실제 갱신. 아카이브·새 파일·매니페스트·임베딩 캐시를 확인한다. 개발 웹앱을 `data/live`로 띄워 바뀐 조문(남녀고용평등법 난임치료휴가 4일) 질문 → 새 본문으로 답하는지 본다 → `--rollback`.
- 개발 웹앱 → 운영 모델 서버 질문 3개 (PR 규칙).
- 운영 반영은 병합 뒤 승인받고.

## 6. 하지 않는 것

- 전체 val 평가 자동 실행 (오래 걸림, 큰 개정 때 손으로).
- 임베딩·4B 재학습.
- 웹앱이 재시작 없이 새 CSV를 읽는 기능.
