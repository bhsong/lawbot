# 계획: 조문 데이터 점검을 monitor에 연결

- 작성일: 2026-11-02
- 목표: 매일 아침 monitor가 돌 때 "챗봇 조문 데이터가 **오늘 시행 중인 법**과 같은지"를 확인하고, 다르면 무엇이 언제부터 다른지와 다시 만드는 명령을 요약 md에 남긴다. CSV를 바꾸거나 웹앱을 재시작하는 일은 자동으로 하지 않는다 (본문이 바뀌면 임베딩·4B 평가를 다시 봐야 하므로 사람이 판단).
- 앞 작업: 조문 CSV 생성기 `lawbot-build-articles` (PR #45, [archives/article-generator.md](../archives/article-generator.md)).

## 1. 미리 확인한 사실

- monitor의 legalize-kr 클론(`services/monitor/data/repos/legalize-kr`, bare 부분 클론)은 `collect`가 매일 HEAD까지 갱신한다. 생성기는 `git log HEAD`·`cat-file`만 쓰므로 이 클론을 그대로 읽을 수 있다. 운영 클론 HEAD = 지금 데이터를 만든 `8c89174`.
- monitor의 추적 대상(`config/targets.yaml` `laws`)은 ERP용 목록이라 챗봇 7개 법률 중 4개(임금채권보장법·파견법·기간제법·남녀고용평등법)가 없다. 그래서 **monitor 이벤트를 계기로 삼지 않고**, 매일 오늘 날짜를 기준일로 생성해 지금 데이터와 비교한다. 이렇게 하면 (1) 새 개정이 저장소에 들어온 것과 (2) 시행일이 되어 데이터가 낡게 된 것을 모두 잡는다.
- 생성은 약 50초 걸린다 (21개 문서 전체 이력). 매일 한 번이면 문제없다.
- 계층: `lawbot-tools`는 웹앱 런타임(`lawbot`)을 import하는 맨 위 계층이고, monitor는 `lawbot-common`만 쓴다. monitor가 `lawbot-tools`를 import하지 않도록 **명령(하위 프로세스)으로 부른다**.

## 2. 설계

### 2.1 `lawbot-articles-check` (tools/lawbot-tools)

```
uv run lawbot-articles-check --repo <legalize-kr 클론> [--asof YYYY-MM-DD] [--data-dir data/v4] [--report-dir DIR]
```

- `--asof` 기본은 오늘(한국 시간). 생성기 `build(repo, asof)`로 메모리에서 만들고, `--data-dir`의 법률+하위법령 CSV(`law_articles_v4_decree.csv`, 법률 행 포함)와 비교한다. 파일은 쓰지 않는다 (보고서만).
- 비교 결과
  - **답변 근거가 다른 조문**: 조문 추가·없어짐, 본문·제목 변경, 삭제 여부 변경 (런타임이 쓰는 열). 조문마다 시행일(`effective_date`)과 짧은 diff.
  - 그 밖의 열 변경은 열별 개수만 (`effective_date` 등).
  - **시행 예정**: 기준일 뒤에 시행되는 개정 (문서·공포번호·시행일·조문). 생성기가 조합하며 이미 알고 있는 "시행 전" 목록을 조문·날짜까지 돌려주게 한다.
  - **데이터를 만든 뒤 들어온 개정**: 매니페스트 `source.ref`..HEAD 사이에 21개 파일을 바꾼 커밋 (공포일·공포번호·시행일자·커밋 제목). `source.ref`가 없거나 클론에 없으면 이 절은 "알 수 없음".
  - 다르면 다음 할 일: 새 폴더에 생성(`lawbot-build-articles --out-dir data/<새 버전>`) → `lawbot-reindex --source-ref` → val 확인 → 재시작.
- 보고서: `--report-dir`를 주면 `<기준일>.md`로 쓴다. 마지막 줄은 한 줄 요약 `articles same=… changed=… pending=… next=… new=… → <보고서>`.
- 종료 코드: 0 같음, 1 답변 근거가 다른 조문 있음, 2 실행 실패 (생성기 실패 포함: 부칙을 못 읽는 등).

### 2.2 monitor 연결

- `config/targets.yaml`에 `checks`(이름 + 명령 목록)를 둔다. 명령 안의 `{bin}`(지금 Python의 bin 폴더, `law-monitor`와 같은 venv), `{home}`, `{data}`, `{repos}`를 채운다. 대상 명령을 코드에 박지 않는다.

  ```yaml
  checks:
    - name: 챗봇 조문 데이터
      command: ['{bin}/lawbot-articles-check', '--repo', '{repos}/legalize-kr', '--report-dir', '{data}/articles']
  ```

- `law-monitor daily`: collect → **checks** → summarize. 점검은 collect가 일부 실패해도 돈다 (클론은 이미 있음). 시간 상한 15분.
- `law-monitor check`: 점검만 실행 (수동 확인용, 요약·상태 안 바뀜).
- 요약 md에 `## 점검` 절: 점검마다 결과 줄(마지막 출력 줄)을 그대로 적는다. 종료 코드 0·1은 정상(1은 "다름"), 그 밖과 시간 초과는 "점검 실패"로 적고 `daily` 종료 코드를 1로.
- 새 의존성 없음. `state.db`도 바꾸지 않는다.

## 3. 순서 (커밋)

1. 이 계획 (+ provenance 3절 제36751호는 앞 커밋, 문서만).
2. 생성기: 시행 전 조문 목록(조문·시행일) 반환, `source.ref` 이후 커밋 목록.
3. `lawbot-articles-check` + 테스트.
4. monitor `checks`·`check` 명령·요약 절 + 테스트, `targets.yaml`.
5. 문서 (`operations.md`, monitor README, `data-provenance.md` 6절), 확인 기록.

## 4. 테스트

- 생성기: 임시 git 저장소에서 시행 전 조문의 조문·시행일이 나옴, `source.ref` 이후 커밋 목록.
- 점검: 비교(추가·없어짐·본문·제목·삭제 여부 / 메타 열 개수), 보고서 내용, 종료 코드 0·1·2, 마지막 줄 형식.
- monitor: `checks` 설정 검증(빈 이름·빈 명령·알 수 없는 자리표시), 자리표시 채우기, `daily`가 점검을 부르고 요약 md에 결과 줄이 들어감(가짜 명령: 종료 코드 0·1·3·시간 초과), `check` 명령.
- 합격(`-m network`, `LEGALIZE_KR`): 기준일 2026-10-20 → 같음(종료 0), 2026-11-27 → 남녀고용평등법 제21700호 조문이 다름(종료 1).

## 5. 수동 확인 (맥 미니)

- 작업 폴더 클론으로 `lawbot-articles-check`를 오늘·2026-11-27·2026-12-08 기준으로 실행해 보고서를 본다.
- 개발 home에서 `law-monitor check` (요약·상태 안 바뀜).
- 운영 반영은 이 PR 뒤 따로 (운영 worktree 갱신, launchd는 그대로 `daily`라 바꿀 것 없음).
- PR 규칙대로 개발 웹앱 → 운영 모델 서버 질문 3개 (웹앱 코드 변경은 없음).

## 6. 이번에 하지 않는 것

- CSV 자동 교체·자동 재인덱싱·웹앱 재시작.
- 보고서 전송 (monitor 3단계).
- monitor 추적 대상에 챗봇 법률 추가 (이 점검이 대신함).
