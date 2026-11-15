"""Deterministic condition checks for a reviewed single-intent query grammar."""

import re
from dataclasses import dataclass
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReusePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_min_length=1)

    intent_id: str
    query_must_include: list[list[str]]
    query_must_not_include: list[str]
    required_slots: dict[str, str]
    allowed_optional_slots: dict[str, list[str]]
    slot_terms: dict[str, dict[str, str]]
    supported_query_patterns: list[str] = Field(min_length=1)
    unsupported_condition_policy: Literal['bypass'] = 'bypass'


@dataclass(frozen=True)
class Evidence:
    name: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Condition:
    status: Literal['known', 'missing', 'ambiguous', 'unsupported']
    reason: str
    evidence: tuple[Evidence, ...] = ()


def check_conditions(question: str, policy: ReusePolicy) -> Condition:
    """Match the whole question against reviewed literals and enumerated slot phrases."""
    matches = {
        found
        for template in policy.supported_query_patterns
        if (found := match_slots(question, template, policy)) is not None
    }
    if not matches:
        return Condition('unsupported', 'unsupported_query')
    if len(matches) != 1:
        return Condition('ambiguous', 'condition_ambiguous')
    evidence = matches.pop()
    slots = {item.name: item.value for item in evidence}
    for term in policy.query_must_not_include:
        if (start := question.find(term)) != -1:
            return Condition(
                'known', 'condition_conflict', (*evidence, Evidence('forbidden', term, start, start + len(term)))
            )
    for group in policy.query_must_include:
        found = next(((term, start) for term in group if (start := question.find(term)) != -1), None)
        if found is None:
            return Condition('missing', 'condition_missing', evidence)
        term, start = found
        evidence += (Evidence('required_expression', term, start, start + len(term)),)
    if policy.required_slots.keys() - slots.keys():
        return Condition('missing', 'condition_missing', evidence)
    if any(slots[name] != value for name, value in policy.required_slots.items()):
        return Condition('known', 'condition_conflict', evidence)
    if any(
        value not in policy.allowed_optional_slots.get(name, [])
        for name, value in slots.items()
        if name not in policy.required_slots
    ):
        return Condition('unsupported', 'unsupported_query', evidence)
    return Condition('known', 'matched', evidence)


def match_slots(question: str, template: str, policy: ReusePolicy) -> tuple[Evidence, ...] | None:
    """Build a full match from literal text and an explicit lexicon for each named slot."""
    pieces, names = [], []
    for literal, name, spec, conversion in Formatter().parse(template):
        pieces.append(re.escape(literal))
        if name is None:
            continue
        terms = policy.slot_terms.get(name)
        if spec or conversion or not name.isidentifier() or name in names or not terms:
            raise ValueError('invalid reuse policy template')
        names.append(name)
        alternatives = '|'.join(re.escape(term) for term in sorted(terms, key=lambda term: (-len(term), term)))
        pieces.append(f'(?P<{name}>{alternatives})')
    match = re.fullmatch(''.join(pieces), question)
    if match is None:
        return None
    return tuple(Evidence(name, policy.slot_terms[name][match[name]], *match.span(name)) for name in names)
