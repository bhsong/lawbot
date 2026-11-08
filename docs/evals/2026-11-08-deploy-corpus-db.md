# 운영 코퍼스 DB 전환 확인 (2026-11-08)

운영 worktree를 태그 `v5.1.1`의 `6657a06`에서 `main`의 `ff101b9a94bb52df31f6a4a37d40ffa4420ced01`(PR #94–#96)로 전환하고, 웹앱과 06:30 조문 자동 갱신이 SQLite DB의 활성 코퍼스 릴리스를 읽도록 바꿨다. 관리자 변수는 아직 넣지 않았다(관리자 기능 꺼짐). 모든 시각은 KST다. [전환 순서](../plans/sqlite-admin-pipeline.md#73-코퍼스-db-전환-pr-1012-병합-후) · [DB 코퍼스 운영](../operations.md#db-코퍼스와-csv-내보내기)

## 반영 범위

- `6657a06..ff101b9`의 실행 코드 변경은 `services/chatbot/src`(DB 코퍼스 읽기, 임베딩 기록, `export-release`, 배포 점검)와 `tools/lawbot-tools`(스냅샷 내보내기, 분할 배정, DB 모드 조문 갱신)다.
- DB 마이그레이션, 두 프런트엔드, `services/model-server`, `services/monitor`, `uv.lock`은 바뀌지 않았다. 모델 서버 재시작과 화면 재빌드는 하지 않았다.

## 절차 (2026-11-08)

1. 반영 전 `check_deploy` 통과: 기준일 2026-11-03, 하위법령 포함 CSV `a379d1193dcf`, 비삭제 조문 1,497개.
2. 백업
   - `lawbot-db backup` → `data/db/backups/lawbot-20261108-193643-pre-corpus.db`
   - `data/live/v5`의 CSV 2개와 매니페스트 2개를 `data/live/archive/pre-corpus-db-20261108/`에 복사. 4개 파일의 SHA-256이 원본과 같다.
3. `git checkout --detach ff101b9`, `uv sync --frozen`(설치 변경 없음). 운영 worktree의 로컬 변경은 기존 `models` 링크뿐이었다.
4. 이관
   - `migrate_csv_to_sqlite corpus --db data/db/lawbot.db --dir data/live/v5 --profile v5 --release-id v5-live-2026-11-03 --dry-run`: 입력 1,525행, DB 미접근
   - 같은 명령에 `--activate`: 변경 1,526건. 릴리스 상태 `validated`, 활성 대상 `v5` → `v5-live-2026-11-03`
   - `verify`: 종료 코드 0, 코퍼스 1개와 QA 세트 8개 모두 `OK`
   - `lawbot-db export-release --release v5-live-2026-11-03`로 임시 폴더에 내보낸 4개 파일이 `data/live/v5`와 같다.

     | 파일 | SHA-256 앞 12자리 |
     |---|---|
     | `articles_v5.csv` | `bd1633defcaf` |
     | `law_articles_v5_decree.csv` | `a379d1193dcf` |
     | `manifest.json` | `fdc3f75cac4a` |
     | `manifest_law_articles_v5_decree.json` | `c3f1d0ffade1` |
5. 사용자 요청으로 운영 `.env` 맨 끝에 `LAWBOT_CORPUS=db` 한 줄을 추가했다(기존 같은 변수 0개 확인, 다른 값은 읽거나 출력하지 않음, 권한 600 유지). 웹앱과 monitor의 `lawbot-articles-update`가 같은 `.env`를 읽는다.
6. 재시작 전 `check_deploy`: `[PASS] 코퍼스 릴리스 v5-live-2026-11-03 · 데이터 버전 v5 · sha256 a379d1193dcf · 비삭제 조문 1497개`, 종료 코드 0.
7. 19:40:04에 `launchctl kickstart -k gui/$(id -u)/com.lawbot.web`. 직전 요청은 12:14였다.

## 확인 (2026-11-08)

| 항목 | 결과 |
|---|---|
| 운영 HEAD | `ff101b9` |
| 시작 로그 | 조문 임베딩 캐시 사용(705개 `e98b86afa4b2`, 792개 `2bba800231b9`), 조문 인덱스 5.7초, 프로필 v5·리랭커·하위법령 켜짐 |
| 웹앱 `/` | 200 |
| `check_deploy` | 코퍼스 릴리스 줄 PASS, 종료 코드 0 |
| `release_embedding_sets` | `v5-live-2026-11-03`의 `law`·`decree` 2건, `lawbot-emb-v5b` revision `sha256:ce8061603299bb6b` |

실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않았다. 에이전트가 운영 웹앱에 질문을 보내지 않았다.

## 첫 DB 모드 자동 실행 (2026-11-09 06:30)

PR #90의 blob 사전 수신 이후 첫 정기 실행이자 DB 모드 조문 갱신의 첫 실행이다.

- monitor 수집 06:30:01–06:30:02 `ok`(수집기 2개 성공, 이벤트 0건). 캘린더 import `imported=0 unchanged=0 failed=0 skipped=0`.
- 챗봇 조문 점검은 06:31:50에 끝났다(수집 종료 후 약 108초, 제한 900초). 2026-11-08에는 같은 점검이 900초 시간 초과였다.
- 마지막 줄: `articles updated=no same=yes changed=0 pending=159 next=2026-11-27 new=0`. 보고서 `articles/2026-11-09.md`의 지금 데이터는 `릴리스 v5-live-2026-11-03`(원문 `bcb7aea7`), 기준 원천 legalize-kr `6b28ed7d`, 결과 "시행 중인 법과 같음", 데이터를 만든 뒤 들어온 개정 없음.
- launchd의 monitor 종료 코드 0. 요약 `summaries/2026-11-09.md` 06:31:50, 모델 호출 0번.
- 변경이 없어 새 릴리스, `archive=`, 웹앱 재시작은 없었다. 운영 DB의 활성 릴리스는 `v5-live-2026-11-03` 그대로이고 `corpus_events`는 `imported`·`activated` 2건뿐이다.
- 07:07 확인: 웹앱 `/` 200, `check_deploy` 코퍼스 릴리스 줄 PASS, 운영 HEAD `ff101b9`.

## 복구

자동 갱신으로 새 릴리스가 활성화된 뒤라면 먼저 `uv run lawbot-db export-release --db data/db/lawbot.db --release <활성 릴리스> --out data/live/v5 --overwrite`로 CSV를 맞춘다. 운영 `.env`에서 `LAWBOT_CORPUS` 줄을 빼고 웹앱을 재시작한다. 코드까지 되돌릴 때는 운영 worktree를 `6657a06`(`v5.1.1`)으로 checkout한다. DB의 릴리스·이벤트와 2단계 백업은 지우지 않고 보존한다.
