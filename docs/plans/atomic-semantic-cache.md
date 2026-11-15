# 계획: 원자 단위 시맨틱 캐시와 어드민 승인 연동

- 작성: 2026-11-15 KST. 기준 `main` `b65cec5`, 운영 `e165933`(v6 임베딩).
- 문서 ID `atomic-semantic-admin-spec-2.0`(2026-11-11, 이하 "명세")는 PR 1 첫 커밋에 원문 그대로 보존한다. [작업 지시서 v2](atomic-semantic-cache-spec-v2.md)
- 이 문서는 명세를 이 레포에 맞춘 것이다. **명세와 다르게 하는 것**, **미리 확인한 사실**, **PR 나누기와 승인 지점**만 적는다. 나머지(처리 순서, 게이트 9개, 스키마 책임 분리, 모드, 로그, 테스트 V2-01~38, 출시·롤백)는 명세를 따른다.
- PR 1과 PR 2 개발 검증을 완료했다. PR 3~5는 아래 계획을 따른다. [PR 1 검증 기록](../evals/2026-11-15-semantic-cache-core.md), [PR 2 검증 기록](../evals/2026-11-15-atomic-index-tools.md)

## 1. 명세와 다르게 하는 것

| # | 명세 | 이 레포에서 | 이유 |
| --- | --- | --- | --- |
| 1 | §2.1 `CONTEXT.md` 먼저 읽기 | 뺀다 | 레포에 없다. 대신 `AGENTS.md`, `docs/architecture.md`, `docs/operations.md` |
| 2 | §2.2 셸 예시는 fish | **zsh** | 사용자 셸이 zsh다 |
| 3 | §4 Step 2 "A/B를 한 배치에 담는 것을 우선 시험" | **리랭커 호출 2회(A, B)로 확정.** 모델 서버는 고치지 않는다 | 모델 서버 rerank는 요청 하나에 query 하나·instruction 하나다(`services/model-server/.../rerank.py` `score(query, docs, instruction)`, 챗봇 `api_client.rerank(model, query, documents, instruction)`). 페어별 instruction은 모델 서버 변경이고, 이는 별도 사용자 요청이 필요하다 |
| 4 | §4.1 Step 0 앞 단계 없음 | **계산 질문 게이트를 맨 앞에 둔다.** `is_calculation_question(question)`이 참이면 exact·semantic 모두 조회하지 않고 RAG(계산기 경로)로 간다. pending 자동 등록과 원자 질문·seed에서도 제외한다 | 2026-11-10 사용자 결정: 계산 질문은 모든 캐시에서 제외. 지금 `web.chat_events`가 exact 앞에서 이미 이렇게 한다 |
| 5 | §5.4·§7.3 지연은 측정·보고만 | **miss 추가 지연 상한을 출시 조건으로 둔다.** semantic `on` 전 shadow에서 miss 경로의 추가 지연 p95를 재고, 사용자에게 수치와 함께 상한 승인을 받는다. 상한을 넘으면 `on` 하지 않는다 | 답변이 느려지는 변경은 피한다는 사용자 방침. miss는 원자 검색 + 리랭크 2회 + 합집합 리랭크가 더해진다 |
| 6 | §7.1 단계 A~J를 한 작업으로 | **PR 5개로 나눈다**(§3) | 범위가 커서 리뷰·되돌리기가 어렵다 |
| 7 | §5.2 snapshot이 바뀌면 시맨틱 전체 우회 | **binding(답변·원자·정책 연결)별로 유지·검토 대기·끔을 판정한다.** 통째로 끄지 않는다(사용자 결정 2026-11-15). 규칙은 exact의 `cache_carry.py`를 따르고 원자 의존을 더한다: 표시·의존 근거, 원자 부모 조문, 원자 질문이 모두 같은 원문 hash면 새 snapshot으로 **유지**(감사 actor `articles-update`); 같은 법령의 다른 조문만 바뀌면 **검토 대기**; 의존 조문·부모 조문이 바뀌거나 사라지면 **끔**(새 revision 필요). 판정이 실패하면 새 snapshot의 시맨틱은 꺼진 채로(fail closed) 두고 조문 갱신은 계속한다. 유지된 binding의 원자 질문 벡터는 그대로 새 index generation으로 옮긴다 | 06:30 자동 갱신으로 snapshot이 자주 바뀌므로 전체 우회는 시맨틱을 사실상 늘 끈다 |
| 8 | §10 Q1 초기 원자 범위 미정 | **A안 확정**(사용자 2026-11-15): 통과한 노동법 답변(통과 ZIP 634개)과 연결된 조문부터. B안(전체 조문)은 스키마·런타임만 지원 | 검수·평가 규모를 줄인다 |

### 그대로 두는 것(사용자 확인)

- **Group B(사용자 질문 ↔ 원자 질문을 리랭커로 비교)는 명세대로 쓴다.** 리랭커(Qwen3-Reranker)는 레포에서 학습시킨 적이 없는 기본 모델이고, 질문↔질문 관계 판단에 쓰는 것도 괜찮다고 사용자가 판단했다(2026-11-15). B의 instruction 문구와 threshold는 val에서 고르고, 리랭커 점수가 조건 검사를 뒤집지 못한다는 명세 규칙은 유지한다.
- 보호 test에는 명세 §7.2대로 v1 시절 test 650을 포함해 `docs/evals/README.md` "평가 데이터 사용 제한"의 모든 test 문항이 들어간다.

### PR 2 초기 seed 승인 범위 (사용자 결정 2026-11-15)

- 사용자가 전수 검수한 통과 ZIP의 답변을 캐시 답변으로 쓰고, 통과 질문을 exact 질문과 원자 질문으로 등록한다. 원자는 주 조문과 `review_unit_id`의 조합이다. 별도 내용 재검수, 모델을 통한 질문·명제 생성, val 재라벨링은 생략한다.
- import 시 승인 근거를 `actor=import`, `사용자 전수 검수 승인 2026-11-15`, 통과 ZIP SHA-256으로 기록한다. 명세의 pending 정책은 운영 반영 이후 자동 생성되는 답변에 적용한다. 초기 seed의 전수 검수 승인에는 적용하지 않는다.
- 현행 근거 조문이 다르거나 없으면 해당 답변·질문을 미승인으로 남기고 binding의 `carry_status`를 `off`로 기록한다. 계산 질문은 exact·원자 질문에서 제외한다. 보호 val/test와 겹치는 질문은 seed와 새 val에서 제외한다.
- 승인 여부와 활성 모드는 독립이다. exact는 기존 `set-mode`로 바꾸고, 시맨틱은 calibration과 후속 출시 조건을 따른다. import와 인덱스 교체는 캐시 모드를 유지한다.


### PR 2 리뷰 반영 (사용자 결정 2026-11-15)

- 시맨틱 재사용 조건은 1안이다. 조건이 없는 원자는 의도만 확인한다. 질문과 binding 의도가 모두 판정되고 다르면 `intent_conflict`로 우회한다. 의도가 같거나 어느 한쪽이 미판정이면 조건을 통과하고 부모 조문 A와 질문 B의 리랭크 점수 및 답변 간 마진으로 결정한다. 임계값과 마진은 PR 3에서 val로 고른다.
- seed 원문을 `supported_query_patterns`에 넣어 통문장 대조하면 시맨틱 조건을 만족하는 질문이 이미 exact에서 걸린다. seed에는 의도 전용 정책을 쓰고 금지 표현 검사와 기존 검수용 패턴·슬롯 문법을 유지한다. 근거 오류·계산 질문·보호 문항 및 기존 exact의 답변 리비전 충돌을 제외한 승인 binding은 시맨틱 사용이 가능하다. 미판정 의도에는 ID를 넣지 않는다.
- 인라인 지적 10건을 모두 이번 PR에서 처리한다. 답변 리비전 불일치와 P07 승인·보호 해시 보존을 먼저 회귀 테스트로 재현한다. 기존 개발 DB의 시맨틱 binding만 멱등하게 갱신하고 원자 질문 순서를 검증한다.
- 보호 목록과 fingerprint는 `cache_seed.load_protection`에서 구하고 시맨틱 seed 승인 ID는 감사 기록에 둔다. 기존 exact 어드민·이월·명령 경로를 대역으로 확인한다. 활성 인덱스 접근, 모델 revision 공용 함수와 번들 오류도 회귀 테스트로 확인한다. v6 학습 원본을 구할 수 있으면 val ID별 훈련 노출을 대조한다.
- 인터페이스는 `off`와 `shadow`를 유지한다. 이번 수정은 실제 모델 0회로 검증하고, 질문 행 순서가 달라졌을 때만 인덱스 재생성의 요청 수와 승인을 별도로 확인한다.

### PR 3 검색 평가와 보충 원자 (사용자 결정 2026-11-15)

- 노동 v4 val/test의 모든 정답 조문에 승인된 원자 질문이 있는지 먼저 검사한다. 어느 쪽이든 빈 조문이 있으면 모델 평가를 중단한다.
- 커버리지가 충족되면 val에서 기존 조문 검색과 원자 질문 검색을 먼저 비교한 뒤 합집합 후보 예산을 고른다. 이어서 시맨틱 val에서 오적중을 우선해 임계값과 마진을 선택한다. 설정을 고정한 뒤 test에서 세 검색 경로를 한 번 비교한다.
- 사용자 승인 보충 질문은 답변 binding 없이 검색에 사용한다. 승인 이력에 actor `import`, 사유 “서브에이전트 검토 후 사용자 승인 2026-11-15”, 원본 SHA-256을 보존한다. 운영 적재는 PR 3~5 완료 후 별도 승인으로 진행한다.
- 입력 ZIP·JSONL은 오프라인 도구에서만 읽는다. 웹앱은 DB와 인덱스를 읽으며 기본 검색 경로는 유지한다.
- 클라우드 모델과의 답변 품질 비교는 전체 구현 이후 별도 계획으로 다룬다.

## 2. 미리 확인한 사실 (2026-11-15, `b65cec5`)

- 캐시 분기는 `services/chatbot/src/lawbot/web.py` `chat_events`에 있다: 계산 질문 → bypass, 아니면 `ExactCache.lookup` → `on`이고 match면 `cached_events`로 끝, 아니면 RAG. 시맨틱은 이 exact miss 뒤에 들어간다.
- exact 캐시: `exact_cache.py`, `cache_runtime.py`(`ExactCache`, `Decision.metrics()`), `cache_store.py`, `cache_review_store.py`, `cache_release_store.py`, `cache_admin.py`, `cache_carry.py`(release 바뀔 때 keep/pending/off), migration `db/cache_migrations/0001_exact_cache.sql`, `0002_release_review.sql` → 새 migration은 `0003_`부터.
- 설정: `settings.py`의 `LAWBOT_EXACT_CACHE_DB`, 번들 경로. 새 설정 이름은 `LAWBOT_` 접두로 맞춘다(명세 §5.1의 이름은 제안).
- 질문 임베딩: 모델 서버 `input_type='query'` → `prompt_name='query'`, 정규화 float32. 운영 임베딩은 `lawbot-emb-v6`.
- 어드민: `frontend/src/admin/CachePanel.tsx`, `adminApi.ts`, `types.ts`, 서버 `admin.py`(인증·Origin·감사 로그).
- 관련 지난 계획: `docs/archives/exact-cache-p07.md`, `exact-cache-carry-forward.md`, `cache-approval-ux.md`, `cache-panel-layout.md`, `sqlite-admin-pipeline-spec.md`.

## 3. PR 나누기

모든 PR은 `docs/plans/`에 이 계획을 따르는 PR별 절(또는 하위 계획)을 갱신하고, TDD(Red → Green → Refactor), 기본 테스트 전체 Green, ruff·pyright·프론트 빌드 통과 후 올린다. 실제 모델 호출은 각 PR 설명의 승인 지점에서만 한다.

| PR | 브랜치(예) | 범위(명세 단계) | 완료 기준 | 실제 모델 |
| --- | --- | --- | --- | --- |
| 1 | `feat/semantic-cache-core` | A 사전 탐색 기록, B 스키마(0003 migration, revision·binding·정책·index manifest, **binding별 carry 상태와 판정 이력 칸**), D 결정적 조건 검사·A/B 페어 매핑·`linked_parent_score`, E dispatcher(계산 게이트 → exact → semantic `off/shadow`) | V2-01~12, 19, 20, 28(exact 부분), 31, 37 Green. 기본값 `off`에서 기존 응답·SSE·로그가 바이트 단위로 같음 | 없음(가짜 모델) |
| 2 | `feat/atomic-index-tools` | C 통과 ZIP import, 인덱스 generation·manifest·원자적 교체, 규칙 기반 val 세트와 누수 검사 | V2-20, 30, 35, 36 Green. import·승인·예외·P07 연결 및 val 구성 집계 | **PR 생성 뒤 질문 임베딩만 승인 요청** |
| 3 | `feat/semantic-union-rag` | F 미스 합집합 RAG(D_base ∪ D_atomic, 점수 재사용 조건), H shadow 평가 도구와 계측 필드 | V2-13~18, 32, 38 Green. shadow 평가(val)로 A/B threshold·margin·후보 예산 선택, miss 추가 지연 p50/p95, 합집합 vs 기존 검색 recall 비교 → `docs/evals/` 기록 | **val shadow 평가 승인**(요청 수·시간 먼저 제시) |
| 4 | `feat/semantic-admin-pending` | G 어드민 검수 화면 최소 확장, 정상 완료 후보만 pending, 승인·배포·철회, 동시성(조건부 기록) | V2-21~27, 29, 33 Green. 대역 E2E 390px·1280px. `AUTO_STAGE_PENDING` 기본 off | 출시 판단용 실제 모델 평가(V2-33·34)는 **별도 승인** |
| 5 | `feat/semantic-carry-forward` | §1 #7 binding별 유지·검토 대기·끔: DB 모드 `lawbot-articles-update`에서 활성화 직후·웹 재시작 전에 판정, 갱신 보고서에 판정 수, 유지 binding의 벡터를 새 index generation으로 이전, 어드민 캐시 탭에 binding별 판정·바뀐 조문(전후 원문)·재승인 | 유지/검토 대기/끔/판정 실패(fail closed)/롤백 각각 통합 테스트, V2-24 Green, 대역 E2E로 어드민 판정 화면 | 없음(가짜 모델) |

- 운영 `on`(명세 I)은 PR 5까지 병합된 뒤, 사용자가 threshold·지연 상한·출시 표본을 승인했을 때만 한다. 운영 반영은 별도 승인.

### PR 1 사전 탐색 (2026-11-15, `3acba50`)

| 명세 §2 계약 | 실제 파일과 진입점 | PR 1 연결 지점 |
| --- | --- | --- |
| `CONTEXT.md` | 파일 없음. `AGENTS.md`, `docs/architecture.md`, `docs/operations.md`에서 개발·운영 분리와 런타임 구성 확인 | §1 #1 적용 |
| 계산 질문 → exact → semantic | `services/chatbot/src/lawbot/web.py`의 `chat_events`, `calc_gate.py`의 `is_calculation_question`, `cache_runtime.py`의 `ExactCache.lookup`, `exact_cache.py`의 `normalize`·`Snapshot.resolve` | NFC·공백 정규화, Pro 간결형·공개 namespace, 유효기간·활성화 이벤트·근거 해시 확인은 기존 exact가 수행. 계산 게이트와 exact 응답 반환 뒤 shadow 조회 추가 |
| 기존 응답, SSE, 요청 로그 | `service.py`의 `system_text`·`ask`·`request_record`, `llm.py`, `web.py`의 `cached_events`·`sse`·`answer_verdict`, `answer_format.py`, `citation.py` | 현재 프롬프트와 후처리를 사용. 가짜 모델과 고정한 시간·요청 ID로 기존 출력 바이트 대조 |
| 캐시 마이그레이션과 답변 revision | `db/cache_migrations/0001_exact_cache.sql`, `0002_release_review.sql`, `cache_store.py`의 `Store`, `read_control`, `revision_id` | 독립 캐시 DB에 0003 추가. `semantic_store.py`가 읽기를 맡고 답변 본문은 `cache_answers`, 표시 근거는 `cache_citations`를 참조 |
| 검수 후보와 배포 이력 | `cache_review_store.py`의 `ReviewCatalog`, `cache_release_store.py`의 `apply_release`, `cache_admin.py`, `cache_carry.py`, `corpus_store.py`의 `active_release`와 `active_token` | 기존 draft·approved·revoked와 cache control의 세대·활성화 이벤트를 참조. DB는 트랜잭션마다 연결. binding의 carry 상태와 판정 이력 구조 추가 |
| 질문 임베딩과 조문 검색 | `api_client.py`의 `Embedder.encode`, `retriever.py`의 `Retriever`, `embed_cache.py`, `service.py`의 `retrieve`·`retrieve_decrees`, `deploy.py`, 모델 서버의 `embeddings.py`·`revision.py` | v5 기본 `lawbot-emb-v6`, query prompt, MPS fp16 → 정규화 float32. v5 법률 길이 4,352, 리랭킹 기본 후보 10 → 법률 3, 하위법령은 `decree.py` 별도 필터. 현재 차원과 revision은 인덱스·서버 계약에서 확인. 인덱스 생산은 PR 2 |
| 리랭크 A/B | `reranker.py`의 `Reranker.score`, `api_client.py`의 `ModelServerClient.rerank`와 `_ordered_results`, 모델 서버의 `rerank.py`, `lawbot_common.api` | 요청마다 instruction 하나, 최대 문서 1,000개, 내부 pointwise 1페어씩. 최대 2,560토큰, 오른쪽 잘림, float32 softmax P(yes). 응답 인덱스를 검증한 뒤 `pair_id`에 대응. API에 잘림 필드가 없어 모델 계약의 보수적 입력 상한 사용 |
| 웹 시작 설정과 앱 조립 | `settings.py`의 `load(web=True)`, `service.py`의 `load_app`, `web.py`의 `main` | 기본 `off`, `shadow`의 calibration 검증, `on` 거부 |
| 관리자 검수 | `admin.py`, `services/chatbot/frontend/src/admin/CachePanel.tsx` | 어드민 화면과 승인 동작은 PR 4 |
| 법령 범위와 권한 | 명세의 `lawbot/law_scope.py`는 없음. 실제 `packages/lawbot-common/src/lawbot_common/law_scope.py`, `web.py`의 인증, `exact_cache.py`의 공개 namespace | 활성 코퍼스와 승인된 answer namespace에 묶어 조회 |
| 보호 데이터·실행 환경 | `cache_seed.py`의 `load_protection`, `cache_carry.py`의 `allowed_question`, `qa_rules.py`, `docs/evals/README.md`, `.env.example`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml` | PR 1 런타임은 읽기 전용. 개발 환경 Python 3.14.8, FastAPI 0.143.3, NumPy 2.5.4, OpenAI 3.42.0, pytest 9.1.1에서 가짜 모델과 합성 질문으로 확인 |

- 0003 업그레이드와 FK 검사, 조건·점수와 shadow 게이트, dispatcher와 회귀 비교 순으로 구현한 뒤 기본 테스트·정적 검사·프론트 빌드를 실행한다.
- V2-02는 승인된 합성 답변으로 shadow 판정이 적중하는지, 실제 사용자 응답은 기존 RAG인지 확인한다. 시맨틱 반환·생성 생략 검증은 `on`을 지원할 때 실행한다.

### PR 1 런타임 계약

- 설정 `LAWBOT_SEMANTIC_CACHE_MODE`의 기본값은 `off`다. `shadow`는 DB 코퍼스, 기존 `LAWBOT_EXACT_CACHE_DB`, v5 리랭커, `LAWBOT_SEMANTIC_CALIBRATION` JSON을 요구한다. calibration은 테스트 또는 검증에서 정한 모델 ID·revision, 쿼리 길이·후보 수, index revision, A/B instruction·threshold·margin을 담는다. 코드가 임계값을 채우지 않는다.
- 기존 exact는 캐시 DB 0002와 0003을 지원한다. shadow를 관측할 개발 사본에는 `uv run python -m lawbot.cache_admin migrate --db <캐시 DB 사본>`으로 0003을 적용한다. shadow 인덱스는 `LAWBOT_SEMANTIC_INDEX`의 float32 `.npy`와 DB manifest의 행 순서, 차원, fingerprint, SHA-256을 읽는다. 파일이 없거나 호환되지 않으면 기존 RAG를 사용한다. 인덱스 생성 및 교체 도구는 PR 2 범위다.
- 조건 정책은 질문 전체를 검수된 문형과 대조한다. 문형의 `{slot}`은 정책이 열거한 원문 표현만 받는다. 각 표현의 구조화 값과 범위를 비교하고, 질문 안에서 잡힌 조건의 위치를 판정에 남긴다. 문형 밖 표현과 슬롯 해석이 여러 개인 경우는 미스로 처리한다.
- `shadow`는 `LAWBOT_SEMANTIC_RERANK_TOKENIZER`로 기존 Qwen3 리랭커의 로컬 `tokenizer.json`을 읽고 calibration의 `reranker_tokenizer_sha256`과 대조한다. 서버와 같은 prefix·중간 입력·suffix를 따로 인코딩해 2,560토큰을 넘는 페어를 제외한다. 모델 revision은 shadow 추론 전후 서버 모델 목록에서 확인한다.
- SSE 종료 프레임 전송과 채팅 슬롯 해제 후 `shadow`를 한 건씩 실행한다. 이미 실행 중이면 `shadow_busy`를 기록한다. `shadow_timeout_s`는 기본 2초이며 각 호출에 남은 시간을 넘긴다. 마감 후 받은 점수는 폐기하고 `deadline_exceeded`로 기록한다. 요청 로그 `call_counts`에 shadow 임베딩·리랭크·모델 목록 호출을 합산한다.

### PR #151 리뷰 반영 순서

1. P1에서 SSE 종료 프레임과 요청 슬롯 해제 뒤 shadow를 실행하고 전체 deadline을 적용한다. 로컬 리랭커 토크나이저로 실제 입력 토큰 수를 확인해 긴 페어만 제외한다. 출처 질문의 철회·계산 제외를 binding 자격에 반영한다.
2. P2에서 인덱스 시작 경고와 구 스키마 점검을 추가한다. exact 상태 판단을 `Decision`에 두고, 요청 중 모델 revision과 실패한 호출까지 기록한다.
3. P3에서 카탈로그 자격 판정에 필요한 열을 읽고 임베딩 후 Top-K만 구성한다. SSE와 요청 로그 회귀 검사는 이벤트와 필드를 비교한다.

가짜 모델로 회귀 테스트를 먼저 실패시키고, 전체 pytest·정적 검사·프론트 빌드가 통과하면 기존 PR을 갱신한다. [리뷰 요약](https://github.com/bhsong/lawbot/pull/151#issuecomment-6887095214)

### PR 2 실행 계획

1. 지정 해시와 ZIP의 참조를 검증한다. 정규화와 근접 비교에는 기존 QA 보호 규칙을 쓴다. 보호 질문 원문은 비교 과정에서만 읽는다. 질문, 답변, 복수 표시·검토 근거와 출처는 개발용 캐시 사본에 보존한다.
2. 승인과 감사는 캐시 DB의 한 트랜잭션에 묶는다. 모드·기존 P07 승인 및 원문을 보존한다. 원문 위치는 주 조문에 포함되는 검토 근거 구절로 찾고, 일치하는 구절이 없으면 주 조문 전체를 기록한다. 초기 seed에는 위 의도 전용 정책을 적용한다.
3. 질문·후보 답변 ID 쌍과 라벨을 공개 가능한 val 산출물로 만든다. ID는 원문을 포함하지 않는 안정적 식별자로 만든다. 답변당 해시 순으로 대표 검증 질문 하나를 고르고, 평가용 generation에서 빼도 나머지 질문이 남도록 확인한다. 전체 질문을 쓰는 generation과 구분한다.
4. 질문 임베딩과 manifest를 준비하고 검증한 다음 활성 포인터를 원자적으로 바꾼다. 길이·차원·유한값·모델 revision과 snapshot이 일치할 때만 교체한다. 테스트와 PR 전 개발 검증에는 대역 임베딩만 쓰고, 실제 요청 수는 PR 이후 승인받는다.


### PR 2 도구 계약

- 챗봇 uv 프로젝트에서 `uv run python -m lawbot.semantic_tools`를 호출한다. 코퍼스 사본과 0003 캐시 사본을 별개로 준비한다. 코퍼스·검색 프로필·시스템 프롬프트·하위법령 설정은 개발 웹앱과 같다. 도구는 활성 코퍼스와 activation event를 확인한다. 개발 코퍼스에는 기존 exact 검증에 필요한 QA 8개 세트를 이관한다. 시맨틱 import와 seed 활성화의 exact 보호 목록은 P07 ZIP 없이 코퍼스 DB 분할과 추가 보호 CSV로 만든다(#153).
- `import --bundle <pass ZIP> --corpus-db <코퍼스 사본> --cache-db <캐시 사본>`은 승인 상태와 감사 기록을 한 번만 남긴다. 보호 문항과 겹치는 기존 질문의 답변까지 동일하면 해당 exact 플래그를 끄고 기존 승인과 이력을 보존한다.
- `val --bundle <pass ZIP> --deferred-bundle <deferred ZIP> --corpus-db <코퍼스 사본> --output <새 JSONL>`은 원문 없는 ID와 라벨을 생성한다. pass ID는 출처와 원본 ID의 해시, 답변 ID는 주 조문과 검수 단위 ID의 해시다. 같은 주 조문의 다른 답변은 대표 질문과 대상 답변의 쌍으로 기록한다. `--v6-training-dir`에는 Colab 기록의 QA·분할 CSV 8개를 지정한다. 임베딩 train만 대상으로 정규화 문장 또는 동일 원본 ID의 노출을 대조한다.
- `index --cache-db <캐시 사본> --corpus-db <코퍼스 사본>`은 인덱스 행 수와 필요 요청 수만 출력한다. 승인 후 `--embed-live`와 승인 요청 수, 시간 상한, 모델·revision·최대 길이·차원·로컬 모델 디렉터리·출력 디렉터리를 지정한다. 전체 generation은 승인된 질문을 담는다. PR 3 평가 generation은 별도 출력 디렉터리에 `--exclude-val data/v5/semantic_val_ids.jsonl`을 지정해 양성 질의 524개를 뺀다.
- 관리형 인덱스 디렉터리를 `LAWBOT_SEMANTIC_INDEX`에 지정하면 shadow는 `active.json`의 generation과 calibration의 revision을 대조한다. 기존 단일 `.npy` 경로도 계속 지원한다. manifest 등록과 검증 뒤 포인터를 교체한다.
- exact 활성화는 별도 절차다. `uv run python -m lawbot.cache_admin set-mode shadow --semantic-seed --db <캐시 사본> --corpus-db <코퍼스 사본> --approval <import 승인 ID> --reason <활성화 사유>`에서 `shadow` 또는 `on`을 지정한다. import와 index는 모드를 바꾸지 않는다. 활성화는 현재 exact 보호 지문과 seed 영수증의 보호 지문을 함께 확인하며 ZIP은 읽지 않는다. 추가 보호 CSV를 사용하면 import와 활성화에 같은 `--protected-csv` 인자를 전달한다. 최초 활성화로 새 exact 승인 ID가 생기면 결과의 `approval_id`로 출력한다.


### PR 2 두 번째 인라인 리뷰 반영

1. 레거시 시맨틱 정책 업그레이드를 제거하고 새 개발용 DB에 검증된 P07과 동일한 보호 지문으로 import한다. 새 DB에서 답변·binding 집계와 질문별 감사를 확인한다.
2. 승인 ID가 없는 shadow 조회는 `not_released`로 막는다. 명시적 seed 활성화는 승인 영수증과 exact 보호 지문을 함께 검사한다. 활성화 시에는 ZIP을 읽지 않고, 새로 발급된 exact 승인 ID는 CLI 결과로 출력한다.
3. 번들 오류의 범위를 파일 읽기와 데이터 검증으로 한정한다. 포인터의 일시적 읽기 실패가 캐시에 남지 않는지 확인한다. 공용 모델 지문 설명은 영어로 쓴다.
4. Red를 확인한 뒤 기본 pytest·Ruff·Pyright·프런트엔드 빌드를 통과시킨다. 새 개발 DB의 정렬된 질문 ID와 기존 manifest 및 벡터 SHA-256을 대조한다. 기존 벡터를 재사용할 수 없는 경우 임베딩 전에 사용자 승인을 요청한다.


### PR 2 세 번째 인라인 리뷰 반영

1. 새 import 전에 기존 exact 보호 지문과 seed 지문을 대조한다. 현재 영수증에 해당하는 재실행은 멱등으로 유지하고, 이전 seed 영수증 또는 원자가 있는 사본은 명시적 오류로 거부한다.
2. Shadow는 exact 모드와 승인 ID를 각각 확인한다. 모드 활성화는 ZIP 파일 열기를 차단한 테스트로 확인하고, 추가 보호 CSV 경로 해석을 import와 공유한다. #153 병합 후 import와 활성화의 보호 지문은 P07 ZIP 없이 코퍼스 DB 분할과 추가 보호 CSV로만 계산한다.
3. 압축 해제 실패를 CLI 번들 오류로 처리하고 고정 SHA-256 번들의 구조 검사를 줄인다. 조문 CSV는 한 번 읽는다. Red 확인 후 기본 pytest·Ruff·Pyright·프런트엔드 빌드를 실행한다. 기존 개발 사본의 집계와 인덱스는 읽기 전용으로 대조한다.

### PR 3 실행 계획

#### 승인된 val 평가 후속 작업

- 커버리지, 노동 val base·atomic, 노동 val union, 시맨틱 val 순으로 실행한다. test 질문은 후속 승인을 받기까지 사용하지 않는다.
- 문항별 결과와 요청 수를 비공개 결과 디렉터리에 저장한다. 이미 기록된 문항은 재개 시 건너뛰고 작업 중 확인한 모델·데이터 지문과 운영 지표를 기록한다.
- union의 조문·원자 질의에 하나의 v6 query 벡터를 전달한다. 두 경로의 길이 제한을 확인하고 기존 base 경로 및 계산 결과와 대조한다.
- 노동 val union과 향후 test union은 같은 질문·문서·리랭커 계약의 저장 pointwise 점수를 재사용한다. 두 경로의 후보를 복원해 합집합을 만들며 가장 큰 예산에 필요한 누락 페어만 리랭크한다. val 해시 표본 100개에서 실제 union 상위 3개 순위·점수를 대조한다. 같은 표본에 base와 union을 연달아 실행하며 해시 순서에 따라 먼저 실행할 경로를 교대한다. 같은 문항의 두 실측 시간 차이로 추가 지연을 구한다.
- 후보 수 대조군은 노동 val의 기존 base 질문 벡터로 조문 상위 15·20개를 복원한다. 저장된 base·atomic·union 점수를 합치고, base@20의 누락 페어만 문항당 최대 한 번 채점한다. 임베딩 요청은 0회, 리랭크 요청 상한은 1,110회다. 같은 후보 상한의 union과 문항별 승패, McNemar 정확검정, 후보 포함과 R@3의 겹침을 비교한다. 전체 경로 지연 추정과 누락 페어 호출 실측을 구분한다. test 경로 변경은 사용자 결정 후 진행한다.
- 운영 로그는 시간·상태·오류 유형·전체 지연만 집계한다. 지정된 일시정지와 중단 기준을 적용하고, 실제 val 결과를 같은 PR에 기록한다.

1. 합성 데이터로 보충 원자 적재, 중복·보호 검사와 binding 없는 검색, 커버리지 중단 경로를 먼저 재현한다. 개발 사본을 사용해 승인·제외·커버리지를 집계한다.
2. 평가용 base, atomic, union 경로를 추가하고 후보 보존·버전 충돌·리랭크 입력 계약·장애와 shadow 검증을 대역으로 실행한다. 기존 웹 경로 회귀를 포함해 기본 pytest, lint, 타입 검사와 프론트 빌드를 실행한다.
3. 개발 사본과 불변 인덱스 생성 경로를 대역으로 검증한다. PR 이후 커버리지가 충족되면 요청 수·시간·실행 조건을 명시해 실제 임베딩과 평가 승인을 받는다.

## 데이터 ZIP 위치

통과·추후 검토 ZIP은 답변과 질문 원문을 담고 있어 커밋하지 않는다. ZIP은 적재 명령의 입력으로만 쓰고 운영 트리에 복사하지 않는다. 운영 런타임은 ZIP을 읽지 않고 DB만 읽는다(사용자 결정 2026-11-15, [exact-cache-zip-free-runtime](exact-cache-zip-free-runtime.md)).

- 개발 트리: `data/db/semantic/pass-handoff.zip`, `data/db/semantic/deferred-handoff.zip` (`data/db/`는 `.gitignore` 대상). 권한 600.
- 사용자가 넣은 뒤 SHA-256을 이 계획서에 적고, 도구는 경로를 인수(`--bundle`)나 `LAWBOT_` 설정으로 받는다. 코드에 경로를 박지 않는다.
- 운영에는 출시 반영 때 같은 상대 경로로 복사한다(별도 승인).

| 개발 트리 경로 | 존재 여부 (2026-11-15 KST) | SHA-256 |
| --- | --- | --- |
| `data/db/semantic/pass-handoff.zip` | 있음 | `2da4c96ed8541c7c13fdc948db1f3c39e9e19d42c55972842851c0f77a75166f` |
| `data/db/semantic/deferred-handoff.zip` | 있음 | `41dd33d4c1e7eae01bdfc95bb18793eeb1f11a24d0fc7b5b917d799955183862` |

## 4. 사용자 확인 필요

1. PR 3 후 miss 추가 지연 상한 값(측정 결과를 보고 결정).
2. pending 원문 보존 기간·접근 권한(PR 4 전에 결정, 명세 §5.3).

## 5. 지키는 규칙

- 모델 서버 코드·설정·재시작 변경 없음. 개발 추론 한도(동시 1, max_tokens 1024, 재시도 없음).
- 평가 실행은 `EVALUATION_MODE`로 모든 캐시·pending 쓰기를 막는다. threshold 등 설정은 val로만 고른다.
- 운영 트리·운영 DB를 건드리지 않는다. 통과·추후 검토 ZIP 원본은 수정하지 않는다.
- 각 PR은 리뷰 후 사용자의 명시적 요청이 있을 때만 병합한다.
