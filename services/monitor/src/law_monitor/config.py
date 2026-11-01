"""config/targets.yaml 읽기·검증. 잘못된 값은 실행 전에 ConfigError로 거부한다."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from law_monitor.paths import SERVICE_DIR

CONFIG_FILE = SERVICE_DIR / 'config' / 'targets.yaml'
CATEGORIES = range(1, 8)  # 한눈에 보기 표 번호
REPOS = ('legalize-kr', 'admrule-kr')


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LawTarget:
    name: str  # legalize-kr 폴더명 (띄어쓰기 없음)
    category: int


@dataclass(frozen=True)
class FormTarget:
    law: str
    kinds: tuple[str, ...]


@dataclass(frozen=True)
class AdmruleName:
    agency: str
    pattern: re.Pattern
    category: int


@dataclass(frozen=True)
class KeywordRule:
    agency: str
    keywords: tuple[str, ...]
    category: int


@dataclass(frozen=True)
class Config:
    repos: dict[str, str]
    laws: tuple[LawTarget, ...]
    forms: tuple[FormTarget, ...]
    admrule_names: tuple[AdmruleName, ...]
    keyword_rules: tuple[KeywordRule, ...]
    prelegislation_keywords: tuple[str, ...]

    def law_category(self, folder: str) -> int | None:
        return next((law.category for law in self.laws if law.name == folder), None)

    @property
    def admrule_agencies(self) -> set[str]:
        return {r.agency for r in self.admrule_names} | {r.agency for r in self.keyword_rules}

    def admrule_categories(self, agency: str, name: str) -> list[int]:
        """소관부처명이 같고 이름이 정규식이나 키워드에 맞는 규칙들의 category (설정 순서, 중복 제거)."""
        cats = [r.category for r in self.admrule_names if r.agency == agency and r.pattern.search(name)]
        cats += [r.category for r in self.keyword_rules if r.agency == agency and any(k in name for k in r.keywords)]
        return list(dict.fromkeys(cats))


def load(path: Path = CONFIG_FILE) -> Config:
    with open(path, encoding='utf-8') as f:
        return from_dict(yaml.safe_load(f) or {})


def from_dict(data: dict) -> Config:
    repos = data.get('repos') or {}
    for key in REPOS:
        if not repos.get(key):
            raise ConfigError(f'repos.{key} 주소가 없습니다')

    laws = tuple(LawTarget(_name(x.get('name'), 'laws'), _category(x, 'laws')) for x in data.get('laws') or [])
    dup = sorted({law.name for law in laws if [x.name for x in laws].count(law.name) > 1})
    if dup:
        raise ConfigError(f'laws에 중복된 법령: {", ".join(dup)}')

    forms = tuple(
        FormTarget(_name(x.get('law'), 'forms'), tuple(x.get('kinds') or ())) for x in data.get('forms') or []
    )

    adm = data.get('admrules') or {}
    names = []
    for x in adm.get('names') or []:
        try:
            pattern = re.compile(x.get('pattern') or '')
        except re.error as e:
            raise ConfigError(f'admrules.names 정규식 오류 ({x.get("pattern")!r}): {e}') from None
        if not pattern.pattern:
            raise ConfigError('admrules.names에 빈 정규식')
        names.append(AdmruleName(_name(x.get('agency'), 'admrules.names agency'), pattern, _category(x, 'names')))
    rules = []
    for x in adm.get('keyword_rules') or []:
        keywords = tuple(k for k in x.get('keywords') or [] if k)
        if not keywords:
            raise ConfigError(f'admrules.keyword_rules({x.get("agency")})에 키워드가 없습니다')
        rules.append(
            KeywordRule(_name(x.get('agency'), 'keyword_rules agency'), keywords, _category(x, 'keyword_rules'))
        )

    pre = tuple((data.get('prelegislation') or {}).get('keywords') or ())
    return Config(dict(repos), laws, forms, tuple(names), tuple(rules), pre)


def _name(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'{where}: 이름이 비어 있습니다')
    return value.strip()


def _category(item: dict, where: str) -> int:
    cat = item.get('category')
    if not isinstance(cat, int) or cat not in CATEGORIES:
        raise ConfigError(f'{where}: category는 1~7이어야 합니다 ({cat!r})')
    return cat
