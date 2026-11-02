# tests/test_embed_cache.py  →  조문 임베딩 캐시 (lawbot.embed_cache): 재시작 때 조문 전체를 다시 임베딩하지 않음
import numpy as np
import pytest

from lawbot import embed_cache
from lawbot import retriever as retriever_mod


class Enc:
    """api_client.Embedder 대역: 호출을 기록, offset으로 '모델 폴더가 바뀐' 상황을 흉내"""

    def __init__(self, model='emb', offset=0.0):
        self.model, self.offset, self.calls = model, offset, []

    def encode(self, texts, input_type='document', max_seq_length=None):
        self.calls.append((len(texts), input_type, max_seq_length))
        v = np.zeros((len(texts), 8), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 8] += 1
        return v / np.linalg.norm(v, axis=1, keepdims=True) + self.offset


DOCS = ['근로기준법 제1조(목적)\n본문 가', '근로기준법 제2조(정의)\n본문 나', '최저임금법 제6조(효력)\n본문 다']


def test_second_start_reuses_vectors_bit_identical(tmp_path):
    c = embed_cache.EmbedCache(tmp_path)
    e1 = Enc()
    first = c.encode(e1, DOCS, 2304)
    assert e1.calls == [(3, 'document', 2304), (1, 'document', 2304)]  # 전체 + 확인용 1개
    e2 = Enc()
    second = c.encode(e2, DOCS, 2304)
    assert e2.calls == [(1, 'document', 2304)]  # 확인용 1개만
    assert second.dtype == np.float32 and np.array_equal(first, second) and np.array_equal(second, Enc().encode(DOCS))


def test_key_changes_with_text_model_or_max_seq(tmp_path):
    c = embed_cache.EmbedCache(tmp_path)
    c.encode(Enc(), DOCS, 2304)
    c.encode(Enc(), [DOCS[0] + '.'] + DOCS[1:], 2304)  # 한 글자
    c.encode(Enc(model='emb-2'), DOCS, 2304)
    c.encode(Enc(), DOCS, 3072)
    assert len(list(tmp_path.glob('*.npz'))) == 4


def test_probe_mismatch_rebuilds(tmp_path, capsys):
    c = embed_cache.EmbedCache(tmp_path)
    c.encode(Enc(), DOCS, 2304)
    changed = Enc(offset=0.5)  # 같은 id로 모델 폴더가 바뀜
    got = c.encode(changed, DOCS, 2304)
    assert changed.calls[0] == (1, 'document', 2304) and changed.calls[1] == (3, 'document', 2304)
    assert np.array_equal(got, Enc(offset=0.5).encode(DOCS)) and '다시 임베딩' in capsys.readouterr().out
    again = Enc(offset=0.5)
    c.encode(again, DOCS, 2304)
    assert again.calls == [(1, 'document', 2304)]  # 새 캐시로 바뀜


def test_unwritable_or_broken_cache_only_warns(tmp_path, capsys):
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    got = embed_cache.EmbedCache(blocker / 'sub').encode(Enc(), DOCS, 2304)  # 폴더를 만들 수 없음
    assert np.array_equal(got, Enc().encode(DOCS)) and '경고' in capsys.readouterr().out
    c = embed_cache.EmbedCache(tmp_path / 'c')
    c.encode(Enc(), DOCS, 2304)
    next((tmp_path / 'c').glob('*.npz')).write_bytes(b'broken')
    e = Enc()
    assert np.array_equal(c.encode(e, DOCS, 2304), Enc().encode(DOCS)) and e.calls[0] == (3, 'document', 2304)


def test_retriever_uses_cache_for_both_indexes(tmp_path):
    from lawbot.paths import V4_DECREE_ARTICLES

    c = embed_cache.EmbedCache(tmp_path)
    off = retriever_mod.Retriever(Enc(), V4_DECREE_ARTICLES, 2304, decree_max_seq=3072)
    e = Enc()
    on = retriever_mod.Retriever(e, V4_DECREE_ARTICLES, 2304, decree_max_seq=3072, cache=c)
    assert np.array_equal(on.D, off.D) and np.array_equal(on.D_dec, off.D_dec)
    e2 = Enc()
    again = retriever_mod.Retriever(e2, V4_DECREE_ARTICLES, 2304, decree_max_seq=3072, cache=c)
    assert e2.calls == [(1, 'document', 2304), (1, 'document', 3072)]  # 법률·하위법령 확인용 1개씩만
    assert np.array_equal(again.D, off.D) and np.array_equal(again.D_dec, off.D_dec)


@pytest.mark.parametrize('value,on', [(None, True), ('1', True), ('0', False)])
def test_setting(env, value, on):
    from lawbot import settings
    from test_settings import BASE

    s = settings.load(BASE | ({'LAWBOT_EMBED_CACHE': value} if value else {}))
    assert (s.embed_cache is not None) == on
    if on:
        assert s.embed_cache == embed_cache.CACHE_DIR


def test_bad_setting(env):
    from lawbot import settings
    from test_settings import BASE

    with pytest.raises(settings.SettingsError, match='LAWBOT_EMBED_CACHE'):
        settings.load(BASE | {'LAWBOT_EMBED_CACHE': 'yes'})


def test_cache_dir_is_gitignored():
    from lawbot.paths import ROOT

    assert 'cache/' in (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert embed_cache.CACHE_DIR == ROOT / 'cache' / 'embeddings'


def test_load_app_passes_cache(env, tmp_path):
    import lawbot.service as service
    from fakes import FakeLLM, FakeRetriever

    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setattr(embed_cache, 'CACHE_DIR', tmp_path)
    env.setenv('LAWBOT_EMBED_CACHE', '1')
    a = service.load_app()
    assert a.retriever.args[1]['cache'].folder == tmp_path
    env.setenv('LAWBOT_EMBED_CACHE', '0')
    assert 'cache' not in service.load_app().retriever.args[1]
