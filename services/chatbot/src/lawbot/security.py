# src/lawbot/security.py  →  로컬 전용 실행 설정 (app.py·model_server.py가 모델 로딩 전에 호출)
# 주소는 127.0.0.1 고정. 포트는 .env의 LAWBOT_PORT(웹앱)·LAWBOT_MODEL_SERVER_PORT(모델 서버)
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


def model_server_port():
    """모델 서버 포트: LAWBOT_MODEL_SERVER_PORT 필수 (코드 기본값 없음)"""
    value = (os.environ.get('LAWBOT_MODEL_SERVER_PORT') or '').strip()
    if not value:
        raise RuntimeError('모델 서버 포트가 없어 실행을 거부합니다: .env에 LAWBOT_MODEL_SERVER_PORT를 적으세요.')
    return parse_port('LAWBOT_MODEL_SERVER_PORT', value)


def model_server_url():
    """원격 모드 모델 서버 주소 (LAWBOT_MODEL_SERVER). 비어 있으면 None(로컬 모드)
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
