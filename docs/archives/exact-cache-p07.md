# Lawbot v5 정확 일치 캐시 구현 계획 (P01~P07)

- 2026-11-09 작성 · 개발 구현 및 검증, 운영 승인 대기
- 개발 기준 `main`의 `5e5d5351b404e38af7b0dc3c7b3079ff498e8c4b`
- 정확 일치 캐시의 기능과 안전 조건은 인계 ZIP의 `requirements/Lawbot_v5_01_정확일치캐시_요구사항.md` v1.0을 따른다. `00_READ_FIRST.md`와 `START_PLAN_PROMPT.txt`가 초기 작업 범위를 정한다. `requirements/Lawbot_v5_02_시맨틱캐시_요구사항.md` v1.0의 §1·§7은 후속 호환성에만 적용한다.
- **기술 전제와 연결 방식은 현행 레포의 코드·잠금 파일·운영 절차를 우선한다.** 구현 착수 때 실제 작업 HEAD를 다시 확인한다. 전제가 달라졌으면 기존 동작과 정확 일치의 기능·안전 조건을 함께 만족하도록 이 계획을 갱신한다. 둘을 함께 충족할 수 없는 항목은 출시 보류 사유로 남긴다.

## 1. 목표와 입력 범위

인증된 공용 한국어 단독 질문을 정규화해 먼저 조회한다. 질문과 답변 리비전이 모두 승인되어 있고 현재 스냅샷에서 표시 근거를 전부 복원할 수 있을 때, 기존 웹 응답 계약으로 답변과 근거를 반환한다. 이 요청의 질문 임베딩·벡터 검색·리랭킹·답변 생성 호출은 각각 0회다. 그 외에는 기존 RAG가 한 번 처리한다.

| 입력 | 사용 범위 |
| --- | --- |
| `data/pass_seed_candidates.jsonl` | **유일한 초기 질문 허용 목록**. 답변과 연결 판정이 모두 `pass`인 177개 질문. 배치별 P01부터 21·23·31·18·43·27·14개 |
| `data/pass_answers.jsonl` | 허용 질문이 참조하는 고유 답변 32개. 같은 답변을 여러 질문이 공유한다. CSV 쌍 목록은 열람용이다. |
| `data/query_keys_normalizer_v1.csv` | 177행의 원문 및 정규화 해시 대조용. 키는 등록기가 원문에서 다시 계산한다. |
| `corpus/law_articles_v5_decree.csv`, `data/citation_catalog.jsonl` | 검토 기준 1,525행과 편의 색인 54개 조문. 표시 근거는 답변마다 현재 1개이며, 중복 제거 시 31개다. 검토 참고 54개 조문은 감사·현행성 검토에 사용한다. |
| `data/protection/` | v4 분할 7,618행과 보호 해시 3,629행. 개발·출시 시 실제 보호 데이터와 합쳐 검사한다. |
| `results/`, `receipts/`, `provenance/` | 판정과 원문 출처 대조용. `batches/`는 검토 전 입력 이력이다. |

보류 질문 55개와 답변 3개, B001 이후 배치, 평가 질문, 동일 `answer_key`를 공유하는 허용 목록 밖 질문, 신규 생성 QA는 초기 등록에서 제외한다. 답변과 판정 원문은 보존한다. ZIP의 `content_pass_pending_release`, `approval_status=not_approved`, `currency_status=unverified`, `final_rendering_policy_status=unverified`, `exact_enabled=false`, `semantic_enabled=false`는 출시 전 상태다. 177/32는 후보 상한이며 실제 승인 수는 게이트를 통과한 부분집합으로 집계한다.

이번 계획의 구현 범위는 정확 조회와 승인된 정적 QA의 반환이다. 시맨틱 유사도·벡터 인덱스·원자 검색·질문 조건 분류 모델·자동 답변 저장·학습·개인 상담 및 과거/미래 법령 캐시는 범위 밖이다. 후속 단계에는 안정적인 `query_id`·`answer_id`, namespace, 순서 있는 근거와 승인 이력, seed 출처를 넘긴다. `semantic_enabled`는 이번 스키마에서 `false`로 고정한다.

### 읽기 전용 확인 결과

- ZIP SHA-256 `69034f850b39769e0684d8e83b4a1b3653e2b3750bd74d43b37a2a8b43349f59`. `FILE_SHA256.json`의 101개 항목이 ZIP 원본과 일치했다. `audit/package_validation.json`의 패키징 검사는 구조 오류 0, 정규화 키 중복 0, 보호 해시 충돌 0을 기록했다.
- 개발 레포의 `data/v4/split_v4.csv`가 인계 분할과 바이트 단위로 일치했다. 레포 `qa_full_v4.csv`의 후보 질문 원문 177개가 모두 일치했고, 인계 보호 해시 3,629개를 재계산해 대조했다. 아래 8개 개발 보호셋의 정규화 질문과 후보 177개의 교집합은 각 0개였다.
- 인계 법령 CSV와 레포의 `data/v5/law_articles_v5_decree.csv`는 같은 SHA-256 `a379d1193dcf082d6492417c8b3a63ef3d358a0bb9c65aa22f13fcf997b7e540`을 가진다. 답변 32개는 모두 4개 섹션 표제를 갖고 표시 근거 31개는 모두 법률이다. 이 비교의 기준은 인계 자료와 개발 레포다. 출시 시점의 활성 릴리스와 법령 현행성·표시 승인은 별도로 확인한다.
- 개발 레포의 표시용 조문 행과 `answer_format.render(..., fmt='concise', done=True)`로 답변 32개를 메모리에서 대조했다. 모두 원문과 다른 간결형 표시본이 만들어졌다. 30개는 표시 근거와 인용 판정이 일치했고, 2개에는 현행 렌더러의 미확인 인용 경고가 남았다. 이는 표시 호환성 검사다. 이 2개는 초기 승인 대상에서 보류하고, 30개도 운영 코퍼스와 내용·표시 적합성 승인을 별도로 거친다.

## 2. 확인한 연결 지점과 미확인 사항

Python 패키지는 uv workspace의 `services/chatbot/src/lawbot/`에 있다. `pyproject.toml`과 서비스 명세는 Python `>=3.14`를 사용한다. `uv.lock` 기준 FastAPI `0.143.3`, Starlette `1.8.0`, Uvicorn `0.54.0`, AnyIO `4.15.1`, HTTPX `0.28.1`, OpenAI SDK `3.42.0`, pytest `9.1.1`, Playwright `1.64.1`이다. 개발 `.venv`에서 Python `3.14.8`·SQLite `3.53.1`과 위 런타임 패키지 6개의 동일 버전을 확인했다. 개발 uv는 `0.13.6`, Node는 `26.13.0`이다. 챗봇 프런트엔드 `package-lock.json`에는 React `19.3.1`, React Markdown `10.1.0`, Vite `8.3.4`, Vitest `5.0.5`, TypeScript `7.0.2`가 잠겨 있다. 레포에서 `CONTEXT.md`는 발견되지 않았다.

| 영역 | 개발 기준에서 확인한 동작 | 구현 시 연결 |
| --- | --- | --- |
| 웹 요청 | `web.py`의 `create_app()`이 `POST /api/chat`에 `require_json`, 세션 `Who`, `valid_chat()`을 적용한다. `stream()`은 동시 처리 슬롯과 AnyIO 스레드에서 `chat_events()`를 진행하고 연결 해제를 검사한다. | 동일 슬롯 내 `chat_events()`의 첫 검색 이벤트와 `ask()` 앞에 조회를 삽입한다. `service.ask()` 자체는 평가·검색 경로로 유지한다. |
| RAG | `service.py`의 `ask()`·`retrieve()`·`retrieve_decrees()`가 검색·리랭킹·생성을 담당한다. `embed_cache.py`는 조문 벡터의 기존 캐시다. | 캐시 미적중 및 `shadow`에서 기존 호출 인자·질문 원문과 함께 기존 생성기로 진행한다. |
| SSE와 표시 | `web.py`의 `status`, `sources`, `answer`, `stats`, `done`, `error`를 `frontend/src/api.ts`·`sse.ts`·`turns.ts`가 소비한다. `TurnView.tsx`와 `SourcesView.tsx`가 답변·조문 카드를 표시한다. `answer_format.py`의 `render()`는 `full`/`concise`에 따라 다르다. `deploy._profile()`의 v5는 `pro`와 `concise`를 지정하고, `App.tsx`는 서버가 보낸 모델의 형식으로 초기화한다. | 기존 SSE와 카드 페이로드를 재사용한다. v5의 실제 기본 형식인 간결형으로 최종 표시본을 검토한다. |
| 코퍼스 | `service.load_app()`이 DB 모드에서 `corpus_store.active_release()`를 시작 시 읽는다. `LawbotApp`은 `corpus_release_id`·`dataset_version`·`corpus_manifest`·`articles_sha256`를 보유한다. `db/corpus_import.py`의 `release_bytes()`는 두 CSV를 복원하고 해시를 검증한다. `articles_update.py`가 `active_corpus`를 바꾸고 웹앱 재시작을 처리한다. | 시작 시 읽은 불변 원문으로 근거 맵을 만든다. 적중 후보는 실제 활성 릴리스와 로드된 릴리스의 일치까지 확인한다. |
| SQLite | `db/connection.py`는 읽기 전용 연결·FK·writer WAL·`BEGIN IMMEDIATE`를 지원한다. `db/migrate.py`는 `0001_init.sql`·`0002_corpus_csv_format.sql`과 이력 체크섬을 검증한다. 웹앱·조문 갱신의 `startup_path()`는 코드보다 새 스키마도 거부한다. | 전용 캐시 SQLite에 연결·트랜잭션·백업 코드를 재사용한다. 코퍼스 DB는 참조만 한다. |
| 권한·피드백 | `web.py`·`admin.py`에 관리자 권한이 있고, `feedback.py`의 `remember_turn()`이 세션의 턴을 기억해 `POST /api/feedback`에 연결한다. `feedback_store.py`가 append-only 행의 `record` JSON을 보관한다. | 승인·철회는 호스트 권한으로 제한한 CLI에서 시작한다. 피드백은 같은 API와 저장 경로에 출처·리비전만 보강한다. |
| 평가·분할 | `dataset_store.protection_rows()`가 동결 QA 세트를 읽고, `db/dataset_import.py`의 `DATASETS`가 기본 8세트를 정의한다. `qa_rules.norm()`은 구두점 제거·소문자화와 유사 중복 검사에 쓰인다. `lawbot_tools/eval_v4.py`는 `service.ask()`를 직접 호출한다. | 보호셋의 원문과 분할을 재사용해 `normalizer_v1`으로 따로 계산한다. 모델 평가에는 exact 조회와 seed 쓰기 경로를 연결하지 않는다. |

표의 Python 경로는 챗봇 패키지 기준이며, 조문 갱신과 기존 평가 모듈은 `tools/lawbot-tools/src/lawbot_tools/`에 있다. [DB 운영 절차](../operations.md#db-코퍼스와-csv-내보내기) · [이전 DB 전환 기록](../evals/2026-11-08-deploy-corpus-db.md)

### 인계 초안과 현행 레포의 차이

| 초안의 전제 또는 예시 | 이번 구현에서 따를 현행 계약 |
| --- | --- |
| 일반 HTTP 응답 예시와 스트리밍 `answer_delta` 예시 | 인증된 `POST /api/chat`은 `text/event-stream`을 반환한다. 전체 본문은 기존 `answer.text`와 `done.text`에 실으며, 카드와 세션별 피드백도 기존 계약을 사용한다. |
| 모델 서빙 서버를 포함한 추상 요청 경로 | 세션·입력·슬롯이 있는 챗봇 웹앱에 조회를 연결한다. `service.ask()`의 RAG와 평가 경로, `load_app()`의 기존 모델 서버 준비·인덱스 생성 절차를 유지한다. 모델 호출 0회는 준비된 웹앱의 개별 캐시 적중 요청 기준이다. |
| 가이드·노트북의 벌칙 줄과 답변 형식 | 현행 `answer_format`·`citation`·Markdown 표시를 기준으로 판단한다. 초기 형식은 v5 기본 `concise`다. 표시 과정에서 필수 조건이나 예외가 사라지면 후보를 보류한다. |
| 기존 DB에 캐시 테이블을 추가할 수 있다는 선택지 | 현행 `startup_path()`가 코드와 DB의 스키마 버전을 엄격히 확인한다. 기존 코퍼스·피드백 DB를 참조하고 별도 SQLite에 캐시를 저장해 이전 웹앱·조문 갱신기의 코드 복귀를 보장할 수 있도록 검증한다. |
| 업로드 법령 CSV와 v4 분할 중심의 예시 | 승인 대상 버전은 DB의 현재 활성 릴리스와 앱이 로드한 해시로 확인한다. 보호 분할은 현행 동결 QA 및 평가 세트를 합쳐 확인하며 ZIP의 v4는 추가 대조에 사용한다. |

**출시 전 확정할 값**은 현재 운영 릴리스 ID·활성화 이벤트·두 CSV 해시·기준일, 요청별 모델 옵션 및 표시 정책 지문, 실제 보호 분할과 누락 여부, 운영 SQLite 버전과 웹앱 프로세스 수, 승인자·최초 활성 행·관측 기간·허용 지연이다. 과거 운영 확인 기록은 초기 조사 근거로만 사용한다. 이 계획에서는 운영 DB와 `.env`를 조회해 그 값을 확정하는 작업을 수행하지 않았다.

## 3. 정규화, namespace, 저장 구조

### 조회 키와 승인 범위

1. 입력 길이와 공백 검사는 현행 API에서 수행한다. `normalizer_v1`은 NFC → CRLF를 LF로 통일 → 양끝 공백 제거 순서다. 내부 공백·단독 CR·구두점·숫자·부정어·대소문자·호환 문자를 보존하며, 잘라서 합치지 않는다. 같은 함수로 seed·보호 질문·실시간 요청의 정규화 SHA-256을 계산한다. `question_sha256`은 별도로 원문 UTF-8 문자열에서 구한다.
2. 서버가 `corpus_version`, `answer_policy_version`, `normalizer_version`, `domain_scope`, `visibility_scope`, `locale`을 결정한다. 키가 정렬된 JSON의 고정 UTF-8 직렬화(고정 구분자, 개행 없음)에서 `namespace_id` SHA-256을 생성한다. `corpus_version`은 DB 프로필·릴리스 ID·법률/조합 CSV 해시로 전체 스냅샷을 구별한다. `domain_scope`는 프로필과 요청에 유효한 법률·하위법령 범위 및 필터를 구분한다. `visibility_scope=public`은 인증된 사용자가 공유하는 자료 범위이고 언어는 `ko-KR`이다.
3. `answer_policy_version`은 요청의 모델 선택 키와 출력 형식, 실제 적용 system 지침, 결정적 후처리·인용·렌더링 계약을 식별한다. 버전 매핑과 system 지문은 시작 시 로드해 승인 때 기록한다. 생성 모델의 가중치만 바뀌면 이력을 남기고 정책 변경 여부를 검토한다. 정책이 달라지거나 불명확하면 우회한다. 사용자 입력으로 namespace를 지정할 수 없다.
4. 최초 출시 범위는 현행 v5 웹앱의 기본 선택에 맞춰 **DB 코퍼스의 `v5` / `pro` / `concise` 한 범위**로 잡는다. 승인한 형식과 실제 운영 설정이 일치할 때만 적중을 허용한다. `full`, 다른 모델·프로필, CSV 모드 및 미지원 요청은 기존 RAG로 보낸다. 공용 단독 질문만 승인하며 시간 의존·개별 계산·대화 맥락·개인정보 포함 입력을 seed에서 차단한다. 런타임에서 의미 분류를 추가하지 않는다.
5. `(namespace_id, normalized_question_sha256)` 고유 인덱스로 찾고 정규화 문자열을 정확히 비교한다. 원문 해시 일치는 적중 조건이 아니다. 조회 중 발견한 충돌은 우회하며, 등록에서 같은 키에 다른 문자열 또는 다른 답변이 연결되면 충돌을 격리한다.

### SQLite 논리 스키마

기본안은 **전용 로컬 SQLite 파일**이다. 기존 코퍼스 DB에 추가 마이그레이션을 넣으면 구 웹앱과 조문 갱신기의 스키마 검사가 롤백을 막을 수 있고, 캐시 락이 기존 저장소와 경합한다. 별도 파일·마이그레이션 이력을 두되 `lawbot.db.connection`의 경로·연결·트랜잭션 정책과 `sqlite3.Connection.backup()` 기반 백업을 재사용한다. 캐시 파일 경로는 웹 전용 설정으로 받고 개발·테스트·운영 간 분리한다. 캐시 마이그레이션은 CLI가 명시적으로 실행하고 웹앱에는 읽기 권한만 부여한다. 최초 운영 대상에서 코퍼스 DB 스키마 변경은 필요 없다.

| 테이블 | 필드와 제약 |
| --- | --- |
| `cache_namespaces` | PK `namespace_id`, 정렬된 구성 요소, 생성 시각. 사용한 코퍼스 릴리스와 CSV 해시, 정책 계약을 추적한다. |
| `cache_answers` | PK `answer_id`는 불변 리비전. `namespace_id`, `answer_key`, 원문 해시·출처, 검토된 최종 표시 본문·해시, `status`, 승인자·시각·근거, `valid_from`, `valid_until`, `expires_at`. 원문·표시본·근거·정책 변경 시 새 리비전을 만든다. |
| `cache_queries` | PK `query_id`, `namespace_id`, `answer_id`, 질문 원문·원문 해시·정규화 문자열·정규화 해시, 배치·원본 ID·두 분할·manifest 참조, `status`, 승인자·시각·사유, 유효기간, `exact_enabled=false`, `semantic_enabled=false` 기본값. `(namespace_id, answer_id)` 복합 FK로 동일 범위만 참조하고 namespace·정규화 해시는 고유하다. |
| `cache_citations` | PK `(answer_id, position)`, 원본 `article_id`, 법령/조문 번호 매핑, `article_text_sha256`. 순서를 보존한다. 검토 참고 조문 ID는 감사 출처에 분리해 보관한다. |
| `cache_audit_events` | 추가 전용. 시각·행위·주체·대상·이전/새 리비전·사유·입력 manifest 해시·승인 묶음 ID를 남긴다. 등록, 승인, 교체, 철회, 모드 변경, 보호 manifest 변경의 트랜잭션에 함께 기록한다. |
| `cache_control` | 기본 `off`. 현재 모드, 허용된 namespace와 코퍼스 활성화 이벤트, 승인 manifest, 보호 manifest, 변경 세대를 기록한다. 상태가 없거나 불일치하면 반환을 허용하지 않는다. |

쿼리는 바인딩하고 기간은 UTC 기준 `valid_from ≤ now < valid_until`, `now < expires_at`로 검사한다. 승인된 무기한 항목은 명시적으로 상한을 비워 둘 수 있다. 코퍼스의 시행 기준은 기존 파이프라인의 `Asia/Seoul` 기준을 따른다. 짧은 읽기 트랜잭션과 10ms SQLite busy timeout을 사용한다. 잠금 때문에 읽지 못하면 같은 요청을 RAG로 넘긴다. 검증을 마친 적중은 반환하고 전체 조회 지연은 `exact_lookup_ms`로 기록한다. `stream()`의 스레드 실행과 기존 동시성·취소 절차를 활용한다. 적중 시 DB 쓰기나 worker별 답변 복제는 필요 없다.

## 4. 등록, 분할 보호와 승인

### 입력 검사와 draft 등록

1. 입력 경로와 허용할 ZIP/manifest 해시는 설정으로 받는다. ZIP과 각 manifest 경로·파일 해시, JSONL 스키마, 177개 ID·32개 답변 연결, 이중 `pass`, `results/` 및 `source_report_sha256`, 원문·정규화 해시, 답변 본문, `source_snapshot_id`, 표시 근거 배열·순서·텍스트 해시, 비활성 상태를 읽기 전용으로 검증한다. ZIP의 `FILE_SHA256.json`은 내부 일관성 확인용이며, 관리자가 신뢰한 ZIP 지문과 함께 사용한다. CSV는 `utf-8-sig`, JSONL은 UTF-8이다. 과거 교정 제안과 학습 원본은 출처 검증에만 사용한다.
2. 먼저 P01~P07 및 허용 목록으로 제한한 후 원본 ID와 두 분할의 `train` 여부를 확인한다. 다른 데이터셋과 ID가 겹칠 수 있으므로 ID는 출처와 함께 비교한다. 모든 보호셋의 정규화 해시 합집합에서 겹치면 출처가 다른 경우에도 차단한다. 보호 해시 불일치, split 미확정, 소스 누락은 게이트 실패로 처리한다. 공용 범위·단독 질문·시점·개인정보·정상 답변 적격 여부를 승인 체크리스트에 남긴다. `normal` 이외 학습 행, 오류·검색 실패·잘린 답변·거절 학습 행은 제외한다. 채점 점수나 피드백만으로 승인하지 않는다.
3. `--dry-run`에서 쓰기 없이 집계와 행별 전체 사유 및 `primary_reason`을 생성한다. 순서는 무결성/형식 → 범위 → 분할 → 공개·맥락·시점·거절 → 근거 → 키 중복·충돌로 고정한다. 입력 전체가 어떤 결과에 해당하는지 대사한다. 같은 파일을 다시 가져오면 기존 ID·리비전·철회 상태를 유지한다. 같은 질문·답변의 중복은 보고하고, 문자열/답변 충돌은 자동 덮어쓰기 없이 격리한다. 검증된 draft의 질문·답변·근거·감사를 한 트랜잭션으로 저장하고 실패 시 배치 쓰기를 되돌린다.

| 개발 레포에서 확인한 보호 원천 | 초기 대조 결과 |
| --- | --- |
| `data/v4/qa_full_v4.csv` + `split_v4.csv` | 보호 3,629행, 일치 0 |
| `data/decree_exp/qa_decree_v4.csv` + `split_decree_v4.csv` | 보호 2,505행, 일치 0 |
| `data/v5/qa_tax_v5.csv` + `split_tax_v5.csv` | 보호 6,327행, 일치 0 |
| `data/v5/qa_reasoning_val.csv`, `data/oos_eval.csv`, `data/refusal_hard_cases.csv`, `data/scope_controls.csv`, `data/labor_eval_keywords.csv` | 평가 전용 전체 30·102·86·20·650행, 각각 일치 0 |

출시 전에는 실제 동결 QA 세트와 활성 평가·피드백 분할까지 목록을 확정한다. `protection_rows()`에 들어오지 않는 신규/평가 세트는 원본 manifest와 원문에서 합집합에 보탠다. 소스별 지문·행 수·정규화 버전을 `protection_manifest`에 기록하고 import/approve/on에서 재확인한다. 보호셋이 바뀌는 작업은 기존 캐시를 먼저 `off`로 전환하는 절차와 연결하고, 새 보호셋과 겹친 활성 질문을 차단한 뒤 새 manifest로 재검증한다. 분할·질문 원문과 평가 결과는 레포 및 운영 로그에 복제하지 않는다.

### 승인과 철회

- `draft`를 `approved`로 바꾸는 작업은 원본 이중 pass와 별도다. 법령·콘텐츠 승인자가 후보와 연결 질문, 현행 전체 릴리스, 표시·검토 근거, 최종 화면 본문, 공용 범위·유효기간을 검토한다. 본문과 검토 맥락을 보존하며 적합성 확인에 실패한 후보는 보류한다. 활성 해시가 같아도 현재 적용성과 표시 정책에 대한 승인 기록이 필요하다.
- 서빙 승인 묶음은 코퍼스 활성화 이벤트, namespace, 보호 manifest, 질문→답변 리비전과 표시 근거 해시, 승인자·시각·사유를 고정한다. 권한이 검증된 CLI에서 예상 리비전·묶음 세대를 확인하고 선택된 답변·질문을 함께 승인하고 exact 플래그를 바꾼다. 운영 응답 전환은 별도 `on` 게이트에서 수행한다.
- 질문 철회는 해당 연결만 막고, 답변 철회는 그 리비전과 연결된 질문을 같은 트랜잭션에서 차단한다. 철회된 리비전의 재등록으로 상태가 되살아나지 않게 한다. 수정본은 별도 `answer_id`로 만들고 승인 후 질문 연결을 원자적으로 교체한다. 상태·감사·피드백 리비전은 보존한다.
- 제안 CLI 모듈은 `services/chatbot/src/lawbot/cache_admin.py`다. 향후 fish 실행 계약은 `uv run python -m lawbot.cache_admin inspect --bundle "$cache_bundle"`, `uv run python -m lawbot.cache_admin seed --bundle "$cache_bundle" --dry-run`이다. 같은 모듈에서 대상 DB를 명시한 `migrate`·`seed`·`approve`·`revoke`·`set-mode`를 구현하고 위험 상태 변경에는 승인 묶음 ID와 사유를 요구한다.

## 5. 요청, 근거 표시와 버전 전환

### 요청 처리와 SSE

1. `/api/chat`의 JSON·세션·입력·모델/형식 검증과 기존 동시성 제한을 먼저 적용한다. 웹 전용 설정의 기본값은 `off`다. 설정이 `off`면 캐시 DB를 열지 않는다. 기능이 설정되어 있으면 제어 행을 먼저 읽고 `off` 상태에서는 질문 조회를 생략한다. `shadow`는 같은 판정으로 `would_hit`·우회 이유를 남기고 RAG를 한 번 실행한다. `on`은 유효한 승인 묶음의 질문만 반환한다.
2. `on` 후보의 답변·근거·페이로드를 첫 캐시 답변 이벤트 전에 전부 준비한다. 상태/기간/버전/근거가 하나라도 틀리거나 파일 없음·SQLite 락·타임아웃·손상 등 예상 저장소 오류가 나면 사유를 남기고 원래 질문으로 `ask()`를 호출한다. RAG의 코퍼스 자체가 준비되지 않은 경우에는 기존 오류 정책을 따른다. 캐시 전송이 시작된 요청에서 다른 답변으로 중간 전환하지 않는다.
3. 적중 이벤트는 `sources` → `answer {text}` 한 번 → `stats {text}` → `done {text, finish, turn_id}`로 구성한다. 기존 `status`는 대기 안내 등 실제로 수행하는 단계에만 사용한다. 측정 줄은 캐시 조회와 응답 시간을 표시한다. `sources.groups[].items`의 `title`, `body`, `decree`, `scores` 형식을 유지하되 캐시 카드 `scores`는 빈 표시로 보낸다. `done`에 선택적 `request_id`, `response_source`, `cache_answer_id`, `corpus_version`, `answer_policy_version`을 더한다. `response_source`는 `exact_cache`·`rag`를 사용하고 `semantic_cache`는 예약한다. 기존 HTTP 오류·SSE 오류·연결 해제·`Cache-Control` 정책을 유지한다.
4. `turn_id`가 있는 캐시 답변도 `remember_turn()`과 기존 세션 검사를 사용한다. 피드백에 실제 사용자 질문, 표시 본문, 표시 근거, `request_id`, 출처, 코퍼스·정책, 불변 `cache_answer_id`를 넣는다. RAG는 `request_id`로 생성 턴을 추적한다. 기존 DB의 `record` JSON/JSONL에 추가하고 모델 호출 정보는 실제 호출이 없었음을 표시한다. 관리자 검수에서 출처를 구분하고, 캐시 피드백을 기존 모델 원문 전용 승격·학습 export의 자동 입력에서 제외한다. 사용자 원문은 기존 피드백 정책으로 보호한다.

### 근거 원문과 후처리

- DB 릴리스의 검증된 CSV 바이트에서 표시용 원문 맵을 한 번 만든다. ZIP의 법령을 포함한 `article_id`를 보존하고, 화면 카드의 `law`와 조문 번호·제목·종류로 명확히 매핑한다. 중복·삭제·누락·지원 범위 밖인 행은 실패다. `Retriever`가 `article_id`를 번호로 바꾸고 `docs`에 제목을 붙이므로, 그 배열을 그대로 본문 해시 검증에 쓰지 않는다. `article_versions.content_sha256`도 여러 열의 해시다. 비교 대상은 원본 CSV `text` 문자열 그대로의 UTF-8 SHA-256이다.
- `display_citation_article_ids`의 순서대로 모든 조문을 같은 요청 스냅샷에서 검증한다. `review_evidence_article_ids`는 승인·감사용이다. 카드는 한 개 또는 여러 개를 지원한다. 검색 결과를 보충하거나 순위를 합성하지 않는다. 기존 RAG의 3개 법률 카드는 RAG 브라우저 테스트에서 계속 검증한다.
- 승인할 스냅샷의 표시 조문을 사용해 원문에 `answer_format.render(..., fmt='concise', done=True)`를 한 번 적용한다. 현행 `concise()`는 결론과 인용 조문을 남기므로 설명·참고에 있는 조건, 예외, 벌칙 등 답변에 필요한 내용이 빠지는지 확인한다. 인용 경고가 남거나 본문 인용과 승인할 카드가 어긋나거나 필수 내용이 빠지면 해당 후보를 보류한다. 인계 원문을 편집하거나 `full`로 바꿔 보내는 방식으로 통과시키지 않는다. 승인 단위는 표시본 바이트·근거 배열·정책 지문이다. 승인된 표시본은 그대로 전송하고 `TurnView`의 기존 Markdown·링크 안전 처리를 거친다.

### 코퍼스 전환과 동시성

- `load_app()`의 불변 코퍼스와 승인 정책을 요청 단위로 고정한다. 첫 캐시 이벤트를 보내기 직전에 코퍼스 DB의 `active_corpus`와 해당 프로필의 마지막 `activated`/`rolled_back` 이벤트 ID를 한 읽기에서 대조한다. 새 캐시 읽기 트랜잭션에서 제어 행·승인·리비전·기간을 확인한 후, 코퍼스 활성화 이벤트를 다시 읽어 앞선 값과 일치하는지 검사한다. 코퍼스 읽기 두 번은 서로 다른 SQLite 읽기 스냅샷을 사용한다. 둘 다 일치하면 캐시의 최종 상태 읽기 시점을 판정 경계로 삼는다. 경계 뒤에 이미 확정된 요청은 같은 본문·근거·버전으로 끝낼 수 있고, 철회 커밋 뒤의 새 판정은 구 답변을 보낼 수 없다. 이 경계는 동기화된 통합 테스트로 검증한다.
- 입법예고·장래 시행 데이터는 스테이징 상태로 분리한다. `active_corpus`가 바뀌면 이전 namespace를 신규 요청에 쓰지 않는다. DB 전환과 웹앱 재시작 사이에 릴리스가 다르면 캐시를 닫고 기존 웹앱의 RAG·재시작 정책을 따른다. 새 프로세스는 현재 활성 릴리스를 검증해 로드한 뒤 해당 버전의 승인 묶음이 있을 때만 캐시를 허용한다. 데이터·정책·정규화·범위가 바뀌면 새 namespace를 만들고 재검증·재승인한다. 조문 일부가 같아도 예전 답변과 새 근거를 섞지 않는다.
- 롤백이 같은 릴리스 ID를 다시 활성화해도 새 활성화 이벤트를 사용하므로 옛 승인 묶음이 저절로 반환되지 않는다. 만료·철회는 버전 확인과 별개로 읽는다. 시간 의존 답변의 시행일·적용례 해석은 기존 법령 데이터 파이프라인을 따른다.

## 6. 구현 순서와 요구사항 추적

| 단계 | 선행 | 작업 | 완료 조건 |
| --- | --- | --- | --- |
| E1 | 구현 착수 승인 | 개발 HEAD·CI·런타임 재확인. `web.py`, DB 코퍼스, v5 기본 간결형·피드백의 기준 동작을 고정한다. 인계 초안의 기술 전제와 달라진 항목을 §2에 반영한다. | 현행 SSE와 `off` 기준선, 설정·운영 미확인 값의 확인 담당자가 기록됨 |
| E2 | E1 | `lawbot.exact_cache`, `lawbot.cache_store`, `lawbot.db/cache_migrations`에 정규화·키·저장소·버전·상태 모델을 TDD로 구현한다. | 복합 키/외래 키·만료 경계, 병렬 읽기/쓰기·마이그레이션 재실행과 rollback 실패 경로 통과 |
| E3 | E2 | seed 입력 검사, 보호 manifest, dry-run/draft와 승인·철회 CLI, 근거 원문 맵과 표시 정책 검사를 추가한다. | 177/32 대사, 충돌/변조/보호 차단, 멱등 import·불변 리비전, 승인 없이 반환 0 |
| E4 | E2·E3 | 웹 요청에 `off/shadow/on`, SSE·피드백·요청 로그를 연결한다. | `off`와 기존 RAG 동등, 대역 E2E에서 적중의 4단계 호출 0, 미적중 RAG·카드·종료·피드백 정상 |
| E5 | E4 | 조문 전환·재시작·철회·장애·모드 변경을 통합 검증하고 같은 입력으로 성능을 측정한다. | E-T01~20 및 기본 테스트와 CI 검사 Green, 백업·복구 리허설, 측정/제한 기록 |
| E6 | E5 및 운영 반영 승인 | 운영 스냅샷·정책·보호셋 대조, draft, 별도 승인, shadow 관찰 후 제한 `on`. | 승인 범위·결과·복구 절차와 실제 지표를 운영 기록에 남김 |

| 요구사항 | 설계 대응 | 검증 |
| --- | --- | --- |
| EX-001 | §3–4의 검증·draft·승인·불변 리비전·감사 | E-T05, 09–12, 17 |
| EX-002 | §3의 `normalizer_v1`·서버 namespace·문자열 최종 비교 | E-T01–04, 14, 20 |
| EX-003 | §5의 승인 표시본과 같은 스냅샷의 순서 있는 근거 | E-T01, 06, 16 |
| EX-004 | §3·§5의 코퍼스/정책/정규화 버전과 활성화 이벤트 | E-T04, 07, 08, 18 |
| EX-005 | §5의 승인·기간·근거·장애 실패 시 기존 `ask()` | E-T03, 05, 06, 13, 20 |
| EX-006 | §5의 기존 HTTP/SSE·렌더러·피드백 | E-T14–16, 20 |
| EX-007 | §8의 요청 경로·우회 이유·단계별 지연/호출·적중률 | E-T01, 13, 15, 16 |
| EX-008 | §4의 분할/정규화 해시 보호 및 §8의 모델 평가 분리 | E-T11, 12, 19 |
| EX-009 | §4·§5·§9의 철회·`off`·롤백 | E-T13, 15, 17, 18 |
| EX-010 | §1·§3의 공통 식별자·근거·namespace·별도 승인 상태 | E-T10, 17에서 리비전 추적과 `semantic_enabled=false` 검증 |

| 데이터 불변 조건 | 등록 기준 | 운영 기준 |
| --- | --- | --- |
| EX-D01 복합 고유 키·충돌 격리 (§3–4) | EX-S01 단독 질문 심사 (§4) | EX-O01 로컬 전용 DB (§3) |
| EX-D02 동일 namespace 복합 FK (§3) | EX-S02 현행 스냅샷과 질문 연결 승인 (§4) | EX-O02 파라미터 바인딩 (§3) |
| EX-D03 `semantic_enabled=false` (§1·§3) | EX-S03 구조화한 표시 근거 확인 (§4–5) | EX-O03 권한이 검증된 CLI (§4) |
| EX-D04 새 답변 리비전 및 기존 피드백 보존 (§4–5) | EX-S04 공개 범위 및 개인정보 검토 (§4) | EX-O04 읽기·락 대기 예산 (§3) |
| EX-D05 여러 질문의 동일 답변 FK (§3) | EX-S05 시점 의존·실시간·개별 계산 제외 (§3–4) | EX-O05 기존 스트림 스레드에서 처리 (§3·§5) |
| EX-D06 법령 포함 ID (§5) | EX-S06 오류·실패·잘린 답변 제외 (§4) | EX-O06 실패 사유와 기존 RAG (§5·§8) |
| EX-D07 위치가 있는 근거 배열 (§3·§5) | EX-S07 `normal` 이외 학습 행 제외 (§4) | EX-O07 질문·답변 원문 로그 제외 (§8) |
| EX-D08 요청 시 상태·기간·철회 검사 (§3·§5) | EX-S08 채점과 승인 분리 (§4) | EX-O08 질문 해시 접근 제한 및 로그 제외 (§4·§8) |
| EX-D09 원본·분할·승인 출처 추적 (§3–4) | EX-S09 draft → 명시적 approved (§4) | EX-O09 SQLite 온라인 백업과 무결성 검사 (§9) |
| EX-D10 질문·답변·근거·감사 원자적 반영 (§4) | EX-S10 피드백과 공용 승인 분리 (§4–5) | EX-O10 기존 Markdown 안전 정책 (§5) |

## 7. 수용 테스트 E-T01~E-T20

아래는 실행할 테스트다. 각 실행은 테스트 ID·입력 fixture/manifest·기대·실제·결과를 남긴다. Red → Green → Refactor 순서로 해당 테스트를 만들고, 임시 DB 및 합성 대역 fixture로 버전과 오류를 제어한다.

| ID | 입력과 층 | 판정 |
| --- | --- | --- |
| E-T01 | 승인 질문 원문, `on` · 계약/대역 E2E | 승인 표시본·근거 카드와 `done`, 질문 임베딩·벡터 검색·리랭커·LLM 각각 0회 |
| E-T02 | 앞뒤 공백·한글 NFC/NFD·CRLF 변형 · 단위 | 저장된 질문과 일치하며 원문 해시는 달라질 수 있음 |
| E-T03 | 물음표·내부 공백·숫자·부정어 변경 · 단위/통합 | 미적중 및 원문 그대로 RAG. 대소문자·호환 문자도 보존 |
| E-T04 | 같은 질문의 다른 코퍼스/정책/정규화/범위/언어 · 단위/통합 | 교차 namespace 조회 차단 |
| E-T05 | 질문/답변 미승인·비활성·철회·만료 및 시각 경계 · 단위 | 시작 경계 포함, 유효 종료/만료 경계 제외. 거부 사유 확인 |
| E-T06 | 다중 근거 중 하나 누락·삭제·중복 또는 본문 해시 변경 · 통합 | 캐시 SSE 전송 전 전부 거부, RAG |
| E-T07 | 같은 질문이 있는 구·신 릴리스, 활성화와 앱 재시작 사이도 재현 · 통합 | 전환 직후 구 답변을 반환하지 않음. 로드와 승인이 일치하면 신 버전만 적중 |
| E-T08 | 입법예고·장래 시행 릴리스 스테이징 · 통합 | 활성 이벤트와 namespace 유지, 현재 응답에 신규 자료 혼입 0 |
| E-T09 | 같은 정규화 키의 서로 다른 답변, 강제 해시 충돌 · 단위/통합 | 충돌 보고·격리, 기존 행 덮어쓰기 0, 충돌 문자열 조회 우회 |
| E-T10 | 같은 파일 반복 등록, 중간 실패 및 재시도 · 통합 | ID·행 수·철회 유지, 부분 승인/중복 생성 0 |
| E-T11 | `emb`/`sllm` 한쪽만 val/test, 다른 ID 같은 질문, 분할 누락·변경 · 통합 | 전부 차단하고 사유를 기록. 보호 manifest 갱신 전 출시 불가 |
| E-T12 | 거절 학습 행 및 허용 목록 밖·보류·B 배치 입력 · 단위/통합 | 등록 거부 및 제외 사유 확인 |
| E-T13 | DB 부재·읽기 실패·스키마 오류·락·시간 초과 · 통합/대역 E2E | 제한 시간 안에 이유 기록, RAG 한 번. RAG도 불능이면 기존 오류 이벤트 |
| E-T14 | 일치 질문의 미인증/권한 밖 요청·잘못된 HTTP 입력 · 통합 | 기존 401/검사 응답, DB 조회 및 캐시 본문 노출 0 |
| E-T15 | 같은 질문을 `off`·`shadow`·`on`에 전송 · 계약/통합 | `off` 조회 0, `shadow`는 `would_hit`와 RAG, `on`만 반환·호출 절감. `off`/miss 반복으로 자동 저장되지 않음 |
| E-T16 | 로그인 후 v5 기본 간결형의 답변·카드·측정 줄·피드백 · 대역 E2E | 승인 간결형과 카드가 표시되고 기존 SSE 파서, 모바일 조문 UI, 종료·세션 `turn_id` 정상. 연결 해제와 실패 경로도 회귀 확인 |
| E-T17 | 답변 교체와 동시 철회/조회, 이전 턴의 피드백 · 통합 | 커밋 후 조회는 신 승인 리비전 또는 RAG. 이전 피드백의 ID·근거 유지 |
| E-T18 | 시행 시각 중 정지 뒤 재시작, 코퍼스 롤백 · 통합 | 활성 확인 전 캐시 금지. 동일 릴리스 재활성화에도 구 승인 자동 재사용 0 |
| E-T19 | 모델 평가 진입점과 서비스 모드를 별도 실행 · 단위/계약 | `eval_v4`가 RAG 직행, 캐시 조회·seed 추가 0, 기존 채점 유지 |
| E-T20 | `full`·다른 모델 요청, 미확인 인용·필수 정보가 빠진 표시본, 시점·맥락 의존 입력 · 단위/통합 | 형식 오류는 기존 HTTP 응답, 미승인 범위는 RAG. 표시본이 부적합한 후보는 승인 보류, 부적격 seed는 등록 거부 |

기존 `test_web.py`, `test_db.py`, `test_web_integration.py`, `test_corpus_from_db.py`, `test_articles_update_db.py`, `test_feedback.py`, `test_eval_v4.py`, `test_e2e_stub.py`의 실행 방식과 프런트엔드 Vitest를 사용한다. 기본 실행은 단위·계약·통합·대역 E2E를 포함한 `uv run pytest`, `ruff check`·`ruff format --check`·`pyright`·`compileall`, CI의 프런트엔드 타입 검사·Vitest·빌드와 캘린더 검사를 따른다. 대역 E2E는 실제 웹앱과 모델 서버 앱을 사용하고 모델만 3개 호출 모두 대역으로 바꾼다. 실제 모델 E2E·모델 스모크·품질 평가는 이번 계획 작성에서 실행 안 함이며 구현 검증의 기본 실행에서도 제외한다. 향후 필요 시 별도 승인과 요청 수·시간 상한·운영 영향 확인 절차를 따른다. [검증 명령](../operations.md#테스트-실행)

## 8. 관측과 결과 기록

`service.request_record()`·`log_request()`의 기존 JSONL 계약을 확장하고 `lawbot_tools.log_summary` 집계에 경로를 구분한다. 웹 요청에서 `request_id`를 먼저 정하고 RAG의 기존 기록에도 동일 ID를 전달해 중복 집계를 피한다. `cache_mode`, `response_source`, `eligible`, `would_hit`, 조회·완료·취소 상태, 사유 코드, 버전·정책과 실제 단계별 호출 수를 기록한다. 이유는 `no_match`, `namespace_mismatch`, `inactive`, `expired`, `citation_mismatch`, `protected_manifest_mismatch`, `db_timeout`, `db_error`처럼 제한된 값으로 관리한다. 시작 기준의 검색·리랭커 지연과 캐시 조회 지연은 별도 필드다. 운영 로그에는 질문·답변·질문 해시·개인 정보를 기록하지 않는다. 지표 수집 실패로 RAG 응답을 막지 않는다.

- `N`은 인증·정상 입력 검사를 통과한 요청 수다. 전체 적중률은 실제 exact 반환 수 / `N`, 적격 내 적중률은 같은 반환 수 / 적격 요청 수, LLM 호출 감소율은 `1 - 실험 실제 LLM 호출 / 동일 요청 묶음의 off 실제 LLM 호출`로 계산한다. 분모가 0이면 `N/A`다. `would_hit`, 승인 후보 수, 고정 질문 반복 측정은 운영 반환 수와 분리한다.
- 같은 요청 manifest·장비·코퍼스·모델 대역·동시성으로 `off`와 `on`을 비교하고 `shadow` 비용도 따로 잰다. 초기/준비 상태와 hit/miss 집단을 나눠 표본 수·평균·p50·p95를 기록한다. `exact_lookup_ms`는 정규화/키부터 승인·근거·최종 버전 확인까지, `request_to_first_answer_ms`는 요청 수신부터 첫 `answer`, `request_to_complete_ms`는 근거와 `done` 전송 완료까지다. 브라우저 완료 시간은 별도다. 미적중 추가 비용은 동일 입력의 `on` miss와 `off`를 짝지어 잰다.
- 적중 임베딩·검색·리랭킹·LLM 0회와 승인/버전/권한/근거 차단 실패 0건은 필수다. 준비 상태 조회 및 미적중 추가 p95 각각 50ms 이하는 초기 목표다. 초과 시 락·DB 조회·근거 복원·로깅 비용을 분리해 측정하고 출시 여부를 결정한다. 운영 적중률의 최소 목표는 실측 전 정하지 않는다.
- 모델 평가는 캐시 OFF 경로에서 기존 지표를 유지한다. 서비스 평가를 별도 run과 seed manifest로 기록하고 cache hit를 실시간 검색 적중으로 집계하지 않는다. 설정 선택은 val에 한정하고 보호된 평가 입력은 캐시 보강에 사용하지 않는다.

| 실행 기록 | 기록할 값 | 현재 |
| --- | --- | --- |
| 환경·재현 | `run_id`, 시각, commit, Python/SQLite/uv/FastAPI/OS/장비/worker, 동시성, 설정 지문, 코퍼스와 정책·정규화·스키마 버전 | §2의 개발 버전만 확인 |
| 등록·제외 | seed/보호/승인 manifest 지문. `input_rows`, `allowed_train_rows`, `excluded_split_rows`, `excluded_context_rows`, `excluded_time_sensitive_rows`, `excluded_privacy_rows`, `excluded_refusal_rows`, `missing_citation_rows`, `duplicate_same_answer_rows`, `conflicting_answer_rows`, `approved_query_rows`, `approved_answer_rows` | 패키지 후보 177/32, 운영 승인 미확정 |
| 모드별 트래픽·모델 | 모드, `N`·적격 수·실제 hit·`would_hit`, 우회 사유, 4단계 실제 호출 수, 적중률·LLM 감소율 | 미측정 |
| 모드별 지연 | `exact_lookup_ms`, 첫 답변·완료·브라우저 완료, miss 추가 비용, 표본 수·평균·p50·p95 | 미측정 |
| 안전과 회귀 | E-T01~E-T20 통과/전체, 차단 실패 수, 철회·버전 전환·SSE·장애 우회, CI 및 롤백 리허설 | 미실행 |

실행별 임시 결과는 `seed_summary.json`, `seed_exclusions.csv`, `exact_cache_test_results.csv`, `exact_cache_benchmark.json`으로 분리한다. CSV는 `utf-8-sig`로 쓰고 보호 원문은 포함하지 않는다. 계측용 `lawbot.cache_eval`의 실제 명령은 §10을 따른다. 결과는 `docs/evals/YYYY-MM-DD-exact-cache-*.md`와 목록에 남기고, 중간 파일은 정리한다.

## 9. 출시 게이트와 롤백

| 게이트 | 작업과 통과 조건 |
| --- | --- |
| G0 개발 완료 | E1~E5 완료, 전체 기본 테스트·필수 CI·데이터 무결성 통과. 제어 DB 손상과 코드 롤백을 개발 복사본에서 리허설한다. 별도 운영 반영 요청이 있어야 다음으로 간다. |
| G1 운영 `off` | 승인한 커밋을 운영 절차로 반영한다. 조문 자동 갱신 시간대를 피하고 기존 상태를 확인한다. 전용 캐시 DB를 명시적으로 초기화·마이그레이션하고 검증된 후보만 draft로 가져온다. DB/웹앱 점검과 기존 RAG 경로를 확인한다. |
| G2 승인 묶음 | 운영 담당자가 실제 활성 릴리스와 앱 로드 버전, 법령 현행성, 최신 보호 분할, v5 기본 간결형 계약을 대조한다. 법령·콘텐츠 승인자가 질문 연결과 최종 화면을 승인한다. 인용 경고나 필수 내용 누락을 해결하지 못한 항목은 보류하고 활성 가능 수와 차단 사유를 기록한다. 승인과 등록으로 실사용 모드가 전환되지 않는다. |
| G3 `shadow` | 승인된 후보에 대해 실제 반환 없이 `would_hit`·실패 이유·비용을 관찰한다. 표본 수·관측 기간·허용 지연은 운영 담당자가 시작 전에 정한다. 지연과 RAG 오류/취소율이 허용 범위에 있고 실제 exact 반환 0건, 보호·권한·버전 위반 0건을 확인한다. |
| G4 제한 `on` | 사용자가 승인 묶음과 전환 시점을 확인한 후, 한 namespace 안의 승인된 질문 일부 또는 전부를 명시해 켠다. 운영 담당자는 반환 수, 카드·피드백, 실제 호출 절감, 버전·오류·지연을 확인한다. 후보 범위 확대는 별도 검토다. |

1. 잘못된 법령·권한·철회 답변·근거 누락이 한 건이라도 확인되거나 지속적인 캐시 오류/지연 회귀가 있으면 우선 제어 행을 `off`로 바꾼다. 특정 리비전 문제는 해당 답변과 연결 질문을 철회한다. 새 판정에서 exact 반환 0, 기존 RAG 응답·오류 계약과 현재 코퍼스 유지 여부를 확인한다.
2. 제어 파일이 잠겼거나 접근할 수 없으면 조회 실패 자체가 RAG 우회로 이어진다. 웹앱이 조회를 반복하지 않게 할 때는 시작 설정을 `off`로 바꾸고 승인된 운영 절차로 웹앱을 재시작한다. 코드 복귀가 필요하면 승인된 이전 운영 커밋으로 되돌린다. 코퍼스·피드백 DB와 모델 서버는 별도의 운영 절차를 사용하며 캐시 롤백으로 조문 버전을 되돌리지 않는다.
3. 캐시 DB 복구는 SQLite 온라인 백업과 무결성 확인을 거친 별도 경로에서 수행한다. 옛 백업에 담긴 승인·모드 상태로 서비스를 시작하지 않는다. 사고 시점의 철회·감사 기록을 대사하고 현행 스냅샷·보호·정책을 재검증한 뒤 `off`부터 게이트를 다시 밟는다. 누가 언제 무엇을 전환했는지, 반환 중단 확인과 재발 방지 테스트를 기록한다.

## 10. 구현 명령과 개발 기록

웹앱에서 `LAWBOT_EXACT_CACHE_DB`를 지정하면 독립 캐시 DB를 읽는다. 새 DB의 제어 행은 `off`이며, 설정이 비어 있으면 캐시 DB를 조회하지 않는다. CLI는 기존 `LAWBOT_PROFILE=v5`, `LAWBOT_CORPUS=db`, `LAWBOT_DB` 설정에서 코퍼스와 보호 분할을 읽는다. 캐시 DB와 검토 출력은 개발·운영별로 분리한 비공개 로컬 경로를 사용한다.

아래 fish 명령에서 `$cache_db`는 전용 캐시 DB 경로, `$cache_bundle`은 원본 ZIP 경로, `$review_dir`는 새 비공개 검토 폴더다. 운영에서는 §9의 승인과 단계에 따라 실행한다.

```fish
uv run python -m lawbot.cache_admin init --db "$cache_db"
uv run python -m lawbot.cache_admin seed --db "$cache_db" --bundle "$cache_bundle" --dry-run --report-dir "$review_dir"
uv run python -m lawbot.cache_admin seed --db "$cache_db" --bundle "$cache_bundle"
```

`review.json`의 `candidates`에는 질문, 표시본, 근거 키와 해시가 들어 있다. 승인자는 질문별 적용 조건, 법령 현행성, 화면에서 빠진 조건·예외 및 공개 적합성을 대조하고 선택된 `query_id` → `answer_id`만 `selection`에 넣는다. 현행 DB 밖의 추가 보호셋은 `question` 열을 갖는 CSV로 `--protected-csv`를 반복 지정한다. 검토·등록·승인·모드 전환에 같은 보호 입력을 사용한다. 보호 범위가 바뀌기 전에 `off`로 전환한 후 다시 대조한다.

```fish
uv run python -m lawbot.cache_admin approve --db "$cache_db" --bundle "$cache_bundle" --review-file "$review_dir/review.json" --reviewed --reason "$reason"
uv run python -m lawbot.cache_admin set-mode shadow --db "$cache_db" --bundle "$cache_bundle" --approval "$approval_id" --reason "$reason"
uv run python -m lawbot.cache_admin set-mode on --db "$cache_db" --bundle "$cache_bundle" --approval "$approval_id" --reason "$reason"
uv run python -m lawbot.cache_admin set-mode off --db "$cache_db" --reason "$reason"
uv run python -m lawbot.cache_admin revoke answer "$answer_id" --db "$cache_db" --reason "$reason"
uv run python -m lawbot.cache_admin backup --db "$cache_db" --out "$backup_path"
```

같은 namespace에서 승인 질문 하나의 답변을 정정하려면 캐시를 `off`로 전환한다. 비공개 JSON 검토 파일에 `review.json`의 `namespace_id`, `release_id`, `activation_event`, `protection_hash`를 옮기고 `query_id`, `previous_answer_id`, 정정 `answer_raw`, 검토한 간결형 `answer_shown`, 순서 있는 `citation_article_ids`를 적는다. 기존 질문과 답변 리비전이 일치하는지, 표시본이 현행 렌더러 및 근거와 일치하는지 확인하고 아래 명령을 실행한다. 한 답변을 공유하던 다른 질문은 각 연결을 따로 검토한다.

```fish
uv run python -m lawbot.cache_admin replace --db "$cache_db" --bundle "$cache_bundle" --approval "$approval_id" --review-file "$correction_file" --reviewed --reason "$reason"
```

교체는 기존 질문의 유효기간을 유지하면서 새 불변 답변과 질문 연결을 한 트랜잭션에서 저장한다. 이전 답변과 피드백은 기존 리비전을 가리킨다. 교체가 반환한 새 승인 ID로 `shadow`를 확인한 후 `on`으로 전환한다. 원본 ZIP의 동일 행을 다시 seed해도 교체한 연결은 유지한다. 같은 namespace에서 정정 리비전을 재승인할 때는 최신 검토 기준으로 정정 파일을 대조하고 `approve --correction-file "$correction_file"`을 지정한다. `selection`은 현재 연결된 정정 답변 ID를 가리킨다.

계측은 동일 순서의 케이스 ID JSON 배열과 웹 요청 JSONL을 `uv run python -m lawbot.cache_eval exact --cases "$cases" --off "$off_log" --on "$on_log"`에 전달해 비교한다. off 기준선은 동일 웹앱에서 캐시 DB의 제어 모드를 `off`로 두고 기록한다. 설정·동시성·워밍업 조건을 함께 기록한다.

초기 import는 원본 P07 ZIP의 해시에 고정한다. 개발 코퍼스의 간결형에서 173개 질문·30개 답변이 draft 적격이고, 미확인 인용 경고에 연결된 질문 4개가 보류됐다. 보호 분할 제외는 0개였다. 실제 내용·표시 승인은 별도다. [개발 검증 기록](../evals/2026-11-09-exact-cache-p07.md)
