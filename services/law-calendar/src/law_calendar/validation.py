"""Strict civil dates and official evidence links for offline review inputs."""

from datetime import date, datetime
from urllib.parse import urlsplit

HOSTS = {'www.law.go.kr', 'law.go.kr', 'opinion.lawmaking.go.kr'}


class StaleSnapshotError(ValueError):
    """A review is no longer tied to the current source snapshot."""


def civil_date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('Expected a KST civil date YYYY-MM-DD')
    return value


def timestamp(value: str) -> str:
    if datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError('Timestamp must include a timezone')
    return value


def official_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != 'https'
        or url.netloc not in HOSTS
        or not url.path.startswith('/')
        or any(c.isspace() for c in value)
    ):
        raise ValueError('An official HTTPS evidence URL is required')
    return value
