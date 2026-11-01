# services/model-server/tests/test_ms_models.py  →  모델 래퍼 (chat·embedding·rerank). 모델 파일 불필요
# chat: 가짜 mlx_lm.load·stream_generate / rerank: 작은 무작위 MLX Qwen3 + 가짜 토크나이저 / embedding: 가짜 SentenceTransformer
# 챗봇의 이전 llm.py·reranker.py·retriever.py 테스트에서 옮긴 부분은 계산이 그때와 같은지 본다
import types

import mlx.core as mx
import mlx.nn as nn
import numpy as np
import pytest
from mlx_lm.models import qwen3

import lawbot_model_server.chat as chat_mod
import lawbot_model_server.embeddings as emb_mod
import lawbot_model_server.rerank as rr_mod
from lawbot_model_server.chat import THINK, ChatModel, close_thinking, hide_thinking


# ---- chat: 생각 단계 끄기 (튜닝 전 모델의 chat template이 enable_thinking을 무시할 때) ----
def test_close_thinking_prompt():
    ok = '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert close_thinking(ok, THINK) == ok  # Qwen3 템플릿이 이미 끈 경우: 프롬프트 그대로
    assert close_thinking('<|im_start|>assistant\n', THINK) == '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert close_thinking('<|im_start|>assistant\n<think>\n', THINK) == '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert close_thinking('<|im_start|>assistant\n', None) == '<|im_start|>assistant\n'  # 생각 토큰이 없는 모델


def chunks(*texts):
    return [types.SimpleNamespace(text=t, generation_tps=1.0) for t in texts]


def test_hide_thinking_block():
    out = list(hide_thinking(chunks('<thi', 'nk>\n음…', '고민', '</think>\n\n【결론】', ' 답'), THINK))
    assert len(out) == 5 and all(o.generation_tps == 1.0 for o in out)  # 토큰마다 응답 유지 (측정값 그대로)
    assert [o.text for o in out] == ['', '', '', '【결론】', ' 답']


def test_hide_thinking_passthrough():
    for texts in (('【결론】', '답'), (' ', '\n【결론】', '답'), ('<', 'b>굵게'), ('생각 <think> 아님',)):
        assert ''.join(o.text for o in hide_thinking(chunks(*texts), THINK)) == ''.join(texts)
    assert ''.join(o.text for o in hide_thinking(chunks('<think>', '끝나지 않음'), None)) == '<think>끝나지 않음'
    assert ''.join(o.text for o in hide_thinking(chunks('<think>', '끝나지 않음'), THINK)) == ''


class FakeTokenizer:
    def __init__(self, think=None):
        self.calls = []
        if think:
            self.think_start, self.think_end = think

    def apply_chat_template(self, messages, **kw):
        self.calls.append((messages, kw))
        return 'PROMPT'


@pytest.fixture
def fake_chat(ms_env, tmp_path):
    """mlx_lm.load·stream_generate·make_sampler를 가짜로. seen['gen']으로 생성 결과를 바꾼다"""
    seen = {'tok': FakeTokenizer(), 'gen': lambda: iter(chunks('【결론】', '답'))}
    ms_env.setattr(chat_mod, 'load', lambda path: seen.update(path=path) or ('MODEL', seen['tok']))
    ms_env.setattr(chat_mod, 'make_sampler', lambda **kw: seen.update(sampler=kw) or 'SAMPLER')

    def stream_generate(model, tok, prompt, max_tokens, sampler):
        seen.update(prompt=prompt, max_tokens=max_tokens, used_sampler=sampler)
        yield from seen['gen']()

    ms_env.setattr(chat_mod, 'stream_generate', stream_generate)
    return tmp_path, seen


MESSAGES = [{'role': 'system', 'content': 'SYS'}, {'role': 'user', 'content': '[참고 조문]\n…\n\n[질문]\nq'}]


def test_chat_matches_previous_llm_call(fake_chat):
    """챗봇의 이전 LLM.stream과 같은 호출: 받은 messages 그대로, 생각 끔, 탐욕적 생성(temp 0)"""
    path, seen = fake_chat
    m = ChatModel(path)
    assert ''.join(r.text for r in m.stream(MESSAGES, max_tokens=1024)) == '【결론】답'
    messages, kw = seen['tok'].calls[0]
    assert messages == MESSAGES
    assert kw == {'add_generation_prompt': True, 'tokenize': False, 'enable_thinking': False}
    assert seen['prompt'] == 'PROMPT' and seen['max_tokens'] == 1024 and seen['path'] == str(path)
    assert seen['sampler'] == {'temp': 0.0, 'top_p': 0.0}  # 이전 make_sampler(temp=0.0)와 같은 탐욕적 생성


def test_chat_closes_thinking_when_template_ignores_it(fake_chat):
    path, seen = fake_chat
    seen['tok'] = FakeTokenizer(THINK)  # Qwen 어휘, 템플릿은 'PROMPT'만 냄 (끄기 무시)
    seen['gen'] = lambda: iter(chunks('<think>', '생각', '</think>', '【결론】답'))
    assert ''.join(r.text for r in ChatModel(path).stream(MESSAGES)) == '【결론】답'
    assert seen['prompt'] == 'PROMPT<think>\n\n</think>\n\n'


def test_chat_thinking_on_passes_through(fake_chat):
    path, seen = fake_chat
    seen['tok'] = FakeTokenizer(THINK)
    seen['gen'] = lambda: iter(chunks('<think>', '생각', '</think>', '답'))
    m = ChatModel(path)
    assert ''.join(r.text for r in m.stream(MESSAGES, enable_thinking=True, temperature=0.7, top_p=0.9)) == (
        '<think>생각</think>답'
    )
    assert seen['prompt'] == 'PROMPT' and seen['tok'].calls[-1][1]['enable_thinking'] is True
    assert seen['sampler'] == {'temp': 0.7, 'top_p': 0.9}


def test_chat_stops_generation_when_closed(fake_chat):
    path, seen = fake_chat
    state = {'closed': False}

    def gen():
        try:
            for i in range(100):
                yield types.SimpleNamespace(text=str(i))
        finally:
            state['closed'] = True

    seen['gen'] = gen
    s = ChatModel(path).stream(MESSAGES)
    next(s)
    s.close()
    assert state['closed']


def test_chat_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match='모델 폴더 없음'):
        ChatModel(tmp_path / '없음')


def test_chat_failure_propagates(fake_chat):
    path, seen = fake_chat

    def broken():
        yield types.SimpleNamespace(text='【결론】')
        raise RuntimeError('generation failed')

    seen['gen'] = broken
    with pytest.raises(RuntimeError, match='generation failed'):
        ''.join(r.text for r in ChatModel(path).stream(MESSAGES))


# ---- embedding ----
class FakeST:
    """SentenceTransformer 대역: encode 호출 인자와 그때의 max_seq_length를 기록"""

    def __init__(self, path, device=None, model_kwargs=None):
        self.path, self.device, self.kwargs, self.calls = path, device, model_kwargs, []
        self.max_seq_length = 512

    def encode(self, texts, **kw):
        self.calls.append((list(texts), kw, self.max_seq_length))
        return np.arange(len(texts) * 3, dtype=np.float16).reshape(len(texts), 3)


@pytest.fixture
def embedder(ms_env):
    emptied = []
    ms_env.setattr(emb_mod, 'SentenceTransformer', FakeST)
    ms_env.setattr(emb_mod.torch.mps, 'empty_cache', lambda: emptied.append(1))
    e = emb_mod.Embedder('models/emb')
    e.emptied = emptied
    return e


def test_embedding_calls_match_previous_retriever(embedder):
    """챗봇의 이전 Retriever와 같은 encode 인자: 문서 batch 4, 질문 batch 16 + prompt 'query', 정규화"""
    m = embedder.m
    assert m.device == 'mps' and str(m.kwargs['torch_dtype']) == 'torch.float16'
    v = embedder.encode(['조문1', '조문2'], 'document', 2304)
    embedder.encode(['질문'], 'query', 2304)
    assert m.calls[0] == (
        ['조문1', '조문2'],
        {'normalize_embeddings': True, 'batch_size': 4, 'convert_to_numpy': True},
        2304,
    )
    assert m.calls[1] == (
        ['질문'],
        {'normalize_embeddings': True, 'batch_size': 16, 'convert_to_numpy': True, 'prompt_name': 'query'},
        2304,
    )
    assert v.dtype == np.float32 and v.shape == (2, 3)
    assert m.max_seq_length == 512  # 요청 뒤 원래 값
    assert len(embedder.emptied) == 1  # 문서 인코딩 뒤에만 MPS 캐시 반납


def test_embedding_default_max_seq_and_bad_type(embedder):
    embedder.encode(['x'])
    assert embedder.m.calls[-1][2] == 512
    with pytest.raises(ValueError, match='input_type'):
        embedder.encode(['x'], 'passage')


# ---- rerank (작은 무작위 MLX Qwen3) ----
VOCAB = 64


class FakeTok:
    """글자마다 토큰 하나 (yes=1, no=2). 실제 BPE와 달리 길이를 쉽게 셀 수 있다"""

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False  # 노트북과 같이 특수 토큰을 붙이지 않음
        return [3 + ord(c) % (VOCAB - 3) for c in text]

    def convert_tokens_to_ids(self, token):
        return {'yes': 1, 'no': 2}.get(token)


def tiny_qwen3(tie=True, seed=0):
    mx.random.seed(seed)
    args = qwen3.ModelArgs(
        model_type='qwen3',
        hidden_size=64,
        num_hidden_layers=2,
        intermediate_size=128,
        num_attention_heads=4,
        rms_norm_eps=1e-6,
        vocab_size=VOCAB,
        num_key_value_heads=2,
        max_position_embeddings=4096,
        head_dim=16,
        tie_word_embeddings=tie,
        rope_theta=10000.0,
    )
    model = qwen3.Model(args)
    mx.eval(model.parameters())
    return model


@pytest.fixture
def make_reranker(ms_env, tmp_path):
    loads = []

    def build(model=None, max_length=rr_mod.MAX_LENGTH, tok=None):
        m = model or tiny_qwen3()

        def load(path):
            loads.append(path)
            return m, tok or FakeTok()

        ms_env.setattr(rr_mod, 'load', load)
        return rr_mod.Reranker(tmp_path, max_length=max_length)

    build.loads = loads
    return build


LAW_TASK = (
    'Given a Korean labor-law question, retrieve the statutory article that directly '
    'provides the legal basis for answering it.'
)


def test_prompt_strings_match_notebook():
    # lawbot_v4_reranker.ipynb 셀 3의 값을 글자 그대로 (바뀌면 Colab 결과와 비교할 수 없음)
    assert rr_mod.MAX_LENGTH == 2560
    assert rr_mod.PREFIX == (
        '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query '
        'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
        '<|im_start|>user\n'
    )
    assert rr_mod.SUFFIX == '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert rr_mod.pair_text(LAW_TASK, '질문', '문서') == f'Instruct: {LAW_TASK}\nQuery: 질문\nDocument: 문서'


def test_encode_layout_and_truncation(make_reranker):
    r = make_reranker(max_length=400)
    tok = FakeTok()
    pre, suf = tok.encode(rr_mod.PREFIX, add_special_tokens=False), tok.encode(rr_mod.SUFFIX, add_special_tokens=False)
    short = r.encode('q', 'd', LAW_TASK)
    assert short == pre + tok.encode(rr_mod.pair_text(LAW_TASK, 'q', 'd'), add_special_tokens=False) + suf
    long = r.encode('q', '가' * 1000, LAW_TASK)  # 가운데만 오른쪽에서 잘라 앞뒤 특수 문자열은 그대로
    assert len(long) == 400 and long[: len(pre)] == pre and long[-len(suf) :] == suf
    assert r.max_mid == 400 - len(pre) - len(suf)


@pytest.mark.parametrize('tie', [True, False])
def test_last_position_logits_match_full_forward(make_reranker, tie):
    # lm_head를 마지막 위치에만 곱해도 Model.__call__ 전체 계산의 마지막 위치와 같아야 함
    # 계산 구조 검증이므로 CPU에서 비교 (일부 Apple GPU는 fp32 행렬 곱을 저정밀 경로로 계산해 오차가 1e-3 수준)
    model = tiny_qwen3(tie=tie)
    r = make_reranker(model=model)
    ids = r.encode('해고 예고?', '근로기준법 제26조(해고의 예고)\n사용자는 30일 전에 예고', LAW_TASK)
    with mx.stream(mx.cpu):
        full = model(mx.array([ids]))[0, -1]
        got = r._no_yes_logits(ids)
        assert mx.allclose(got, full[mx.array([2, 1])], atol=1e-4).item()


def test_quantized_model_logits(make_reranker):
    # 배포는 4비트: QuantizedEmbedding.as_linear(tie) 경로도 전체 계산과 같아야 함
    model = tiny_qwen3(tie=True)
    nn.quantize(model, group_size=32, bits=4)
    r = make_reranker(model=model)
    ids = r.encode('q', 'doc')
    assert mx.allclose(r._no_yes_logits(ids), model(mx.array([ids]))[0, -1][mx.array([2, 1])], atol=1e-3).item()


def test_yes_probability_is_float32_softmax():
    assert rr_mod.yes_probability(mx.array([0.0, 0.0])) == 0.5
    p = rr_mod.yes_probability(mx.array([1.0, 3.0], dtype=mx.float16))
    assert abs(p - 1 / (1 + 2.718281828**-2)) < 1e-6
    # 노트북(float32)과 같이 차이가 크면 1.0으로 포화 → 동점은 챗봇이 임베딩 순위로 가름
    assert rr_mod.yes_probability(mx.array([-20.0, 20.0])) == rr_mod.yes_probability(mx.array([-30.0, 30.0])) == 1.0


def test_scores_in_unit_interval_and_instruction_used(make_reranker):
    r = make_reranker()
    docs = [f'근로기준법 제{i}조(제목{i})\n본문 {i}' for i in range(1, 5)]
    s = r.score('질문', docs, LAW_TASK)
    assert len(s) == 4 and all(0 <= x <= 1 for x in s)
    assert s == r.score('질문', docs, LAW_TASK)  # 같은 입력은 같은 점수
    assert s != r.score('질문', docs)  # instruction이 입력에 들어감 (없으면 기본 문구)
    assert len(make_reranker.loads) == 1  # 채점마다 다시 불러오지 않음


def test_bad_scores_raise(make_reranker):
    r = make_reranker()
    r._no_yes_logits = lambda ids: mx.array([float('nan'), 0.0])
    with pytest.raises(RuntimeError, match='점수가 올바르지 않습니다'):
        r.score('질문', ['a'])


def test_rerank_load_errors(tmp_path, make_reranker):
    with pytest.raises(FileNotFoundError, match='리랭커 모델 폴더 없음'):
        rr_mod.Reranker(tmp_path / '없음')
    m = tiny_qwen3()
    m.model_type = 'qwen2'
    with pytest.raises(ValueError, match='Qwen3 모델이 아닙니다'):
        make_reranker(model=m)

    class NoYes(FakeTok):
        def convert_tokens_to_ids(self, token):
            return None

    with pytest.raises(ValueError, match='yes/no 토큰'):
        make_reranker(tok=NoYes())
