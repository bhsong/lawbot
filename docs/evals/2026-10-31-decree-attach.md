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
| 3 | 로컬 웹앱 경로 질문 3개 | `uv run python -m lawbot.smoke_decree --out results/v4/smoke_decree_local.json` | 하위법령 질문 첨부·인용, 법률 3개 켜짐/꺼짐 동일 | 대기 (운영 모델 서버 중지 필요) |
| 4 | 원격 모드 동등성 | 개발 모델 서버(시험 포트) + `smoke_decree --out …_remote.json` → `--compare` | 조문·답변 동일, τ 불일치 거부 | 대기 |
| 5 | 검색 지표 (법률 val 1,110) | `lawbot-eval-v4 retrieval --split val` | 법률 Recall 기준선과 같음, 다른 법 비율 < 0.1 | 대기 |
| 6 | 하위법령 지표 (val 444 / 표본 200) | `lawbot-eval-v4 decree --split val` | 첨부율 ≈ 0.91, 정답 인용 ≈ 0.9, 밖 인용 ≈ 0 | 대기 |
| 7 | 답변 지표 (법률 val 200) | `lawbot-eval-v4 answers --split val` + `compare` | 경고 없음 | 대기 |
| 8 | refusal (val 100×2) | `lawbot-eval-v4 refusal --split val` | 기준선과 같음 (고정 법률 조문, 하위법령 무관) | 대기 |
| 9 | 시간·메모리 | summary, `log_summary`, `/info` memory | 법률 리랭킹 ≈ 1.95초, 하위법령 ≈ 2초, 완료 ≈ 17초, MLX 최대 메모리 기록 | 대기 |

## 메모
- 2번 실행 중 transformers가 "3017 > 2304" 경고를 냈습니다. 이 경고는 확인용으로 토큰 수를 세는 `tokenizer()` 호출에서 나온 것이고, 인코딩 자체는 하위법령 max_seq 3,072로 했습니다.
- 3번 이후는 27B를 한 벌 더 올려야 해서, 운영 모델 서버(약 20GB)를 내린 뒤에 돌립니다. 32GB에서 두 벌을 함께 올릴 수 없습니다.
