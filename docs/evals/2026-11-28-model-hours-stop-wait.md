# 모델 서버 등록 해제 대기 검증 (2026-11-28)

[운영 절차](../operations.md#launchd-모델-운영-시간)

- Red: `uv run pytest -q services/chatbot/tests/test_model_hours_script.py -k waits_for_unregistration`에서 수동 `stop`과 야간 `apply`가 등록 해제 지연을 즉시 실패로 처리했다(2건 실패).
- Green: 고정된 시계와 가짜 `launchctl`에서 두 서비스에 먼저 `bootout`을 보낸 뒤 등록이 해제될 때까지 확인해 두 경로 모두 성공했다. `--stop-wait 1`에서는 남은 한 라벨만, `--stop-wait 0`에서는 즉시 확인에서 남은 두 라벨을 표시했다. 잘못된 대기 값은 `launchctl` 호출 전에 거부했다.

| 명령 | 결과 |
| --- | --- |
| `uv run pytest -q --tb=no --show-capture=no` | 3,054개 통과, 10개 기본 실행 대상 제외 (249.98초). 단위·계약·통합·대역 E2E 포함 |
| `uv run ruff check --output-format=github` | 통과 |
| `uv run ruff format --check` | 639개 파일 통과 |
| `PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright` | 오류 0, 경고 0, 정보 0 |

운영 반영과 실제 launchd 정지 확인은 범위 밖이다. 실제 모델 E2E, 모델 스모크, 품질 평가: 실행 안 함.

## 리뷰 반영

- Red: `test_model_hours_script.py -k 'stop or apply_matching_state or apply_at_night'`에서 4건 실패, 13건 통과. 기본 대기와 1회 대기가 마지막 재확인 전에 끝났고, 공유 예산 검사에서는 해제될 서비스까지 실패로 보고했다.
- Green: 0·1·30회 경계, 두 서비스의 공통 예산, 셸 정수 범위 밖 인자의 사전 거부를 확인했다. 저장소 파일은 그대로 두고 `stop`의 `sleep 1`만 뺀 변형을 실행하자 지연 정지 테스트 4건이 모두 실패했다.

| 명령 | 결과 |
| --- | --- |
| `LAWBOT_ENV_FILE= uv run --locked --no-env-file pytest -q --tb=no --show-capture=no -o console_output_style=count -r N` | 3,058개 통과, 10개 기본 실행 대상 제외 (266.93초). 단위·계약·통합·대역 E2E 포함 |
| `uv run --locked --no-env-file ruff check --output-format=github` | 통과 |
| `uv run --locked --no-env-file ruff format --check` | 640개 파일 포맷 일치 |
| `PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run --locked --no-env-file pyright` | 오류 0, 경고 0, 정보 0 |

실제 모델 E2E, 모델 스모크, 품질 평가: 실행 안 함.
