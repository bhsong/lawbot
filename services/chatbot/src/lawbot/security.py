# lawbot/security.py  →  로컬 전용 실행 설정 (app.py가 모델 서버에 접속하기 전에 호출)
# 웹앱 주소는 127.0.0.1 고정, 포트는 .env의 LAWBOT_PORT. 모델 서버(임베딩·리랭크·생성)는 http://127.0.0.1:<포트>만
# 생성(chat completions)만 다른 OpenAI 호환 주소로 바꿀 수 있다: LAWBOT_LLM_BASE_URL + LAWBOT_LLM_ALLOW_EXTERNAL=1 (https만)
import os
from urllib.parse import urlsplit

SERVER_NAME = '127.0.0.1'  # 고정값: 환경변수(GRADIO_SERVER_NAME 등)나 설정으로 바꾸지 않음
SERVER_PORT = 7860  # LAWBOT_PORT가 없을 때의 기존 기본값 (하위 호환). 운영에서는 .env에 LAWBOT_PORT를 따로 정함
INSTANCE_MAX = 40


def parse_port(name, value):
    """1024–65535 정수. 아니면 ValueError (변수 이름과 함께)"""
    try:
        port = int(value.strip())
    except ValueError:
        raise ValueError(f'{name}는 1024–65535 정수: {value!r}') from None
    if not 1024 <= port <= 65535:
        raise ValueError(f'{name}는 1024–65535 정수: {port}')
    return port


def server_port():
    """웹앱 포트: LAWBOT_PORT, 없거나 비어 있으면 SERVER_PORT. 모델 서버 포트와 같으면 거부"""
    value = (os.environ.get('LAWBOT_PORT') or '').strip()
    port = parse_port('LAWBOT_PORT', value) if value else SERVER_PORT
    url = model_server_url()
    if url and urlsplit(url).port == port:
        raise ValueError(f'LAWBOT_PORT({port})가 LAWBOT_MODEL_SERVER의 포트와 같습니다. 서로 다른 포트를 적으세요.')
    return port


def model_server_url():
    """모델 서버 주소 (LAWBOT_MODEL_SERVER). 비어 있으면 None (웹앱은 시작을 거부)
    질문이 맥 밖으로 나가지 않도록 http://127.0.0.1:<포트>만 허용한다"""
    value = (os.environ.get('LAWBOT_MODEL_SERVER') or '').strip()
    if not value:
        return None
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        port = None
    if (
        u.scheme != 'http'
        or u.hostname != SERVER_NAME
        or port is None
        or u.username
        or u.password
        or u.path not in ('', '/')
        or u.query
        or u.fragment
    ):
        raise ValueError(f'LAWBOT_MODEL_SERVER는 http://{SERVER_NAME}:<포트> 형식만 허용합니다: {value!r}')
    parse_port('LAWBOT_MODEL_SERVER', str(port))
    return f'http://{SERVER_NAME}:{port}'


def model_token(min_len=0):
    """모델 서버 토큰 (LAWBOT_MODEL_TOKEN). 없거나 min_len자 미만이면 RuntimeError (값은 출력하지 않음)"""
    token = (os.environ.get('LAWBOT_MODEL_TOKEN') or '').strip()
    if not token:
        raise RuntimeError(
            'LAWBOT_MODEL_TOKEN이 없거나 비어 있어 실행을 거부합니다 (openssl rand -hex 32로 만들어 .env에 적기)'
        )
    if len(token) < min_len:
        raise RuntimeError(f'LAWBOT_MODEL_TOKEN이 너무 짧아 실행을 거부합니다 ({min_len}자 이상)')
    return token


def llm_endpoint(server_url):
    """생성 API (base_url, api_key, 외부 여부). LAWBOT_LLM_BASE_URL이 없으면 모델 서버의 /v1 + LAWBOT_MODEL_TOKEN
    127.0.0.1 밖 주소는 질문이 맥 밖으로 나가므로 LAWBOT_LLM_ALLOW_EXTERNAL=1과 https가 있어야 허용 (키 값은 출력하지 않음)"""
    value = (os.environ.get('LAWBOT_LLM_BASE_URL') or '').strip().rstrip('/')
    if not value:
        return f'{server_url}/v1', model_token(), False
    try:
        u = urlsplit(value)
        host, port = u.hostname, u.port
    except ValueError:
        host = port = None
    if host is None or u.username or u.password or u.query or u.fragment:
        raise ValueError(f'LAWBOT_LLM_BASE_URL 형식 오류: {value!r}')
    if host == SERVER_NAME:
        if u.scheme != 'http' or port is None:
            raise ValueError(f'LAWBOT_LLM_BASE_URL이 이 맥이면 http://{SERVER_NAME}:<포트>/v1 형식: {value!r}')
        external = False
    else:
        if (os.environ.get('LAWBOT_LLM_ALLOW_EXTERNAL') or '').strip() != '1':
            raise ValueError(
                f'LAWBOT_LLM_BASE_URL({host})은 이 맥 밖이라 질문이 외부로 나갑니다. '
                '허용하려면 LAWBOT_LLM_ALLOW_EXTERNAL=1을 함께 적으세요.'
            )
        if u.scheme != 'https':
            raise ValueError(f'외부 LAWBOT_LLM_BASE_URL은 https만 허용합니다: {value!r}')
        external = True
    key = (os.environ.get('LAWBOT_LLM_API_KEY') or '').strip()
    if not key and not external:
        key = model_token()
    if not key:
        raise RuntimeError('외부 LAWBOT_LLM_BASE_URL에는 LAWBOT_LLM_API_KEY가 필요합니다')
    return value, key, external


def instance_name():
    """LAWBOT_INSTANCE (운영/개발 구분용 화면 표시). 없으면 None"""
    name = (os.environ.get('LAWBOT_INSTANCE') or '').strip()
    if len(name) > INSTANCE_MAX:
        raise ValueError(f'LAWBOT_INSTANCE는 {INSTANCE_MAX}자 이하: {len(name)}자')
    return name or None


def get_local_launch_config():
    """demo.launch(**config)에 넘길 설정. 인증 정보가 없거나 비어 있으면 RuntimeError, 포트가 잘못되면 ValueError"""
    user = (os.environ.get('LAWBOT_USER') or '').strip()
    pw = os.environ.get('LAWBOT_PW') or ''
    bad = [name for name, ok in (('LAWBOT_USER', user), ('LAWBOT_PW', pw.strip())) if not ok]
    if bad:  # 값은 출력하지 않고 변수 이름만 알림
        raise RuntimeError(f'인증 환경 변수가 없거나 비어 있어 실행을 거부합니다: {", ".join(bad)}')
    return {'server_name': SERVER_NAME, 'server_port': server_port(), 'auth': (user, pw), 'share': False}
