"""Validate registered source types, public endpoints and the independent collection scope."""

import re
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH = Path(__file__).with_name('sources.yaml')
HOSTS = {
    'lawgo': {'www.law.go.kr', 'open.law.go.kr', 'github.com'},
    'assembly': {'www.assembly.go.kr', 'open.assembly.go.kr'},
    'moel': {'www.moel.go.kr'},
    'mofe': {'mofe.go.kr', 'www.mofe.go.kr'},
    'nts': {'www.nts.go.kr'},
    'hometax': {'www.hometax.go.kr', 'www.nts.go.kr'},
}
QUERY_FIELDS = {'bbsId', 'boardId', 'menuNo', 'mi', 'target'}
MODES = {
    'legacy': {'legacy'},
    'local': {'local'},
    'lawgo': {'api'},
    'moel': {'rss', 'html', 'metadata'},
    'mofe': {'rss', 'html', 'metadata'},
    'nts': {'api', 'html', 'metadata'},
    'hometax': {'html', 'browser', 'metadata'},
}
Group = Literal['law', 'erp']
Target = Literal['group', 'agency', 'source', 'law', 'law-part']


class Source(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, str_strip_whitespace=True)

    source_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{1,79}$')
    group: Group
    agency: str
    board_name: str = Field(min_length=1, max_length=120)
    official_homepage: str
    source_url: str | None = None
    adapter: str
    mode: Literal['legacy', 'local', 'api', 'rss', 'html', 'browser', 'metadata']
    enabled: bool = False
    interval_minutes: int = Field(default=1440, ge=60, le=10080)
    reconcile_interval_minutes: int = Field(default=10080, ge=60, le=43200)
    baseline_policy: Literal['suppress_existing', 'review_backfill'] = 'suppress_existing'
    backfill_days: int = Field(default=30, ge=0, le=365)
    review_policy: Literal['manual', 'auto_metadata'] = 'manual'
    category_tags: list[str] = Field(min_length=1, max_length=16)
    license_state: Literal['unverified', 'links_only', 'approved'] = 'unverified'
    verification: Literal['not_tested', 'ready', 'blocked', 'manual_review'] = 'not_tested'
    parser_version: str = Field(default='1', pattern=r'^[a-zA-Z0-9._-]{1,24}$')
    law_scope: bool = False

    @model_validator(mode='after')
    def validate_source(self):
        if self.agency not in HOSTS or self.mode not in MODES.get(self.adapter, set()):
            raise ValueError('기관·수집 모드·어댑터 조합을 확인하세요')
        if self.adapter not in {self.agency, 'legacy', 'local'}:
            raise ValueError('기관에 등록된 어댑터를 사용하세요')
        if self.adapter in {'legacy', 'local'} and self.agency not in {'lawgo', 'assembly'}:
            raise ValueError('기존 수집 어댑터의 기관을 확인하세요')
        if self.law_scope and (self.group != 'law' or self.agency != 'lawgo'):
            raise ValueError('개별 법령 설정은 법령 범위 수집원에서만 지원합니다')
        for value in (self.official_homepage, self.source_url):
            if value is not None:
                self.validate_url(value)
        if self.enabled and self.source_url is None:
            raise ValueError('소스를 활성화하려면 공식 수집 URL이 필요합니다')
        if self.reconcile_interval_minutes < self.interval_minutes:
            raise ValueError('목록 대조 간격은 수집 간격 이상이어야 합니다')
        if self.review_policy == 'auto_metadata' and self.license_state == 'unverified':
            raise ValueError('자동 메타데이터 공개 전에 출처의 이용 조건을 확인하세요')
        return self

    def validate_url(self, value: str) -> None:
        if re.search(r'[\s\\\x00-\x1f\x7f]', value):
            raise ValueError('공식 HTTPS URL을 입력하세요')
        url = urlsplit(value)
        if (
            url.scheme != 'https'
            or url.netloc not in HOSTS[self.agency]
            or url.fragment
            or (
                url.netloc == 'github.com'
                and url.path
                not in {
                    '/legalize-kr',
                    '/legalize-kr/legalize-kr.git',
                    '/legalize-kr/admrule-kr.git',
                }
            )
            or any(
                key not in QUERY_FIELDS or not re.fullmatch(r'[A-Za-z0-9_]{1,48}', val)
                for key, val in parse_qsl(url.query, keep_blank_values=True, max_num_fields=8, strict_parsing=True)
            )
        ):
            raise ValueError('허용된 공식 HTTPS URL과 공개 조회 파라미터를 사용하세요')


class Config(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    sources: list[Source] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError('중복 source_id')
        return self


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)]
        if len(keys) != len(set(keys)):
            raise ValueError('YAML 설정 키가 중복되었습니다')
        return super().construct_mapping(node, deep=deep)


def load(path: Path = CONFIG_PATH) -> Config:
    return Config.model_validate(yaml.load(path.read_text(encoding='utf-8'), Loader=UniqueLoader))
