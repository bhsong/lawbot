# 계획: 검토된 비슷한 질문 추천과 시맨틱 캐시·원자 질문 RAG 제거

- 작성: 2026-11-15 KST. 기준 `main` `2d5e777`, 운영 `e165933`.
- 이전 계획 [원자 시맨틱 캐시](../archives/atomic-semantic-cache.md)는 **취소**했다. PR 1·2(#151, #152)는 병합됐고 PR 3(#154)은 평가까지 했으며 PR 4·5는 시작하지 않았다.
- [ZIP 없는 정확 일치 캐시 런타임](../archives/exact-cache-zip-free-runtime.md)(#153)은 **그대로 유지**한다. 운영 반영 단계는 이 계획의 §5로 옮겼다.

## 1. 결정 (사용자 2026-11-15)

1. **시맨틱 캐시와 원자 질문 RAG를 코드에서 모두 제거한다.** 근거는 [PR 3 val 기록](../evals/2026-11-15-semantic-union-rag.md)이다.
   - 원자 질문 RAG: 노동 val R@3 base 93.87%, atomic 93.78%. union@20의 95.05%는 후보 수 효과다. 같은 후보 수의 base@20은 94.86%였고, union@20 대 base@20은 7:5, McNemar p=0.77로 유의하지 않았다.
   - 시맨틱 캐시: 1,008개 설정 중 `not_reusable` 오적중 0건을 지키면서 1건이라도 재사용하는 설정이 없었다. miss마다 시맨틱 검색이 p50 3.7초를 더했다.
2. **정확 일치 캐시는 유지하고, "검토된 비슷한 질문" 추천으로 활용한다.** 사용자가 추천을 누르면 검토 질문 원문이 그대로 전송되어 정확 일치 캐시에 적중한다. 같은 질문인지는 사용자가 판단하므로, 시맨틱 자동 재사용의 오적중 문제를 피한다.
3. **v5 예시 질문 하나는 정확 일치 캐시 시연용으로 유지한다.** 현재 `view.V5_EXAMPLES[0]`이 P07 seed 원문과 같다. 예시가 캐시에 적중하는지 테스트로 고정한다.
4. **운영의 모든 적재·확인·사용은 DB로 수행한다.** 2026-11-16 사용자 결정에 따라 통과 ZIP은 개발에서만 개발 캐시 DB로 변환한다. 운영 명령과 런타임은 ZIP·JSONL을 열지 않는다.

## 2. PR 나누기

| PR | 내용 | 실제 모델 호출 |
| --- | --- | --- |
| A | 시맨틱 캐시·원자 질문 RAG 제거, 평가 기록 보존 | 0회 |
| B | 검토된 비슷한 질문 추천 | 개발 인덱스 생성과 τ 선택 val(승인 후) |

A를 먼저 병합하고 B는 A 위에서 시작한다. 운영 반영은 B까지 끝난 뒤 한 번에 한다(§5).

## 3. PR A: 제거

[PR A 구현 및 개발 검증 계획](../archives/remove-semantic-cache.md) · [DB 전송과 리뷰 반영](../archives/reviewed-cache-db-transfer.md)

### 제거 대상 (main 기준)

- 모듈: `semantic_gate`, `semantic_handoff`, `semantic_import_store`, `semantic_index_store`, `semantic_index_tools`, `semantic_pointer`, `semantic_policy`, `semantic_runtime`, `semantic_settings`, `semantic_store`, `semantic_tools`, `semantic_val`.
- 캐시 DB 마이그레이션 `0003_atomic_semantic_cache.sql`, `cache_store.SCHEMA=3`과 `MINIMUM_EXACT_SCHEMA`, `migrate.check_schema(minimum_version=)`. 적용된 0001·0002의 바이트는 유지한다. 개발 사본(스키마 3)은 더 쓰지 않는다.
- 런타임 연결: `settings.semantic_shadow`, `service.load_app`의 shadow 로드와 calibration 검사, `web.chat_events`의 `defer_shadow`와 `ChatResponse`의 지연 기록, `Decision.allows_semantic_shadow`.
- 테스트 `test_semantic_cache.py`, `test_semantic_seed.py`, `test_semantic_index_tools.py`와 다른 테스트의 shadow 사례.

### 남기는 것

- #153의 DB 전용 보호 목록과 `protection_unavailable` 가드, P07 적재·검수 도구(`inspect`·`seed`·`approve`·`replace`).
- 시맨틱과 무관하게 쓰이는 공용 코드. `cache_store.verify_answer`(정확 일치 조회가 사용), `lawbot_common.model_revision`, `api_client.REQUEST_ID`, `tokenizers` 의존성은 각각 사용처를 확인해 남은 사용처가 있으면 둔다. 사용처가 없어진 코드만 지운다.
- `data/v5/semantic_val_ids.jsonl`(질문↔답변 라벨)은 PR B의 τ 선택에 다시 쓴다. PR B에서 이름을 `suggestion_val_ids.jsonl`로 바꾼다.
- 평가 기록: PR 1·2 기록은 그대로 둔다. #154에만 있던 PR 3 기록과 README 한 줄은 이 계획 PR에서 main으로 옮겼다.
- 웹앱의 기본 응답·SSE·요청 로그는 시맨틱이 꺼진 지금과 바이트 단위로 같아야 한다.

### 정리

- 이전 계획서 두 개(`atomic-semantic-cache.md`, `atomic-semantic-cache-spec-v2.md`)와 `exact-cache-zip-free-runtime.md`는 이 계획 PR에서 `docs/archives/`로 옮겼고, 앞의 두 문서에는 취소를 표시했다.
- `docs/operations.md`, `.env.example`, `docs/architecture.md`에서 시맨틱 설정이 있으면 지운다.
- #154는 평가 기록을 옮긴 뒤 병합하지 않고 닫는다(사용자 확인 후).

### 완료 기준

- `grep -ri semantic services/chatbot/src services/chatbot/tests`의 유일한 소스 일치는 원래 `0001_exact_cache.sql`에 있는 항상 0인 `semantic_enabled` 열이다. 추천 기능의 새 이름에는 `semantic`을 쓰지 않는다.
- 기본 pytest 전체, ruff, pyright, 프론트 타입 검사·테스트·빌드가 통과한다. 정확 일치 캐시·이월·검수 화면·`set-mode` 테스트가 그대로 통과한다.

## 4. PR B: 검토된 비슷한 질문 추천

### 동작

1. 사용자가 질문하면 지금처럼 RAG로 답한다.
2. 답변이 끝나면 같은 질문의 v6 질문 벡터로 검토 질문 벡터를 검색한다. 추가 임베딩 호출은 0회다. 검색에 이미 쓴 질문 벡터를 재사용한다.
3. 코사인 유사도 τ 이상인 것 중 답변이 다른 상위 2개를 "검토된 비슷한 질문"으로 보여준다. 한 답변에 질문이 여러 개면 가장 가까운 질문 하나만 보여준다.
4. 사용자가 누르면 그 질문 원문이 새 질문으로 전송되고 정확 일치 캐시가 검토 답변을 바로 보여준다.

### 추천 후보와 차단

- 후보: 현재 namespace에서 질문과 답변이 모두 승인·유효하고, 정확 일치 조회가 `hit`를 내는 질문만. 누르면 반드시 적중해야 한다.
- 정확 일치 캐시 모드가 `on`일 때만 보여준다. `off`·`shadow`에서는 눌러도 적중하지 않으므로 숨긴다.
- 계산 질문, 캐시 적중 응답, 오류·거절 응답, 방금 물은 질문과 정규화 결과가 같은 후보에는 보여주지 않는다.
- 06:30 조문 갱신으로 승인 상태가 바뀌면 다음 조회부터 반영한다. 캐시 control의 generation이 바뀌면 후보를 다시 읽는다.

### 저장

- 캐시 DB에 새 마이그레이션 `0003_question_vectors.sql`을 추가한다. 테이블 `cache_question_vectors(query_id, embedding_model, model_revision, normalized_sha256, dimension, vector)`.
- 벡터는 승인 질문의 정규화 원문을 검색과 같은 질의 인코더로 임베딩한다. 런타임은 모델 서버가 알려 주는 임베딩 revision과 `normalized_sha256`이 모두 일치하는 벡터만 쓴다. 일치하지 않는 질문은 추천하지 않는다(fail closed). 시작 로그에 후보 수와 벡터가 없는 수를 남긴다.
- 벡터 생성은 `cache_admin` 하위 명령으로 한다. 이미 맞는 벡터는 건너뛴다. 임베딩 요청 수를 먼저 보여 주고 승인 후 실행한다. 런타임은 임베딩을 새로 만들지 않는다.

### 화면과 로그

- 답변 아래 "검토된 비슷한 질문" 칩 1~2개. 칩을 누르면 질문 입력과 전송이 일어난다. 역할·라벨·테스트 id로 찾을 수 있게 만든다.
- SSE는 `done` 전에 `suggestions` 이벤트를 한 번 보낸다. 추천이 없으면 보내지 않는다.
- 요청 로그에는 추천 수, 최고 점수, 추천을 눌러 온 요청인지만 남긴다. 질문 원문은 남기지 않는다.

### τ 선택 (val, 실제 모델, 승인 후)

- val: PR 2에서 만든 질문↔답변 라벨(`suggestion_val_ids.jsonl`, `reusable`·`not_reusable`·`uncertain`)의 질의를 다시 쓴다. 양성 질의는 자기 질문을 후보에서 빼고 검색한다. 이 질의들은 train 질문이라 test 보호와 충돌하지 않는다. ZIP 원문은 개발 평가 입력으로만 읽고, 결과에는 ID와 집계만 남긴다.
- 지표: τ별 추천 노출률, 라벨이 있는 추천 중 `reusable` 비율, `not_reusable` 비율, 라벨 없는 추천 비율.
- 선택 규칙(기본값, 사용자 확인 가능): 라벨이 있는 추천 중 `not_reusable` 비율이 10% 이하인 τ 중에서 노출률이 가장 높은 값.
- 요청 수: 질의 임베딩 1,797회 이하, 리랭크·생성 0회. 개발 인덱스 생성 임베딩은 승인 질문 수 ÷ 16회.

### 테스트 (Red → Green)

- 단위: 후보 차단 규칙, 답변별 중복 제거, τ 경계, revision·해시 불일치 시 추천 없음, generation 변경 시 재로드.
- 계약: 검색 질문 벡터 재사용으로 추가 임베딩 요청이 없음.
- 대역 E2E: 로그인 → 질문 → 답변 아래 추천 칩 → 칩 클릭 → 캐시 답변 표시(`response_source=cache`). v5 예시 질문 클릭이 캐시에 적중함.
- 기본 응답 회귀: 추천이 없을 때 SSE·로그가 PR A와 같음.

### 완료 기준

- 위 테스트와 기본 pytest 전체, ruff, pyright, 프론트 검사·빌드 통과. PR을 먼저 올리고 승인 후 개발 벡터와 val을 실행한다.
- τ를 설정하기 전에는 추천을 숨긴다. 승인된 val 결과와 τ별 곡선을 `docs/evals/`에 기록한다. 실제 모델 E2E는 별도 승인 후 실행하고, 실행하지 않았으면 "실행 안 함"으로 적는다.

## 5. 운영 반영 (PR A·B 병합 후, 사용자 승인)

1. 운영에 반영할 버전의 캐시 스키마로 개발 DB를 준비하고 `cache_seed_pass`로 통과 ZIP을 검증된 정확 일치 캐시 DB로 한 번 변환한다. 운영 적재에는 닫힌 DB 사본을 준비한다.
2. 운영 `e165933`과 대상 커밋의 diff를 확인한다. #148~#153과 이 계획의 PR이 함께 들어간다. 운영 `.env`에서 `LAWBOT_EXACT_CACHE_BUNDLE`을 지운다.
3. 캐시를 `off`로 두고 #149에 맞춰 운영 코퍼스 DB에 QA를 다시 가져온다. 캐시 DB를 마이그레이션하고 `cache_admin rebaseline-protection`으로 현재 보호 지문을 채택한다.
4. 사용자 승인 후 `cache_admin import-db`로 검수 답변을 적재하고, 제외·충돌 집계와 보호 지문을 확인한다.
5. 관리자 화면에서 검토 대기 P07 답변을 검수·재승인한다. 사용자 승인 후 추천 벡터를 생성하고, 별도 승인 후 최종 승인 ID로 `set-mode on`을 실행한다.
6. 다음 06:30 조문 갱신 뒤 06:41에 캐시 판정과 추천 후보 수를 확인한다.
7. `data/db/p07-handoff.zip`을 운영 트리 밖으로 옮기는 일은 별도로 승인받는다.

배포 직전에는 운영 코퍼스·캐시 DB의 읽기 전용 백업에서 만든 작업 사본으로 3~5단계를 리허설한다. QA 재적재 → `rebaseline-protection` → `import-db` → P07 재승인 → 추천 벡터 생성 → `set-mode on` 순서다. 운영 백업은 보존하고 작업 사본에 쓴다. 보호 지문, 검토 대기 질문 수, 적재 결과와 P07 재승인을 대조한다. 모델 호출은 요청 수 승인 후 실행한다.

## 6. 사용자 결정 (2026-11-15)

1. **추천 후보는 전수 검수된 답변 634개와 질문 2,813개를 정확 일치 캐시에 적재해 넓힌다.** 2026-11-16 사용자 결정에 따라 개발에서 `cache_seed_pass`로 통과 ZIP을 개발 캐시 DB로 한 번 변환한다. 운영 적재는 §5에서 승인 후 `cache_admin import-db`로 수행한다. DB 전송은 원천 DB의 답변 무결성과 운영 코퍼스의 조문·보호 분할을 확인하고, DB와 원천 ZIP의 SHA-256을 대상 DB 감사에 기록한다.
2. **보충 원자 질문 60개의 운영 적재를 취소한다.** 파일도 삭제했다.
3. **#154를 병합하지 않고 닫았다.** 평가 기록은 이 계획 PR로 main에 옮겼다.
4. **개발 데이터 삭제를 마쳤다.** `data/db/semantic/`의 `pr2-dev-index`, `pr2-fresh-import`, `pr3-search`, 보충 JSONL과 `results/semantic-union-pr154/`. `pass-handoff.zip`은 1번 적재 입력이라 남겼다. `deferred-handoff.zip`은 τ val 라벨 2,424행 중 1,174행(`uncertain` 1,007, `not_reusable` 167)의 질의 원문이라 삭제를 보류하고 사용자에게 다시 확인한다.
5. **보호 지문은 DB 명령으로 명시적으로 갱신한다(2026-11-16 사용자 결정).** `rebaseline-protection`은 현재 scope의 승인·활성 질문을 `protection_changed` 검토 대기로 내리고 이전 승인 ID를 비운다. 운영 순서는 §5를 따른다. 코퍼스 재활성화나 `import-db`의 부수 효과에 의존하지 않는다.
