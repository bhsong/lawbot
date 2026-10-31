# B6 답변 피드백을 하위법령 첨부 main 위로 다시 올리기

PR #25(`feat/feedback`, 기준 `bc4b2a4` = #24 병합)를 하위법령 첨부(#26)·정리(#27)·운영 기록(#28)이 들어간 main(`c8dbd3e`) 위로 옮긴다.
새 브랜치 `feat/feedback-main`, 새 PR. #25는 새 PR로 대체한다고 적고 닫지는 않는다 (사용자가 정함).

## 1. 옮기기

- `git cherry-pick 72175ed` (B6 커밋 하나)
- 충돌 3곳은 양쪽을 모두 살린다
  - `.env.dev.example`: 하위법령 블록 뒤에 피드백 주석 블록
  - `.gitignore`: `data_gd/` 뒤에 피드백 항목
  - `app.py`: import(`feedback`, `feedback_ui` + `is_decree`), `intro_text`·`sources_heading` 뒤에 `make_on_feedback`, `build_demo(app, fb=None)`
- 옮긴 상태에서 `pytest` 통과 확인 (main 381 + 피드백 20 = 401)

## 2. 하위법령 첨부와 맞추기 (main에 새로 생긴 것)

main에서는 첨부 모델(기본 Pro)의 `hits`가 법률 3개 + 시행령·시행규칙 최대 k개다. B6 코드는 이것을 모른다.

| 문제 | 바꿀 것 |
|---|---|
| 저장 기록의 `hits`가 `HIT_FIELDS`만 남겨 `kind`·`parent_law`가 빠짐 → 어느 것이 하위법령인지 기록에서 알 수 없음 | `HIT_FIELDS`에 `kind`, `parent_law` 추가 (법률 hit에는 원래 없으므로 그대로 없음) |
| 이번 답에 하위법령 첨부가 켜졌는지, τ·k가 무엇이었는지 기록 없음 | `decree`: 첨부 모델이면 `{tau, k}`, 아니면 `None`. `n_decree`(첨부 수) |
| 측정값에 `decree_search`·`decree_rerank` 없음 | `TIMING_FIELDS`에 추가 |
| 내보내기의 조문 본문 대조(`stale`)가 법률 CSV(`articles_v4.csv`)만 봐서 하위법령 hit를 모두 "바뀜"으로 셈. h_gold에 하위법령 조문을 쓸 수 없음 | 기본 조문 CSV를 `articles_v4.csv` + `law_articles_v4_decree.csv` 둘 다로 (`--articles`는 여러 개 받음) |

프롬프트 재구성(`build_prompt_v4(hits, question)`)은 그대로 맞다: main의 `service.ask`도 하위법령을 포함한 `hits` 전체로 같은 함수를 부른다.
검수의 평가 데이터 중복 제거 대상은 바꾸지 않는다 (v4 test 2,075 등). val 제외 여부는 사용자 결정 대기.

## 3. 테스트 (먼저 작성)

`tests/test_feedback.py`

- **T11** 첨부 모델(Pro) 턴: 저장된 `hits`가 `service.ask`의 hits와 같고(하위법령 `kind`·`parent_law`·`rerank_score` 포함), `decree == {'tau': 0.9, 'k': 2}`, `n_decree`가 hits 수와 맞고, `timing`에 `decree_rerank`가 있음. 같은 앱의 Fast 턴은 `decree is None`
- **T12** 내보내기: 하위법령 hit가 있는 기록을 법률+하위법령 CSV로 내보내면 `stale` 경고 없음, SFT 프롬프트 = `build_prompt_v4(hits, 질문)`(하위법령 블록 포함), `h_gold`에 하위법령 조문을 쓰면 positive가 그 본문

## 4. 바꾸지 않는 것

`service.py`, `decree.py`, `retriever.py`, `remote.py`, `model_server.py`, `llm.py`. 답변 결과·모델 서버 API 변화 없음. 운영 반영은 웹앱 재시작만.
