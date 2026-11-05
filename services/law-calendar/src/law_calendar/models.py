"""Public response contract; Python schemas generate the client TypeScript interfaces."""

from typing import Literal

from pydantic import BaseModel


class PublicAssembly(BaseModel):
    agency: str | None
    billNo: str
    dateRole: str
    noticeStart: str | None
    noticeEnd: str | None
    billProposed: str | None
    billDecided: str | None
    committeeDecided: str | None
    committeeResult: str | None
    judiciaryDecided: str | None
    judiciaryResult: str | None


class PublicChange(BaseModel):
    id: str
    eventId: str
    kind: Literal['notice', 'effective', 'bill']
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
    assembly: PublicAssembly | None = None


class PublicNoticeContent(BaseModel):
    summary: str | None
    pdfUrl: str | None
    comparisonPages: list[int]


class PublicDetail(PublicChange):
    diff: list[str] | None
    noticeContent: PublicNoticeContent | None = None


class ChangePage(BaseModel):
    items: list[PublicChange]
    total: int
    page: int
    page_size: int


class CalendarDay(BaseModel):
    event_on: str
    notice_count: int
    effective_count: int
    bill_count: int = 0


class CalendarMonth(BaseModel):
    month: str
    timezone: Literal['Asia/Seoul']
    days: list[CalendarDay]


class CalendarStatus(BaseModel):
    collectedAt: str | None
    verifiedAt: str | None
    delayed: bool
    timezone: Literal['Asia/Seoul']
    billCollectedAt: str | None = None
    noticeCollectedAt: str | None = None


class HeldChange(BaseModel):
    id: str
    lawName: str
    article: str
    reason: str
    sourceUrl: str
    collectedAt: str


class HoldPage(BaseModel):
    items: list[HeldChange]
    total: int
    page: int
    page_size: int
