# tests/test_models.py  →  uv run pytest -m models   (맥 + models/ 필요, 기본 실행·CI에서는 제외)
# 실제 튜닝 임베딩·sLLM으로 한 질문을 끝까지 처리하는 스모크 테스트. 품질 수치는 lawbot-eval로 잰다
import os

import pytest

from lawbot.paths import EMB, LLM_4BIT

pytestmark = pytest.mark.models


@pytest.fixture(scope='module')
def models():
    llm_path = os.environ.get('LLM_PATH', LLM_4BIT)
    missing = [str(p) for p in (EMB, llm_path) if not os.path.isdir(p)]
    if missing:  # -m models로 직접 요청했으므로 건너뛰지 않고 실패
        pytest.fail(f"모델 폴더가 없습니다: {', '.join(missing)} (README '모델 준비' 참고)")
    from lawbot.llm import LLM
    from lawbot.retriever import Retriever

    return Retriever(), LLM(llm_path)


def test_end_to_end_answer(models):
    from lawbot.refusal import cited_articles, is_refusal

    R, L = models
    hits = R.search('수습 기간에도 해고 예고를 해야 하나요?', k=3)
    assert len(hits) == 3
    answer = ''.join(g.text for g in L.stream('수습 기간에도 해고 예고를 해야 하나요?', hits))
    for part in ('【결론】', '【근거 조문】', '【설명】', '【참고】'):
        assert part in answer
    assert not is_refusal(answer)
    assert set(cited_articles(answer)) & {h['article_id'] for h in hits}
