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
