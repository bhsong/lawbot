# 모델 서버 정지 판정 대기 추가

## 문제

2026-11-28 21:00 첫 자동 정지에서 `model-hours.log`에 `apply: Model server bootout failed.`가 남고 `com.lawbot.model-hours`가 종료 코드 1로 끝났다. 21:04 점검에서는 두 서비스 모두 등록 해제됐고 모델 프로세스도 없었다. 21:30 실행은 이미 원하는 상태라 아무 일 없이 종료 코드 0으로 끝났다.

## 원인(추정)

`deploy/model-hours/lawbot-model-hours`의 `unregister`는 `launchctl bootout` 직후 `launchctl print`로 한 번만 확인한다. 서비스가 SIGTERM을 받고 끝나는 동안(두 plist에 `ExitTimeOut`이 없으므로 launchd 기본값 20초 뒤 SIGKILL)에는 아직 등록된 것으로 보일 수 있다. 큰 모델을 내리는 생성 서버가 이렇게 늦게 끝났을 가능성이 높다. launchd 시스템 로그에는 이 시각의 기록이 없었고, 실패한 서비스 이름도 로그에 남지 않아 어느 쪽이었는지는 확인하지 못했다.

## 변경

- `unregister`: bootout 뒤 등록 해제를 1초 간격으로 다시 확인한다. 최대 대기 시간은 기본 30초(launchd 기본 종료 제한 20초보다 길게)이고, `--stop-wait SECONDS` 옵션으로 바꿀 수 있다(테스트용; 운영 plist는 기본값 사용). 시계 대신 횟수로 제한해 테스트의 고정 시계에서도 끝난다.
- 두 서비스는 지금처럼 각각 bootout한다. 두 서비스를 먼저 모두 bootout한 뒤 함께 기다리도록 해서, 최대 대기가 서비스 수만큼 늘어나지 않게 한다.
- 실패 메시지에 해제되지 않은 서비스 이름을 붙인다: `Model server bootout failed: <label>[, <label>].`
- `.env`, 시간 판정(`lawbot.model_hours`), 웹앱, launchd plist는 바꾸지 않는다.

## 완료 기준

- 가짜 `launchctl`에서 bootout 뒤 몇 번의 확인 동안 등록 상태로 남는 서비스를 정지하면 성공(`Model servers unregistered.`)으로 끝난다.
- 계속 해제되지 않는 서비스는 대기 상한 뒤 실패하고, 메시지에 그 서비스 이름만 있다.
- 기존 `test_model_hours_script.py` 항목과 전체 기본 테스트, 린트, pyright가 통과한다.
- 운영 반영 뒤 다음 21:00 정지에서 `Model servers unregistered.`와 종료 코드 0을 확인한다.

## 운영 반영

셸 스크립트만 바뀌므로 서비스 재시작은 필요 없다. launchd가 매 실행마다 운영 트리의 스크립트를 읽는다. 반영은 :00·:30 실행과 06:20–06:50을 피해서 한다. 버전은 Z 자리(v6.8.2)다.
