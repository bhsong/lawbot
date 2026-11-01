# src/lawbot/envfile.py  →  실행 설정 파일(.env)을 환경 변수로 읽는다 (app·model_server·check_deploy·bench_models의 main이 맨 먼저 호출)
# 파일: LAWBOT_ENV_FILE이 가리키는 파일, 없으면 레포 최상위 .env (없으면 아무것도 안 함). LAWBOT_ENV_FILE=''이면 읽지 않음
# 셸에 이미 있는 변수가 우선한다 (파일 값으로 덮어쓰지 않음). 형식: KEY=VALUE 한 줄씩, # 주석, 'export ' 앞머리·따옴표 허용
# 읽는 변수는 LAWBOT_* 와 LLM_PATH만: GRADIO_* 같은 서버 설정은 security.py가 고정하므로 파일로도 바꾸지 않음
import os
import re
import stat
from pathlib import Path

from lawbot.paths import ROOT

DEFAULT = ROOT / '.env'
SECRETS = ('LAWBOT_PW', 'LAWBOT_MODEL_TOKEN')   # 파일 권한이 넓으면 경고
LINE = re.compile(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)')

def parse(text, name='.env'):
    """{변수: 값}. 형식이 틀린 줄은 줄 번호와 함께 ValueError (값은 출력하지 않음: 비밀번호가 들어 있을 수 있음)"""
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
            value = value[1:-1]          # 따옴표 안은 그대로 (# 포함 가능)
        else:
            value = re.sub(r'\s+#.*$', '', value)   # 따옴표 없는 값 뒤 ' # 주석'
        if not (key.startswith('LAWBOT_') or key == 'LLM_PATH') or key == 'LAWBOT_ENV_FILE':
            raise ValueError(f'{name} {n}번째 줄: 이 파일에서 설정할 수 없는 변수 {key}')
        out[key] = value
    return out

def load():
    """설정 파일을 읽어 셸에 없는 변수만 os.environ에 넣는다. 읽은 파일 경로(없으면 None)"""
    env = os.environ.get('LAWBOT_ENV_FILE')
    if env == '':
        return None
    path = Path(env).expanduser() if env else DEFAULT
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        if env:                          # 직접 지정한 파일이 없으면 오류
            raise FileNotFoundError(f'LAWBOT_ENV_FILE 파일 없음: {path}')
        return None
    values = parse(path.read_text(encoding='utf-8'), path.name)
    secrets = [k for k in SECRETS if values.get(k)]
    if secrets and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):   # 값은 출력하지 않고 변수 이름만
        print(f"[lawbot] 경고: {path.name}에 비밀 값({', '.join(secrets)})이 있는데 다른 사용자도 읽을 수 있습니다. "
              f'chmod 600 {path}')
    for k, v in values.items():
        os.environ.setdefault(k, v)
    print(f'[lawbot] 설정 파일 {path} ({len(values)}개, 셸에 있는 변수 우선)')
    return path
