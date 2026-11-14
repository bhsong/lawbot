# 챗봇 답변 공개 전 검증

2026-11-14 KST. 기준 `main` `9be6850`. [구현 계획](../archives/hold-unverified-chat-answers.md)

| 검증 | 결과 |
| --- | --- |
| Red | 잘린 답변과 인용 미확인 7건의 본문이 그대로 남고, 계산 대조 3건에서 확인 전에 본문을 보냈다. 새 테스트 10건이 예상한 동작 차이로 실패했다. |
| 단위·계약·통합·대역 E2E | `.venv/bin/python -m pytest --tb=short -q`에서 1,995건 통과, 선택 시험 13건 제외, 160.32초. 계산 안내 대역은 실제로 받은 조문을 인용하도록 수정했다. 프런트엔드 빌드를 마친 뒤 전체를 다시 실행했다. |
| 브라우저 | 조문 근거가 틀린 스트리밍 초안은 안내로 교체됐다. 보류 상태에서 복사와 좋아요가 표시되지 않았다. 390px·1280px에서 계산 결과는 유지됐고 수치 불일치 설명은 전송하지 않았다. 정상 답변, 계산 입력 안내, 중지와 승인 캐시 경로도 포함했다. |
| 프런트엔드 | 챗봇 55건과 캘린더 31건 통과. 두 웹앱의 `npm run typecheck`, `npm test`, `npm run build` 통과. 챗봇 빌드에 Vite의 기존 설정 로더 경고가 표시됐다. |
| 정적 검사 | `ruff check`, `ruff format --check`, 기본·서비스별 `pyright`, Python `compileall`, 캘린더 스키마 검사, 루트·ERP·Source Hub `uv lock --offline --check`, `git diff --check` 통과. |
| 실제 모델 E2E·모델 스모크·품질 평가 | 각각 실행 안 함. 실제 추론 요청 0건. |

조문 확인은 제공된 조문과 인용의 일치 여부를 검사한다. 계산 설명에는 기존 결론 수치 비교를 적용한다. 검사 결과는 `answer_verdict`로 기록한다.

## 병합과 운영 확인

- [PR #144](https://github.com/bhsong/lawbot/pull/144)의 head `ce43c1a`에서 코드·보안 리뷰와 필수 CI가 완료됐다. 미해결 스레드는 0개였다. `main` 병합 커밋 `af0b556`을 운영에 반영했다. [PR CI](https://github.com/bhsong/lawbot/actions/runs/43541573221)
- 운영 챗봇 `npm ci`와 `npm run build`가 통과했다. 웹앱을 재시작하고 정적 번들, 비인증 사용자 정보 401, 로그인·사용자 정보·설정·로그아웃 200, 로그아웃 이후 401을 확인했다. 모델 서버와 캘린더의 프로세스 ID는 전후 같았다.
- `lawbot.check_deploy`는 OK 24, PASS 1, INFO 8, WARN·FAIL 0이었다. 운영에서 사용자 질문을 보내는 실제 모델 E2E는 실행 안 함이다.
- 운영 태그는 [v5.9.1](https://github.com/bhsong/lawbot/tree/v5.9.1)이다. 복구가 필요하면 직전 [v5.9.0](https://github.com/bhsong/lawbot/tree/v5.9.0) 커밋에서 챗봇 번들을 다시 만들고 운영 웹앱을 재시작한다.
