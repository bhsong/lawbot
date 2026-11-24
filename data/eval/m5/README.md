# M5 평가 세트 (Lawbot2 `internal/eval/testdata/m5`, plan 브랜치 `feat/m5-eval-audit` `ee02a5c`)

모든 파일은 `{"name", "meta", "items": [...]}` 형식의 UTF-8 JSON이다. **test 분할 문항은 없다**(v4·v5·시행령 세 split의 test 7,375건과 ID 교집합 0, 질문 2-gram Jaccard ≥ 0.8 유사 문항은 규칙으로 제외). 설정·프롬프트 조정은 이 val 세트로만 하고, v6 `data/labor_eval_keywords.csv`(650) 등 test로 조정하지 않는다.

| 파일 | 건수 | 기대 행동 `expected` | 주요 필드 |
|---|---:|---|---|
| `reasoning.json` | 32 | `answer` 30, `conditional` 2(CARD01·02: 비법령 참고자료가 있으면 답, 없으면 판단 유보) | `article_ids`(필수 근거 조문, 법령명+조문), `answer`(참고 답변), `error_type`, `other_refs` |
| `general.json` | 600 | `answer` | `article_ids`(정답 조문 1개), `answer_key`(`must_include` 그룹, `must_not_include`, `must_not_in_conclusion`), `eval_cat`(A_lsa·B_newlaw·D_tax), `question_type` |
| `out_of_scope.json` | 15 | `out_of_scope` | 합성. `scope_reason` |
| `fact_missing.json` | 15 | `ask_or_condition`(빠진 사실을 묻거나 조건별로 나눠 답함) | 합성. `missing_facts` |
| `oos_v6.json` | 102 | `v6_gold_only`(현행 분모 제외, v6 category별 분포만) | `category`, `source`, `refer_lsa`(안내 허용 조문) |

원천: v6 `8c8ff60`의 CSV(각 파일 `meta.sources`에 경로와 SHA-256), 일반 val은 `split_sllm=val` 후보 1,582건에서 `eval_cat × question_type` 층화 표본(시드 20261123). 참고 답변이 자기 키로 만점이 아니거나 거절로 판정되는 문항은 제외.

## 채점 (v6 `score_answers.py`·`refusal.py`와 같은 규칙)
- 정규화: 공백 제거, `ㆍ`→`·`, 숫자 사이 쉼표 제거. 숫자로 시작하는 키워드는 앞에 숫자가 붙으면 불일치(`3년`≠`13년`).
- `cite_ok`: 정답 조문 번호 인용(`제43조`가 `제43조의2`에 매칭되지 않음). 거절이면 0.
- 거절 판정: 【결론】(없으면 앞 150자)에 `확인할 수 없` 포함.
- `total = 그룹 충족률 × cite_ok × (금지 표현 있으면 0.5)`. 일반 val은 **검색이 정답 조문을 찾은 문항**을 분모로 한다(`total`), 전체 문항 0점 포함 진단은 `total_all`.
- 추론 val: 필수 근거 조문 **전부** 확보·writer 제공·인용(`cite_ok` = 전부 인용), 근거를 다 받지 못하고 단정한 비율(`answer_without_gold_basis`), 전부 확보 후 거절(`false_refusal`).
- 범위 밖: 올바른 거절 또는 허용 조문 안내 비율(기술 실패는 분모에 남기고 오답).
- 사실 부족: 확인 질문 발생률 + 질문 문구와 `missing_facts`를 사람이 대조.
- 추론 val 결론 내용은 조건 이름을 가린 0/0.5/1 블라인드 채점.
