# PR #79 실제 모델 E2E 동시 생성 상한 기록 검증

2026-11-07. 기존 E2E 구성 기록에는 실제 서버의 동시 생성 상한이 빠져 있었다. 인증된 `/health?runtime=1`이 현재 스케줄러의 `max_concurrent`를 반환한다. E2E가 이 값을 시작·종료 구성에 넣는다. 시작에 값이 없으면 질문을 보내기 전에 실패하고, 종료 값이 달라지거나 빠지면 결과가 무효다. 일반 `/health` 응답은 기존 형식을 유지한다. [계획](../archives/v5-live-concurrency.md) · [리뷰](https://github.com/bhsong/lawbot/pull/79#discussion_r4499148717)

- Red에서 신규 검증 5개가 상한 미제공·미기록으로 실패했다. 수정 후 `python -m pytest -q services/model-server/tests/test_ms_api.py services/chatbot/tests/test_e2e_live_preflight.py`의 54개가 통과했다. 대역 서버에서 상한 3, 인증 거부, 구버전 응답의 사전 실패, 종료 시 값 변경·누락·잘못된 타입을 확인했다.
- `python -m pytest -q`의 전체 기본 테스트 923개가 통과했다(기본 선택에서 제외 13개). Ruff, Pyright 두 프로젝트, Python 문법 검사도 통과했다.
- 기존 v5 실제 모델 E2E·품질 평가 기록은 이전 실행의 결과다. 그 실행의 동시 생성 상한은 운영 설정 파일에서 확인했으며, 이후 운영 준비 로그에서도 1을 확인했다. 이번 수정 검증에서는 실제 모델 E2E·모델 스모크·품질 평가를 다시 실행하지 않았다. 운영 서버는 별도 반영 단계에서 갱신한다. [기존 실행](2026-11-07-v5-transition.md)
