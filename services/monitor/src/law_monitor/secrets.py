"""비밀값 읽기: 환경변수 → services/monitor/.env → (모델 서버 설정만) 레포 최상위 .env. 모두 git에 올리지 않음."""

import os

from law_monitor.paths import SERVICE_DIR

ENV_FILE = SERVICE_DIR / '.env'
ROOT_ENV_FILE = SERVICE_DIR.parents[1] / '.env'  # 웹앱·모델 서버가 쓰는 파일 (LAWBOT_MODEL_SERVER·TOKEN)


def _from_file(path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'") or None
    return None


def get(key: str, *, root: bool = False) -> str | None:
    """root=True면 레포 최상위 .env도 본다 (모델 서버 주소·토큰을 운영 .env에서 그대로 쓰려고)."""
    value = os.environ.get(key) or _from_file(ENV_FILE, key)
    if value is None and root:
        value = _from_file(ROOT_ENV_FILE, key)
    return value


def law_oc() -> str | None:
    """법제처 Open API 인증키 (OC)."""
    return get('LAW_OC')
