# lawbot_common/ports.py  →  포트 값 검증 (웹앱 LAWBOT_PORT·LAWBOT_MODEL_SERVER, 모델 서버 LAWBOT_MODEL_SERVER_PORT)


def parse_port(name, value):
    """1024–65535 정수. 아니면 ValueError (변수 이름과 함께)"""
    try:
        port = int(value.strip())
    except ValueError:
        raise ValueError(f'{name}는 1024–65535 정수: {value!r}') from None
    if not 1024 <= port <= 65535:
        raise ValueError(f'{name}는 1024–65535 정수: {port}')
    return port
