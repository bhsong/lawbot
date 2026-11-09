# 운영 관리자·정확 일치 캐시·국회 수집기 반영 (2026-11-09)

운영 worktree를 `ff101b9`(PR #94–#96)에서 `main`의 `26f44ac`(PR #97–#102)로 전환했다. 같은 재시작에서 v5.1.1 관리자 기능, P07 정확 일치 캐시(모드 `off`), 국회 입법예고·법률안 수집기를 켰다. 모든 시각은 KST다. [P07 정확 일치 캐시 운영](../operations.md#p07-정확-일치-캐시)

## 반영 범위

- `ff101b9..26f44ac`의 실행 코드 변경은 `services/chatbot`(정확 일치 캐시, 릴리스 판정, 관리자 캐시 API·탭, 시작 화면 예시 질문)과 `tools/lawbot-tools`(DB 모드 조문 갱신 뒤 캐시 판정, 로그 요약)다.
- 챗봇 프런트엔드가 바뀌어 재빌드했다. `services/model-server`, `services/monitor`, 캘린더, `uv.lock`은 바뀌지 않아 웹앱만 재시작했다.

## 절차

1. 사용자가 운영 `.env`에 관리자 계정·비밀번호 해시, 캘린더 API 주소, `LAWBOT_EXACT_CACHE_DB=data/db/exact-cache.db`, `LAWBOT_EXACT_CACHE_BUNDLE=data/db/p07-handoff.zip`을 추가했다. 운영 `services/monitor/.env`에는 국회 API 키와 `LAW_ASSEMBLY_ENABLED=1`을 추가했다. 에이전트는 값을 읽거나 출력하지 않았다.
2. P07 ZIP을 `data/db/p07-handoff.zip`(권한 600)에 두고 SHA-256 `69034f850b39…`이 고정값과 같음을 확인했다.
3. `git checkout --detach 26f44ac`, `uv sync --frozen`(설치 변경 없음).
4. 캐시 준비 (`uv run --frozen python -m lawbot.cache_admin`)
   - `init --db data/db/exact-cache.db`: 스키마 2, 권한 600
   - `seed --dry-run --report-dir data/db/cache-review-20261109`: 입력 177행, 표시 형식 불일치 제외 4행, 초안 질문 173개·답변 30개, 릴리스 `v5-live-2026-11-03`, namespace `abd13b3e6357`
   - `seed`: 같은 결과로 초안 등록, 모드 `off`
5. 국회 수집기 기준점: `law-monitor bootstrap --only assembly_notice`, `--only assembly_bill` 모두 `ok=1 failed=0 events=0`.
6. 챗봇 프런트엔드 `npm ci && npm run build` 성공.
7. 재시작 전 `check_deploy`: 코퍼스 릴리스 줄 PASS, `정확 일치 캐시: 사용 안 함 (스키마 2 · 모드 off · namespace abd13b3e6357)`. 웹 설정을 읽어 관리자 계정과 캘린더 주소가 설정됨(값 미출력)을 확인했다.
8. 16:46:15에 사용자 요청으로 `launchctl kickstart -k gui/$(id -u)/com.lawbot.web`.

## 확인

| 항목 | 결과 |
|---|---|
| 운영 HEAD | `26f44ac` |
| 시작 로그 | 조문 임베딩 캐시 사용(705개 `e98b86afa4b2`, 792개 `2bba800231b9`), 조문 인덱스 0.3초, 캐시 줄 `모드 off` |
| 웹앱 `/` | 200 |
| 관리자 로그인 (휴대폰) | 채팅·피드백 & 법령 관리 전환 표시 |
| 캘린더 패널 | 일정 표시 |
| 피드백 검수 저장 | 성공 (Origin 검사 통과) |
| 캐시 탭 | 모드 `off`, 초안 답변 30개 |
| 일반 계정 | 관리 전환 없음 |
| 질문 1건 | 답변·조문 카드·측정 줄 정상 (캐시 `off`이므로 RAG) |

휴대폰 확인은 사용자가 했다. 실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않았고, 에이전트는 운영 웹앱에 질문을 보내지 않았다.

## 남은 일

- 국회 수집기의 첫 정기 실행은 2026-11-10 06:30이다. 요약의 국회 항목과 캘린더 반영을 확인한다.
- 캐시는 캐시 탭에서 초안을 `off`로 승인한 뒤 `shadow`를 거쳐 `on`으로 켠다.

## 복구

- 캐시만 끌 때는 캐시 탭의 **모드 off**나 `cache_admin set-mode off`를 쓴다.
- 국회 수집기는 `LAW_ASSEMBLY_ENABLED` 줄을 빼면 다음 실행부터 멈춘다.
- 코드까지 되돌릴 때는 운영 worktree를 `ff101b9`로 checkout하고 챗봇 프런트엔드를 재빌드한 뒤 웹앱을 재시작한다. 캐시 DB와 검토 폴더는 보존한다.
