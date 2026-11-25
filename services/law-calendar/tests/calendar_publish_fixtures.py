import pytest


@pytest.fixture(autouse=True)
def offline_calendar(monkeypatch):
    monkeypatch.setenv('LAWBOT_ENV_FILE', '')
    for key in ('LAWBOT_CALENDAR_PAGES_PROJECT', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN'):
        monkeypatch.delenv(key, raising=False)
