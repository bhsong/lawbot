# Configuration, resident models, startup checks and authenticated HTTP integration.
import contextlib
import threading
import time

import httpx
import pytest

from lawbot_model_server import api, check, config, server
from lawbot_model_server.config import ModelEntry
from lawbot_model_server.envfile import ROOT
from lawbot_model_server.registry import Registry, UnknownModel

from .ms_fakes import AUTH, ENTRIES, TOKEN, FakeEmbedder, FakeReranker, registry


def set_minimal(env, **extra):
    env.setenv('LAWBOT_MODEL_SERVER_PORT', '65535')  # 의미 없는 경계값 (테스트에 실제 포트를 쓰지 않음)
    env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    env.setenv('LAWBOT_SERVE_EMBEDDING', 'lawbot-emb-v4=models/v4/emb_v4_fp16')
    for k, v in extra.items():
        env.setenv(k, v)


# ---- 설정 ----
def test_config_minimal(ms_env):
    set_minimal(ms_env, LAWBOT_SERVE_RERANK='lawbot-rerank-v4=/abs/rerank')
    c = config.load()
    assert c.port == 65535 and c.token == TOKEN
    assert [(e.id, e.kind) for e in c.models] == [
        ('lawbot-emb-v4', 'embedding'),
        ('lawbot-rerank-v4', 'rerank'),
    ]
    assert c.models[0].path == ROOT / 'models/v4/emb_v4_fp16'
    assert str(c.models[1].path) == '/abs/rerank'


@pytest.mark.parametrize(
    'key,value,exc,msg',
    [
        ('LAWBOT_MODEL_SERVER_PORT', '', RuntimeError, 'LAWBOT_MODEL_SERVER_PORT를 적으세요'),
        ('LAWBOT_MODEL_SERVER_PORT', '1023', ValueError, '1024–65535'),
        ('LAWBOT_MODEL_TOKEN', 'short', RuntimeError, '16자 미만'),
        ('LAWBOT_SERVE_EMBEDDING', 'no-path', ValueError, "'id=경로'"),
        ('LAWBOT_SERVE_EMBEDDING', 'bad id=/x', ValueError, "'id=경로'"),
        ('LAWBOT_SERVE_EMBEDDING', 'a=/x,a=/y', ValueError, '겹칩니다'),
        ('LAWBOT_SERVE_EMBEDDING', '', RuntimeError, '제공할 모델이 없습니다'),
    ],
)
def test_config_errors(ms_env, key, value, exc, msg):
    set_minimal(ms_env)
    ms_env.setenv(key, value)
    with pytest.raises(exc, match=msg):
        config.load()


def test_config_never_prints_token(ms_env, capsys):
    set_minimal(ms_env, LAWBOT_MODEL_TOKEN='x' * 10)
    with pytest.raises(RuntimeError) as e:
        config.load()
    assert 'x' * 10 not in str(e.value) + capsys.readouterr().out


def test_host_is_fixed():
    assert config.HOST == '127.0.0.1'
    s = server.build_server(api.create_app(TOKEN), 0)
    assert s.config.host == '127.0.0.1' and s.config.access_log is False


# ---- 레지스트리 ----
def test_registry_resident_models_and_kinds():
    loads = []
    reg = registry(loads=loads)
    assert loads == [entry.path for entry in ENTRIES]
    assert all(reg.is_loaded(entry.id) for entry in ENTRIES)
    assert reg.get('emb', 'embedding') is reg.fixed['emb']
    assert loads == [entry.path for entry in ENTRIES]
    with pytest.raises(UnknownModel):
        reg.get('emb', 'rerank')
    with pytest.raises(UnknownModel):
        reg.get('없음', 'embedding')


def test_loading_failure_is_not_hidden():
    def broken(path):
        raise FileNotFoundError(path)

    reg = Registry(ENTRIES, {'embedding': broken, 'rerank': FakeReranker})
    with pytest.raises(FileNotFoundError):
        reg.load_fixed()


def test_load_models_sets_registry():
    app = api.create_app(TOKEN)
    cfg = config.Config(0, TOKEN, ENTRIES)

    class Reg(Registry):
        def __init__(self, entries):
            super().__init__(entries, {'embedding': FakeEmbedder, 'rerank': FakeReranker})

    server.load_models(app, cfg, Reg)
    assert all(app.state.registry.is_loaded(entry.id) for entry in ENTRIES)


# ---- 점검 (모델 없이) ----
def test_check_reports_missing(ms_env, tmp_path):
    good = tmp_path / 'rerank'
    good.mkdir()
    (good / 'config.json').write_text('{}')
    emb = tmp_path / 'emb'
    emb.mkdir()
    (emb / 'config.json').write_text('{}')
    set_minimal(ms_env, LAWBOT_SERVE_RERANK=f'r={good},missing={tmp_path / "없음"}', LAWBOT_SERVE_EMBEDDING=f'e={emb}')
    out = check.run()
    assert ('OK', f'rerank r: {good}') in out
    assert any(lv == 'FAIL' and 'rerank missing: 폴더 없음' in m for lv, m in out)
    assert any(lv == 'FAIL' and 'config_sentence_transformers.json 없음' in m for lv, m in out)
    assert all(TOKEN not in m for _, m in out)


def write_safetensors(path, dtype):
    import json
    import struct

    header = json.dumps({'w': {'dtype': dtype, 'shape': [1], 'data_offsets': [0, 2]}}).encode()
    path.write_bytes(struct.pack('<Q', len(header)) + header + b'\0\0')


def fake_mlx(folder, bits=4, hidden=2560, vocab=None):
    import json

    folder.mkdir(parents=True)
    (folder / 'config.json').write_text(
        json.dumps({'model_type': 'qwen3', 'hidden_size': hidden, 'quantization': {'group_size': 64, 'bits': bits}})
    )
    (folder / 'tokenizer.json').write_text(json.dumps({'model': {'vocab': {v: i for i, v in enumerate(vocab or [])}}}))
    write_safetensors(folder / 'model.safetensors', 'U32')


def test_check_reports_each_model_folder(ms_env, tmp_path):
    """Check embedding precision/prompts and reranker quantization/yes-no vocabulary."""
    import json

    emb = tmp_path / 'emb'
    emb.mkdir()
    write_safetensors(emb / 'model.safetensors', 'F16')
    (emb / 'config.json').write_text('{}')
    (emb / 'config_sentence_transformers.json').write_text(json.dumps({'prompts': {'query': 'Instruct: q\nQuery:'}}))
    (emb / 'modules.json').write_text('[]')
    (emb / 'tokenizer.json').write_text('{}')
    fake_mlx(tmp_path / 'rr', vocab=['yes', 'no'])
    fake_mlx(tmp_path / 'rr8', bits=8, vocab=['a'])
    set_minimal(
        ms_env,
        LAWBOT_SERVE_EMBEDDING=f'emb={emb}',
        LAWBOT_SERVE_RERANK=f'rr={tmp_path / "rr"},rr8={tmp_path / "rr8"}',
    )
    text = '\n'.join(f'{lv} {m}' for lv, m in check.run())
    assert "OK embedding emb: dtype {'F16': 1}" in text and "query 프롬프트 'Instruct: q\\nQuery:'" in text
    assert 'OK rerank rr: qwen3 Qwen3-4B' in text and 'OK rerank rr: tokenizer.json yes/no 토큰 있음' in text
    assert 'FAIL rerank rr8: qwen3' in text and 'FAIL rerank rr8: tokenizer.json' in text


# ---- Authenticated HTTP server ----
@contextlib.contextmanager
def running(app):
    s = server.build_server(app, 0)
    t = threading.Thread(target=s.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not s.started and time.monotonic() < deadline:
        time.sleep(0.01)
    port = s.servers[0].sockets[0].getsockname()[1]
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        s.should_exit = True
        t.join(10)


@pytest.mark.integration
def test_real_server_requires_token(ms_env):
    with running(api.create_app(TOKEN, registry())) as url, httpx.Client(trust_env=False) as c:
        assert c.get(f'{url}/health').status_code == 401
        assert c.get(f'{url}/health', headers=AUTH).json() == {'ready': True, 'commit': None}


def test_entry_kinds_unique_ids():
    assert len({e.id for e in ENTRIES}) == len(ENTRIES)
    assert ModelEntry('x', 'embedding', ROOT).kind == 'embedding'
