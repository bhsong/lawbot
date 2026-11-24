# M5 PR #193 Codex 리뷰 대역 검증 (2026-11-24)

[계획](../archives/consult-trace-clarify.md)의 추가 리뷰 2건. 공유 추론 요청 없이 검증했다.

- 인용: `근로기준법에 따른 방법은 제26조에 규정되어 있습니다.`의 `제26조`는 `근로기준법 / ok / same_sentence`다. 적법·위법·불법과 세법·과세법은 앞 법령의 귀속을 덮어쓰지 않고, `사회복지사업법 제2조제1호`는 `outside`다. 기대 원문 범위까지 대조했다.
- M5: 가짜 답변의 정답 키워드·조문 번호가 일치해도 화면이 `missing_citation`이면 미응답으로 처리했다. 일반 `total`·`total_all`·`cite_ok`는 0, 검색 적중은 1이었다. 추론 채점에서도 인용 성공이나 근거 없는 단정으로 세지 않았다.

## 검증 명령과 결과

- `uv run pytest -q --show-capture=no`: 기본 단위·계약·통합·대역 브라우저 **2,853개 통과**, 실패·skip 없음. 기본 제외 10개는 통과로 세지 않았다.
- `uv run ruff check`, `uv run ruff format --check`, `PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright` 통과.
- 실제 모델 E2E·모델 스모크·M5 품질 평가: 실행 안 함.
