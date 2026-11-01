# services/model-server/tests/test_ms_real_models.py  →  uv run pytest -m models services/model-server
# 실제 모델 폴더(models/)와 Apple Silicon이 필요한 스모크 테스트 (기본 실행·CI에서는 제외)
# 폴더는 .env와 같은 변수(LAWBOT_SERVE_*)의 첫 번째 항목, 없으면 README '모델 준비'의 기본 경로
# 챗봇의 이전 test_models.py·test_reranker_models.py에서 옮김. 품질 수치는 docs/evals 기록 참고
import csv
import os

import pytest

from lawbot_model_server.config import parse_models
from lawbot_model_server.envfile import ROOT

pytestmark = pytest.mark.models


def first(kind, default):
    """conftest가 환경 변수를 지우기 전(모듈 import 때)에 읽음"""
    env = {'chat': 'LAWBOT_SERVE_CHAT', 'embedding': 'LAWBOT_SERVE_EMBEDDING', 'rerank': 'LAWBOT_SERVE_RERANK'}[kind]
    entries = parse_models(kind, os.environ.get(env))
    return entries[0].path if entries else ROOT / default


CHAT = first('chat', 'models/v4/sllm_4b_lora_mlx_4bit')
EMB = first('embedding', 'models/v4/emb_v4_fp16')
RERANK = first('rerank', 'models/v4/reranker_4b_mlx_4bit')
QUESTION = '해고 예고 없이 갑자기 해고되면 어떻게 하나요?'


def need(path):
    if not path.is_dir():  # -m models로 직접 요청했으므로 건너뛰지 않고 실패
        pytest.fail(f'모델 폴더가 없습니다: {path} (README 모델 준비 참고)')


def v4_docs():
    with (ROOT / 'data/v4/articles_v4.csv').open(encoding='utf-8-sig') as f:
        rows = [r for r in csv.DictReader(f) if r['deleted'] == '0']
    return {f'{r["law"]} {r["article_no"]}': f'{r["law"]} {r["article_no"]}({r["title"]})\n{r["text"]}' for r in rows}


@pytest.fixture(scope='module')
def reranker():
    need(RERANK)
    from lawbot_model_server.rerank import Reranker

    return Reranker(RERANK)


LAW_TASK = (
    'Given a Korean labor-law question, retrieve the statutory article that directly '
    'provides the legal basis for answering it.'
)


def test_reranker_tokenizer_matches_notebook_format(reranker):
    from lawbot_model_server.rerank import PREFIX, SUFFIX

    assert reranker.tok.decode(reranker.prefix) == PREFIX and reranker.tok.decode(reranker.suffix) == SUFFIX
    assert reranker.tok.convert_ids_to_tokens(reranker.no_yes.tolist()) == ['no', 'yes']
    ids = reranker.encode('질문', '가나다 ' * 5000, LAW_TASK)
    assert len(ids) == 2560 and ids[-len(reranker.suffix) :] == reranker.suffix


def test_reranker_one_question(reranker):
    docs = v4_docs()
    keys = ['최저임금법 제6조', '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률 제19조', '근로기준법 제26조']
    s = reranker.score(QUESTION, [docs[k] for k in keys], LAW_TASK)
    assert max(range(3), key=lambda i: s[i]) == 2 and all(0 <= x <= 1 for x in s)
    assert reranker.score(QUESTION, [docs[k] for k in keys], LAW_TASK) == s  # 같은 입력 같은 점수


def test_embedding_finds_article():
    need(EMB)
    import numpy as np

    from lawbot_model_server.embeddings import Embedder

    docs = v4_docs()
    keys = ['최저임금법 제6조', '근로기준법 제26조', '근로기준법 제60조']
    e = Embedder(EMB)
    d = e.encode([docs[k] for k in keys], 'document', 2304)
    q = e.encode([QUESTION], 'query', 2304)
    assert d.dtype == np.float32 and np.allclose(np.linalg.norm(d, axis=1), 1, atol=1e-3)
    assert keys[int(np.argmax(d @ q[0]))] == '근로기준법 제26조'


def test_chat_answers_in_format():
    need(CHAT)
    from lawbot_model_server.chat import ChatModel

    docs = v4_docs()
    user = f'[참고 조문]\n[1] {docs["근로기준법 제26조"]}\n\n[질문]\n{QUESTION}'
    system = (ROOT / 'data/v4/system_v4.txt').read_text(encoding='utf-8').strip()
    out = list(
        ChatModel(CHAT).stream(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], max_tokens=512
        )
    )
    answer = ''.join(r.text for r in out)
    assert '【결론】' in answer and '제26조' in answer
    assert out[-1].finish_reason in ('stop', 'length') and out[-1].prompt_tokens > 0


@pytest.fixture(scope='module')
def chat_model():
    need(CHAT)
    from lawbot_model_server.chat import ChatModel

    return ChatModel(CHAT)


def answer_messages(article, question):
    docs = v4_docs()
    system = (ROOT / 'data/v4/system_v4.txt').read_text(encoding='utf-8').strip()
    user = f'[참고 조문]\n[1] {docs[article]}\n\n[질문]\n{question}'
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


def run_engine(engine, requests):
    """requests: {이름: messages}. 모두 끝날 때까지 step → {이름: 답변}"""
    out = {n: '' for n in requests}
    kw = {'max_tokens': 256, 'temperature': 0.0, 'top_p': None, 'enable_thinking': False}
    for n, m in requests.items():
        engine.add(n, m, kw)
    ended = set()
    while len(ended) < len(requests):
        for t, kind, v in engine.step():
            if kind == 'item':
                out[t] += v.text
            elif kind == 'end':
                ended.add(t)
            else:
                raise v
    engine.close()
    return out


def test_batch_engine_one_request_matches_sequential(chat_model):
    """배치 경로 1건이 순차(stream_generate)와 같은 답인지. 같으면 SequentialEngine을 지울 수 있다 (계획 §4.1)"""
    from lawbot_model_server.chat import BatchEngine, SequentialEngine

    m = {'q': answer_messages('근로기준법 제26조', QUESTION)}
    assert run_engine(BatchEngine(chat_model, 4), m) == run_engine(SequentialEngine(chat_model), m)


def test_batch_engine_two_requests_finish(chat_model):
    from lawbot_model_server.chat import BatchEngine

    out = run_engine(
        BatchEngine(chat_model, 4),
        {
            'a': answer_messages('근로기준법 제26조', QUESTION),
            'b': answer_messages('근로기준법 제60조', '1년 일하면 연차휴가가 며칠인가요?'),
        },
    )
    assert all('【결론】' in v for v in out.values()) and '제26조' in out['a'] and '제60조' in out['b']
