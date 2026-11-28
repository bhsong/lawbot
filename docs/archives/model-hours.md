# 모델 서버 운영 시간 (06:00–21:30)

## 목표

맥 미니 메모리를 밤에는 다른 프로젝트에 쓴다. 임베딩·리랭크 모델 서버(`com.lawbot.model-server`)와 단독 생성 서버(`local.qwen27b`)는 06:00–21:30에만 띄운다. 웹앱(`com.lawbot.web`)과 캘린더 서버(`com.lawbot.calendar`)는 24시간 그대로 둔다. 운영 시간 밖의 질문에는 오류 대신 운영 시간 안내를 보여 준다.

## 요구사항

- R1. 모델 시간 스크립트 `deploy/model-hours/lawbot-model-hours`(POSIX sh)는 `start`, `stop`, `apply`, `status`를 제공한다.
  - `stop`은 두 서버를 `launchctl bootout gui/<uid>/<label>`로 해제한다. 두 서버 모두 KeepAlive라 프로세스 종료로는 내려가지 않는다. 이미 해제된 서버는 성공으로 본다.
  - `start`는 `~/Library/LaunchAgents/<label>.plist`를 `launchctl bootstrap`한다. 이미 등록된 서버는 그대로 둔다. 그다음 두 서버가 준비될 때까지 정해진 마감 시각(기본 06:25, 인자로 변경)까지 기다린다. 준비 확인은 운영 트리의 `uv run python -m lawbot.check_deploy`를 쓴다. 마감까지 실패하면 0이 아닌 코드로 끝나고 로그에 실패를 남긴다.
  - `apply`는 지금 시각이 운영 시간 안이면 `start`, 밖이면 `stop`을 실행한다. 운영 시간 기본값은 06:00–21:30이고 인자로 바꿀 수 있다.
  - `status`는 두 라벨의 등록·실행 상태만 출력한다.
  - 라벨과 plist 경로, 운영 트리 경로는 스크립트 상단 변수(인자로 덮어쓸 수 있음)로 둔다. 포트·토큰·`.env` 내용은 읽어서 출력하거나 로그에 쓰지 않는다. `check_deploy` 출력도 로그에는 성공·실패 줄만 남긴다.
- R2. launchd 템플릿 `deploy/launchd/com.lawbot.model-hours.plist.example`: `StartCalendarInterval` 06:00과 21:30, `RunAtLoad` true로 `lawbot-model-hours apply`를 실행한다. 로그는 `__LOG_DIR__/model-hours.log`. 로그인(재부팅) 직후에도 `apply`가 돌기 때문에 밤에 재부팅해도 두 서버는 내려간 상태로 돌아간다.
- R3. 웹앱 운영 시간 안내: 설정 `LAWBOT_MODEL_HOURS`(예: `06:00-21:30`, 기본 없음 = 항상 운영).
  - 설정돼 있고 지금(로컬 시각)이 시간 밖이면 질문 처리 경로는 검색·리랭크·계산 분류·생성 서버를 호출하지 않는다. 화면에는 "답변 서비스 운영 시간은 06:00–21:30입니다. 운영 시간에 다시 질문해 주세요."(설정값으로 시각 표시)를 보여 준다.
  - 요청 로그에는 새 오류 유형 `off_hours`로 남긴다. 피드백 버튼은 붙이지 않는다(기존 오류 답변과 같음).
  - 형식이 틀린 값은 기존 설정 검사처럼 시작 전에 `SettingsError`로 알린다. 자정을 넘는 구간(`21:30-06:00`)도 해석한다.
  - 운영 시간 안에서 서버가 아직 준비 중이면 기존 `model_server` 안내를 그대로 쓴다.
- R4. 문서: `docs/operations.md`의 launchd 절에 설치·확인·해제 명령, 운영 시간 중 배포·재시작 주의, 밤에 수동으로 켜는 방법(`lawbot-model-hours start`)과 다시 내리는 방법을 적는다. `.env.example`에 `LAWBOT_MODEL_HOURS`를 주석과 함께 추가한다.
- R5. 기존 동작 유지: `LAWBOT_MODEL_HOURS`가 없으면 웹앱 동작은 그대로다. 06:30 monitor(조문 갱신·요약)는 06:00 시작 뒤 실행되므로 바꾸지 않는다.

## 제한 (이번 범위 밖)

- 웹앱은 시작할 때 모델 서버와 생성 서버가 준비될 때까지 기다린다(`LAWBOT_MODEL_SERVER_WAIT`). 밤에 웹앱이 재시작되면(재부팅, 비정상 종료) launchd가 재시도하다가 06:00에 서버가 올라온 뒤에야 뜬다. 밤에도 웹앱 화면을 보장하려면 모델 없이 시작하는 구조 변경이 필요하며 별도 PR로 다룬다.
- 운영 반영·웹앱 재시작·모델 서버 재시작은 운영 시간(06:00–21:30)에 한다. 06:20–06:50 자동 갱신 시간대는 기존처럼 피한다.
- 개발 웹앱의 실제 모델 검증(e2e_live·models·평가)은 운영 시간에만 할 수 있다.

## 테스트

- 단위: `LAWBOT_MODEL_HOURS` 해석(정상, 자정 넘김, 형식 오류), 시간 판정 경계(06:00 포함, 21:30 제외), 시간 밖 질문이 모델 호출 0회로 안내를 반환하고 요청 로그에 `off_hours`가 남는지, 미설정 시 기존 경로.
- 스크립트: `launchctl`·`uv`를 PATH의 가짜 실행 파일로 바꿔 `apply`의 시간 분기, `stop`의 이미 해제 처리, `start`의 마감 실패 코드를 pytest로 검증한다(시각은 인자나 환경 변수로 주입). 실제 launchd는 건드리지 않는다.
- 대역 E2E: 기존 웹앱 e2e_stub에 시간 밖 설정으로 질문하면 안내가 표시되고 가짜 생성 서버 호출이 없는 경우를 추가한다.
- 전체 기본 테스트, 린트, pyright.

## 운영 반영 (승인 후)

1. 병합 커밋을 운영 트리에 반영하고 운영 `.env`에 `LAWBOT_MODEL_HOURS=06:00-21:30`을 추가한다. 웹앱을 재시작한다(운영 시간 안).
2. plist 템플릿을 채워 `~/Library/LaunchAgents/com.lawbot.model-hours.plist`로 설치하고 bootstrap한다. `RunAtLoad`의 `apply`가 시간 안이므로 아무것도 내리지 않는지 `status`와 로그로 확인한다.
3. 그날 21:30 로그와 `status`로 두 서버 해제, 휴대폰 질문 안내를 확인한다. 다음 날 06:00 시작 성공과 06:30 daily 결과를 06:41에 점검한다.
4. 버전: 기능 추가이므로 Y 자리(v6.8.0).

## 구현 기록

- [운영 절차](../operations.md#launchd-모델-운영-시간-06002130)
- [기본 테스트·대역 검증 결과](../evals/2026-11-28-model-hours.md)
