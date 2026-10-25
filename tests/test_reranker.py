# tests/test_reranker.py  →  Qwen3-Reranker 계층 (reranker.py)과 service·app·check_deploy 연결
# 실제 4B 리랭커 대신 작은 무작위 MLX Qwen3 모델·가짜 토크나이저를 쓴다: 모델 파일·네트워크 불필요
# 실제 모델 스모크 테스트는 test_models.py (-m models)
import json
import types
import mlx.core as mx
import mlx.nn as nn
import pytest
from mlx_lm.models import qwen3
import lawbot.app as app_mod
import lawbot.reranker as rr_mod
import lawbot.service as service
from fakes import FakeLLM, FakeRetriever
from lawbot import check_deploy, deploy, paths
from lawbot.llm import build_prompt_v4

VOCAB = 64

class FakeTok:
    """글자마다 토큰 하나 (yes=1, no=2). 실제 BPE와 달리 길이를 쉽게 셀 수 있다"""
    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False            # 노트북과 같이 특수 토큰을 붙이지 않음
        return [3 + ord(c) % (VOCAB - 3) for c in text]

    def convert_tokens_to_ids(self, token):
        return {'yes': 1, 'no': 2}.get(token)

def tiny_qwen3(tie=True, seed=0):
    mx.random.seed(seed)
    args = qwen3.ModelArgs(model_type='qwen3', hidden_size=64, num_hidden_layers=2, intermediate_size=128,
                           num_attention_heads=4, rms_norm_eps=1e-6, vocab_size=VOCAB, num_key_value_heads=2,
                           max_position_embeddings=4096, head_dim=16, tie_word_embeddings=tie, rope_theta=10000.0)
    model = qwen3.Model(args)
    mx.eval(model.parameters())
    return model

@pytest.fixture
def make_reranker(env, tmp_path):
    loads = []
    def build(model=None, max_length=rr_mod.MAX_LENGTH, tok=None):
        m = model or tiny_qwen3()
        def load(path):
            loads.append(path)
            return m, tok or FakeTok()
        env.setattr(rr_mod, 'load', load)
        return rr_mod.Reranker(tmp_path, max_length=max_length)
    build.loads = loads
    return build

def cands(n):
    return [{'law': '근로기준법', 'article_id': f'제{i}조', 'title': f'제목{i}', 'score': 1 - i / 100,
             'doc': f'근로기준법 제{i}조(제목{i})\n본문 {i}'} for i in range(1, n + 1)]

def with_scores(r, table):
    """score를 문서별 고정 점수로 바꾼다 (정렬 규칙만 시험)"""
    r.score = lambda q, docs: [table[d] for d in docs]
    return r

# ---- 실험 노트북과 같은 입력·채점 ----
def test_prompt_strings_match_notebook():
    # lawbot_v4_reranker.ipynb 셀 3의 값을 글자 그대로 (바뀌면 Colab 결과와 비교할 수 없음)
    assert rr_mod.TASK == ('Given a Korean labor-law question, retrieve the statutory article that directly '
                           'provides the legal basis for answering it.')
    assert rr_mod.MAX_LENGTH == 2560 and rr_mod.CANDIDATES == 10
    assert rr_mod.PREFIX == ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query '
                             'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
                             '<|im_start|>user\n')
    assert rr_mod.SUFFIX == '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    assert rr_mod.pair_text('질문', '문서') == f'Instruct: {rr_mod.TASK}\nQuery: 질문\nDocument: 문서'

def test_encode_layout_and_truncation(make_reranker):
    r = make_reranker(max_length=400)
    tok = FakeTok()
    pre, suf = tok.encode(rr_mod.PREFIX, add_special_tokens=False), tok.encode(rr_mod.SUFFIX, add_special_tokens=False)
    short = r.encode('q', 'd')
    assert short == pre + tok.encode(rr_mod.pair_text('q', 'd'), add_special_tokens=False) + suf
    long = r.encode('q', '가' * 1000)                  # 가운데만 오른쪽에서 잘라 앞뒤 특수 문자열은 그대로
    assert len(long) == 400 and long[:len(pre)] == pre and long[-len(suf):] == suf
    assert r.max_mid == 400 - len(pre) - len(suf)

@pytest.mark.parametrize('tie', [True, False])
def test_last_position_logits_match_full_forward(make_reranker, tie):
    # lm_head를 마지막 위치에만 곱해도 Model.__call__ 전체 계산의 마지막 위치와 같아야 함
    model = tiny_qwen3(tie=tie)
    r = make_reranker(model=model)
    ids = r.encode('해고 예고?', '근로기준법 제26조(해고의 예고)\n사용자는 30일 전에 예고')
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
    assert abs(p - 1 / (1 + 2.718281828 ** -2)) < 1e-6
    # 노트북(float32)과 같이 차이가 크면 1.0으로 포화 → 동점은 임베딩 순위로 가름
    assert rr_mod.yes_probability(mx.array([-20.0, 20.0])) == rr_mod.yes_probability(mx.array([-30.0, 30.0])) == 1.0

def test_real_scores_in_unit_interval(make_reranker):
    r = make_reranker()
    s = r.score('질문', [c['doc'] for c in cands(4)])
    assert len(s) == 4 and all(0 <= x <= 1 for x in s)
    assert s == r.score('질문', [c['doc'] for c in cands(4)])   # 같은 입력은 같은 점수
    assert len(make_reranker.loads) == 1                          # 채점마다 다시 불러오지 않음

# ---- 정렬 규칙 ----
def test_ten_candidates_return_three_sorted(make_reranker):
    c = cands(10)
    r = with_scores(make_reranker(), {x['doc']: s for x, s in zip(c, [.1, .5, .2, .9, .3, .8, .05, .7, .4, .6])})
    out = r.rerank('질문', c, top_k=3)
    assert [h['article_id'] for h in out] == ['제4조', '제6조', '제8조']
    assert [h['rerank_score'] for h in out] == [.9, .8, .7]
    assert [h['emb_rank'] for h in out] == [4, 6, 8]

def test_ties_keep_embedding_order(make_reranker):
    c = cands(5)
    r = with_scores(make_reranker(), {x['doc']: s for x, s in zip(c, [.5, 1.0, .5, 1.0, 1.0])})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=3)] == ['제2조', '제4조', '제5조']
    r = with_scores(make_reranker(), {x['doc']: .5 for x in c})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=5)] == [x['article_id'] for x in c]

def test_few_candidates(make_reranker):
    c = cands(2)
    r = with_scores(make_reranker(), {c[0]['doc']: .1, c[1]['doc']: .9})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=3)] == ['제2조', '제1조']
    c = cands(3)
    r = with_scores(make_reranker(), {x['doc']: s for x, s in zip(c, [.2, .3, .1])})
    assert [h['article_id'] for h in r.rerank('질문', c, top_k=3)] == ['제2조', '제1조', '제3조']

def test_metadata_preserved_and_input_untouched(make_reranker):
    c = cands(4)
    before = json.dumps(c, ensure_ascii=False)
    r = with_scores(make_reranker(), {x['doc']: s for x, s in zip(c, [.1, .4, .3, .2])})
    out = r.rerank('질문', c, top_k=3)
    assert json.dumps(c, ensure_ascii=False) == before            # 입력 dict를 바꾸지 않음
    for h in out:
        orig = c[h['emb_rank'] - 1]
        assert {k: h[k] for k in orig} == orig                     # 조문·법령·제목·임베딩 유사도 그대로
        assert set(h) - set(orig) == {'rerank_score', 'emb_rank'}

def test_invalid_inputs(make_reranker):
    r = with_scores(make_reranker(), {})
    for q, c, k in (('', cands(3), 3), ('  ', cands(3), 3), (None, cands(3), 3), ('질문', [], 3),
                    ('질문', [{'article_id': '제1조'}], 3), ('질문', ['문자열'], 3), ('질문', cands(3), 0),
                    ('질문', cands(3), 2.5)):
        with pytest.raises(ValueError):
            r.rerank(q, c, top_k=k)

def test_bad_scores_raise(make_reranker):
    r = make_reranker()
    r._no_yes_logits = lambda ids: mx.array([float('nan'), 0.0])
    with pytest.raises(RuntimeError, match='점수가 올바르지 않습니다'):
        r.rerank('질문', cands(3))

def test_load_errors(env, tmp_path, make_reranker):
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

# ---- 설정 ----
def test_reranker_config(env):
    v4, v1 = deploy.get_profile('v4'), deploy.get_profile('v1')
    assert deploy.reranker(v4) is None and deploy.reranker(v1) is None   # 기본 꺼짐
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    assert deploy.reranker(v4) == deploy.RerankerConfig(paths.V4_RERANKER, 10)
    env.setenv('LAWBOT_RERANKER_MODEL_PATH', 'models/v4/rr')
    env.setenv('LAWBOT_RERANKER_CANDIDATES', '20')
    assert deploy.reranker(v4) == deploy.RerankerConfig(paths.ROOT / 'models/v4/rr', 20)
    with pytest.raises(ValueError, match='v4 프로필에서만'):
        deploy.reranker(v1)
    env.setenv('LAWBOT_RERANKER_ENABLED', 'yes')
    with pytest.raises(ValueError, match='0 또는 1'):
        deploy.reranker(v4)

# ---- 질의응답 흐름 ----
class CandRetriever(FakeRetriever):
    """v4 형식 후보 10개를 임베딩 순으로 돌려주고 요청한 k를 기록"""
    def search(self, query, k=3):
        self.query = query
        self.ks = getattr(self, 'ks', []) + [k]
        return cands(10)[:k]

class FakeReranker:
    created = 0
    fail = None

    def __init__(self, path, candidates=10):
        FakeReranker.created += 1
        self.path, self.candidates, self.calls = path, candidates, []

    def rerank(self, question, candidates, top_k=3):     # 임베딩 역순이 리랭킹 순서라고 가정
        if FakeReranker.fail:
            raise FakeReranker.fail
        self.calls.append((question, [c['article_id'] for c in candidates], top_k))
        rev = list(reversed(candidates))
        return [c | {'rerank_score': 1 - i / 10, 'emb_rank': len(candidates) - i} for i, c in enumerate(rev[:top_k])]

def v4_app(env, tmp_path, enabled, **extra):
    FakeRetriever.created = FakeLLM.created = FakeReranker.created = 0
    FakeReranker.fail = None
    FakeLLM.chunks = ['【결론】', '예고해야', ' 합니다.']
    env.setattr(service, 'Retriever', CandRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(service, 'Reranker', FakeReranker)
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_MAX_RESIDENT', '1')
    folder = tmp_path / 'rr'
    folder.mkdir(exist_ok=True)
    env.setenv('LAWBOT_RERANKER_MODEL_PATH', str(folder))
    env.setenv('LAWBOT_RERANKER_ENABLED', '1' if enabled else '0')
    for k, v in extra.items():
        env.setenv(k, v)
    return service.load_app()

def test_disabled_is_embedding_top3(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=False)
    assert a.reranker is None and FakeReranker.created == 0         # 꺼져 있으면 리랭커를 만들지 않음
    *_, (stage, hits, raw, st) = service.ask(a, '해고 예고?', 'fast')
    assert stage == 'done' and a.retriever.ks == [3]
    assert hits == cands(3)                                          # 기존 검색 결과 그대로 (키 추가 없음)
    assert a.models.loaded['fast'].args == ('해고 예고?', cands(3))
    assert 'rerank' not in st

def test_disabled_v1_unchanged(env):
    # v1 기본 실행: 리랭커 없이 기존 검색 결과를 그대로 답변 모델에 넘김
    FakeReranker.created = 0
    FakeLLM.chunks = ['【결론】', '예고해야', ' 합니다.']
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(service, 'Reranker', FakeReranker)
    a = service.load_app()
    from fakes import HITS
    *_, (_, hits, _, st) = service.ask(a, '질문', 'fast')
    assert a.reranker is None and FakeReranker.created == 0 and hits == HITS and 'rerank' not in st

def test_enabled_reranks_top10_to_top3(env, tmp_path, capsys):
    a = v4_app(env, tmp_path, enabled=True)
    assert FakeReranker.created == 1 and a.reranker.path == tmp_path / 'rr'
    *_, (stage, hits, raw, st) = service.ask(a, '해고 예고?', 'fast')
    assert a.retriever.ks == [10]
    assert a.reranker.calls == [('해고 예고?', [f'제{i}조' for i in range(1, 11)], 3)]
    assert [h['article_id'] for h in hits] == ['제10조', '제9조', '제8조']   # 리랭킹 점수순
    assert a.models.loaded['fast'].args == ('해고 예고?', hits)               # 최종 3개만 답변 모델로
    assert st['search'] >= 0 and st['rerank'] >= 0
    assert '리랭킹' in capsys.readouterr().out                                # 단계별 시간 로그

def test_reranker_loaded_once(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=True)
    chat = app_mod.make_chat(a)
    for key in ('fast', 'pro', 'fast'):
        list(chat('질문', [], key, 'full'))
    assert FakeReranker.created == 1 and len(a.reranker.calls) == 3

def test_enabled_candidates_env(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=True, LAWBOT_RERANKER_CANDIDATES='5')
    list(service.ask(a, '질문', 'fast'))
    assert a.retriever.ks == [5]
    with pytest.raises(ValueError, match='3 이상'):
        v4_app(env, tmp_path, enabled=True, LAWBOT_RERANKER_CANDIDATES='2')

def test_enabled_refuses_before_loading(env, tmp_path):
    env.setenv('LAWBOT_RERANKER_MODEL_PATH', str(tmp_path / '없음'))
    env.setattr(service, 'Retriever', CandRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(service, 'Reranker', FakeReranker)
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    FakeRetriever.created = FakeLLM.created = FakeReranker.created = 0
    with pytest.raises(RuntimeError, match='리랭커 모델 폴더가 없습니다'):
        service.load_app()
    env.setenv('LAWBOT_PROFILE', 'v1')
    with pytest.raises(ValueError, match='v4 프로필에서만'):
        service.load_app()
    assert FakeRetriever.created == FakeLLM.created == FakeReranker.created == 0

def test_reranker_failure_is_not_hidden(env, tmp_path):
    # 리랭커 오류를 임베딩 순위로 몰래 되돌리지 않고 오류로 올린다. 답변 생성도 하지 않음
    a = v4_app(env, tmp_path, enabled=True)
    FakeReranker.fail = RuntimeError('리랭커 점수가 올바르지 않습니다')
    chat = app_mod.make_chat(a)
    with pytest.raises(RuntimeError, match='점수가 올바르지'):
        list(chat('질문', [], 'fast', 'full'))
    assert not hasattr(a.models.loaded['fast'], 'args')

def test_chat_shows_rerank_info(env, tmp_path):
    a = v4_app(env, tmp_path, enabled=True)
    text, src, stats = list(app_mod.make_chat(a)('해고 예고?', [], 'fast', 'full'))[-1]
    assert text == '【결론】예고해야 합니다.'                         # 스트리밍 답변 그대로
    assert src.index('제10조') < src.index('제9조') < src.index('제8조')
    assert '**근로기준법 제10조 제목10** · 유사도 0.900 · 리랭커 1.000 (임베딩 10위)' in src
    assert '리랭킹' in stats and stats.index('검색') < stats.index('리랭킹')
    off = v4_app(env, tmp_path, enabled=False)
    _, src, stats = list(app_mod.make_chat(off)('해고 예고?', [], 'fast', 'full'))[-1]
    assert '리랭커' not in src and '리랭킹' not in stats

def test_prompt_format_unchanged_by_rerank_keys():
    # 리랭커가 더한 키(rerank_score·emb_rank)는 답변 모델의 [참고 조문]에 영향이 없다
    hits = cands(3)
    tagged = [h | {'rerank_score': .9, 'emb_rank': 7} for h in hits]
    assert build_prompt_v4(tagged, '질문') == build_prompt_v4(hits, '질문')

# ---- 배포 검사 ----
def fake_reranker_folder(folder, bits=4, vocab=('yes', 'no')):
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'model_type': 'qwen3', 'hidden_size': 2560,
                                                    'quantization': {'group_size': 64, 'bits': bits}}))
    (folder / 'tokenizer.json').write_text(json.dumps({'model': {'vocab': {v: i for i, v in enumerate(vocab)}}}))
    (folder / 'model.safetensors').write_bytes(b'')

def test_check_deploy_reranker(env, tmp_path):
    def run():
        return [(lv, m) for lv, m in check_deploy.run('v4') if '리랭커' in m]
    assert run() == [('INFO', '리랭커 꺼짐 (LAWBOT_RERANKER_ENABLED=1이면 리랭커 폴더도 검사)')]
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    env.setenv('LAWBOT_RERANKER_MODEL_PATH', str(tmp_path / 'rr'))
    assert ('FAIL', f"리랭커 모델 폴더 없음: {tmp_path / 'rr'}") in run()
    fake_reranker_folder(tmp_path / 'rr')
    out = run()
    assert all(lv == 'OK' for lv, _ in out), out
    assert any('qwen3 Qwen3-4B' in m for _, m in out)
    env.setenv('LAWBOT_RERANKER_MODEL_PATH', str(tmp_path / 'rr8'))
    fake_reranker_folder(tmp_path / 'rr8', bits=8, vocab=('a',))
    assert [lv for lv, _ in run()].count('FAIL') == 2
    env.setenv('LAWBOT_PROFILE', 'v1')
    assert any(lv == 'FAIL' and 'v4 프로필에서만' in m for lv, m in check_deploy.run('v1'))
