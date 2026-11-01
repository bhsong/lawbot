# lawbot_common/envfile.py  →  .env 파서 (웹앱·모델 서버·monitor 공통 규칙)
# 형식: KEY=VALUE 한 줄씩, # 주석, 'export ' 앞머리, 따옴표 허용 (따옴표 안은 그대로), 따옴표 없는 값 뒤 ' # 주석'은 뺀다
# 서비스마다 적을 수 있는 변수(allowed)만 다르다. 형식이 틀리거나 허용하지 않는 변수면 줄 번호만 알린다 (값은 출력하지 않음)
import os
import re
import stat
from pathlib import Path

LINE = re.compile(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)')


def lawbot_keys(key):
    """레포 최상위 .env (웹앱·모델 서버): LAWBOT_*만. GRADIO_* 같은 서버 설정은 코드가 고정하므로 파일로 바꾸지 않음"""
    return key.startswith('LAWBOT_') and key != 'LAWBOT_ENV_FILE'


def parse(text, name='.env', allowed=lawbot_keys):
    """{변수: 값}. 형식이 틀리거나 allowed(key)가 아닌 줄은 줄 번호와 함께 ValueError"""
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = LINE.fullmatch(line)
        if not m:
            raise ValueError(f'{name} {n}번째 줄 형식이 KEY=VALUE가 아닙니다')
        key, value = m[1], m[2].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]  # 따옴표 안은 그대로 (# 포함 가능)
        else:
            value = re.sub(r'\s+#.*$', '', value)  # 따옴표 없는 값 뒤 ' # 주석'
        if not allowed(key):
            raise ValueError(f'{name} {n}번째 줄: 이 파일에서 설정할 수 없는 변수 {key}')
        out[key] = value
    return out


def read(path, allowed):
    """파일이 없으면 None, 있으면 parse 결과"""
    path = Path(path)
    if not path.is_file():
        return None
    return parse(path.read_text(encoding='utf-8'), path.name, allowed)


def load(default, root, secrets, tag):
    """레포 최상위 .env를 읽어 셸에 없는 변수만 os.environ에 넣는다 (웹앱·모델 서버의 main이 맨 먼저 호출). 읽은 경로 또는 None
    파일: LAWBOT_ENV_FILE이 가리키는 파일(상대 경로는 root 기준), 없으면 default. LAWBOT_ENV_FILE=''이면 읽지 않음
    secrets: 값이 있는데 파일을 다른 사용자도 읽을 수 있으면 경고할 변수. tag: 출력 머리 ('[lawbot]')"""
    env = os.environ.get('LAWBOT_ENV_FILE')
    if env == '':
        return None
    path = Path(env).expanduser() if env else Path(default)
    if not path.is_absolute():
        path = Path(root) / path
    if not path.is_file():
        if env:  # 직접 지정한 파일이 없으면 오류
            raise FileNotFoundError(f'LAWBOT_ENV_FILE 파일 없음: {path}')
        return None
    values = read(path, lawbot_keys)
    found = [k for k in secrets if values.get(k)]
    if found and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):  # 값은 출력하지 않고 변수 이름만
        print(
            f'{tag} 경고: {path.name}에 비밀 값({", ".join(found)})이 있는데 다른 사용자도 읽을 수 있습니다. '
            f'chmod 600 {path}'
        )
    for k, v in values.items():
        os.environ.setdefault(k, v)
    print(f'{tag} 설정 파일 {path} ({len(values)}개, 셸에 있는 변수 우선)')
    return path
