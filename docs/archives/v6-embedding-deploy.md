# v6 임베딩 운영 반영 (v6.0.0)

v5 프로필의 검색 임베딩을 `lawbot-emb-v5b`에서 `lawbot-emb-v6`로 바꾸고 v5b를 모델 서버에서 내린다. 근거는 [4단계 A 평가](../evals/2026-11-14-v6-stage4a-eval.md)다. 세금·보험 법률 리랭킹 R@3 98.04% → 98.73%, 하위법령 첨부율은 노동 89.19%로 같고 세금 94.60% → 95.27%, 노동 법률 리랭킹 R@3은 95.05% → 93.87%다. 사용자가 노동 하락을 받아들이고 반영을 결정했다(2026-11-14).

## 범위

| 변경 | 내용 |
| --- | --- |
| 코드 기본값 | `deploy._profile`의 v5 임베딩 기본값 `lawbot-emb-v6`. 인코딩 최대 토큰(법률 4,352 · 하위법령 5,120)은 4단계 A 평가와 같게 그대로 둔다 |
| 설정 예시 | `.env.example`의 `LAWBOT_SERVE_EMBEDDING=lawbot-emb-v6=models/v6/emb_v6_fp16`, `LAWBOT_EMB_MODEL=lawbot-emb-v6`. `.env.dev.example`도 같은 id |
| 문서 | `docs/operations.md` 모델 폴더와 기본값, 루트 README의 현재 검색 지표 |
| 테스트 | v5 프로필의 기본 임베딩 id 기대값 |

정확 일치 캐시는 코퍼스 릴리스·system 프롬프트·인용 조문으로 키를 만들고 임베딩 모델을 쓰지 않는다. 승인된 답변은 임베딩을 바꿔도 그대로 쓴다.

## 운영 반영 (병합 승인 뒤, 별도 진행)

1. 운영 트리를 병합 커밋으로 옮긴다. 운영 `.env`의 `LAWBOT_SERVE_EMBEDDING`을 `lawbot-emb-v6`만 남기고 `LAWBOT_EMB_MODEL=lawbot-emb-v6`으로 바꾼다. v5b 가중치 폴더는 지우지 않는다(되돌리기용).
2. `lawbot_model_server.check` 뒤 모델 서버를 재시작하고, 웹앱을 재시작한다. 06:20–06:50은 피한다. 웹앱은 시작할 때 v6 조문 인덱스를 새로 만든다(4단계 A에서 약 4.5분).
3. `/v1/models`에 `lawbot-emb-v6`·리랭커·27B만 있고 revision이 4단계 A와 같은지, 운영 웹과 `check_deploy`가 정상인지 확인한다.
4. 태그 `v6.0.0`(X: 임베딩 모델 교체). 기록은 `docs/evals/YYYY-MM-DD-deploy-v6.0.0.md`. 다음 06:30 자동 갱신 뒤 읽기 전용 점검을 예약한다.

되돌리기: 운영 `.env`의 두 값을 v5b로 돌리고 이전 커밋 `a7a47db`로 옮긴 뒤 두 서비스를 재시작한다.

## 완료 기준

- 기본 테스트·린트가 통과한다.
- 운영 모델 서버에 `lawbot-emb-v5b`가 없고, 운영 웹앱이 `lawbot-emb-v6`로 답한다.
