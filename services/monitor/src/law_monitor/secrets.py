"""비밀값 읽기: 환경변수 → services/monitor/.env → (모델 서버 설정만) 레포 최상위 .env. 모두 git에 올리지 않음."""

import os

from law_monitor.paths import SERVICE_DIR
from lawbot_common import envfile

ENV_FILE = SERVICE_DIR / '.env'
ROOT_ENV_FILE = SERVICE_DIR.parents[1] / '.env'  # 웹앱·모델 서버가 쓰는 파일 (LAWBOT_MODEL_SERVER·TOKEN)
ROOT_KEYS = ('LAWBOT_MODEL_SERVER', 'LAWBOT_MODEL_TOKEN')  # 레포 최상위 .env에서 읽는 것은 이 둘만


def monitor_keys(key):
    """services/monitor/.env에 적을 수 있는 변수: LAW_*(LAW_OC·LAW_MONITOR_*), DATA_GO_KR_*, 모델 서버 주소·토큰"""
    return key.startswith(('LAW_', 'DATA_GO_KR_')) or key in ROOT_KEYS


def get(key: str, *, root: bool = False) -> str | None:
    """환경변수 → services/monitor/.env → (root=True이고 모델 서버 주소·토큰이면) 레포 최상위 .env. 빈 값은 None
    파일 형식은 웹앱·모델 서버와 같은 규칙 (lawbot_common.envfile)"""
    value = os.environ.get(key) or (envfile.read(ENV_FILE, monitor_keys) or {}).get(key)
    if not value and root and key in ROOT_KEYS:
        value = (envfile.read(ROOT_ENV_FILE, envfile.lawbot_keys) or {}).get(key)
    return value or None


def law_oc() -> str | None:
    """법제처 Open API 인증키 (OC)."""
    return get('LAW_OC')
