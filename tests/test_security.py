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
