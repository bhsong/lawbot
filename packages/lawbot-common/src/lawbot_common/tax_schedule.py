"""Validate the published NTS schedule without coupling it to law events."""

from datetime import date

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

NTS_HOST = 'https://www.nts.go.kr'
NTS_SCHEDULE_PATH = '/nts/ad/taxSchdul/selectList.do?mi=135747'


def year_path(year: int) -> str:
    return f'{NTS_SCHEDULE_PATH}&taxMonth=&taxYear={year}'


class Deadline(BaseModel):
    model_config = ConfigDict(extra='forbid')
    upstream_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    title: str = Field(min_length=1, max_length=300)
    canonical_url: str
    deadline_on: date
    target_period: str | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def official_schedule(self):
        if self.canonical_url != NTS_HOST + year_path(self.deadline_on.year):
            raise ValueError('Expected the official NTS schedule for the deadline year')
        return self


class TaxSchedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmedAt: AwareDatetime
    items: list[Deadline] = Field(max_length=500)

    @model_validator(mode='after')
    def unique_deadlines(self):
        if len({item.upstream_id for item in self.items}) != len(self.items):
            raise ValueError('Duplicate NTS schedule item')
        return self
