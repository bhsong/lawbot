"""Require distinct source evidence for every material labor quantity.

Roles describe statutory inputs, not calculator names or evaluation sentences.
Calendar references and measurement windows qualify other values; they are not
independent operands. Matching stays on original offsets throughout.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from lawbot.calc_numbers import _QUANTITY_PATTERNS, _SOURCE_NUMBER

_FIELD_ROLES = {
    'service_period': 'service',
    'monthly_wage': 'monthly_wage',
    'wage_total': 'period_wage',
    'wage_days': 'wage_days',
    'ordinary_daily_wage': 'daily_wage',
    'adjusted_daily_wage': 'daily_wage',
    'annual_bonus': 'bonus',
    'annual_unused_leave_pay': 'leave_pay',
    'excluded_wages': 'excluded_wage',
    'excluded_days': 'excluded_days',
    'workplace_size': 'staff',
    'staff_count': 'staff',
    'weekly_hours': 'weekly_hours',
    'weekly_scheduled_hours': 'weekly_hours',
    'four_week_scheduled_hours': 'period_hours',
    'daily_scheduled_hours': 'daily_hours',
    'paid_holiday_hours': 'paid_holiday_hours',
    'weekly_paid_holiday_hours': 'paid_holiday_hours',
    'monthly_basis_hours': 'monthly_hours',
    'attendance_rate': 'attendance',
    'full_attendance_months': 'attendance_months',
    'hourly_wage': 'hourly_wage',
    'ordinary_hourly_wage': 'hourly_wage',
    'reference_days_per_week': 'weekly_days',
    'weekly_workdays': 'weekly_days',
    'four_week_reference_days': 'period_days',
    'overtime_hours': 'overtime',
    'night_hours': 'night',
    'holiday_hours': 'holiday',
    'holiday_within_hours': 'holiday_within',
    'holiday_excess_hours': 'holiday_excess',
    'total_hours': 'total_hours',
}
_LABELS = {
    'duration': '기간·일수',
    'hours': '근로시간',
    'money': '임금·수당',
    'people': '상시 근로자 수',
    'percent': '출근율',
    'weeks': '기간·일수',
    'service': '계속근로 기간·일수',
}
_CALENDAR = re.compile(
    r'(?<!\d)\d{4}\s*(?:[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}'
    r'|년(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?)?)(?!\d)'
    r'|(?<!\d)\d{1,2}\s*월\s*\d{1,2}\s*일'
    r'|제\s*\d+\s*(?:조(?:의\s*\d+)?|항|호)'
    r'|(?<!\d)\d{1,2}\s*(?:시(?:\s*\d{1,2}\s*분)?|:\s*\d{2})(?!간|\d)'
)
_PATTERNS = {
    **_QUANTITY_PATTERNS,
    'people': re.compile(rf'{_SOURCE_NUMBER}\s*(?:명|인)(?![가-힣]*원)'),
    'weeks': re.compile(rf'{_SOURCE_NUMBER}\s*주(?!년)'),
}
_SERVICE = r'근속|계속\s*근로|(?:근로|근무|재직)\s*(?:기간|일수|일\s*수)|(?:일했|근무했|재직했)'
_ROLE_WORDS = {
    'money': {
        'monthly_wage': r'월\s*(?:급|임금|급여)|기본급',
        'hourly_wage': r'시급|시간\s*급',
        'daily_wage': r'일급|일\s*(?:통상|평균)\s*임금|(?:통상|평균)\s*일급',
        'period_wage': r'임금\s*(?:합계|총액)|(?:총|합산)\s*임금',
        'bonus': r'상여(?:금)?',
        'leave_pay': r'(?:연차|휴가)\s*(?:미사용\s*)?수당',
        'excluded_wage': r'(?:제외|공제)\s*임금|제외\s*(?:금액|급여)',
    },
    'hours': {
        'weekly_hours': r'(?:매\s*주|주\s*(?:당|간)?)(?:\s*소정)?',
        'period_hours': r'주\s*(?:간\s*)?(?:합계|총|전체)',
        'daily_hours': r'하루|일\s*(?:소정|당)',
        'paid_holiday_hours': r'(?:유급\s*)?주휴',
        'monthly_hours': r'월[^,.!?;]*?(?:환산|기준|소정)|월간',
        'overtime': r'연장',
        'night': r'야간',
        'holiday': r'휴일',
        'holiday_within': r'휴일[^,.!?;]*?(?:이내|이하)',
        'holiday_excess': r'휴일[^,.!?;]*?(?:초과|이후)',
        'total_hours': r'총|전체',
    },
    'duration': {
        'service': _SERVICE,
        'wage_days': r'(?:산정|임금)\s*(?:기간|일수|일\s*수)|역일\s*수|분모',
        'excluded_days': r'제외\s*(?:기간|일수|일\s*수)',
        'attendance_months': r'개근',
        'weekly_days': r'주\s*(?:당|간)?\s*(?:소정|근무|근로)?|통상\s*근로자',
        'period_days': r'주\s*(?:간\s*)?(?:합계|총|전체)',
        'employment_window': r'근로관계|고용관계',
    },
}
_COMPILED_ROLES = {
    kind: {role: re.compile(pattern) for role, pattern in roles.items()} for kind, roles in _ROLE_WORDS.items()
}
_ACTUAL_WORK = {'overtime', 'night', 'holiday', 'holiday_within', 'holiday_excess'}
_MEASURE_HEAD = re.compile(r'\s*(?:동안|간|의)?\s*(?:출근율|평균|합계|총|임금|월급|급여|역일)')
_LOOKBACK = re.compile(r'(?:직전|최근|지난)\s*$')
_DENOMINATOR = re.compile(r'\s*(?:당|통상\s*임금|평균\s*임금)')
_BAND_BOUNDARY = re.compile(r'\s*(?:이내|이하|초과|이상)\s*분')
_AFTER_PARTICLE = re.compile(r'^\s*(?:동안|을|를|은|는|이|가|의|간)?\s*')
_BOUNDARY = re.compile(r'[,，.!?;\n](?!\d)')
_WINDOW_AND_DAYS = re.compile(rf'(?P<window>{_SOURCE_NUMBER}\s*(?:년|개월))\s*(?P<days>{_SOURCE_NUMBER}\s*일)')


@dataclass(frozen=True)
class _Quantity:
    start: int
    end: int
    kind: str


def _quantities(question: str) -> list[_Quantity]:
    masked = _CALENDAR.sub(lambda match: ' ' * len(match[0]), question)
    candidates = []
    for kind, pattern in _PATTERNS.items():
        for match in pattern.finditer(masked):
            start = match.start() + len(match[0]) - len(match[0].lstrip())
            candidates.append(_Quantity(start, match.end(), kind))
    found: list[_Quantity] = []
    for item in sorted(candidates, key=lambda item: (item.start, -item.end)):
        if not found or found[-1].end <= item.start:
            found.append(item)
    separated = []
    for index, item in enumerate(found):
        before, after = _context(question, found, index)
        compound = _WINDOW_AND_DAYS.fullmatch(question[item.start : item.end])
        if item.kind == 'duration' and compound and (_LOOKBACK.search(before) or _MEASURE_HEAD.match(after)):
            separated.extend(
                (
                    _Quantity(item.start, item.start + compound.end('window'), 'measurement_window'),
                    _Quantity(item.start + compound.start('days'), item.end, 'duration'),
                )
            )
        else:
            separated.append(item)
    return separated


def _context(question: str, quantities: list[_Quantity], index: int) -> tuple[str, str]:
    item = quantities[index]
    previous = index - 1
    while previous >= 0 and quantities[previous].kind == 'weeks':
        previous -= 1
    start = quantities[previous].end if previous >= 0 else 0
    end = quantities[index + 1].start if index + 1 < len(quantities) else len(question)
    before = _BOUNDARY.split(question[start : item.start])[-1]
    after = _BOUNDARY.split(question[item.end : end])[0]
    return before, after


def _role(question: str, item: _Quantity, before: str, after: str) -> str | None:
    value = question[item.start : item.end]
    if item.kind == 'measurement_window':
        return 'measurement_window'
    if item.kind == 'people':
        return 'staff'
    if item.kind == 'percent':
        return 'attendance' if re.search(r'출근|출석', before + after) else None
    if item.kind == 'weeks':
        return 'service' if re.search(_SERVICE, before) else 'measurement_window'
    if item.kind == 'hours' and _BAND_BOUNDARY.match(after):
        return 'band_boundary'
    if item.kind == 'duration':
        if _DENOMINATOR.match(after):
            return 'denominator'
        if not value.endswith('일') and _LOOKBACK.search(before) and re.search(r'월급|임금|급여|출근율', after):
            return 'measurement_window'
        if not value.endswith('일') and _MEASURE_HEAD.match(after):
            return 'measurement_window'
    words = _COMPILED_ROLES.get(item.kind, {})
    matches = [
        (match.end(), len(match[0]), role) for role, pattern in words.items() for match in pattern.finditer(before)
    ]
    suffix = [role for role, pattern in words.items() if pattern.match(_AFTER_PARTICLE.sub('', after))]
    if item.kind == 'hours' and (actual := [role for role in suffix if role in _ACTUAL_WORK]):
        return actual[0]
    if matches:
        return max(matches)[2]
    return next(iter(suffix), None)


def unaccounted_labor_quantity(
    question: str,
    fields: Mapping[str, str],
    positions: Mapping[str, tuple[tuple[int, int], ...]],
) -> str | None:
    """Return a role label unless all relevant quantities have distinct input evidence.

    One extracted field can account for only one occurrence. Fact/date spans are
    intentionally absent: a long factual excerpt cannot swallow other operands.
    """
    supported = {_FIELD_ROLES[field] for field in fields if field in _FIELD_ROLES}
    quantities = _quantities(question)
    evidence: list[tuple[str, tuple[str, ...]]] = []
    for index, item in enumerate(quantities):
        role = _role(question, item, *_context(question, quantities, index))
        if role is not None and role not in supported:
            continue
        # Unlabelled money/time must still be accounted for when its type is an input.
        if role is None and item.kind not in ('money', 'hours', 'duration'):
            continue
        if role is None and not (
            item.kind in fields.values() or (item.kind == 'duration' and 'count' in fields.values())
        ):
            continue
        candidates = tuple(
            field
            for field, spans in positions.items()
            if fields.get(field) not in ('fact', 'date', 'year')
            and any(start <= item.start and item.end <= end for start, end in spans)
        )
        evidence.append((_LABELS.get(role or '', _LABELS[item.kind]), candidates))

    assigned: dict[str, int] = {}

    def assign(index: int, visited: set[str]) -> bool:
        for field in evidence[index][1]:
            if field in visited:
                continue
            visited.add(field)
            if field not in assigned or assign(assigned[field], visited):
                assigned[field] = index
                return True
        return False

    for index, (label, _) in enumerate(evidence):
        if not assign(index, set()):
            return label
    return None
