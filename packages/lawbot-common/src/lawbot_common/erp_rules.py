"""Public file and response types for independently reviewed ERP rule editions."""

from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from lawbot_common.erp_news import NEWS_HOSTS

RULE_FEED_MAX_BYTES = 2 * 1024 * 1024
Identifier = Annotated[str, StringConstraints(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$')]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
ValueText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
OfficialUrl = Annotated[str, StringConstraints(min_length=1, max_length=1500)]
Applicability = Literal[
    'legal_effective_date', 'payment_date', 'work_period', 'income_year', 'insurance_month', 'filing_date', 'other'
]
FinalEvidence = Literal['promulgated_law', 'final_notice', 'final_specification']
CONFIRMATION_HOSTS = NEWS_HOSTS['lawgo'] | NEWS_HOSTS['moel'] | NEWS_HOSTS['mofe'] | NEWS_HOSTS['nts']
PROPOSAL_HOSTS = CONFIRMATION_HOSTS | {'pal.assembly.go.kr', 'likms.assembly.go.kr'}


def official_link(url: str, *, proposal: bool = False) -> bool:
    if any(char.isspace() or ord(char) < 32 or char in '\\\x7f' for char in url):
        return False
    try:
        link = urlsplit(url)
    except ValueError:
        return False
    hosts = PROPOSAL_HOSTS if proposal else CONFIRMATION_HOSTS
    return link.scheme == 'https' and link.netloc in hosts and link.path not in ('', '/')


def target_date(day: date, applicability: Applicability) -> date:
    if applicability == 'income_year':
        return day.replace(month=1, day=1)
    if applicability == 'insurance_month':
        return day.replace(day=1)
    return day


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ErpRuleVersion(Contract):
    ruleVersionId: Identifier
    sourceRevisionId: int = Field(gt=0)
    predecessorId: Identifier | None = None
    effectiveFrom: date
    appliesFrom: date
    appliesUntil: date | None = None
    evidenceKind: FinalEvidence
    evidenceUrl: OfficialUrl
    valueText: ValueText

    @model_validator(mode='after')
    def validate_evidence(self):
        if not official_link(self.evidenceUrl):
            raise ValueError('Use a confirmed official source URL')
        if self.appliesUntil and self.appliesUntil < self.appliesFrom:
            raise ValueError('Application end precedes application start')
        return self


class ErpRuleProposal(Contract):
    ruleVersionId: Identifier
    evidenceUrl: OfficialUrl

    @model_validator(mode='after')
    def validate_evidence(self):
        if not official_link(self.evidenceUrl, proposal=True):
            raise ValueError('Use an official proposal URL')
        return self


class RuleReviewMark(Contract):
    at: AwareDatetime
    decision: Literal['confirmed', 'recheck_required']


class RuleEdition(Contract):
    version: ErpRuleVersion
    reviews: list[RuleReviewMark] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_reviews(self):
        if self.reviews[0].decision != 'confirmed' or self.reviews != sorted(self.reviews, key=lambda mark: mark.at):
            raise ValueError('Review history must begin with confirmation and be ordered')
        return self


class RuleScope(Contract):
    scopeId: Identifier
    scope: Label
    applicability: Applicability


class RuleSnapshotScope(RuleScope):
    editions: list[RuleEdition]

    @model_validator(mode='after')
    def validate_timeline(self):
        dates = set()
        ids = set()
        for edition in self.editions:
            version = edition.version
            if version.ruleVersionId in ids or version.appliesFrom in dates:
                raise ValueError('Duplicate ERP rule edition or application start')
            ids.add(version.ruleVersionId)
            dates.add(version.appliesFrom)
            for day in (version.appliesFrom, version.appliesUntil):
                if day and target_date(day, self.applicability) != day:
                    raise ValueError('Application date must start the selected month or year')
        return self


class RuleIdentity(Contract):
    ruleId: Identifier
    sourceKey: Identifier
    title: Label


class RuleSnapshotItem(RuleIdentity):
    scopes: list[RuleSnapshotScope]
    proposalLinks: list[ErpRuleProposal]


class RuleSnapshot(Contract):
    schemaVersion: Literal[2]
    generatedAt: AwareDatetime
    rules: list[RuleSnapshotItem]

    @model_validator(mode='after')
    def validate_identity(self):
        ids = set()
        for item in self.rules:
            if item.ruleId in ids or len({scope.scopeId for scope in item.scopes}) != len(item.scopes):
                raise ValueError('Duplicate ERP rule or scope')
            ids.add(item.ruleId)
        return self


class VerifiedErpRuleVersion(ErpRuleVersion):
    verifiedAt: AwareDatetime


class ErpRuleScope(RuleScope):
    targetOn: date
    currentVersion: VerifiedErpRuleVersion | None
    scheduledChanges: list[VerifiedErpRuleVersion]
    recheckRequired: bool


class ErpRule(RuleIdentity):
    scopes: list[ErpRuleScope]
    proposalLinks: list[ErpRuleProposal]


class ErpRules(Contract):
    state: Literal['ok', 'unconfigured', 'missing', 'invalid']
    generatedAt: AwareDatetime | None = None
    asOfDate: date
    rules: list[ErpRule] = Field(default_factory=list)
