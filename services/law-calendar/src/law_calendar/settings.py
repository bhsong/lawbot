"""Environment and location conventions shared by calendar entry points."""

import os
from logging import Logger
from pathlib import Path

from lawbot_common import envfile

SERVICE = Path(__file__).resolve().parents[2]
PROJECT_KEY = 'LAWBOT_CALENDAR_PAGES_PROJECT'
ACCOUNT_KEY = 'CLOUDFLARE_ACCOUNT_ID'
TOKEN_KEY = 'CLOUDFLARE_API_TOKEN'
DAILY_KEY = 'LAWBOT_CALENDAR_ERP_DAILY_FEED'
DB_KEY = 'LAW_CALENDAR_DB'
PAGES_KEYS = (ACCOUNT_KEY, TOKEN_KEY)
PUBLISH_KEYS = {
    PROJECT_KEY,
    DB_KEY,
    DAILY_KEY,
    'LAWBOT_CALENDAR_NODE',
    'LAWBOT_CALENDAR_FRONTEND_DIST',
    'LAWBOT_CALENDAR_SITE',
}


def discard_pages_environment() -> None:
    for key in PAGES_KEYS:
        os.environ.pop(key, None)


def _service_key(key: str) -> bool:
    return envfile.lawbot_keys(key) or key == DB_KEY


def publish_config() -> dict[str, str]:
    """Read the fixed service file, never shell overrides or Pages credentials."""
    from law_calendar.publish_state import checked_file, trusted_directory

    trusted_directory(SERVICE)
    try:
        with checked_file(SERVICE / '.env', os.O_RDONLY, private=False) as fd:
            with open(fd, encoding='utf-8', closefd=False) as stream:
                values = envfile.parse(stream.read(), allowed=_service_key)
                return {key: value for key, value in values.items() if key in PUBLISH_KEYS}
    except FileNotFoundError:
        return {}


def load_environment(log: Logger) -> None:
    discard_pages_environment()
    envfile.load(
        SERVICE / '.env',
        SERVICE,
        ('LAWBOT_CALENDAR_ADMIN_TOKEN',),
        log,
        allowed=_service_key,
    )
