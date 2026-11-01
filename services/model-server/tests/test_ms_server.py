# services/model-server/tests/test_ms_server.py  →  설정·레지스트리·시작·점검과 실제 HTTP 서버(uvicorn)의 끊김 처리
import contextlib
import threading
import time

import httpx
import pytest

from lawbot_model_server import api, check, config, server
from lawbot_model_server.config import ModelEntry
from lawbot_model_server.envfile import ROOT
from lawbot_model_server.registry import Registry, UnknownModel

from .ms_fakes import AUTH, ENTRIES, TOKEN, FakeChat, registry


def set_minimal(env, **extra):
    env.setenv('LAWBOT_MODEL_SERVER_PORT', '65535')  # 의미 없는 경계값 (테스트에 실제 포트를 쓰지 않음)
    env.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    env.setenv('LAWBOT_SERVE_CHAT', 'qwen3.8-27b-4bit=models/v4/qwen3.8_27b_mlx_4bit, lawbot-fast-v4=/abs/fast')
    for k, v in extra.items():
        env.setenv(k, v)


# ---- 설정 ----
def test_config_minimal(ms_env):
    set_minimal(ms_env, LAWBOT_SERVE_EMBEDDING='lawbot-emb-v4=models/v4/emb_v4_fp16')
    c = config.load()
    assert c.port == 65535 and c.token == TOKEN and c.max_resident == 2
    assert [(e.id, e.kind) for e in c.models] == [
        ('qwen3.8-27b-4bit', 'chat'),
        ('lawbot-fast-v4', 'chat'),
        ('lawbot-emb-v4', 'embedding'),
    ]
    assert c.models[0].path == ROOT / 'models/v4/qwen3.8_27b_mlx_4bit' and str(c.models[1].path) == '/abs/fast'
    assert c.preload == ('qwen3.8-27b-4bit', 'lawbot-fast-v4')  # 기본: chat 앞에서부터 상주 수만큼


def test_config_preload_and_resident(ms_env):
    set_minimal(ms_env, LAWBOT_MAX_RESIDENT='1')
    assert config.load().preload == ('qwen3.8-27b-4bit',)
    ms_env.setenv('LAWBOT_SERVE_PRELOAD', '')
    assert config.load().preload == ()
    ms_env.setenv('LAWBOT_SERVE_PRELOAD', 'lawbot-fast-v4')
    assert config.load().preload == ('lawbot-fast-v4',)
    for bad, msg in (('없음', 'chat 모델이 아닌'), ('qwen3.8-27b-4bit,lawbot-fast-v4', 'LAWBOT_MAX_RESIDENT')):
        ms_env.setenv('LAWBOT_SERVE_PRELOAD', bad)
        with pytest.raises(ValueError, match=msg):
            config.load()


@pytest.mark.parametrize(
    'key,value,exc,msg',
    [
        ('LAWBOT_MODEL_SERVER_PORT', '', RuntimeError, 'LAWBOT_MODEL_SERVER_PORT를 적으세요'),
        ('LAWBOT_MODEL_SERVER_PORT', '1023', ValueError, '1024–65535'),
        ('LAWBOT_MODEL_TOKEN', 'short', RuntimeError, '16자 미만'),
        ('LAWBOT_SERVE_CHAT', 'no-path', ValueError, "'id=경로'"),
        ('LAWBOT_SERVE_CHAT', 'bad id=/x', ValueError, "'id=경로'"),
        ('LAWBOT_SERVE_CHAT', 'a=/x,a=/y', ValueError, '겹칩니다'),
        ('LAWBOT_SERVE_CHAT', '', RuntimeError, '제공할 모델이 없습니다'),
        ('LAWBOT_MAX_RESIDENT', '0', ValueError, 'LAWBOT_MAX_RESIDENT'),
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
def test_registry_lru_and_kinds():
    loads = []
    reg = registry(max_resident=1, loads=loads)
    assert loads == [ENTRIES[2].path, ENTRIES[3].path]  # embedding·rerank는 시작할 때
    a, s = reg.get('chat-a', 'chat')
    assert s is not None and reg.get('chat-a', 'chat') == (a, None)  # 두 번째는 불러오지 않음
    reg.get('chat-b', 'chat')
    assert list(reg.chat) == ['chat-b'] and not reg.is_loaded('chat-a')
    with pytest.raises(UnknownModel):
        reg.get('emb', 'chat')
    with pytest.raises(UnknownModel):
        reg.get('없음', 'embedding')


def test_preload_first_failure_stops_rest_warn(capsys):
    def flaky(path):
        if path.name == 'b':
            raise FileNotFoundError(path)
        return FakeChat(path)

    reg = Registry(ENTRIES, {'chat': flaky, 'embedding': FakeChat, 'rerank': FakeChat}, max_resident=2)
    reg.preload(['chat-a', 'chat-b'])
    assert list(reg.chat) == ['chat-a'] and '미리 불러오기 실패' in capsys.readouterr().out
    with pytest.raises(FileNotFoundError):
        Registry(ENTRIES, {'chat': flaky}, 2).preload(['chat-b', 'chat-a'])


def test_load_models_sets_registry():
    app = api.create_app(TOKEN)
    cfg = config.Config(0, TOKEN, ENTRIES, ('chat-a',), 1)

    class Reg(Registry):
        def __init__(self, entries, max_resident):
            super().__init__(entries, registry().loaders, max_resident)

    server.load_models(app, cfg, Reg)
    assert app.state.registry.is_loaded('chat-a') and app.state.registry.is_loaded('emb')


# ---- 점검 (모델 없이) ----
def test_check_reports_missing(ms_env, tmp_path):
    good = tmp_path / 'chat'
    good.mkdir()
    (good / 'config.json').write_text('{}')
    emb = tmp_path / 'emb'
    emb.mkdir()
    (emb / 'config.json').write_text('{}')
    set_minimal(ms_env, LAWBOT_SERVE_CHAT=f'c={good},d={tmp_path / "없음"}', LAWBOT_SERVE_EMBEDDING=f'e={emb}')
    out = check.run()
    assert ('OK', f'chat c: {good}') in out
    assert any(lv == 'FAIL' and 'chat d: 폴더 없음' in m for lv, m in out)
    assert any(lv == 'FAIL' and 'config_sentence_transformers.json 없음' in m for lv, m in out)
    assert all(TOKEN not in m for _, m in out)


def write_safetensors(path, dtype):
    import json
    import struct

    header = json.dumps({'w': {'dtype': dtype, 'shape': [1], 'data_offsets': [0, 2]}}).encode()
    path.write_bytes(struct.pack('<Q', len(header)) + header + b'\0\0')


def fake_mlx(folder, bits=4, hidden=2560, adapter=False, vocab=None):
    import json

    folder.mkdir(parents=True)
    (folder / 'config.json').write_text(
        json.dumps({'model_type': 'qwen3', 'hidden_size': hidden, 'quantization': {'group_size': 64, 'bits': bits}})
    )
    (folder / 'tokenizer.json').write_text(json.dumps({'model': {'vocab': {v: i for i, v in enumerate(vocab or [])}}}))
    (folder / 'tokenizer_config.json').write_text(json.dumps({'chat_template': '{{ messages }}'}))
    write_safetensors(folder / 'model.safetensors', 'U32')
    if adapter:
        (folder / 'adapters.safetensors').write_bytes(b'')


def test_check_reports_each_model_folder(ms_env, tmp_path):
    """챗봇의 이전 check_deploy 모델 폴더 검사: 양자화·크기·어댑터·chat template·query 프롬프트·yes/no 토큰"""
    import json

    emb = tmp_path / 'emb'
    emb.mkdir()
    write_safetensors(emb / 'model.safetensors', 'F16')
    (emb / 'config.json').write_text('{}')
    (emb / 'config_sentence_transformers.json').write_text(json.dumps({'prompts': {'query': 'Instruct: q\nQuery:'}}))
    (emb / 'modules.json').write_text('[]')
    (emb / 'tokenizer.json').write_text('{}')
    fake_mlx(tmp_path / 'fast')
    fake_mlx(tmp_path / 'pro', hidden=5120, adapter=True)
    fake_mlx(tmp_path / 'rr', vocab=['yes', 'no'])
    fake_mlx(tmp_path / 'rr8', bits=8, vocab=['a'])
    set_minimal(
        ms_env,
        LAWBOT_SERVE_CHAT=f'fast={tmp_path / "fast"},pro={tmp_path / "pro"}',
        LAWBOT_SERVE_EMBEDDING=f'emb={emb}',
        LAWBOT_SERVE_RERANK=f'rr={tmp_path / "rr"},rr8={tmp_path / "rr8"}',
    )
    text = '\n'.join(f'{lv} {m}' for lv, m in check.run())
    assert 'OK chat fast: qwen3 Qwen3-4B' in text and 'chat pro: qwen3 Qwen3-14B' in text
    assert "FAIL chat pro: 어댑터 파일 남아 있음 ['adapters.safetensors']" in text
    assert "OK embedding emb: dtype {'F16': 1}" in text and "query 프롬프트 'Instruct: q\\nQuery:'" in text
    assert 'OK rerank rr: qwen3 Qwen3-4B' in text and 'OK rerank rr: tokenizer.json yes/no 토큰 있음' in text
    assert 'FAIL rerank rr8: qwen3' in text and 'FAIL rerank rr8: tokenizer.json' in text


# ---- 실제 HTTP 서버: 스트림 중 끊김 ----
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
def test_disconnect_stops_generation_and_frees_lock(ms_env):
    FakeChat.chunks, FakeChat.delay, FakeChat.finish = ['【결론】', '답'] + ['더'] * 2000, 0.002, 'stop'
    reg = registry()
    app = api.create_app(TOKEN, reg)
    body = {'model': 'chat-a', 'messages': [{'role': 'user', 'content': 'q'}], 'stream': True}
    try:
        with running(app) as url, httpx.Client(trust_env=False, timeout=10) as c:
            with c.stream('POST', f'{url}/v1/chat/completions', headers=AUTH, json=body) as r:
                assert r.status_code == 200
                next(r.iter_lines())  # 첫 토큰을 받고 끊음 (정지 버튼)
            llm = reg.chat['chat-a']
            deadline = time.monotonic() + 10
            while not llm.closed and time.monotonic() < deadline:
                time.sleep(0.01)
            assert llm.closed and llm.yielded < 2002  # 끝까지 생성하지 않음
            assert app.state.lock.acquire(timeout=5)  # 락이 풀림
            app.state.lock.release()
            r = c.post(f'{url}/v1/embeddings', headers=AUTH, json={'model': 'emb', 'input': 'x'}, timeout=5)
            assert r.status_code == 200  # 다음 요청을 바로 받음
    finally:
        FakeChat.chunks, FakeChat.delay = ['【결론】', '예고해야', ' 합니다.'], 0.0


@pytest.mark.integration
def test_real_server_requires_token(ms_env):
    with running(api.create_app(TOKEN, registry())) as url, httpx.Client(trust_env=False) as c:
        assert c.get(f'{url}/health').status_code == 401
        assert c.get(f'{url}/health', headers=AUTH).json() == {'ready': True}


def test_entry_kinds_unique_ids():
    assert len({e.id for e in ENTRIES}) == len(ENTRIES)
    assert ModelEntry('x', 'chat', ROOT).kind == 'chat'
