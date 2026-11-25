"""Public response contract; Python schemas generate the client TypeScript interfaces."""

from typing import Literal

from pydantic import BaseModel

from lawbot_common.erp_news import Agency, NewsItem


class PublicNewsBoard(BaseModel):
    name: str
    status: Literal['ok', 'disabled', 'unverified', 'pending', 'delayed', 'blocked', 'error', 'partial']
    lastSuccessAt: str | None


class ErpNews(BaseModel):
    agency: Agency
    state: Literal['ok', 'missing', 'invalid', 'link_only', 'unavailable']
    generatedAt: str | None = None
    officialHome: str
    navigation: str | None = None
    boards: list[PublicNewsBoard] = []
    items: list[NewsItem] = []


class PublicChange(BaseModel):
    id: str
    eventId: str
    date: str
    lawName: str
    article: str
    summary: str
    status: str
    before: str | None
    after: str | None
    comparable: bool
    promulgatedOn: str | None
    effectiveOn: str | None
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
    effective_count: int


class CalendarMonth(BaseModel):
    month: str
    timezone: Literal['Asia/Seoul']
    days: list[CalendarDay]


class CalendarIndex(BaseModel):
    months: list[str]
    erpRuleDates: list[str]


class TaxDeadline(BaseModel):
    id: str
    title: str
    date: str
    targetPeriod: str | None
    sourceUrl: str


class PublicTaxSchedule(BaseModel):
    items: list[TaxDeadline]
    confirmedAt: str | None


class CalendarStatus(BaseModel):
    collectedAt: str | None
    verifiedAt: str | None
    delayed: bool
    timezone: Literal['Asia/Seoul']


class HoldItem(BaseModel):
    id: str
    lawName: str
    reason: str
    sourceUrl: str
    collectedAt: str


class HeldChange(HoldItem):
    article: str


class HoldPage(BaseModel):
    items: list[HeldChange]
    total: int
    page: int
    page_size: int


class ErpAttachment(BaseModel):
    title: str
    pdfUrl: str
    sha256: str | None
    bytes: int | None


class ErpItem(BaseModel):
    id: str
    sourceKey: str
    sourceTitle: str
    category: str
    kind: Literal['admrule', 'form', 'article']
    title: str
    agency: str | None
    lawName: str | None
    article: str | None
    issuedNo: str | None
    issuedOn: str | None
    effectiveFrom: str | None
    asOf: str | None
    excerpt: str | None
    sourceUrl: str | None
    attachments: list[ErpAttachment]
    contentSha256: str
    collectedAt: str
    reviewedAt: str


class ErpHistory(BaseModel):
    itemId: str
    sourceKey: str
    title: str
    at: str
    action: Literal['collected', 'unchanged', 'attached', 'approved', 'rejected']
    origin: str | None


class ErpFeed(BaseModel):
    """File written by services/erp-updates (`erp-updates export`)."""

    schemaVersion: Literal[1]
    generatedAt: str
    items: list[ErpItem]
    relatedLaws: list[str]
    history: list[ErpHistory]


class ErpUpdates(BaseModel):
    state: Literal['ok', 'missing', 'invalid']
    generatedAt: str | None = None
    items: list[ErpItem] = []
    relatedLaws: list[str] = []
    history: list[ErpHistory] = []
