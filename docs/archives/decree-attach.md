# 작업 계획: 시행령·시행규칙(하위법령) 첨부를 웹앱·모델 서버·평가에 반영

- 작업 브랜치: `feat/decree-attach` (`exp/decree-filter` 87b8ec3에서 분기). PR은 `main` 대상, 병합은 사용자
- 작성: 2026-10-31
- 원칙: 실험(`decree_filter`·`build_decree_ctx`·`eval_decree`)에서 정한 방식과 규칙 `same1+other0.9`를 그대로 옮긴다. 새 설정을 고르지 않는다. test 분할은 쓰지 않는다

---

## 0. 확인한 사실 (설계의 근거)

| 항목 | 값 | 확인 방법 |
| --- | --- | --- |
| 812 CSV의 법률 415행 | `articles_v4.csv` 비삭제 415행과 `law`·`article_no`·`title`·`text`가 같고 순서도 같음 | pandas 비교 |
| 812 CSV 순서 | 비삭제 기준 0~414 법률, 415~811 하위법령 (`decree_filter`의 j와 같음) | `decree_filter.load` |
| 문서 토큰 (`doc_tokens.csv`) | 법률 최장 2,088(임금채권보장법 제23조) < 2,304. 하위법령 중 2,304 초과는 임금채권보장법 시행령 제24조(3,017) 하나 | CSV |
| 리랭커 `MAX_LENGTH` 2,560 | 위 시행령 제24조는 리랭커에서 잘림. MLX 실험 점수(`mlx_decree_scores`)도 같은 `Reranker`로 잘린 채 채점 → 그대로 둠 | `reranker.py` |
| 실험 점수 파일 | `data/decree_exp/*` 전부 Git에 있음 → CI에서 필터 재현 테스트 가능 | `git ls-files` |
| 실험 값 (fp16, k=2) | dq_recall 0.921, law_other_ratio 0.055 (`filter_rules.csv`, 첨부된 하위법령 중 다른 법 비율) | CSV |
| 기본 테스트 | **359 passed, 3 deselected** (요청서의 138개와 다름: 파라미터화 포함 수로 보임. 359개를 그대로 통과시킨다) | `uv run pytest` |

---

## 1. 열린 설계 질문에 대한 선택

### Q1. 812조문 매니페스트 → **같은 폴더에 `manifest_*.json`을 더 두고, `articles_file`로 고른다**

- 새 파일 `data/v4/manifest_decree.json` (`articles_file: law_articles_v4_decree.csv`, `articles_total` 812, `articles_by_law` 21개 법령명, `articles_by_kind` {법률 415, 시행령 273, 시행규칙 124}, sha256, `trained_on` {embedding v4, fast_lora v4}).
- `manifest.load(articles)`: 폴더의 `manifest.json`, 그다음 `manifest_*.json`(이름순)을 보고 `articles_file == articles.name`인 것 하나를 돌려준다. 둘 이상 맞으면 오류(어느 쪽을 믿을지 모름). `path_for`는 찾은 파일, 없으면 기존처럼 `manifest.json` 경로(안내 문구용).
- 기존 `data/v4/manifest.json`·`data/manifest.json`은 **한 글자도 바꾸지 않는다** → 하위법령을 끈 운영은 데이터 버전·sha256·`/info` dataset 값이 지금과 같다.
- 테스트: `test_decree_csv_kinds`는 "아직 매니페스트 없음(None)"을 단언하므로, "`manifest_decree.json`이 로드되고 verify가 모두 OK"로 바꾼다(이 테스트 하나만 의미가 바뀜). 나머지 매니페스트 테스트는 그대로.
- 다른 안(`data/v4_decree/` 폴더): 812 CSV를 옮기거나 복사해야 해서 실험 코드(`DATA/'v4'/'law_articles_v4_decree.csv'` 하드코딩 5곳)와 기존 결과 경로가 깨지거나 같은 데이터가 두 벌 생김 → 택하지 않음. 매니페스트 하나에 여러 파일을 넣는 안은 `/info` dataset 비교·`describe` 형식까지 바뀌어 범위가 커짐 → 택하지 않음.
- `dataset_version` `v4-decree`, `reference_date`·`collected_date` 2026-10-20 (사용자 확인, 법률과 같은 Legalize KR 수집일).

### Q2. 하위법령 리랭킹 API → **기존 `/rerank`를 그대로 두 번 호출. `/search`에 `kind` 필드만 추가. 묶지 않음**

- 필터는 웹앱 쪽 `service`가 한다. 모델 서버는 지금처럼 검색·리랭킹 "부품"만 준다 → 로컬·원격이 **같은 함수**로 첨부를 고르므로 동등성이 구조로 보장된다. 새 `/retrieve` 같은 묶음 엔드포인트를 두면 필터 로직이 서버로 옮겨 가고, 설정 비교·테스트 대상이 늘어난다.
- `/rerank`는 이미 후보 dict를 복사해 `rerank_score`·`emb_rank`만 붙이므로 하위법령 dict(`kind`·`parent_law` 포함)도 그대로 통과한다.
- 묶지 않는 이유: 리랭커는 후보마다 batch 1로 계산(노트북과 같게)하므로 묶어도 연산 시간은 같다. 줄어드는 것은 127.0.0.1 HTTP 왕복 2회(ms 단위)뿐이다. 하위법령 리랭킹 ~2초에 비해 무시할 수 있다.
- 순서: 법률 검색 → 법률 리랭킹 → (그 모델이 첨부 대상일 때만) 하위법령 검색 → 하위법령 리랭킹 → 필터. 필터만 법률 1위를 쓰므로 하위법령 검색은 법률과 독립.
- API 변경 → `API_VERSION` 1 → **2** (`model_server.py`·`remote.py` 둘 다). 변경점:
  - `POST /search`: 선택 필드 `kind` (`'law'` 기본 / `'decree'`). 하위법령 인덱스가 없는 서버에 `decree`면 400.
  - `GET /articles`: 812조문(법률 먼저, 하위법령 뒤). 새 필드 `kinds`·`parent_laws`(법률 행은 kind '법률', parent_law = law). 하위법령을 끈 서버는 지금처럼 415.
  - `GET /info`: `decree` 필드. 꺼짐이면 `null`, 켜짐이면 `{tau, k, candidates, max_seq, n_articles}`.
- 웹앱 시작(원격): `check_info`에 하위법령 항목 비교 추가. 켜짐 여부·τ·k·후보 수가 다르면 시작 거부. 버전 1 서버에는 api_version 불일치로 거부(기존 동작).

### Q3. 하위법령 인코딩 길이 → **하위법령만 따로 3,072. 프로필 `max_seq`(2,304)는 그대로**

- 법률 415개를 **지금과 같은 문서 목록·같은 호출(`encode(docs_law, batch_size=4)`)·같은 `max_seq_length` 2,304**로 따로 인코딩하고, 하위법령 397개를 `max_seq_length` 3,072로 따로 인코딩한다(같은 SentenceTransformer 객체, 모델은 한 번만 올림).
- 812개를 한꺼번에 3,072로 인코딩하면 법률 문서가 잘리지는 않지만(최장 2,088) 길이 정렬 뒤 batch 구성과 패딩 길이가 바뀌어 MPS fp16 값이 미세하게 달라질 수 있다. 따로 인코딩하면 법률 임베딩은 지금과 **비트 단위로 같아야** 한다 → 법률 3개의 순위·점수 회귀 보장.
- 확인: (모델 없이) 가짜 SentenceTransformer로 법률 encode 호출 인자(문서 목록·max_seq)가 하위법령 끈 경우와 같음을 테스트. (맥미니) 새 일회성 스크립트 `lawbot.verify_decree_emb`로 두 방식의 법률 행렬 `np.array_equal`, 인코딩 시간·MPS 메모리를 출력 → 수동 검증 표.
- 3,072는 코드 상수(`deploy.DECREE_MAX_SEQ`)로 둔다(실험 값, 환경 변수로 바꿀 이유 없음). `/info`·`check_deploy`에 표시.

### Q4. 환경 변수 (모두 `deploy.decree(profile)`에서 읽음, `deploy.reranker`와 같은 방식)

| 변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `LAWBOT_DECREE_ENABLED` | `0` | `0`/`1`만 허용. 1인데 리랭커 꺼짐 → 시작 시 `ValueError`. v1 프로필 → `ValueError` |
| `LAWBOT_DECREE_TAU` | `0.9` | 다른 법 하위법령 통과 점수 (0~1 실수) |
| `LAWBOT_DECREE_K` | `2` | 최대 첨부 수 (1 이상 정수) |
| `LAWBOT_DECREE_MODELS` | `pro` | 첨부를 받을 답변 모델 key (쉼표). 프로필에 없는 key면 오류. Fast는 기본 제외 |
| `LAWBOT_V4_DECREE_ARTICLES` | `data/v4/law_articles_v4_decree.csv` | 하위법령을 켰을 때 v4 프로필이 쓰는 조문 CSV |

- 하위법령 후보 수 10은 상수(`deploy.DECREE_CANDIDATES`, 실험과 같음). 환경 변수로 열지 않는다.
- 켜면 `Profile.articles`가 `LAWBOT_V4_DECREE_ARTICLES`(812)로 바뀌고 `LAWBOT_V4_ARTICLES`는 쓰지 않는다. `check_deploy`가 어느 CSV를 쓰는지 INFO로 보여 준다.
- `.env.example`(맥미니 32GB)에 `LAWBOT_DECREE_ENABLED=1`과 나머지 기본값을 적는다. `.env.dev.example`(원격 개발 웹앱)에도 같은 값을 적는다(서버와 다르면 시작 거부되므로). `tests/conftest.py`의 `ENV_KEYS`에 새 변수를 넣는다.
- 모델별 켜기를 `/info` 비교에 넣지 않는 이유: 서버는 이 값을 쓰지 않는다(첨부 결정은 웹앱). 개발 웹앱에서 서버를 다시 띄우지 않고 바꿔 볼 수 있다. 요청 로그·`run_<tag>.json`에는 남긴다.

---

## 2. 구현 설계

### 2.1 `deploy.py`
- `DecreeConfig(tau, k, models: tuple, candidates=10, max_seq=3072)`, `decree(profile)`, 상수 `DECREE_CANDIDATES`·`DECREE_MAX_SEQ`.
- `_profile('v4')`: `decree_enabled()`이면 articles = `LAWBOT_V4_DECREE_ARTICLES`. 리랭커 필수 검사는 `decree()` 안에서(`reranker(profile)`가 None이면 오류).

### 2.2 새 모듈 `lawbot/decree.py` (모델 라이브러리 import 없음)
- `attach(candidates, top1_law, tau, k)`: 리랭킹된 하위법령 후보 dict 목록 → 첨부 목록. `reranker.rank_order`와 같은 정렬(점수 내림차순, 동점은 `emb_rank` 오름차순)을 다시 적용한 뒤 `parent_law == top1_law or rerank_score >= tau`인 것 앞에서 k개. 통과 없음 → `[]`.
  - `reranker.rank_order`는 mlx를 import하는 `reranker.py`에 있으므로, 같은 한 줄 함수를 mlx 없는 곳(`decree.py`)으로 옮기고 `reranker.py`가 그것을 import한다(동작 동일).
  - `top1_law`는 법률 리랭커 1위 hit의 `law`(법률 행은 `law == parent_law`).
- `decree_filter.py`·`build_decree_ctx.py`는 실험 기록이라 고치지 않는다. 테스트가 두 구현의 결과를 문항마다 대조한다.

### 2.3 `retriever.py`
- CSV에 `kind` 열이 있으면 법률 행과 하위법령 행을 나눈다. 배열 `ids`·`titles`·`docs`·`laws`(+ 새 `kinds`·`parent_laws`)는 **법률 먼저, 하위법령 뒤** 812개. `n_law` = 415.
- `D` = 법률 415개 임베딩(지금과 같은 호출), `D_dec` = 하위법령 397개(`decree_max_seq`를 줄 때만 만듦). `search(q, k)`·`rank_all`은 지금처럼 법률만 → 반환 순번이 0~414라 `ids` 앞부분과 맞음.
- `search_decrees(q, k)`: `D_dec` 대상. hit dict = 기존 형식(`article_id` '제2조', `law` '근로기준법 시행령', `title`, `doc`, `score`) + `kind`·`parent_law`.
- 하위법령을 끈 구성(415 CSV, `kind` 없음)은 지금 코드 경로와 같다.

### 2.4 `service.py`
- `LawbotApp.decree`(DecreeConfig 또는 None). `uses_decrees(app, model_key)`.
- `retrieve(app, question, k, st, model_key=None)` → **`(law_hits, decree_hits)`**. 법률 부분은 지금 코드 그대로. 하위법령은 모델이 첨부 대상일 때만 검색·리랭킹(`reranker.rerank(q, cands, top_k=len(cands))`로 10개 전부 점수) → `decree.attach`. 측정값 `st['decree_search']`·`st['decree_rerank']`, `st['n_decree']`(첨부 수), `st['decree_candidates']`(평가용 10개, 로그에는 안 남김).
- `ask`: `hits = law_hits + decree_hits`를 LLM·화면·`answer_check`에 넘긴다(하위법령 0개면 지금과 같은 리스트 → 프롬프트 글자 단위 동일). 오류 단계: 하위법령 검색 실패는 `search`, 리랭킹 실패는 `rerank`(기존 유형 재사용). 임베딩 순위로 되돌리지 않음.
- 요청 로그: `MS_FIELDS`에 `decree_search`·`decree_rerank` 추가, 필드 `decree`(이 요청에 첨부를 적용했는지)·`n_decree`. 질문 원문 없음 유지. 콘솔 `log_timing`에 '하위법령 검색'·'하위법령 리랭킹'.
- `load_app`(로컬): `decree(p)` → `Retriever(..., decree_max_seq=3072)`. `load_remote`: `check_info`에 웹앱 `decree(p)` 전달.

### 2.5 `model_server.py`·`remote.py`
- 위 Q2대로. `RemoteRetriever`는 `/articles`의 `kinds`·`parent_laws`·`n_law`를 받고 `search_decrees`를 `/search kind=decree`로 감쌈. `info(app)`에 `decree`. `check_info(info, profile, decree_cfg)`.

### 2.6 인용 판정 (`citation.py`, `eval_v4.py`)
- `citation.article_keys(retriever)`는 `laws`·`ids`가 812개가 되므로 자동으로 하위법령을 포함한다('근로기준법 시행령', '제5조'). 원격도 `/articles`가 812개라 같음.
- `eval_v4.article_keys()`는 이미 프로필 CSV(`article_no`)를 읽으므로 하위법령을 켜면 812개가 된다. → **웹앱 화면과 평가가 같은 프로필 기준**으로 판정. 하위법령을 끈 구성에서는 지금처럼 하위법령 인용이 `nonexistent`.
- `check`의 docstring(“비삭제 415개”) 수정. 판정 규칙 자체는 바꾸지 않는다(법률 '근로기준법 제2조'를 인용했는데 참고 조문에 '근로기준법 시행령 제2조'만 있으면 기존 규칙대로 `wrong_law`).

### 2.7 화면 (`app.py`, `answer_format.py`)
- 조문 카드: 하위법령은 제목 앞에 `〔시행령〕`/`〔시행규칙〕` 표시, 점수 줄은 '리랭커 0.97 (하위법령 임베딩 3위)'. 순서는 hits 순서(법률 → 하위법령)를 유지하고, 인용한 조문 펼침/나머지 접힘 규칙을 그대로 적용(인용 그룹 안에서도 법률 먼저).
- 오른쪽 제목 '검색된 조문 (상위 3개)' → 켜짐이면 '검색된 조문 (법률 3개 + 시행령·시행규칙 최대 2개)', 첫 안내 문장도 같이. 측정 칸에 '하위법령 x.xx초'.
- 간결형 근거 줄·경고: `cited_hits`가 `citation.check(parse(...), hits)`를 쓰므로 하위법령 hit도 같은 규칙(`hit_label` → '근로기준법 시행령 제2조(정의)'). 테스트로 확인만 하고 로직은 바꾸지 않을 예정.
- `data_notice`: 매니페스트 `articles_by_kind`가 있으면 '법령 기준일 2026-10-20 (데이터 v4-decree · 법률 415 · 시행령 273 · 시행규칙 124)'.

### 2.8 `check_deploy.py`
- v4 + 하위법령: 비삭제 812, 법률 415, 종류별 수, `parent_law`가 7개 법률 중 하나, 법률 415행이 `articles_v4.csv`와 같은지(법률 임베딩 동일성의 데이터 쪽 전제) 검사. 하위법령 설정(τ·k·모델·후보 10·max_seq 3,072·리랭커 필수) 표시/검증. 원격이면 서버 `decree`와 비교.

### 2.9 평가 (`eval_v4.py`)
- **tag**: 첨부가 그 모델에 적용되면 기본 tag에 `_dec`를 붙인다(예 `pro_concise_rr_dec_val`). 기존 tag 파일과 열이 섞이지 않게 하기 위함. 추가로 `resume`은 기존 파일 열과 새 행 열이 다르면 이어 쓰지 않고 멈춘다(지금은 열이 밀릴 수 있음).
- **retrieval** (법률 val 1,110): 켜짐이면 행에 `dec_attached`(키 목록)·`dec_n`·`dec_other`(정답 법률과 parent_law가 다른 첨부 수)·`dec_rerank_sec`. summary: '첨부 하위법령 중 다른 법 비율'(실험 law_other_ratio와 같은 정의)·'다른 법이 붙은 문항 비율'·'첨부 수 평균'·'하위법령 리랭킹 초'. 하위법령 리랭킹이 문항당 ~2초 늘어 1,110문항에 약 +40분.
- **새 단계 `decree`** (`--split val`만, test는 거부: 고정 표본이 val에만 있음):
  1. 하위법령 검색: `qa_decree_v4.csv` + `split_decree_v4.csv` val 444. 법률 검색·리랭킹 + 하위법령 검색·리랭킹·필터 → `dqretrieval_<tag>.csv` (정답 하위법령이 후보 10개 안 / 첨부 안, 첨부 수, 질문 유형). 지표: 정답 하위법령 포함률(전체·유형별).
  2. 하위법령 답변: `decree_ctx_dq_val200.csv`의 id 200개를 `service.ask`(웹앱 경로)로 → `dqanswers_<tag>.csv`. 채점: 정답 하위법령 인용(법령명 포함, 거절 아님), 거짓 거절(정답이 첨부됐는데 거절), 참고 조문 밖 인용(`eval_decree.score`와 같은 정의: 참고 조문 키에 없는 인용, 번호만 쓴 인용 포함), 프롬프트 토큰·완료 시간. 파일 이름이 `eval_decree`의 `decree_answers_*`와 겹치지 않게 `dq` 접두사.
  - `all` + `--split val` + 첨부 적용 모델이면 `decree`도 실행. summary·classify·compare에 '하위법령 검색'·'하위법령 답변' 절 추가(compare 경고 기준은 기존 recall/인용 기준 재사용).
- **answers/refusal**: answers 행에 `decrees` 열(첨부 키). `retrieved`·`top3`(법률 3개) 의미는 그대로. 실패 유형 판정의 참고 조문은 `top3 + decrees`. refusal은 정답 없는 **법률** 조문 3개를 주는 시험이므로 후보 풀을 법률 415개로 한정(812로 넓히면 표본이 바뀌어 기준선과 비교 불가). `run_retrieval`의 임베딩 순위 키도 법률 415개.
- `run_meta`에 `decree` 설정(τ·k·모델·켜짐).

---

## 3. 테스트 (모델 없이, 새 파일 `tests/test_decree.py` 중심)

1. **필터 재현 (fp16 저장 점수)**: `decree_filter.cands`(+npz 동점 순서)로 만든 후보를 hit dict로 바꿔 `decree.attach(τ=0.9, k=2)`에 넣고
   - 문항마다 `decree_filter.rules()['same1+other0.9']` + `run`과 첨부 목록이 같음(444 + 1,110문항 전부),
   - 하위법령 val 444 정답 포함 0.921, 법률 val 1,110 다른 법 비율 0.055(소수 셋째 자리),
   - `decree_ctx_val200_rules.csv`의 `ctx_o09`, `decree_ctx_dq_val200.csv`의 `ctx_o09`와 200/200 일치.
2. **동점·빈 결과**: 같은 점수는 `emb_rank`가 앞선 것 먼저. 통과 0개 → 첨부 없음, 프롬프트(`build_prompt_v4`)와 LLM에 넘긴 hits가 하위법령 끈 경우와 같음.
3. **프롬프트 형식**: 법률 3 + 하위법령 2의 `build_prompt_v4`가 `eval_decree.article_table()`로 만든 같은 키 목록의 프롬프트와 글자 단위로 같음(`[4] 근로기준법 시행령 제2조(…)`).
4. **회귀**: 하위법령 꺼짐 / Fast 모델(켜짐이어도) → 하위법령 검색 호출 0회, hits·LLM 입력·요청 로그 필드가 지금과 같음. 켜짐 + Pro → 법률 3개 hits는 꺼짐일 때와 같음.
5. **검색기**: 가짜 SentenceTransformer로 법률 encode 호출(문서 목록·max_seq 2,304)이 켜짐/꺼짐에서 같음, 하위법령은 3,072. `ids`가 법률 먼저 812개, `rank_all` 순번이 법률 범위.
6. **설정**: 기본 꺼짐, 리랭커 꺼짐 + 켜짐 → 오류, v1 → 오류, τ·k·모델 형식 오류.
7. **원격**: 가짜 서버로 로컬·원격 `ask` 결과(hits·첨부·프롬프트) 동일, `/search kind=decree`, `/info.decree` 불일치·API 버전 1 서버 → 시작 거부, `/articles` 812.
8. **매니페스트·인용**: `manifest_decree.json` load·verify OK, 종류별 수 틀리면 FAIL, `manifest.json`이 그대로 415 CSV에 맞음. `citation.check`: 하위법령 hit 인용 → ok, 812 article_keys에 없는 '근로기준법 시행령 제999조' → nonexistent, 하위법령을 끈 article_keys에서는 nonexistent. 간결형 근거 줄·카드 펼침에 하위법령 표시.
9. **평가**: `eval_v4` 열이 다른 기존 파일에 이어 쓰기 거부, `_dec` tag, refusal 후보가 법률 키만, decree 단계 채점 함수.

---

## 4. 문서
- README(한국어·영어 같은 내용): 구조(`decree.py` 등), 설정 표(새 변수), 평가 실행(`lawbot-eval-v4 --split val`의 decree 단계), 성능 절(웹앱 경로 수치는 사용자가 맥미니에서 돌린 뒤 채움. PR에서는 자리를 비우고 "수동 검증 후 기입"으로 둠), "아직 웹앱에 넣지 않았다"는 문장을 실험 절·데이터 절·한계 절에서 고친다(한국어 3곳, 영어도 같은 곳).
- `.env.example`, `.env.dev.example`.

## 5. PR 수동 검증 표 (맥미니, 초안)

| 항목 | 명령 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 배포 검사 | `uv run python -m lawbot.check_deploy` | FAIL 0, 비삭제 812 · 종류별 수 일치 · 하위법령 τ 0.9 k 2 · 리랭커 켜짐 | |
| 법률 임베딩 동일 | `uv run python -m lawbot.verify_decree_emb` | 법률 415×1024 `array_equal` True, 하위법령 인코딩 시간·MPS 메모리 출력 | |
| 로컬 웹앱 질문 3개 | `LAWBOT_MODEL_SERVER= uv run lawbot-app` 후 '체불 융자 이자 몇프로 이하에요?', 평균임금 제외 기간 질문, '해고 예고 없이 갑자기 해고되면 어떻게 하나요?' | 앞 2개: 하위법령 카드(〔시행령〕) 표시·인용, 법률 질문: 법률 3개는 꺼짐일 때와 같은 순위·점수 | |
| 원격 모드 | 모델 서버 + 웹앱, 같은 3질문 | 로컬과 같은 조문·답변, `/info.decree` 표시 | |
| 검색 지표 | `uv run lawbot-eval-v4 retrieval --split val` | 법률 Recall@3·리랭킹 순위가 `summary_pro_concise_rr_val.csv`와 같음, 다른 법 비율 < 0.1 | |
| 하위법령 지표 | `uv run lawbot-eval-v4 decree --split val` | 정답 포함 ≈ 0.91, 정답 인용 ≈ 0.9 안팎, 참고 조문 밖 인용 ≈ 0 | |
| 답변 지표 | `uv run lawbot-eval-v4 answers --split val` 후 `compare --base pro_concise_rr_val --new pro_concise_rr_dec_val` | 경고 없음 (실험: 법률 질문 답변은 법률만과 같음) | |
| 시간·메모리 | 위 실행의 summary / 요청 로그 | 법률 리랭킹 ≈1.95초, 하위법령 리랭킹 ≈2초, 완료 ≈17초, MLX 최대 메모리 (기존 20.4GB 대비) | |
| 시작 시간 | 모델 서버 로그 `불러오기 N초` | 꺼짐 대비 증가분 기록 | |

---

## 6. 범위 밖
- 임베딩·리랭커 재학습, τ·k 재선택, Fast에 하위법령 첨부(평가 전), 별표·서식·고시.
- 실험 스크립트(`decree_filter`·`build_decree_ctx`·`eval_decree`·`mlx_decree_scores`)의 동작 변경.
- 기존 결과 파일(`results/v4/*`) 수정.

---

## 7. 구현하며 정한 세부 사항 (승인 뒤 추가)
- **원격 모드 웹앱의 리랭커 조건**: `deploy.decree(p, require_reranker=False)`. 원격 웹앱은 리랭커를 서버가 정하므로(`.env.dev.example`에 리랭커 변수 없음, 기존 테스트가 검사) 웹앱 쪽 `LAWBOT_RERANKER_ENABLED`를 요구하지 않는다. 서버는 자기 시작 때 리랭커 없는 하위법령을 거부하고, 웹앱은 `/info`에 `decree`가 있는데 `reranker`가 없으면 거부한다. `check_deploy`도 원격 모드면 같은 조건.
- **`rank_order` 위치**: `reranker.py`(mlx import)에서 `decree.py`로 옮기고 `reranker.py`가 다시 import (`verify_reranker`의 `from lawbot.reranker import rank_order`도 그대로 동작).
- **오류 단계**: `st['_stage']`로 법률·하위법령 검색(`search`)·리랭킹(`rerank`) 중 어디서 실패했는지 남긴다 (기존 유형 이름 그대로).
- **`check_deploy` 조문 수 문구**: `kind` 열이 없는 CSV는 기존 문구(`비삭제 조문 N개`) 그대로, 812 CSV는 `비삭제 법률 조문 415개 · 전체 812개`.
- **참고 조문 밖 인용(`dqanswers`)**: 웹앱과 같은 `citation.citations`로 파싱한 인용 키가 참고 조문 키(법률 3 + 첨부)에 없으면 1 (번호만 쓴 인용 포함). `eval_decree`의 별도 파서와 정의는 같고 파서만 웹앱 것을 쓴다.
- **바뀐 기존 테스트**: API 버전 1 → 2를 단언하던 3곳, `test_decree_csv_kinds`(매니페스트 없음 → `manifest_decree.json`), `.env.example` 조문 CSV 단언(415 → 812, 하위법령 켜짐). 나머지 기존 테스트는 그대로.
