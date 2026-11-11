"""Parse source quantities without relying on a model's numeric conversions."""

import re
import unicodedata
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction
from functools import cache
from typing import Literal

from lawbot.labor_language import is_uncertain_quantity

_DIGITS = '영공일이삼사오육칠팔구'
_VALUES = str.maketrans(_DIGITS, '00123456789')
_ARABIC = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
_SMALL_UNITS = '천백십'
_LARGE_UNITS = '조억만'
_SMALL_VALUES = dict(zip(_SMALL_UNITS, (1000, 100, 10)))
_LARGE_VALUES = dict(zip(_LARGE_UNITS, (10**12, 10**8, 10**4)))
_CHARACTERS = f'{_DIGITS}{_SMALL_UNITS}{_LARGE_UNITS}'
_MONEY_START = f'0-9０-９{_CHARACTERS}'
_MONEY_CHARS = f'{_MONEY_START}.,，．'
_MONEY_TOKEN = re.compile(
    rf'(?:(?<![0-9０-９.,，．])|(?<=원[,，]))'
    rf'(?P<sign>[-−]?[ \t]*)(?P<amount>[{_MONEY_START}][{_MONEY_CHARS}]*'
    rf'(?:[ \t]+[{_MONEY_CHARS}]+)*[ \t]*원)'
)
_MAX_SAFE_INTEGER = 2**53 - 1

_PARTICLE = r'\s*(?:은|는|이|가|:)?\s*'
_WEEK_SCOPE = r'(?:매\s*주|1\s*주|주(?:당|간)?)'
_WEEK_AVERAGE = r'[0-9]+\s*주\s*(?:(?:동안|간)\s*(?:의\s*)?)?평균'
_SCHEDULED = r'소정(?:\s*근로)?(?:\s*시간(?:\s*수)?)?'
_HOURS_CONTEXT = re.compile(
    rf'(?:(?P<holiday>(?:주\s*)?(?:유급\s*)?주휴(?:\s*시간)?)'
    rf'|(?P<week>{_WEEK_AVERAGE}(?:\s*{_WEEK_SCOPE})?(?:\s*{_SCHEDULED})?'
    rf'|{_WEEK_SCOPE}(?:\s*평균)?(?:\s*{_SCHEDULED})?)|{_SCHEDULED}){_PARTICLE}'
)
_HOURS_FIELDS = {
    'holiday': {'paid_holiday_hours', 'weekly_paid_holiday_hours'},
    'week': {'weekly_hours', 'weekly_scheduled_hours'},
}
_HOURLY_WAGE_LABEL = re.compile(rf'(?:(?:통상\s*)?시급|시간급(?:\s*통상\s*임금)?){_PARTICLE}')
_COUNT_UNITS = {
    'workplace_size': ('명', '인'),
    'staff_count': ('명', '인'),
    'full_attendance_months': ('개월',),
}
_SOURCE_NUMBER = rf'(?:[0-9０-９]+(?:[,，][0-9０-９]+)*(?:[.．][0-9０-９]+)?|(?<![가-힣])[{_DIGITS}]+)'
_DURATION_JOIN = r'\s*(?:하고|과|및)?\s*'
_QUANTITY_PATTERNS = {
    'hours': re.compile(rf'{_SOURCE_NUMBER}\s*(?:시간(?:\s*{_SOURCE_NUMBER}\s*분)?|분)'),
    'percent': re.compile(rf'{_SOURCE_NUMBER}\s*[%％]'),
    'duration': re.compile(
        rf'{_SOURCE_NUMBER}\s*(?:년(?:{_DURATION_JOIN}{_SOURCE_NUMBER}\s*개월)?'
        rf'(?:{_DURATION_JOIN}{_SOURCE_NUMBER}\s*일)?|개월(?:{_DURATION_JOIN}{_SOURCE_NUMBER}\s*일)?|일)'
    ),
    'money': _MONEY_TOKEN,
}
_UNIT_EXTENSIONS = {
    'hours': re.compile(rf'\s*(?:시간(?:\s*{_ARABIC}\s*분)?|분)'),
    'percent': re.compile(r'\s*[%％]'),
    'duration': re.compile(rf'\s*(?:년|개월|일)(?:\s*(?:하고|과|및)?\s*[\d{_DIGITS}]+\s*(?:개월|일))*'),
}


def _plain_number(text: str) -> Decimal:
    if text and all(char in _DIGITS for char in text):
        return Decimal(text.translate(_VALUES))
    if not re.fullmatch(_ARABIC, text) or len(text) > 25:
        raise ValueError('invalid number')
    return Decimal(text.replace(',', ''))


def _small_number(text: str) -> Decimal:
    if not any(unit in text for unit in _SMALL_UNITS):
        return _plain_number(text)
    total, previous = Decimal(0), 10_000
    for coefficient, unit in re.findall(r'([^천백십]*)([천백십]|$)', text):
        if not coefficient and not unit:
            continue
        value = _plain_number(coefficient) if coefficient else Decimal(1)
        place = _SMALL_VALUES[unit] if unit else 1
        if place >= previous or not 0 < value * place < previous:
            raise ValueError('invalid unit order')
        if unit and value >= 10:
            raise ValueError('invalid unit coefficient')
        total += value * place
        previous = place
    return total


def _amount_in_won(text: str) -> Decimal:
    value = unicodedata.normalize('NFKC', text).strip()
    with_won = value.endswith('원')
    value = value.removesuffix('원').strip()
    if not re.fullmatch(rf'[0-9.,{_CHARACTERS}]+(?:[ \t]+[0-9.,{_CHARACTERS}]+)*', value):
        raise ValueError('invalid amount')
    # A space can separate unit groups, but cannot join two coefficients.
    if re.search(rf'[0-9{_DIGITS}][ \t]+[0-9{_DIGITS}]', value):
        raise ValueError('separated coefficients')
    value = ''.join(value.split())
    if len(value) > 35:
        raise ValueError('invalid amount length')
    total, previous = Decimal(0), 10**16
    for coefficient, unit in re.findall(r'([^조억만]*)([조억만]|$)', value):
        if not coefficient and not unit:
            continue
        number = _small_number(coefficient) if coefficient else Decimal(1)
        place = _LARGE_VALUES[unit] if unit else 1
        if place >= previous or not 0 <= number * place < previous:
            raise ValueError('invalid unit order')
        if not unit and previous != 10**16 and not with_won and not any(u in coefficient for u in _SMALL_UNITS):
            raise ValueError('ambiguous remainder')
        total += number * place
        previous = place
    return total


def parse_amount(text: str) -> int:
    """Read a nonnegative, exact amount in won, with optional Korean unit groups."""
    amount = _amount_in_won(text)
    if amount != amount.to_integral_value() or amount > _MAX_SAFE_INTEGER:
        raise ValueError('amount is not an exact supported integer')
    return int(amount)


def parse_quantity(text: str, unit: Literal['%', '개월']) -> Decimal:
    """Read a percentage in percentage points or a duration in whole months."""
    kind = 'percent' if unit == '%' else 'count'
    value = unicodedata.normalize('NFKC', quantity_excerpt(text, kind, unit=unit))
    if not value.endswith(unit):
        raise ValueError('unit is missing')
    number = _plain_number(value.removesuffix(unit).strip())
    if unit == '개월' and number != number.to_integral_value():
        raise ValueError('duration is not a whole number of months')
    return number


def parse_year(text: str) -> int:
    source = unicodedata.normalize('NFKC', text).strip()
    if not (match := re.fullmatch(r'([0-9]{4})(?:\s*년도?)?', source)):
        raise ValueError('invalid tax year')
    return int(match[1])


def amount_spans(text: str) -> Iterator[tuple[str, int | Decimal]]:
    """Yield explicit won amounts, including fractional values displayed in calculation steps."""
    previous_end: int | None = None
    for match in _MONEY_TOKEN.finditer(text):
        try:
            amount = _amount_in_won(match['amount'])
        except ValueError:
            continue
        value = int(amount) if amount == amount.to_integral_value() else amount
        source = match[0].strip()
        if match['sign'].strip():
            # A minus between adjacent won amounts is a subtraction operator.
            if previous_end is not None and not text[previous_end : match.start('sign')].strip(' \t'):
                source = match['amount']
            else:
                value = -value
        previous_end = match.end()
        yield source, value


def parse_count(text: str, unit: str) -> int:
    value = unicodedata.normalize('NFKC', quantity_excerpt(text, 'count', unit=unit))
    if unit in ('명', '인'):
        unit = value[-1] if value.endswith(('명', '인')) else unit
    if not value.endswith(unit):
        raise ValueError('missing count unit')
    number = _plain_number(value.removesuffix(unit).strip())
    if number != number.to_integral_value() or number > _MAX_SAFE_INTEGER:
        raise ValueError('invalid count')
    return int(number)


def parse_hours(text: str) -> Fraction:
    value = unicodedata.normalize('NFKC', quantity_excerpt(text, 'hours'))
    match = re.fullmatch(rf'(?:(?P<hours>{_ARABIC})\s*시간)?\s*(?:(?P<minutes>{_ARABIC})\s*분)?', value)
    if not match or not any(match.groups()):
        raise ValueError('missing hour or minute unit')
    hours = Fraction(_plain_number(match['hours'])) if match['hours'] else Fraction()
    minutes = Fraction(_plain_number(match['minutes'])) if match['minutes'] else Fraction()
    return hours + minutes / 60


def labor_hour_context_matches(field: str, source: str) -> bool:
    return all(
        match.lastgroup is None or field in _HOURS_FIELDS[match.lastgroup]
        for match in _HOURS_CONTEXT.finditer(unicodedata.normalize('NFKC', source))
    )


class AmbiguousQuantity(ValueError):
    """A source contains multiple quantities or does not assert an exact value."""


def quantity_excerpt(text: str, kind: str, *, unit: str = '') -> str:
    """Read one complete quantity while retaining its original spelling and units."""
    if is_uncertain_quantity(text):
        raise AmbiguousQuantity('quantity is not exact')
    if kind == 'count':
        units = r'(?:명|인)' if unit in ('명', '인') else re.escape(unit)
        pattern = re.compile(rf'{_SOURCE_NUMBER}\s*{units}')
    else:
        pattern = _QUANTITY_PATTERNS[kind]
    matches = list(pattern.finditer(text))
    if len(matches) > 1:
        raise AmbiguousQuantity('multiple quantities')
    if not matches:
        raise ValueError('quantity with the expected unit is missing')
    match = matches[0]
    positions = complete_amount_spans(text, match[0]) if kind == 'money' else complete_labor_spans(text, match[0], kind)
    if match.span() not in positions:
        raise ValueError('incomplete quantity')
    return match[0]


def labor_quantity_excerpt(
    question: str, source: str, field: str, kind: str
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Discard context only around a unique quantity at its original complete position."""
    if kind not in _QUANTITY_PATTERNS and kind != 'count':
        return source, ()
    if kind == 'money' and _HOURLY_WAGE_LABEL.search(source) and field not in ('hourly_wage', 'ordinary_hourly_wage'):
        raise AmbiguousQuantity('hourly wage assigned to another monetary field')
    if kind == 'money' and '원' not in source:
        return source, tuple(labor_amount_spans(question, source, field))
    unit = _COUNT_UNITS.get(field, ('일',))[0]
    excerpt = quantity_excerpt(source, kind, unit=unit)
    positions = set(
        complete_amount_spans(question, excerpt) if kind == 'money' else complete_labor_spans(question, excerpt, kind)
    )
    offset = source.index(excerpt)
    anchored = tuple(
        position
        for match in re.finditer(re.escape(source), question)
        if (position := (match.start() + offset, match.start() + offset + len(excerpt))) in positions
    )
    if not anchored:
        raise AmbiguousQuantity('incomplete original quantity')
    return excerpt, anchored


def parse_labor_amount(text: str) -> int:
    """Read one exact wage amount, optionally including its original context."""
    if '원' in text:
        return parse_amount(quantity_excerpt(text, 'money'))
    return parse_amount(_HOURLY_WAGE_LABEL.sub('', unicodedata.normalize('NFKC', text).strip()))


def labor_amount_spans(question: str, source: str, field: str) -> Iterator[tuple[int, int]]:
    # A label must not bypass the numeric boundary or change the wage's role.
    label = _HOURLY_WAGE_LABEL.match(source.lstrip())
    if label is None:
        yield from complete_amount_spans(question, source)
        return
    if field not in ('hourly_wage', 'ordinary_hourly_wage'):
        return
    offset = len(source) - len(source.lstrip()) + label.end()
    complete = set(complete_amount_spans(question, source[offset:]))
    for match in re.finditer(re.escape(source), question):
        if (match.start() + offset, match.end()) in complete:
            yield match.span()


def restore_labor_unit(question: str, source: str, field: str, kind: str) -> str | None:
    """Extend a unitless excerpt only when there is one complete matching source span."""
    value = unicodedata.normalize('NFKC', source).strip()
    if not re.fullmatch(rf'(?:{_ARABIC}|[{_DIGITS}]+)', value):
        return source
    if kind == 'count':
        units = '|'.join(_COUNT_UNITS.get(field, ('일',)))
        suffix = re.compile(rf'\s*(?:{units})')
    else:
        suffix = _UNIT_EXTENSIONS.get(kind)
    if suffix is None:
        return source
    matches = []
    for match in re.finditer(re.escape(source), question):
        if extension := suffix.match(question, match.end()):
            candidate = question[match.start() : extension.end()]
            if (match.start(), extension.end()) in complete_labor_spans(question, candidate, kind):
                matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def complete_labor_spans(question: str, source: str, kind: str) -> Iterator[tuple[int, int]]:
    """Yield exact offsets for complete counts, dates, percentages, and compound times."""
    value = unicodedata.normalize('NFKC', source).strip()
    for match in re.finditer(re.escape(source), question):
        before = unicodedata.normalize('NFKC', question[: match.start()])
        after = unicodedata.normalize('NFKC', question[match.end() :])
        if re.match(rf'[\d{_DIGITS}]', value) and re.search(r'(?:\d[.,]?|[+\-−~～]\s*)$', before):
            continue
        if re.match(r'[.,]?\d', after):
            continue
        if kind == 'hours':
            if value.endswith('시간') and re.match(r'\s*\d+(?:\.\d+)?\s*분', after):
                continue
            if '시간' not in value and re.search(r'\d\s*시간\s*$', before):
                continue
        if kind == 'duration':
            connector = r'\s*(?:하고|및|와|과)?\s*'
            tail = '개월|달|일' if value.endswith('년') else '일' if value.endswith(('개월', '달')) else ''
            number = rf'[\d{_CHARACTERS}]+'
            if tail and re.match(rf'{connector}{number}\s*(?:{tail})', after):
                continue
            if not re.search(rf'{number}\s*년', value) and re.search(rf'{number}\s*년{connector}$', before):
                continue
        yield match.span()


def complete_amount_spans(question: str, source: str) -> Iterator[tuple[int, int]]:
    """Yield original offsets for exact excerpts at a complete amount boundary."""
    normalized_source = unicodedata.normalize('NFKC', source).strip()
    for match in re.finditer(re.escape(source), question):
        before = unicodedata.normalize('NFKC', question[: match.start()])
        after = unicodedata.normalize('NFKC', question[match.end() :])
        if re.search(rf'[0-9{_SMALL_UNITS}{_LARGE_UNITS}]$|[0-9][.,]$|[-−+~～]\s*$', before):
            continue
        if re.search(rf'[{_DIGITS}]$', before) and re.match(rf'[{_CHARACTERS}]', normalized_source):
            continue
        if re.match(r'[.,]?[0-9]', after):
            continue
        if re.search(rf'[0-9{_CHARACTERS}]\s*[{_SMALL_UNITS}{_LARGE_UNITS}][ \t]+$', before):
            continue
        if not normalized_source.endswith('원'):
            if re.match(rf'\s*(?:[{_SMALL_UNITS}{_LARGE_UNITS}%]|년|개월|시간|일|세|명)', after):
                continue
            if re.match(rf'\s*[{_DIGITS}]+(?:[{_SMALL_UNITS}{_LARGE_UNITS}]|\s*원|$)', after):
                continue
            if re.search(rf'[{_SMALL_UNITS}{_LARGE_UNITS}]$', normalized_source) and re.match(r'\s*[0-9]', after):
                continue
        yield match.span()


def can_assign_spans(candidates: list[tuple[tuple[int, int], ...]]) -> bool:
    """Check that every input field can use a disjoint occurrence."""

    @cache
    def assign(remaining: tuple[tuple[tuple[int, int], ...], ...]) -> bool:
        if not remaining:
            return True
        if not all(remaining):
            return False
        # Even allowing any field to take any span, there must be enough disjoint intervals.
        end, count = -1, 0
        for start, finish in sorted({span for field in remaining for span in field}, key=lambda span: span[1]):
            if start >= end:
                count += 1
                end = finish
        if count < len(remaining):
            return False
        choices, *others = sorted(remaining, key=len)
        return any(
            assign(tuple(tuple(span for span in field if span[1] <= start or end <= span[0]) for field in others))
            for start, end in choices
        )

    return assign(tuple(candidates))
