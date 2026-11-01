"""비밀값 읽기: 환경변수가 먼저, 없으면 services/monitor/.env (git에 올리지 않음)."""

import os

from law_monitor.paths import SERVICE_DIR

ENV_FILE = SERVICE_DIR / '.env'


def _from_file(key: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'") or None
    return None


def get(key: str) -> str | None:
    return os.environ.get(key) or _from_file(key)


def law_oc() -> str | None:
    """법제처 Open API 인증키 (OC)."""
    return get('LAW_OC')
