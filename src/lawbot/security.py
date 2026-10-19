# src/lawbot/security.py  →  로컬 전용 실행 설정 (app.py가 모델 로딩 전에 호출)
import os

SERVER_NAME = '127.0.0.1'   # 고정값: 환경변수(GRADIO_SERVER_NAME 등)나 설정으로 바꾸지 않음
SERVER_PORT = 7860

def get_local_launch_config():
    """demo.launch(**config)에 넘길 설정. 인증 정보가 없거나 비어 있으면 RuntimeError."""
    user = (os.environ.get('LAWBOT_USER') or '').strip()
    pw = os.environ.get('LAWBOT_PW') or ''
    bad = [name for name, ok in (('LAWBOT_USER', user), ('LAWBOT_PW', pw.strip())) if not ok]
    if bad:                  # 값은 출력하지 않고 변수 이름만 알림
        raise RuntimeError(f"인증 환경 변수가 없거나 비어 있어 실행을 거부합니다: {', '.join(bad)}")
    return {'server_name': SERVER_NAME, 'server_port': SERVER_PORT,
            'auth': (user, pw), 'share': False}
