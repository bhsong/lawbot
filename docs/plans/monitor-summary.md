# 계획: 법령 변경 요약 (2단계, qwen3.8-27b-4bit → 요약 md)

- 작성일: 2026-11-01
- 상위: [monitor-collector.md](monitor-collector.md) (1단계), 원 계획서 9장 "2·3단계와의 연결"
- 목표: 매일 06:30 수집이 끝나면, 아직 요약하지 않은 이벤트(`events.processed_at`이 빈 것)를 운영 모델 서버의 `qwen3.8-27b-4bit`로 요약해 **그날의 요약 md 파일**을 만든다

## 1. 동작

```
launchd 06:30 → law-monitor daily
                 ├─ collect   (지금과 같음: 이벤트 JSON + events 테이블)
                 └─ summarize (processed_at이 빈 이벤트 → 모델 서버 → data/summaries/YYYY-MM-DD.md → processed_at 기록)
```

- `daily`는 collect가 일부 실패해도 summarize를 한다 (저장된 이벤트는 요약하고, 실패한 수집기는 md 맨 위에 적음).
- 새 이벤트가 없어도 md를 만든다: "새 변경 없음" + 수집기 상태 한 줄. 매일 돌았는지 파일로 확인할 수 있게.
- 모델 서버가 꺼져 있거나 실패하면: md에 "요약 실패, 대기 이벤트 N건"을 쓰고 `processed_at`을 기록하지 않는다 → **다음 실행에서 밀린 이벤트까지 함께 요약**. 이벤트는 잃지 않는다.
- 수동 실행: `law-monitor summarize` (대기 이벤트), `law-monitor summarize --events-dir data/replay/<구간>` (replay 결과를 상태 변경 없이 요약, md는 그 폴더에).

## 2. 요약 단위와 프롬프트

- **묶음**: 법령·행정규칙 이벤트 하나 = 모델 호출 하나. 같은 개정의 별표서식 이벤트(`context.parent_event_id`)는 부모 호출에 함께 넣는다 (조세특례제한법 시행규칙처럼 서식이 46건 나와도 호출은 1번). `collector_failing`은 모델 없이 경고로만 적는다.
- **입력** (한 호출 최대 약 12,000자, 넘으면 뒤를 자르고 "(이하 생략)" 표시):
  1. 법령·고시 이름, 변경 종류, 공포·시행일, 조문별·서식별 시행일
  2. 제·개정 이유 (법제처 원문)
  3. 바뀐 조문 diff (`diff.chunks`, 추가·삭제 줄만)
  4. 별표서식: 서식마다 제목·시행일 + 바뀐 줄 앞부분(서식당 최대 1,500자). 한도를 넘는 서식은 제목만 나열
- **지시 (system)**: ERP(급여·원천징수·4대보험·부가세 신고) 담당 개발자에게 알려 주는 요약. **주어진 원문에 없는 내용을 지어내지 말 것**, 조문·서식 번호를 그대로 쓸 것, 판단이 어려우면 "원문 확인 필요"라고 쓸 것.
- **출력 형식** (모델에게 요구): `무엇이 바뀌었나` 2~4줄, `시행일`, `ERP 영향`(급여 계산·원천징수·신고 서식·보험료율 중 해당되는 것), `확인할 것`.
- **생성 설정**: `temperature` 0, `max_tokens` 800, `chat_template_kwargs.enable_thinking` false (챗봇과 같음).

## 3. 요약 md 형식 (`data/summaries/YYYY-MM-DD.md`)

```
# 법령 변경 요약 2026-11-02 (06:30 실행)
수집: collectors=2 ok=2 failed=0 · 새 이벤트 3건 (법령 1, 별표서식 2) · 모델 qwen3.8-27b-4bit
## 1. 법률·시행령·시행규칙
### 소득세법 시행규칙 (일부개정, 공포 2026-05-22, 시행 2026-05-22)
<모델 요약>
- 바뀐 서식 5개: 서식 21 원천징수이행상황신고서 (시행 05-22) …
- 원문: 이벤트 JSON 경로 · 법제처 링크
## 7. 4대보험·최저임금
…
## 경고
- legalize_admrule 3회 연속 실패 …
```

- 절 순서는 한눈에 보기 번호(category) 순. 모델 요약 아래에 **원문 경로와 링크를 그대로** 붙여 사람이 확인할 수 있게 한다 (요약은 참고용).
- 머리에 "자동 요약입니다. 시행일·금액은 원문으로 확인하세요"를 넣는다.

## 4. 모델 서버 접속

- 운영 모델 서버(챗봇과 같은 것)를 쓴다. 새 모델을 올리지 않아 메모리가 늘지 않는다 (27B는 이미 상주).
- 주소·토큰은 웹앱과 같은 `LAWBOT_MODEL_SERVER`·`LAWBOT_MODEL_TOKEN`을 읽는다: 환경변수 → `services/monitor/.env` → **레포 최상위 `.env`** (운영 worktree에 이미 있음, 토큰을 복사하지 않음). 모델 id는 `LAW_MONITOR_SUMMARY_MODEL`(기본 `qwen3.8-27b-4bit`).
- 호출: `POST /v1/chat/completions` (httpx, 새 의존성 없음). 타임아웃 300초, 재시도 2회. 요청 사이 간격은 두지 않음 (모델 서버가 순서대로 처리, `LAWBOT_MAX_CONCURRENT=1`).
- 06:30에는 챗봇 사용이 거의 없어 겹칠 일이 적다. 겹치면 모델 서버가 차례로 처리하므로 챗봇 답이 늦어질 뿐이다.

## 5. 파일

```
services/monitor/src/law_monitor/
  summarize.py     # 대기 이벤트 묶기 → 프롬프트 → 모델 → md 쓰기 → processed_at
  llm.py           # OpenAI 호환 chat 클라이언트 (httpx), 토큰 가림
  state.py         # pending_events(), mark_processed()
  secrets.py       # 레포 최상위 .env도 읽기 (LAWBOT_MODEL_SERVER·TOKEN)
  cli.py           # summarize, daily
services/monitor/deploy/com.lawbot.monitor.plist.example   # collect → daily
services/monitor/tests/test_mon_summary.py                 # 가짜 모델 서버 (httpx.MockTransport)
```

`services/monitor/` 밖 변경 (구현 전에 승인):

| 파일 | 내용 |
| --- | --- |
| `docs/plans/monitor-summary.md` | 이 계획 |
| `docs/plans/monitor-collector.md` | 5번 결정(수집 안 함, 3단계 연례 리마인드) + "3단계로 넘기는 것" 절 |
| `docs/evals/2026-11-01-monitor-spike.md` | 5번 출처 확인(국세청·홈택스) 절 |
| `docs/evals/<날짜>-monitor-summary.md` | 실제 모델 확인 결과 |
| 운영: `~/Library/LaunchAgents/com.lawbot.monitor.plist` | `collect` → `daily`로 바꿔 다시 등록 (병합 뒤, 따로 승인) |

## 6. 테스트 (구현보다 먼저, 가짜 모델 서버)

| 테스트 | 확인 |
| --- | --- |
| 묶음 | 법령 1 + 별표서식 2(같은 부모) → 모델 호출 1번, 서식 제목이 프롬프트에 들어감 |
| 길이 제한 | 큰 diff → 12,000자 안으로 자르고 "(이하 생략)", 넘치는 서식은 제목만 |
| md | 절 순서(category), 원문 경로·링크, "새 변경 없음", 경고 절 |
| processed_at | 성공한 이벤트만 기록. 두 번째 summarize는 0건 |
| 실패 | 모델 서버 500 → md에 "요약 실패, 대기 N건", processed_at 그대로 → 다음 실행에서 밀린 것까지 요약 |
| 요청 | `model`·`temperature 0`·`enable_thinking false`·`Authorization: Bearer`, 토큰이 md·로그에 없음 |
| replay 폴더 | `--events-dir`는 DB를 바꾸지 않고 그 폴더에 md |
| daily | collect 실패가 있어도 summarize 실행, 종료 코드는 collect 기준 |
| 설정 읽기 | `LAWBOT_MODEL_SERVER`가 환경변수 → monitor `.env` → 레포 `.env` 순 |

## 7. 실제 확인 (맥미니, 운영 모델 서버, 실행 전 승인)

| # | 항목 | 기대값 |
| --- | --- | --- |
| 1 | `summarize --events-dir data/replay/4220f9dc_51fea869` (임시 HOME, PR 2 확인 때 만든 70건) | 법령 19건 → 호출 19번, md 1개. 소득세법 시행규칙 요약에 퇴직소득 정산·서식 21 등, 조세특례제한법 시행규칙은 호출 1번에 서식 46개 |
| 2 | 1번 품질 (사람이 읽고 판단) | 원문에 없는 금액·날짜를 지어내지 않음, 시행일이 원문과 같음 |
| 3 | 시간·메모리 | 호출당 시간, MLX peak (요청 로그의 timings) |

## 8. 일정 (내일 06:30)

- 오늘 밤: 구현 → PR → 실제 확인(승인) → 병합(사용자) → 운영 반영(plist를 `daily`로, 승인)까지 되면 **내일 06:30부터 요약 md가 나온다**.
- 늦어져도 손해는 없다: 내일 06:30에는 지금 plist대로 `collect`만 돌고, 이벤트는 `processed_at`이 빈 채로 쌓인다. 반영한 뒤 첫 `daily`(또는 수동 `summarize`)가 밀린 것까지 요약한다.
