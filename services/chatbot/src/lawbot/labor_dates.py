"""Calendar inputs for employment duration calculations."""

import re
import unicodedata
from calendar import monthrange
from datetime import date

from lawbot.calc_numbers import parse_count

_DATE = re.compile(r'(\d{4})([-./])\s*(\d{1,2})\2\s*(\d{1,2})\.?')
_KOREAN_DATE = re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일')
_DURATION = re.compile(r'(?P<value>[0-9][0-9,]*|[영공일이삼사오육칠팔구]+)\s*(?P<unit>년|개월|일)')
_DURATION_UNITS = ('년', '개월', '일')


def parse_labor_date(text: str) -> date:
    source = unicodedata.normalize('NFKC', text).strip()
    if match := _KOREAN_DATE.fullmatch(source):
        return date(int(match[1]), int(match[2]), int(match[3]))
    if match := _DATE.fullmatch(source):
        return date(int(match[1]), int(match[3]), int(match[4]))
    raise ValueError('expected a full calendar date')


def parse_service_period(text: str) -> tuple[int, int, int]:
    source = unicodedata.normalize('NFKC', text).strip()
    values = [0, 0, 0]
    previous_index = -1
    end = 0
    for match in _DURATION.finditer(source):
        separator = source[end : match.start()].strip()
        unit = match['unit']
        index = _DURATION_UNITS.index(unit)
        if index <= previous_index or (separator and (not end or separator not in {'하고', '과', '및'})):
            raise ValueError('invalid service period')
        values[index] = parse_count(match[0], unit)
        previous_index, end = index, match.end()
    if not end or end != len(source):
        raise ValueError('invalid service period')
    return values[0], values[1], values[2]


def calendar_month_boundary(start: date, offset: int) -> date:
    """Find the corresponding day, or the day after the target month if absent."""
    year, month_index = divmod(start.year * 12 + start.month - 1 + offset, 12)
    if not date.min.year <= year <= date.max.year:
        raise ValueError('date outside the supported calendar')
    month = month_index + 1
    last_day = monthrange(year, month)[1]
    if start.day > last_day:
        return advance_days(date(year, month, last_day), 1)
    return date(year, month, start.day)


def advance_days(start: date, days: int) -> date:
    ordinal = start.toordinal() + days
    if not date.min.toordinal() <= ordinal <= date.max.toordinal():
        raise ValueError('date outside the supported calendar')
    return date.fromordinal(ordinal)


def completed_calendar_months(start: date, end: date) -> int:
    if end < start:
        raise ValueError('end date precedes start date')
    offset = 12 * (end.year - start.year) + end.month - start.month
    return offset - int(calendar_month_boundary(start, offset) > end)
