# tests/test_reranker_models.py  →  uv run pytest -m models   (맥 + 리랭커 MLX 폴더 필요, 기본 실행·CI에서는 제외)
# 실제 Qwen3-Reranker-4B MLX 4비트로 토크나이저·로딩·채점을 확인하는 스모크 테스트
# 폴더: LAWBOT_RERANKER_MODEL_PATH 또는 models/v4/reranker_4b_mlx_4bit. 실험과의 순위 일치는 lawbot.verify_reranker로 잰다
import os
from pathlib import Path

import pandas as pd
import pytest

from lawbot import paths

pytestmark = pytest.mark.models
PATH = Path(os.environ.get('LAWBOT_RERANKER_MODEL_PATH') or paths.V4_RERANKER)   # conftest가 환경 변수를 지우기 전에 읽음
QUESTION = '해고 예고 없이 갑자기 해고되면 어떻게 하나요?'

@pytest.fixture(scope='module')
def reranker():
    if not PATH.is_dir():   # -m models로 직접 요청했으므로 건너뛰지 않고 실패
        pytest.fail(f'리랭커 모델 폴더가 없습니다: {PATH} (README 리랭커 준비 참고)')
    from lawbot.reranker import Reranker
    return Reranker(PATH)

@pytest.fixture(scope='module')
def docs():
    from lawbot.retriever import DOC_STYLES
    law = pd.read_csv(paths.V4_ARTICLES, encoding='utf-8-sig')
    law = law[law['deleted'] == 0].reset_index(drop=True)
    law['article_id'] = law['article_no']
    return dict(zip(law['law'] + ' ' + law['article_id'], DOC_STYLES['v4'](law)))

def test_tokenizer_matches_notebook_format(reranker):
    from lawbot.reranker import PREFIX, SUFFIX
    assert reranker.tok.decode(reranker.prefix) == PREFIX and reranker.tok.decode(reranker.suffix) == SUFFIX
    assert reranker.tok.convert_ids_to_tokens(reranker.no_yes.tolist()) == ['no', 'yes']
    ids = reranker.encode('질문', '가나다 ' * 5000)
    assert len(ids) == 2560 and ids[-len(reranker.suffix):] == reranker.suffix

def test_rerank_one_question(reranker, docs):
    keys = ['최저임금법 제6조', '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률 제19조', '근로기준법 제26조']
    cands = [{'article_id': k, 'title': '', 'score': 0.0, 'doc': docs[k]} for k in keys]
    out = reranker.rerank(QUESTION, cands, top_k=3)
    assert out[0]['article_id'] == '근로기준법 제26조'
    assert all(0 <= h['rerank_score'] <= 1 for h in out)
    again = reranker.rerank(QUESTION, cands, top_k=3)
    assert [h['rerank_score'] for h in again] == [h['rerank_score'] for h in out]   # 같은 입력 같은 점수
