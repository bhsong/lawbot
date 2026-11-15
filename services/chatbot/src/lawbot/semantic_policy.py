"""Deterministic condition checks for a reviewed single-intent query grammar."""

import re
from dataclasses import dataclass
from string import Formatter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

INTENTS = {
    'deadline': re.compile(
        r'언제(?:까지|부터)?(?:인가|입니까|인가요|까지인가요|까지예요|까지인지|까지 해야 하나요|까지 해야 합니까|까지인가)?\s*[?？.]?$'
    ),
    'requirements': re.compile(
        r'(?:요건|조건)(?:은|이|을)\s*(?:무엇인가요|무엇입니까|뭔가요|알려주세요|알려 주세요)\s*[?？.]?$'
    ),
    'procedure': re.compile(
        r'(?:절차|방법)(?:는|이|을)\s*(?:무엇인가요|무엇입니까|뭔가요|알려주세요|알려 주세요)\s*[?？.]?$'
    ),
    'permission': re.compile(r'(?:해도|하여도|가능한데 해도)\s*(?:되나요|됩니까|되나|되는가)\s*[?？.]?$'),
    'obligation': re.compile(r'(?:해야|하여야)\s*(?:하나요|합니까|하나|하는가)\s*[?？.]?$'),
}


def classify_intent(text: str) -> str | None:
    if text.count('?') + text.count('？') > 1:
        return None
    return next((key for key, ending in INTENTS.items() if ending.search(text.rstrip())), None)


PATTERN_FIELDS = frozenset(
    {'query_must_include', 'required_slots', 'allowed_optional_slots', 'slot_terms', 'supported_query_patterns'}
)


class ReusePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_min_length=1)

    kind: Literal['pattern', 'intent'] = 'pattern'
    intent_id: str | None = None
    query_must_include: list[list[str]] = Field(default_factory=list)
    query_must_not_include: list[str] = Field(default_factory=list)
    required_slots: dict[str, str] = Field(default_factory=dict)
    allowed_optional_slots: dict[str, list[str]] = Field(default_factory=dict)
    slot_terms: dict[str, dict[str, str]] = Field(default_factory=dict)
    supported_query_patterns: list[str] = Field(default_factory=list)
    unsupported_condition_policy: Literal['bypass'] = 'bypass'

    @model_validator(mode='after')
    def validate_shape(self) -> Self:
        if self.kind == 'intent':
            if self.model_fields_set & PATTERN_FIELDS or (self.intent_id is not None and self.intent_id not in INTENTS):
                raise ValueError('invalid intent-only reuse policy')
        elif not (
            self.intent_id
            and self.supported_query_patterns
            and PATTERN_FIELDS <= self.model_fields_set
            and 'query_must_not_include' in self.model_fields_set
        ):
            raise ValueError('incomplete pattern reuse policy')
        return self

    @model_serializer(mode='wrap')
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict:
        row = handler(self)
        if self.kind == 'intent':
            for field in PATTERN_FIELDS:
                row.pop(field, None)
            if self.intent_id is None:
                row.pop('intent_id', None)
        return row


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
    """Check an intent hint or a reviewed pattern with enumerated slot phrases."""
    if policy.kind == 'intent':
        intent = classify_intent(question)
        if intent and policy.intent_id and intent != policy.intent_id:
            return Condition('known', 'intent_conflict')
        if conflict := forbidden_expression(question, policy):
            return Condition('known', 'condition_conflict', (conflict,))
        return Condition('known', 'matched')

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
    if conflict := forbidden_expression(question, policy):
        return Condition('known', 'condition_conflict', (*evidence, conflict))
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


def forbidden_expression(question: str, policy: ReusePolicy) -> Evidence | None:
    for term in policy.query_must_not_include:
        if (start := question.find(term)) != -1:
            return Evidence('forbidden', term, start, start + len(term))
    return None


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
