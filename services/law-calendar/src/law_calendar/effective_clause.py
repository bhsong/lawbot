"""Read commencement dates and explicitly scoped exceptions from an addendum."""

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from law_calendar.article_fragments import ANNOTATION
from law_calendar.effective_scope import ARTICLE, parse_targets

ARTICLE_PREFIX = re.compile(ARTICLE)
HEADING = re.compile(r'^부칙(?:[ \t]*<[^>\n]+>)?[ \t]*\r?\n')
LATER_ARTICLE = re.compile(r'^[ \t]*제(?:[2-9]|[1-9]\d+)조\s*\(([^)\n]+)\)', re.MULTILINE)
LATER_DATE_RULE = re.compile(
    r'시행일\s*(?:은|을|[:：])|시행(?:한다|하고|하며|하되)|효력(?:이|을)\s*발생'
    r'|(?:부터[^.]*?까지\s*|(?P<expiry>까지\s*))?효력(?:이|을)\s*가진'
)
DELEGATED = re.compile(r'(?:대통령령|부령|조례|규칙)으로\s*정하|(?:따로|별도로)\s*정하|정하는\s*날')
MAIN = re.compile(r'(?:제1조\s*\(시행일\)\s*)?(?:①\s*)?이 (?:법|영|규칙)은 (.+?)부터 시행한다\.')
PROVISO = re.compile(r'(.+?(?:개정규정))(?:은|는) (.+?)부터|(.+)(?:은|는) (.+?)부터')
NUMBERED = re.compile(
    r'(.*?)다음\s*각\s*호(?:의 구분에 따른|(?:의\s*(?:개정규정|사항|부분)(?:은|는))?\s+'
    r'(?:다음\s*)?(?:해당\s*)?(?:각\s*)?호(?:의 구분에 따른|에서 정하는|에서 정한)) 날부터 시행한다\.'
)
ENTRY = re.compile(r'^[ \t]*(\d+)\.[ \t]+', re.MULTILINE)
PARAGRAPH = re.compile(r'^[ \t]*[②-⑳][ \t]*', re.MULTILINE)
PROVISO_START = re.compile(r'\s*다만\s*,\s*')
RELATIVE = re.compile(r'공포\s*후\s*(?=\d)(?:(\d+)\s*년\s*)?(?:(\d+)\s*개월)?이\s*경과한\s*날')
ABSOLUTE = re.compile(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일')


@dataclass(frozen=True, slots=True)
class EffectiveSchedule:
    default_on: str
    scopes: dict[str, str | None]
    related_clauses: tuple[str, ...]
    range_order: tuple[str, ...] | None
    prior_amendments: dict[str, str]

    def scopes_for(self, article: str) -> dict[str, str | None]:
        return {
            scope: day
            for scope, day in self.scopes.items()
            if (match := ARTICLE_PREFIX.match(scope)) and match[0] == article
        }

    def date_for(self, article: str) -> tuple[str | None, str]:
        if self.range_order is not None and article not in self.range_order:
            return None, 'complex'
        scoped = self.scopes_for(article)
        if article in scoped and (
            scoped[article] is None or (len(scoped) == 1 and article not in self.prior_amendments)
        ):
            return scoped[article], 'scoped_addendum' if scoped[article] else 'complex'
        if scoped:
            return None, 'partial'
        return self.default_on, 'default_addendum'


def _addendum_date(expression: str, promulgated: str) -> str | None:
    """Use the same next-day and end-of-month convention as article checks."""
    expression = expression.strip()
    if re.fullmatch(r'공포\s*한\s*날', expression):
        return promulgated
    if match := ABSOLUTE.fullmatch(expression):
        return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
    if not (match := RELATIVE.fullmatch(expression)):
        return None
    months = int(match[1] or 0) * 12 + int(match[2] or 0)
    if not months:
        return None
    start = date.fromisoformat(promulgated) + timedelta(days=1)
    years, month = divmod(start.month - 1 + months, 12)
    year, month = start.year + years, month + 1
    if start.day > calendar.monthrange(year, month)[1]:
        return date(year + (month == 12), month % 12 + 1, 1).isoformat()
    return date(year, month, start.day).isoformat()


def _exceptions(text: str) -> list[tuple[str, str]] | None:
    """Read a list of targets with dates, or one target shared by a conditional list."""
    if not text:
        return []
    entries = list(ENTRY.finditer(text))
    if entries:
        head = ' '.join(text[: entries[0].start()].split())
        intro = NUMBERED.fullmatch(head)
        if not intro or [int(match[1]) for match in entries] != list(range(1, len(entries) + 1)):
            return None
        if intro[1]:
            target = re.sub(r'(?:은|는)\s*$', '', intro[1].strip())
            return [(target, '')]
        ends = [match.start() for match in entries[1:]] + [len(text)]
        rules = [
            ' '.join(text[match.end() : end].split()).rpartition(':') for match, end in zip(entries, ends, strict=True)
        ]
        return [(target, day) for target, _, day in rules] if all(sep for _, sep, _ in rules) else None
    text = ' '.join(text.split())
    if not text.endswith(' 시행한다.'):
        return None
    rules = re.split(r'\s+시행(?:하고|하며),\s*', text.removesuffix(' 시행한다.'))
    clauses = [PROVISO.fullmatch(rule) for rule in rules]
    return [(clause[1] or clause[3], clause[2] or clause[4]) for clause in clauses if clause] if all(clauses) else None


def parse_schedule(
    text: str,
    promulgated: str,
    article_order: Sequence[str] = (),
    law: str = '',
    earlier: Mapping[str, str] | None = None,
) -> tuple[EffectiveSchedule | None, str]:
    """Read a complete operative clause before assigning any default or exception."""
    text = HEADING.sub('', text.strip())
    if not text.strip():
        return None, 'missing'
    later = list(LATER_ARTICLE.finditer(text))
    related = ()
    if later:
        starts = [match.start() for match in later] + [len(text)]
        related = tuple(text[first:last].strip() for first, last in pairwise(starts))
        if any(
            '시행일' in match[1]
            or not (
                '적용례' in match[1]
                or '경과조치' in match[1]
                or '특례' in match[1]
                or '준비행위' in match[1]
                or '유효기간' in match[1]
                or match[1] in {'다른 법률의 개정', '다른 법령의 개정'}
            )
            for match in later
        ) or any(
            not rule['expiry']
            for match, clause in zip(later, related, strict=True)
            if match[1] not in {'다른 법률의 개정', '다른 법령의 개정'}
            for rule in LATER_DATE_RULE.finditer(clause)
        ):
            return None, 'complex'
        text = text[: starts[0]]
    first, *paragraphs = PARAGRAPH.split(ANNOTATION.sub('', text).strip())
    head, *provisos = PROVISO_START.split(first, maxsplit=1)
    main = MAIN.fullmatch(' '.join(head.split()))
    if not main:
        return None, 'delegated' if DELEGATED.search(head) else 'complex'

    scopes: dict[str, str | None] = {}
    prior_amendments: dict[str, str] = {}
    range_order = None
    clauses = []
    for rule in (*provisos, *(part for paragraph in paragraphs for part in PROVISO_START.split(paragraph))):
        parsed_rules = _exceptions(rule.strip())
        if parsed_rules is None:
            return None, 'complex'
        clauses.extend(parsed_rules)
    try:
        default_day = _addendum_date(main[1], promulgated)
        if default_day is None:
            return None, 'delegated' if DELEGATED.search(main[1]) else 'complex'
        for target, expression in clauses:
            parsed = parse_targets(target, article_order, law)
            if parsed is None:
                return None, 'complex'
            labels, uses_order = parsed
            if uses_order:
                range_order = tuple(article_order)
            day = _addendum_date(expression, promulgated)
            for scope, (exact, origin) in labels.items():
                prior_day = (earlier or {}).get(origin)
                value = day if exact and (not origin or prior_day) else None
                if scope in scopes:
                    if scopes[scope] and value and scopes[scope] != value:
                        return None, 'conflict'
                    value = value if scopes[scope] else None
                    if prior_day != prior_amendments.get(scope):
                        value = None
                scopes[scope] = value
                if value and prior_day:
                    prior_amendments[scope] = prior_day
    except OverflowError, ValueError:
        return None, 'conflict'
    return (
        EffectiveSchedule(default_day, scopes, related, range_order, prior_amendments),
        'addendum' if clauses else 'simple_addendum',
    )
