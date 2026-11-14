# 계획: 원자 단위 시맨틱 캐시와 어드민 승인 연동

- 작성: 2026-11-15 KST. 기준 `main` `b65cec5`, 운영 `e165933`(v6 임베딩).
- 원본 명세: 사용자가 준 「[작업 지시서 v2] Lawbot 원자 단위 질문 검색·시맨틱 캐싱 및 어드민 승인 연동」(문서 ID `atomic-semantic-admin-spec-2.0`, 2026-11-11, 이하 "명세"). PR 1 첫 커밋에서 원문 그대로 `docs/plans/atomic-semantic-cache-spec-v2.md`로 넣는다.
- 이 문서는 명세를 이 레포에 맞춘 것이다. **명세와 다르게 하는 것**, **미리 확인한 사실**, **PR 나누기와 승인 지점**만 적는다. 나머지(처리 순서, 게이트 9개, 스키마 책임 분리, 모드, 로그, 테스트 V2-01~38, 출시·롤백)는 명세를 따른다.
- 상태: 계획. 구현·실제 모델 평가·운영 반영 안 함.

## 1. 명세와 다르게 하는 것

| # | 명세 | 이 레포에서 | 이유 |
| --- | --- | --- | --- |
| 1 | §2.1 `CONTEXT.md` 먼저 읽기 | 뺀다 | 레포에 없다. 대신 `AGENTS.md`, `docs/architecture.md`, `docs/operations.md` |
| 2 | §2.2 셸 예시는 fish | **zsh** | 사용자 셸이 zsh다 |
| 3 | §4 Step 2 "A/B를 한 배치에 담는 것을 우선 시험" | **리랭커 호출 2회(A, B)로 확정.** 모델 서버는 고치지 않는다 | 모델 서버 rerank는 요청 하나에 query 하나·instruction 하나다(`services/model-server/.../rerank.py` `score(query, docs, instruction)`, 챗봇 `api_client.rerank(model, query, documents, instruction)`). 페어별 instruction은 모델 서버 변경이고, 이는 별도 사용자 요청이 필요하다 |
| 4 | §4.1 Step 0 앞 단계 없음 | **계산 질문 게이트를 맨 앞에 둔다.** `is_calculation_question(question)`이 참이면 exact·semantic 모두 조회하지 않고 RAG(계산기 경로)로 간다. pending 자동 등록과 원자 질문·seed에서도 제외한다 | 2026-11-10 사용자 결정: 계산 질문은 모든 캐시에서 제외. 지금 `web.chat_events`가 exact 앞에서 이미 이렇게 한다 |
| 5 | §5.4·§7.3 지연은 측정·보고만 | **miss 추가 지연 상한을 출시 조건으로 둔다.** semantic `on` 전 shadow에서 miss 경로의 추가 지연 p95를 재고, 사용자에게 수치와 함께 상한 승인을 받는다. 상한을 넘으면 `on` 하지 않는다 | 답변이 느려지는 변경은 피한다는 사용자 방침. miss는 원자 검색 + 리랭크 2회 + 합집합 리랭크가 더해진다 |
| 6 | §7.1 단계 A~J를 한 작업으로 | **PR 4개로 나눈다**(§3) | 범위가 커서 리뷰·되돌리기가 어렵다 |
| 7 | §5.2 snapshot이 바뀌면 시맨틱 전체 우회 | 그대로 따르되 PR 1에서 **조문 release가 실제로 얼마나 자주 바뀌는지** 잰다(최근 30일 활성화 횟수·날짜). 거의 매일 바뀌면 exact처럼 답변별 유지·보류·끄기(`cache_carry.py`)를 시맨틱 binding에 적용하는 후속 PR을 계획에 올리고 사용자에게 묻는다 | 06:30 자동 갱신으로 snapshot이 자주 바뀌면 시맨틱이 사실상 늘 꺼진다 |
| 8 | §10 Q1 초기 원자 범위 미정 | **A안**: 통과한 노동법 답변(통과 ZIP 634개)과 연결된 조문부터. B안(전체 조문)은 스키마·런타임만 지원 | 검수·평가 규모를 줄인다(권고, 사용자 확인 필요 — §4) |

### 그대로 두는 것(사용자 확인)

- **Group B(사용자 질문 ↔ 원자 질문을 리랭커로 비교)는 명세대로 쓴다.** 리랭커(Qwen3-Reranker)는 레포에서 학습시킨 적이 없는 기본 모델이고, 질문↔질문 관계 판단에 쓰는 것도 괜찮다고 사용자가 판단했다(2026-11-15). B의 instruction 문구와 threshold는 val에서 고르고, 리랭커 점수가 조건 검사를 뒤집지 못한다는 명세 규칙은 유지한다.
- 보호 test에는 명세 §7.2대로 v1 시절 test 650을 포함해 `docs/evals/README.md` "평가 데이터 사용 제한"의 모든 test 문항이 들어간다.

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
| 1 | `feat/semantic-cache-core` | A 사전 탐색 기록, B 스키마(0003 migration, revision·binding·정책·index manifest), D 결정적 조건 검사·A/B 페어 매핑·`linked_parent_score`, E dispatcher(계산 게이트 → exact → semantic `off/shadow`) | V2-01~12, 19, 20, 28(exact 부분), 31, 37 Green. 기본값 `off`에서 기존 응답·SSE·로그가 바이트 단위로 같음. release 변경 빈도 측정 결과를 PR에 적음 | 없음(가짜 모델) |
| 2 | `feat/atomic-index-tools` | C 원자·질문 구축 도구(오프라인), 인덱스 generation·manifest·원자적 교체, 통과 ZIP import(V2-35), val 세트(재사용 가능 / 불가 / 불확실, hard-negative 포함)와 누수 검사 | V2-20, 30, 35, 36 Green. A안 범위 manifest 확정. 생성한 원자·질문 수, val 구성 수 보고 | 원자·질문 생성에 모델이 필요하면 **요청 수·시간 추정 후 승인** |
| 3 | `feat/semantic-union-rag` | F 미스 합집합 RAG(D_base ∪ D_atomic, 점수 재사용 조건), H shadow 평가 도구와 계측 필드 | V2-13~18, 32, 38 Green. shadow 평가(val)로 A/B threshold·margin·후보 예산 선택, miss 추가 지연 p50/p95, 합집합 vs 기존 검색 recall 비교 → `docs/evals/` 기록 | **val shadow 평가 승인**(요청 수·시간 먼저 제시) |
| 4 | `feat/semantic-admin-pending` | G 어드민 검수 화면 최소 확장, 정상 완료 후보만 pending, 승인·배포·철회, 동시성(조건부 기록) | V2-21~27, 29, 33 Green. 대역 E2E 390px·1280px. `AUTO_STAGE_PENDING` 기본 off | 출시 판단용 실제 모델 평가(V2-33·34)는 **별도 승인** |

- 운영 `on`(명세 I)은 PR 4 뒤 사용자가 threshold·지연 상한·출시 표본을 승인했을 때만 한다. 운영 반영은 별도 승인.
- §1 #7에서 release가 자주 바뀌면 PR 5(시맨틱 binding carry-forward)를 제안한다.

## 4. 사용자 확인 필요

1. 초기 원자 범위 A안 확정 여부(권고 A).
2. PR 3 후 miss 추가 지연 상한 값(측정 결과를 보고 결정).
3. pending 원문 보존 기간·접근 권한(PR 4 전에 결정, 명세 §5.3).

## 5. 지키는 규칙

- 모델 서버 코드·설정·재시작 변경 없음. 개발 추론 한도(동시 1, max_tokens 1024, 재시도 없음).
- 평가 실행은 `EVALUATION_MODE`로 모든 캐시·pending 쓰기를 막는다. threshold 등 설정은 val로만 고른다.
- 운영 트리·운영 DB를 건드리지 않는다. 통과·추후 검토 ZIP 원본은 수정하지 않는다.
- 각 PR은 리뷰 후 사용자의 명시적 요청이 있을 때만 병합한다.
