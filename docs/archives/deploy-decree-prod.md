# 작업 계획: 하위법령 첨부를 운영에 반영 (병합 전 확인용)

- 작성: 2026-11-01
- 요청: 병합 전에 운영 웹앱으로 하위법령 첨부를 직접 확인 (사용자)
- 배포할 코드: `chore/cleanup` 끝 커밋 6eda75b (= PR #26 + #27. 두 PR을 모두 병합한 뒤 main과 같은 코드). #27은 동작을 바꾸지 않음
- 지금 운영: `~/Work/lawbot-prod` worktree, 303f764 (detached), API 1, 법률 415조문
- 원칙: 포트·토큰·비밀번호는 문서·커밋·출력에 남기지 않는다. `.env`는 키 이름만 확인한다

## 1. 왜 모델 서버와 웹앱을 같이 바꾸나
#26에서 모델 서버 API가 1 → 2로 바뀌었고, 웹앱은 시작할 때 API 버전·하위법령 설정(켜짐·τ·k·후보 수·max_seq)이 서버와 같은지 확인한다. 하나만 바꾸면 웹앱이 시작을 거부한다. 개발 웹앱(원격 모드)도 같은 서버를 쓰므로 개발 `.env`에도 첨부 설정을 넣어야 한다.

## 2. 순서
| # | 할 일 | 누가 | 비고 |
| --- | --- | --- | --- |
| 1 | 운영 모델 서버·웹앱 내리기 (`launchctl bootout` 2개) | 사용자 (`!`) | 코드를 바꾸는 동안 이전 프로세스가 새 파일을 읽지 않게. 이 동안 운영 웹앱 중단 |
| 2 | 운영 `.env` 백업 (`~/Work/lawbot-prod.env.bak-20261101`, 권한 600, git 밖) | Claude | 되돌리기용 |
| 3 | `git -C ~/Work/lawbot-prod checkout --detach 6eda75b` → `uv sync --frozen` | Claude | uv.lock은 바뀌지 않음 (303f764와 차이 없음) |
| 4 | 운영 `.env`에 첨부 설정 추가: `LAWBOT_DECREE_ENABLED=1`, `LAWBOT_V4_DECREE_ARTICLES=data/v4/law_articles_v4_decree.csv`, `LAWBOT_DECREE_TAU=0.9`, `LAWBOT_DECREE_K=2`, `LAWBOT_DECREE_MODELS=pro` | Claude | `.env.example`과 같은 값 |
| 5 | 개발 `.env`에 `LAWBOT_DECREE_ENABLED=1`·TAU·K·MODELS 추가 (`.env.dev.example`과 같음) | Claude | 원격 모드 개발 웹앱이 새 서버와 맞도록 |
| 6 | 운영 worktree에서 `check_deploy` (모델 서버 접속 전 단계까지) | Claude | 모델을 올리지 않음. 포트가 찍히는 줄은 가려서 출력 |
| 7 | 모델 서버 → 웹앱 순서로 다시 등록 (`launchctl bootstrap` 2개) | 사용자 (`!`) | |
| 8 | 서버 로그 `ready`·`/info`의 `api_version 2`·`decree` 확인, 웹앱 로그 오류 없음 확인 | Claude | 로그는 포트 줄 제외하고 읽기 |
| 9 | 사용자가 운영 웹앱에서 질문 3개 확인 | 사용자 | 아래 3절 |
| 10 | 기록: `docs/evals/2026-11-01-deploy-decree-prod.md`, 이 계획서는 `docs/archives/`로 | Claude | |

실제 모델 생성(평가·스모크)은 돌리지 않는다. 서버가 모델을 올리는 것은 평소 운영과 같다.

## 3. 사용자 확인 (운영 웹앱, Pro)
| 질문 | 기대 |
| --- | --- |
| 체불 융자 이자 몇프로 이하에요? | 법률 3개 뒤 〔시행규칙〕 임금채권보장법 시행규칙 제8조의10 첨부·인용 (펼침) |
| 평균임금 계산할 때 수습 기간도 들어가나요? | 〔시행령〕 근로기준법 시행령 제2조 첨부·인용, `근거:` 줄에 시행령, ⚠ 없음 |
| 해고 예고 없이 갑자기 해고되면 어떻게 하나요? | 근로기준법 제26조 인용 |
| (화면) | 안내 문구·"법률 3개 + 시행령·시행규칙 최대 2개" 제목·데이터 안내(종류별 수)·측정 줄 `시행령·시행규칙 x.xx초`. Fast는 첨부 없음 |

## 4. 되돌리기
1. 사용자: 두 서비스 `bootout`
2. Claude: `git -C ~/Work/lawbot-prod checkout --detach 303f764`, `~/Work/lawbot-prod.env.bak-20261101` → `.env` 복원, 개발 `.env`에서 추가한 4줄 삭제
3. 사용자: 두 서비스 `bootstrap`

## 5. 병합 뒤
PR #26·#27을 병합하면 main이 6eda75b와 같은 내용이 된다. 운영 worktree는 그때 main 커밋(또는 태그)으로 다시 옮긴다 (코드 차이 없음, 재시작만).

**정정 (2026-11-01, 병합 후)**: 위 가정은 틀렸다. main에는 #26 브랜치에 없던 #24(`citation.refused` 단서 문장 처리)가 먼저 들어가 있어, main(3e1f427)과 6eda75b는 `src/lawbot/citation.py`가 다르다. 운영 웹앱을 재시작해야 반영된다. 결과는 `docs/evals/2026-11-01-deploy-decree-prod.md` 7·8행.
