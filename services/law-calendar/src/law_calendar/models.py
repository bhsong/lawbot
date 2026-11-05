"""Public response contract; Python schemas generate the client TypeScript interfaces."""

from typing import Literal

from pydantic import BaseModel


class PublicChange(BaseModel):
    id: str
    eventId: str
    kind: Literal['notice', 'effective']
    date: str
    lawName: str
    article: str
    summary: str
    status: str
    before: str | None
    after: str | None
    comparable: bool
    publishedOn: str | None
    promulgatedOn: str | None
    effectiveOn: str | None
    commentDeadline: str | None
    sourceUrl: str
    collectedAt: str
    verifiedAt: str
    reviewState: Literal['approved']


class PublicDetail(PublicChange):
    diff: list[str] | None


class ChangePage(BaseModel):
    items: list[PublicChange]
    total: int
    page: int
    page_size: int


class CalendarDay(BaseModel):
    event_on: str
    notice_count: int
    effective_count: int


class CalendarMonth(BaseModel):
    month: str
    timezone: Literal['Asia/Seoul']
    days: list[CalendarDay]


class CalendarStatus(BaseModel):
    collectedAt: str | None
    verifiedAt: str | None
    delayed: bool
    timezone: Literal['Asia/Seoul']
