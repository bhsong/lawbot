# v4·v5 분할 교차 유사도 (2026-11-08)

기존 `split_emb`와 `split_sllm` 각각에서 서로 다른 분할 사이의 무순서 쌍을 셌다. `lawbot.qa_rules.norm`으로 정규화하고, `Overlap`과 같은 길이·문자 빈도 상한을 거친 뒤 `SequenceMatcher(autojunk=False)` 양방향 점수 중 하나라도 0.9 이상이면 포함했다. 두 목적에서 같은 쌍이 발견되면 목적별로 각각 집계했다.

| 세트 | 분할 열 | train↔val | train↔test | val↔test | 합계 |
|---|---|---:|---:|---:|---:|
| v4 | split_emb | 3 | 7 | 3 | 13 |
| v4 | split_sllm | 0 | 9 | 1 | 10 |
| v5 | split_emb | 0 | 4 | 1 | 5 |
| v5 | split_sllm | 1 | 4 | 1 | 6 |

## v4 · split_emb

| 분할 | ID | ID |
|---|---|---|
| train↔test | Q2800 | Q2799 |
| train↔test | Q3451 | Q3571 |
| train↔test | Q4084 | Q4086 |
| train↔test | Q4721 | Q6909 |
| train↔test | Q6039 | Q6079 |
| train↔test | Q6214 | Q6212 |
| train↔test | Q6957 | Q6956 |
| train↔val | Q2986 | Q2846 |
| train↔val | Q6598 | Q6522 |
| train↔val | Q7364 | Q7362 |
| val↔test | Q3287 | Q3286 |
| val↔test | Q4700 | Q4699 |
| val↔test | Q7579 | Q7577 |

## v4 · split_sllm

| 분할 | ID | ID |
|---|---|---|
| train↔test | Q2800 | Q2799 |
| train↔test | Q3287 | Q3286 |
| train↔test | Q3451 | Q3571 |
| train↔test | Q4084 | Q4086 |
| train↔test | Q4700 | Q4699 |
| train↔test | Q4721 | Q6909 |
| train↔test | Q6039 | Q6079 |
| train↔test | Q6957 | Q6956 |
| train↔test | Q7579 | Q7577 |
| val↔test | Q6214 | Q6212 |

## v5 · split_emb

| 분할 | ID | ID |
|---|---|---|
| train↔test | T06191 | T06292 |
| train↔test | T12168 | T12868 |
| train↔test | T12508 | T12466 |
| train↔test | T13607 | T13606 |
| val↔test | T11885 | T11825 |

## v5 · split_sllm

| 분할 | ID | ID |
|---|---|---|
| train↔test | T11885 | T11825 |
| train↔test | T12168 | T12868 |
| train↔test | T12508 | T12466 |
| train↔test | T13607 | T13606 |
| train↔val | T01530 | T01670 |
| val↔test | T06191 | T06292 |
