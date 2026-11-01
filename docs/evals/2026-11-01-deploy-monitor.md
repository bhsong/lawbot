# 기록: 법령 변경 수집기 운영 반영 (launchd 매일 06:30)

- 날짜: 2026-11-01
- 운영 worktree `~/Work/lawbot-prod`: 33c5565 (v4.4.1, PR #35) → **e98c158** (main, PR #39 병합)
- 챗봇·모델 서버 코드 차이: 주석 2줄(계획 문서 경로)뿐 → 운영 모델 서버·웹앱은 **재시작하지 않음** (PID 그대로 확인)
- 의존성: `uv sync --frozen`이 `law-monitor`·`tenacity` 2개만 추가 (`--dry-run`으로 먼저 확인)
- 비밀값: 개발 레포 `services/monitor/.env`(LAW_OC)를 운영 worktree 같은 경로로 복사, 권한 600, git 제외 확인
- launchd: `~/Library/LaunchAgents/com.lawbot.monitor.plist` (템플릿 `services/monitor/deploy/`, uv `/opt/homebrew/bin/uv`, 로그 `~/Library/Logs/lawbot/monitor.log`)

## 결과

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | `git checkout --detach e98c158` → `uv sync --frozen` | 오류 없음, 2개 추가 | **OK** |
| 2 | `uv run --frozen law-monitor bootstrap` | 이벤트 0, 커서 = 각 HEAD | **OK**. legalize-kr `faa04a9c1dd1`, admrule-kr `a909fccbacba`, 대상 파일 법령 40·행정규칙 36. 96초, `services/monitor/data/` 591MB |
| 3 | `plutil -lint` → `launchctl bootstrap` | OK | **OK** |
| 4 | `launchctl kickstart` (launchd로 collect 1회) | 종료 코드 0, `ok=2 failed=0` | **OK**. `collectors=2 ok=2 failed=0 events=0`, last exit code 0 |
| 5 | 운영 모델 서버·웹앱 | 계속 실행 | **OK**. PID 55593·55635 그대로 |

## 남은 확인

- 첫 자동 실행: 2026-11-02 06:30. `~/Work/lawbot-prod/services/monitor/logs/collector-20261102.log`의 마지막 줄과 `launchctl print gui/$(id -u)/com.lawbot.monitor`의 `runs`·`last exit code`를 본다.
- 이벤트 위치: `~/Work/lawbot-prod/services/monitor/data/events/YYYY-MM-DD/`. 2단계 전에는 쌓이기만 한다.

## 되돌리기

`launchctl bootout gui/$(id -u)/com.lawbot.monitor` → plist 삭제 → 운영 worktree `git checkout --detach 33c5565` → `uv sync --frozen`. 실행 데이터(`services/monitor/data/`)와 `.env`는 git 밖이라 checkout과 무관하며, 지우려면 따로 지운다.

## 추가 반영: 요약(2단계) `daily` 전환 (2026-11-01 23:35)

- 운영 worktree: e98c158 → **800e6df** (main, PR #41 병합). 챗봇·모델 서버·`pyproject.toml`·`uv.lock` 차이 없음 → `uv sync --frozen --dry-run` "Would make no changes", 모델 서버·웹앱 재시작 안 함 (PID 그대로)
- launchd: `bootout` → 템플릿으로 plist 다시 만듦(실행 명령 `collect` → **`daily`** = collect 다음 summarize) → `plutil -lint` → `bootstrap`. 이전 plist는 세션 임시 폴더에 백업
- 요약 모델 설정: 운영 worktree 최상위 `.env`의 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`을 그대로 읽음 (복사 안 함)

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | `launchctl kickstart` (daily 1회) | 종료 코드 0, 요약 md 생성 | **OK**. last exit code 0. `collectors=2 ok=2 failed=0 events=0`, `summarize events=0 calls=0 pending=0` |
| 2 | `services/monitor/data/summaries/2026-11-01.md` | 대상 구간·새 변경 없음 | **OK**. `대상: 2026-11-01 22:28 ~ 2026-11-01 23:35 사이 새로 감지된 변경`, "새 변경 없음" (모델 호출 없음) |
| 3 | `runs` 테이블 | summarize 실행 기록 | **OK**. bootstrap · normal · normal · summarize |

남은 확인: 2026-11-02 06:30 자동 실행 → `summaries/2026-11-02.md` (대상 11-01 23:35 ~ 11-02 06:30).

되돌리기(요약만): plist의 `daily`를 `collect`로 바꿔 `bootout` → `bootstrap`. 쌓인 이벤트는 `processed_at`이 빈 채로 남아 나중에 `law-monitor summarize`로 요약할 수 있다.
