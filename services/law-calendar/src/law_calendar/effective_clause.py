"""Read commencement dates and explicitly scoped exceptions from an addendum."""

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

ARTICLE = r'제\d+조(?:의\d+)*'
WHOLE = re.compile(ARTICLE)
RANGE = re.compile(rf'({ARTICLE})\s*(?:부터|내지)\s*({ARTICLE})(?:\s*까지)?')
PARTIAL = re.compile(rf'({ARTICLE})(.+)')
PART_TOKEN = re.compile(r'제?\d+([항호목])(?:의\d+)*|[가-힣]목')
PART_RANK = {'항': 0, '호': 1, '목': 2}
CHAPTER = re.compile(r'제\d+장(?:의\d+)*의\s*제목')
HEADING = re.compile(r'^부칙(?:[ \t]*<[^>\n]+>)?[ \t]*\r?\n')
LATER_ARTICLE = re.compile(r'^[ \t]*제(?:[2-9]|[1-9]\d+)조\s*\(([^)\n]+)\)', re.MULTILINE)
LATER_DATE_RULE = re.compile(
    r'시행일\s*(?:은|을|[:：])|시행(?:한다|하고|하며|하되)|효력(?:이|을)\s*(?:발생|가진)'
    r'|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일|공포\s*(?:한\s*날|후\s*\d)'
)
DELEGATED = re.compile(r'(?:대통령령|부령|조례|규칙)으로\s*정하|(?:따로|별도로)\s*정하|정하는\s*날')
MAIN = re.compile(r'(?:제1조\s*\(시행일\)\s*)?이 (?:법|영|규칙)은 (.+?)부터 시행한다\.(?:\s*다만\s*,\s*(.+))?')
PROVISO = re.compile(r'(.+?)\s*의 개정규정은 (.+?)부터')
RELATIVE = re.compile(r'공포\s*후\s*(?=\d)(?:(\d+)\s*년\s*)?(?:(\d+)\s*개월)?이\s*경과한\s*날')
ABSOLUTE = re.compile(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일')


def _under(scope: str, parent: str) -> bool:
    return scope.startswith(parent) and PART_TOKEN.match(scope, len(parent)) is not None


@dataclass(frozen=True, slots=True)
class EffectiveSchedule:
    default_on: str
    scopes: dict[str, str]
    related_clauses: tuple[str, ...]
    range_order: tuple[str, ...] | None

    def scopes_for(self, article: str) -> dict[str, str]:
        return {scope: day for scope, day in self.scopes.items() if scope == article or _under(scope, article)}

    def date_for(self, article: str) -> tuple[str | None, str]:
        if self.range_order is not None and article not in self.range_order:
            return None, 'complex'
        scoped = self.scopes_for(article)
        if article in scoped:
            return scoped[article], 'scoped_addendum'
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


def _subscopes(article: str, suffix: str) -> tuple[str, ...] | None:
    """Carry the common parent across same-level dotted references."""
    path: list[tuple[int, str]] = []
    scopes = []
    for part in re.split(r'[ㆍ·]', re.sub(r'\s+', '', suffix)):
        matches = list(PART_TOKEN.finditer(part))
        tokens = [match[0] for match in matches]
        if not tokens or ''.join(tokens) != part:
            return None
        if any(token[0].isdigit() for token in tokens[1:]) or (not path and tokens[0][0].isdigit()):
            return None
        ranks = [PART_RANK[match[1] or '목'] for match in matches]
        if any(a >= b for a, b in pairwise(ranks)) or (path and ranks[0] > path[-1][0]):
            return None
        path = [entry for entry in path if entry[0] < ranks[0]]
        path.extend(
            (rank, f'제{token}' if token[0].isdigit() else token) for rank, token in zip(ranks, tokens, strict=True)
        )
        scopes.append(article + ''.join(token for _, token in path))
    return tuple(scopes)


def _labels(label: str, article_order: Sequence[str]) -> tuple[str, ...] | None:
    if WHOLE.fullmatch(label):
        return (label,)
    if CHAPTER.fullmatch(label):
        return ()  # A chapter title is not an article scope.
    if match := RANGE.fullmatch(label):
        if any(endpoint not in article_order for endpoint in match.groups()):
            return None
        first, last = (article_order.index(endpoint) for endpoint in match.groups())
        return tuple(article_order[first : last + 1]) if first <= last else None
    if match := PARTIAL.fullmatch(label):
        return _subscopes(match[1], match[2])
    return None


def parse_schedule(
    text: str, promulgated: str, article_order: Sequence[str] = ()
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
                '적용례' in match[1] or '경과조치' in match[1] or match[1] in {'다른 법률의 개정', '다른 법령의 개정'}
            )
            for match in later
        ) or any(LATER_DATE_RULE.search(clause) for clause in related):
            return None, 'complex'
        text = text[: starts[0]]
    if DELEGATED.search(text):
        return None, 'delegated'
    main = MAIN.fullmatch(' '.join(text.split()))
    if not main or (main[2] and not main[2].endswith(' 시행한다.')):
        return None, 'complex'

    clauses = re.split(r'\s+시행(?:하고|하며),\s*', main[2].removesuffix(' 시행한다.')) if main[2] else []
    scopes: dict[str, str] = {}
    range_order = None
    try:
        default_day = _addendum_date(main[1], promulgated)
        if default_day is None:
            return None, 'complex'
        for clause in clauses:
            proviso = PROVISO.fullmatch(clause)
            if not proviso or (day := _addendum_date(proviso[2], promulgated)) is None:
                return None, 'complex'
            for label in re.split(r'\s*,\s*|\s+및\s+', proviso[1]):
                labels = _labels(label, article_order)
                if labels is None:
                    return None, 'complex'
                if RANGE.fullmatch(label):
                    range_order = tuple(article_order)
                for scope in labels:
                    if any(
                        prior_day != day and (prior == scope or _under(prior, scope) or _under(scope, prior))
                        for prior, prior_day in scopes.items()
                    ):
                        return None, 'conflict'
                    scopes[scope] = day
    except OverflowError, ValueError:
        return None, 'conflict'
    return EffectiveSchedule(default_day, scopes, related, range_order), 'addendum' if clauses else 'simple_addendum'
