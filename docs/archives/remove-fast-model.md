# Fast 모델 제거와 단일 답변 모델

작성 2026-11-19 KST. 구현은 omp, 계획·검토·병합·운영 반영은 Claude.

## 목표

더 이상 쓰지 않는 Lawbot Fast(4B, v4 4항목 학습)를 지운다. 답변 모델이 Pro 하나만 남으므로 모델을 고르기 위해서만 있던 설정과 화면도 함께 지운다. 이전 PR(#181)에서 미룬 피드백 기록의 `fmt` 열도 지운다. 기존 동작을 위한 호환 코드는 두지 않는다.

## 지울 것

- 모델: `deploy.py`의 Fast `ModelSpec`, `LAWBOT_FAST_MODEL`, v4 프로필의 Fast. v4·v5 프로필 모두 모델은 Pro 하나다.
- 4항목 system: `data/v4/system_v4.txt`, `paths.V4_SYSTEM`, `LAWBOT_V4_SYSTEM`. v4 프로필의 기본 system은 Pro와 같은 `data/v4/system_v4_concise.txt`(`LAWBOT_PRO_SYSTEM`)다.
- 모델 선택: `LAWBOT_MODELS`와 비활성 모델(`Profile.disabled`, `view.DISABLED_MODEL`, `check_deploy`의 "사용 안 함" 안내), `LAWBOT_DECREE_MODELS`(하위법령 첨부는 `LAWBOT_DECREE_ENABLED`로만 켜고 끈다).
- API·화면: `/api/chat`의 `model` 입력, `/api/config`의 `models`·`disabled`·`default_model`, 화면의 "답변 모델" 선택. 측정 줄 등 화면에 나오는 모델 이름(Lawbot Pro)은 유지해도 된다.
- 평가 도구: `eval_v4 --model`과 기본 tag의 모델 부분이 필요 없으면 정리한다. `eval_reasoning`의 모델 선택 확인을 단일 모델에 맞춘다.
- 피드백 `fmt`: `feedback.turn_record`의 `fmt`, `feedback_export`의 필수 열·출력 열, `feedback_review`의 열, `export_dataset_snapshot`의 `COMPLETION` 열과 `missing_fmt` 경고. 기존 기록에 남은 `fmt` 키는 읽지 않을 뿐 데이터를 바꾸지 않는다(DB `feedbacks` 표에는 열이 없고 JSON 기록 안에만 있다).
- 문서·설정 예시: `.env.example`·`.env.dev.example`의 `LAWBOT_MODELS`, `docs/operations.md`의 Fast·모델 선택·`.env.small`(소형 맥 Fast 설정) 설명, `deploy.py`·`view.py` 등의 Fast 주석.

## 유지할 것

- 내부 모델 key `pro`와 요청 로그·피드백 기록의 `model` 값(데이터 형식 유지).
- 간결형 표시(`answer_format.concise`), v4 프로필 자체와 v4 조문 데이터.
- 운영 `.env`의 `LAWBOT_MODELS=pro` 줄은 코드가 읽지 않게 되며, 운영 반영 때 Claude가 지운다.

## 테스트 (TDD)

- 테스트들이 기본 가짜 모델로 쓰는 `fast`를 `pro`로 바꾸고, Fast 전용 기대값(4항목 system, 하위법령 미첨부 Fast 등)은 단일 모델 동작으로 바꾸거나 지운다.
- 새로 검증: 프로필의 모델이 Pro 하나, v4 프로필 기본 system이 간결형, `/api/chat`이 `model` 없이 처리, `/api/config`에 모델 목록이 없음, 화면에 "답변 모델" 선택이 없음, 피드백 기록·내보내기·스냅샷에 `fmt`가 없음, `LAWBOT_MODELS`를 설정해도 영향이 없음(읽지 않음).
- 대역 E2E: 로그인 → 질문 → 스트리밍 답변·조문 카드·측정 줄, 화면에 모델·형식 선택 없음.
- 대화 계약 픽스처(`chat_wire_contract.json`)는 모델 이름·요청 로그처럼 의도한 차이만 바뀌는지 diff로 확인한다.

## 완료 기준

기본 테스트 전체(단위·계약·통합·대역 E2E)와 Ruff·Pyright·두 화면 typecheck·test·build 통과. Pro의 생성 system과 검색 경로는 바뀌지 않으므로 실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않는다. 운영 반영 때는 웹앱 화면 빌드, 운영 `.env`의 `LAWBOT_MODELS` 줄 삭제, 웹앱 재시작이 필요하다.

[구현 검증 기록](../evals/2026-11-19-remove-fast-model.md)
