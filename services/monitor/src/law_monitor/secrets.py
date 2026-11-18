"""Read credentials from the environment, monitor configuration, then shared generation configuration."""

import os

from law_monitor.paths import SERVICE_DIR
from lawbot_common import envfile

ENV_FILE = SERVICE_DIR / '.env'
ROOT_ENV_FILE = SERVICE_DIR.parents[1] / '.env'
ROOT_KEYS = ('LAWBOT_LLM_BASE_URL', 'LAWBOT_LLM_API_KEY', 'LAWBOT_LLM_ALLOW_EXTERNAL')


def monitor_keys(key):
    """Accept monitor settings and the shared generation endpoint credentials."""
    return key.startswith(('LAW_', 'DATA_GO_KR_')) or key in ROOT_KEYS


def get(key: str, *, root: bool = False) -> str | None:
    """Read a nonempty setting; only generation settings may fall back to the root file."""
    value = os.environ.get(key) or (envfile.read(ENV_FILE, monitor_keys) or {}).get(key)
    if not value and root and key in ROOT_KEYS:
        value = (envfile.read(ROOT_ENV_FILE, envfile.lawbot_keys) or {}).get(key)
    return value or None


def law_oc() -> str | None:
    """법제처 Open API 인증키 (OC)."""
    return get('LAW_OC')
