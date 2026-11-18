# lawbot/security.py  →  로컬 전용 실행 설정 (web.py가 모델 서버에 접속하기 전에 settings로 읽음)
# Embedding and reranking use the local model server; generation uses its dedicated endpoint.
import os
from urllib.parse import urlsplit

from lawbot_common.ports import parse_port

SERVER_NAME = '127.0.0.1'  # 고정값: 환경변수나 설정으로 바꾸지 않음
SERVER_PORT = 7860  # LAWBOT_PORT가 없을 때의 기존 기본값 (하위 호환). 운영에서는 .env에 LAWBOT_PORT를 따로 정함
INSTANCE_MAX = 40


def server_port(env=None):
    """웹앱 포트: LAWBOT_PORT, 없거나 비어 있으면 SERVER_PORT. 모델 서버 포트와 같으면 거부"""
    env = os.environ if env is None else env
    value = (env.get('LAWBOT_PORT') or '').strip()
    port = parse_port('LAWBOT_PORT', value) if value else SERVER_PORT
    url = model_server_url(env)
    if url and urlsplit(url).port == port:
        raise ValueError(f'LAWBOT_PORT({port})가 LAWBOT_MODEL_SERVER의 포트와 같습니다. 서로 다른 포트를 적으세요.')
    return port


def model_server_url(env=None):
    """모델 서버 주소 (LAWBOT_MODEL_SERVER). 비어 있으면 None (웹앱은 시작을 거부)
    질문이 맥 밖으로 나가지 않도록 http://127.0.0.1:<포트>만 허용한다"""
    return _loopback_url('LAWBOT_MODEL_SERVER', env, reveal_value=True)


def calendar_api_url(env=None):
    """Validate the optional local calendar endpoint without exposing its value."""
    return _loopback_url('LAWBOT_CALENDAR_API_URL', env, reveal_value=False)


def calendar_admin_token(env):
    """Read the optional service credential without including it in errors."""
    token = (env.get('LAWBOT_CALENDAR_ADMIN_TOKEN') or '').strip()
    if token and any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError('LAWBOT_CALENDAR_ADMIN_TOKEN에는 공백 없는 ASCII 문자를 사용하세요.')
    return token or None


def _loopback_url(key, env, *, reveal_value):
    env = os.environ if env is None else env
    value = (env.get(key) or '').strip()
    if not value:
        return None
    try:
        url = urlsplit(value)
        port = url.port
        allowed = (
            url.scheme == 'http'
            and url.hostname == SERVER_NAME
            and port is not None
            and url.username is None
            and url.password is None
            and url.path in ('', '/')
            and not url.query
            and not url.fragment
        )
    except ValueError:
        allowed, port = False, None
    if not allowed or port is None:
        suffix = f': {value!r}' if reveal_value else ''
        raise ValueError(f'{key}는 http://{SERVER_NAME}:<포트> 형식만 허용합니다{suffix}')
    parse_port(key, str(port))
    return f'http://{SERVER_NAME}:{port}'


def model_token(min_len=0, env=None):
    """모델 서버 토큰 (LAWBOT_MODEL_TOKEN). 없거나 min_len자 미만이면 RuntimeError (값은 출력하지 않음)"""
    env = os.environ if env is None else env
    token = (env.get('LAWBOT_MODEL_TOKEN') or '').strip()
    if not token:
        raise RuntimeError(
            'LAWBOT_MODEL_TOKEN이 없거나 비어 있어 실행을 거부합니다 (openssl rand -hex 32로 만들어 .env에 적기)'
        )
    if len(token) < min_len:
        raise RuntimeError(f'LAWBOT_MODEL_TOKEN이 너무 짧아 실행을 거부합니다 ({min_len}자 이상)')
    return token


def instance_name(env=None):
    """LAWBOT_INSTANCE (운영/개발 구분용 화면 표시). 없으면 None"""
    env = os.environ if env is None else env
    name = (env.get('LAWBOT_INSTANCE') or '').strip()
    if len(name) > INSTANCE_MAX:
        raise ValueError(f'LAWBOT_INSTANCE는 {INSTANCE_MAX}자 이하: {len(name)}자')
    return name or None


def auth(env=None):
    """(사용자, 비밀번호). 없거나 비어 있으면 RuntimeError (값은 출력하지 않고 변수 이름만)"""
    env = os.environ if env is None else env
    user = (env.get('LAWBOT_USER') or '').strip()
    pw = env.get('LAWBOT_PW') or ''
    bad = [name for name, ok in (('LAWBOT_USER', user), ('LAWBOT_PW', pw.strip())) if not ok]
    if bad:
        raise RuntimeError(f'인증 환경 변수가 없거나 비어 있어 실행을 거부합니다: {", ".join(bad)}')
    return user, pw
