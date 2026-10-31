# 평가 기록: 시행령·시행규칙 첨부 웹앱 반영 (PR #26)

- 날짜: 2026-10-31
- 장비: 맥 미니 (Mac18,5, 32GB)
- 코드: `feat/decree-attach`
- 설정: 개발 `.env` + `LAWBOT_MODEL_SERVER=`(로컬 모드) + `LAWBOT_DECREE_ENABLED=1`
  - 프로필 v4, Pro = `models/v4/qwen_27b_mlx_4bit`, 리랭커 `reranker_4b_mlx_4bit`(후보 10)
  - τ 0.9, k 2, 첨부 모델 pro
- 설계: [`docs/archives/decree-attach.md`](../archives/decree-attach.md)
- 분할: val만 사용 (test는 쓰지 않음)

## 결과 요약

| # | 항목 | 명령 | 기대값 | 실제값 |
| --- | --- | --- | --- | --- |
| 1 | 배포 검사 | `LAWBOT_MODEL_SERVER= LAWBOT_DECREE_ENABLED=1 uv run python -m lawbot.check_deploy` | FAIL 0 | **OK** (FAIL 0). 비삭제 법률 415 · 전체 812, `v4-decree` 매니페스트 sha256·법령별·종류별 일치, 하위법령 {시행령 273, 시행규칙 124}, parent_law 모두 7개 법률, 법률 조문이 `articles_v4.csv`와 같음 |
| 2 | 법률 임베딩 동일·인코딩 비용 | `uv run python -m lawbot.verify_decree_emb` | 비트 동일, 검색 차이 0 | **OK**. 법률 임베딩 (415, 1024) 비트 단위로 같음, 문서 문자열 같음, val 200문항 법률 상위 10개(순위·점수) 다른 문항 0. 하위법령 최장 3,017토큰 (3,072 초과 0). 인덱스 18.4초 → 36.5초 (+18.0초). MPS: 꺼짐 1.22GB, 켜짐 모델을 하나 더 올린 누적 2.43GB(켜짐 단독 약 1.2GB) |
| 3 | 로컬 웹앱 경로 질문 3개 | `LAWBOT_MODEL_SERVER= LAWBOT_DECREE_ENABLED=1 uv run python -m lawbot.smoke_decree --out results/v4/smoke_decree_local.json` | 하위법령 질문 첨부·인용, 법률 3개 켜짐/꺼짐 동일 | **OK**. 3문항 모두 법률 3개(순위·점수) 켜짐/꺼짐 동일, 첨부 2개씩. 융자 이자 → 임금채권보장법 시행규칙 제8조의10 인용(5% 이하), 수습 평균임금 → 근로기준법 시행령 제2조 인용, 해고 예고 → 근로기준법 제26조 인용. 확인 안 된 인용·거절 없음. MLX peak 19.96GB (아래 표) |
| 4 | 원격 모드 동등성 | 개발 모델 서버(`LAWBOT_MODEL_SERVER_PORT=<시험 포트> LAWBOT_DECREE_ENABLED=1 uv run lawbot-model-server`) + `smoke_decree --out results/v4/smoke_decree_remote.json` → `smoke_decree --compare` | 조문·답변 동일, τ 불일치 거부 | **OK**. 로컬과 조문·답변 글자 단위 동일. 웹앱 τ 0.8 → `하위법령 tau: 서버 0.9 / 웹앱 0.8` 거부, 웹앱 첨부 꺼짐 → `서버 켜짐 / 웹앱 꺼짐` 거부. 서버 `/info` decree `{tau 0.9, k 2, candidates 10, max_seq 3072, models [pro], n_articles 397}`, 불러오기 44.6초, MLX active 17.4GB |
| 5 | 검색 지표 (법률 val 1,110) | `lawbot-eval-v4 retrieval --split val` | 법률 Recall 기준선과 같음, 다른 법 비율 < 0.1 | 대기 |
| 6 | 하위법령 지표 (val 444 / 표본 200) | `lawbot-eval-v4 decree --split val` | 첨부율 ≈ 0.91, 정답 인용 ≈ 0.9, 밖 인용 ≈ 0 | 대기 |
| 7 | 답변 지표 (법률 val 200) | `lawbot-eval-v4 answers --split val` + `compare` | 경고 없음 | 대기 |
| 8 | refusal (val 100×2) | `lawbot-eval-v4 refusal --split val` | 기준선과 같음 (고정 법률 조문, 하위법령 무관) | 대기 |
| 9 | 시간·메모리 | summary, `log_summary`, `/info` memory | 법률 리랭킹 ≈ 1.95초, 하위법령 ≈ 2초, 완료 ≈ 17초, MLX 최대 메모리 기록 | 대기 |

## 3번 스모크 상세 (로컬, Pro)

| 질문 | 첨부 하위법령 (리랭커 점수) | 인용 | 리랭킹 법률 / 하위법령 (초) | 완료 켜짐 / 꺼짐 (초) | 프롬프트 토큰 켜짐 / 꺼짐 |
| --- | --- | --- | --- | --- | --- |
| 체불 융자 이자 몇프로 이하에요? | 임금채권보장법 시행규칙 제8조의10 (0.993), 근로기준법 시행령 제17조 (0.977) | 시행규칙 제8조의10 | 2.41 / 2.71 | 36.2* / 17.0 | 1,328 / 914 |
| 평균임금 계산할 때 수습 기간도 들어가나요? | 근로기준법 시행령 제2조 (0.992), 근로기준법 시행령 제5조 (0.566) | 시행령 제2조 | 1.78 / 2.13 | 19.2 / 12.3 | 1,729 / 976 |
| 해고 예고 없이 갑자기 해고되면 어떻게 하나요? | 근로기준법 시행규칙 제4조 (0.914), 근로기준법 시행령 제10조 (0.725) | 근로기준법 제26조 | 1.88 / 2.13 | 18.0 / 15.7 | 925 / 648 |

\* 첫 질문은 첫 토큰 20.9초(첫 실행 준비 포함). 두 번째부터 첫 토큰 3.5~6.0초.

## 메모
- 2번 실행 중 transformers가 "3017 > 2304" 경고를 냈습니다. 이 경고는 확인용으로 토큰 수를 세는 `tokenizer()` 호출에서 나온 것이고, 인코딩 자체는 하위법령 max_seq 3,072로 했습니다.
- 3번 이후는 27B를 한 벌 더 올려야 해서, 운영 모델 서버(약 20GB)를 내린 뒤에 돌립니다. 32GB에서 두 벌을 함께 올릴 수 없습니다.
