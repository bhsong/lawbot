"""Environment and location conventions shared by calendar entry points."""

import os
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from lawbot_common import envfile

SERVICE = Path(__file__).resolve().parents[2]
PROJECT_KEY = 'LAWBOT_CALENDAR_PAGES_PROJECT'
ACCOUNT_KEY = 'CLOUDFLARE_ACCOUNT_ID'
TOKEN_KEY = 'CLOUDFLARE_API_TOKEN'
DAILY_KEY = 'LAWBOT_CALENDAR_ERP_DAILY_FEED'
DB_KEY = 'LAW_CALENDAR_DB'
PUBLISH_TIMEOUT = 960


def load_environment(log: Logger) -> None:
    envfile.load(
        SERVICE / '.env',
        SERVICE,
        ('LAWBOT_CALENDAR_ADMIN_TOKEN', ACCOUNT_KEY, TOKEN_KEY),
        log,
        allowed=lambda key: envfile.lawbot_keys(key) or key in (DB_KEY, ACCOUNT_KEY, TOKEN_KEY),
    )


def site_path() -> Path:
    return Path(os.environ.get('LAWBOT_CALENDAR_SITE') or SERVICE / 'data/site')


@dataclass(frozen=True, repr=False)
class PagesSettings:
    project: str
    account: str
    token: str

    @classmethod
    def from_environment(cls) -> PagesSettings | None:
        project, account, token = (os.environ.get(key, '').strip() for key in (PROJECT_KEY, ACCOUNT_KEY, TOKEN_KEY))
        return cls(project, account, token) if project and account and token else None
