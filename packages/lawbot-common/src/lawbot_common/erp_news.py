"""The file contract between the private collector and the public calendar."""

from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Agency = Literal['lawgo', 'moel', 'mofe', 'nts', 'hometax']
AGENCIES = ('lawgo', 'moel', 'mofe', 'nts', 'hometax')
NEWS_HOSTS = {
    'lawgo': {'www.law.go.kr', 'law.go.kr'},
    'moel': {'www.moel.go.kr'},
    'mofe': {'mofe.go.kr'},
    'nts': {'www.nts.go.kr'},
    'hometax': {'www.hometax.go.kr'},
}
HOMETAX_HOME = 'https://www.hometax.go.kr/'


def official_url(value: str, agency: Agency) -> bool:
    if any(char.isspace() or ord(char) < 32 or char in '\\\x7f' for char in value):
        return False
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    return url.scheme == 'https' and url.netloc in NEWS_HOSTS[agency] and not url.fragment


class NewsItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=500)
    board: str = Field(min_length=1, max_length=120)
    publishedOn: date | None
    officialUrl: str = Field(max_length=1500)
    effectiveOn: date | None = None


class NewsBoard(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=120)
    status: Literal['ok', 'disabled', 'unverified', 'pending', 'delayed', 'blocked', 'error', 'partial']
    lastSuccessAt: AwareDatetime | None
    staleAfter: AwareDatetime | None = None


class NewsAgencySnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    boards: list[NewsBoard] = Field(max_length=200)
    items: list[NewsItem] = Field(max_length=10)


class NewsSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schemaVersion: Literal[1]
    generatedAt: AwareDatetime
    agencies: dict[Agency, NewsAgencySnapshot]
    lawNames: list[str] = Field(max_length=400)

    @model_validator(mode='after')
    def validate_public_items(self):
        if set(self.agencies) != set(AGENCIES) or self.agencies['hometax'].items or self.agencies['lawgo'].items:
            raise ValueError('Unexpected agency snapshot')
        for agency, snapshot in self.agencies.items():
            if any(not official_url(item.officialUrl, agency) for item in snapshot.items):
                raise ValueError('Unexpected official link')
        return self


def latest(items: list[NewsItem]) -> list[NewsItem]:
    ordered = sorted(items, key=lambda item: (item.publishedOn or date.min, item.id), reverse=True)
    unique: dict[str, NewsItem] = {}
    for item in ordered:
        unique.setdefault(item.officialUrl, item)
    return list(unique.values())[:10]
