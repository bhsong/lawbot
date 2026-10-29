# tests/test_security.py  →  uv run pytest
# 모델·네트워크 없이 보안 설정만 검사 (env 픽스처·set_auth는 conftest.py)
import subprocess
import sys
import pytest
from conftest import set_auth
from lawbot.security import get_local_launch_config

# 정상 설정
def test_valid_config(env):
    set_auth(env)
    cfg = get_local_launch_config()
    assert cfg['server_name'] == '127.0.0.1'
    assert cfg['server_port'] == 7860
    assert cfg['auth'] == ('tester', 'test-only-pw')
    assert cfg['share'] is False

def test_username_is_stripped(env):
    set_auth(env, user='  tester  ')
    assert get_local_launch_config()['auth'][0] == 'tester'

# 잘못된 설정
@pytest.mark.parametrize('user,pw,missing', [
    (None, 'test-only-pw', ['LAWBOT_USER']),
    ('tester', None, ['LAWBOT_PW']),
    (None, None, ['LAWBOT_USER', 'LAWBOT_PW']),
    ('', 'test-only-pw', ['LAWBOT_USER']),
    ('tester', '', ['LAWBOT_PW']),
    ('   ', 'test-only-pw', ['LAWBOT_USER']),
    ('tester', ' \t\n ', ['LAWBOT_PW']),
], ids=['no-user', 'no-pw', 'none', 'empty-user', 'empty-pw', 'blank-user', 'blank-pw'])
def test_invalid_config_rejected(env, user, pw, missing):
    if user is not None:
        env.setenv('LAWBOT_USER', user)
    if pw is not None:
        env.setenv('LAWBOT_PW', pw)
    with pytest.raises(RuntimeError) as e:
        get_local_launch_config()
    for name in missing:
        assert name in str(e.value)

def test_error_does_not_leak_password(env):
    env.setenv('LAWBOT_PW', 'secret-should-not-appear')
    with pytest.raises(RuntimeError) as e:
        get_local_launch_config()
    assert 'secret-should-not-appear' not in str(e.value)

# 로컬 전용 설정
def test_gradio_env_cannot_expose_server(env):
    set_auth(env)
    env.setenv('GRADIO_SERVER_NAME', '0.0.0.0')
    env.setenv('GRADIO_SERVER_PORT', '8080')
    env.setenv('GRADIO_SHARE', 'True')
    cfg = get_local_launch_config()
    assert cfg['server_name'] == '127.0.0.1'
    assert cfg['server_port'] == 7860
    assert cfg['share'] is False

def test_no_model_libraries_loaded():
    # security 모듈은 모델·UI 라이브러리를 끌어오지 않아야 함
    # 다른 테스트가 같은 프로세스에서 이미 import했을 수 있으므로 새 인터프리터에서 확인
    mods = ('lawbot.app', 'lawbot.retriever', 'lawbot.llm',
            'sentence_transformers', 'mlx_lm', 'torch', 'gradio')
    code = ('import sys, lawbot.security\n'
            f'print(",".join(m for m in {mods!r} if m in sys.modules))')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ''

# 웹앱 포트 (LAWBOT_PORT) · 인스턴스 이름 (LAWBOT_INSTANCE)
from conftest import free_port
from lawbot import security

@pytest.mark.parametrize('value', [None, '', '   '], ids=['unset', 'empty', 'blank'])
def test_port_defaults_to_legacy(env, value):
    if value is not None:
        env.setenv('LAWBOT_PORT', value)
    set_auth(env)
    assert security.server_port() == security.SERVER_PORT
    assert get_local_launch_config()['server_port'] == security.SERVER_PORT

def test_port_from_env(env):
    port = free_port()
    set_auth(env)
    env.setenv('LAWBOT_PORT', str(port))
    env.setenv('GRADIO_SERVER_PORT', str(free_port()))     # Gradio 변수는 계속 무시
    cfg = get_local_launch_config()
    assert cfg['server_port'] == port and cfg['server_name'] == '127.0.0.1'

@pytest.mark.parametrize('value', ['1023', '65536', '0', '-1', 'abc', '12.5'])
def test_port_invalid_rejected(env, value):
    set_auth(env)
    env.setenv('LAWBOT_PORT', value)
    with pytest.raises(ValueError, match='LAWBOT_PORT'):
        get_local_launch_config()

def test_port_same_as_model_server_rejected(env):
    port = free_port()
    env.setenv('LAWBOT_PORT', str(port))
    env.setenv('LAWBOT_MODEL_SERVER', f'http://127.0.0.1:{port}')
    with pytest.raises(ValueError, match='LAWBOT_MODEL_SERVER'):
        security.server_port()

def test_model_server_port_required(env):
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_SERVER_PORT'):
        security.model_server_port()
    env.setenv('LAWBOT_MODEL_SERVER_PORT', ' ')
    with pytest.raises(RuntimeError, match='LAWBOT_MODEL_SERVER_PORT'):
        security.model_server_port()
    for bad in ('1023', '65536', 'x'):
        env.setenv('LAWBOT_MODEL_SERVER_PORT', bad)
        with pytest.raises(ValueError, match='LAWBOT_MODEL_SERVER_PORT'):
            security.model_server_port()
    port = free_port()
    env.setenv('LAWBOT_MODEL_SERVER_PORT', str(port))
    assert security.model_server_port() == port

def test_instance_name(env):
    assert security.instance_name() is None
    env.setenv('LAWBOT_INSTANCE', '  개발 ')
    assert security.instance_name() == '개발'
    env.setenv('LAWBOT_INSTANCE', '가' * 41)
    with pytest.raises(ValueError, match='LAWBOT_INSTANCE'):
        security.instance_name()
