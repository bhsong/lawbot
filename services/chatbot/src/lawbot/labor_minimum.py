"""Convert the applicable minimum hourly wage to a monthly amount."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from typing import TYPE_CHECKING

from lawbot.calc_numbers import parse_count, parse_hours

if TYPE_CHECKING:
    from lawbot.calculators import Calculation, InputRow, Step

FIELDS = {
    'year': 'year',
    'weekly_scheduled_hours': 'hours',
    'daily_scheduled_hours': 'hours',
    'weekly_workdays': 'count',
    'weekly_paid_holiday_hours': 'hours',
    'monthly_basis_hours': 'hours',
    'applicability': 'fact',
}

_CONVERSION_ARTICLE = '최저임금법 시행령 제5조'
_LABELS = {
    'weekly_scheduled_hours': '주 소정근로시간',
    'daily_scheduled_hours': '일 소정근로시간',
    'weekly_workdays': '주 소정근로일수',
    'weekly_paid_holiday_hours': '주 유급주휴시간',
    'monthly_basis_hours': '월 최저임금 적용기준시간',
    'applicability': '최저임금 적용 조건',
}
_MONTH_BASIS_LABEL = r'(?:월(?:간)?|(?:1\s*)?개월(?:의)?)\s*(?:최저임금\s*)?적용\s*기준\s*시간(?:\s*수)?'
_HOUR_TEXT = r'\d+(?:\.\d+)?\s*시간(?:\s*\d+(?:\.\d+)?\s*분)?'
_MONTH_BASIS = re.compile(
    rf'{_MONTH_BASIS_LABEL}\s*(?:은|는|이|:)?\s*({_HOUR_TEXT})|({_HOUR_TEXT})\s*(?:이|을|가)?\s*{_MONTH_BASIS_LABEL}'
)
_MONTH_BASIS_RANGE = re.compile(
    rf'{_MONTH_BASIS_LABEL}\s*(?:은|는|이|:)?\s*\d+(?:\.\d+)?\s*(?:시간)?\s*(?:또는|혹은|[~∼–-])\s*{_HOUR_TEXT}'
)
_SPECIAL_CONDITIONS = re.compile(
    r'수습|감액|적용\s*제외|가사\s*사용인|동거\s*친족|선원|도급|산입|미달|체불|최저임금\s*위반'
)
_ORDINARY_APPLICABILITY = re.compile(
    r'(?:최저임금(?:법)?전액적용|일반근로자|감액(?:이)?없(?:음|습니다)|수습(?:이)?아님)'
)
_ACTUAL_HOURS = re.compile(r'실제|실근로|근태|일한\s*시간|근무했|일했')
_CONDITION_MESSAGE = '수습 감액이나 법 적용 제외 여부, 또는 비교할 임금 항목을 확인해 주세요.'


def _ordinary(source: str) -> bool:
    compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', source)).strip('.!?')
    return _ORDINARY_APPLICABILITY.fullmatch(compact) is not None


FACT_LABELS = {'applicability': '수습 감액이나 최저임금 적용 제외 해당 여부'}


def fact_polarity(field: str, evidence: str) -> bool | None:
    """Return the polarity the wording itself makes certain, or None."""
    if field != 'applicability':
        return None
    if _ordinary(evidence):
        return False
    if _SPECIAL_CONDITIONS.search(evidence) and not re.search(r'아니|않|없', evidence):
        return True
    return None


def question_issue(question: str, inputs: Mapping[str, str], facts: Mapping[str, bool] | None = None) -> str | None:
    """Check that monthly basis hours and material exceptions were reported."""
    normalized_question = unicodedata.normalize('NFKC', question)
    basis = re.sub(r'\s+', '', unicodedata.normalize('NFKC', inputs.get('monthly_basis_hours', '')))
    stated = {re.sub(r'\s+', '', match[1] or match[2]) for match in _MONTH_BASIS.finditer(normalized_question)}
    source = inputs.get('monthly_basis_hours', '')
    described_as_worked = bool(source) and any(
        source in clause and _ACTUAL_HOURS.search(clause) for clause in re.split(r'[.!?\n,;]', question)
    )
    if (
        _MONTH_BASIS_RANGE.search(normalized_question)
        or (stated and stated != {basis})
        or (not stated and described_as_worked)
    ):
        return '질문에 적힌 월 최저임금 적용기준시간을 확인해 주세요.'
    if facts is not None and 'applicability' in facts:
        clauses = re.split(r'[!?\n;]|(?<!\d)\.|\.(?!\d)', question)
        if facts['applicability'] or any(fact_polarity('applicability', clause) for clause in clauses):
            return _CONDITION_MESSAGE
    elif _SPECIAL_CONDITIONS.search(question):
        condition = inputs.get('applicability', '')
        if not _ordinary(condition):
            return _CONDITION_MESSAGE
        clauses = re.split(r'[!?\n;]|(?<!\d)\.|\.(?!\d)', question)
        if any(_SPECIAL_CONDITIONS.search(clause) and not _ordinary(clause) for clause in clauses):
            return _CONDITION_MESSAGE
    return None


def _shown(value: Fraction | int) -> int | str:
    return int(value) if value == int(value) else f'{value:.4f}'.rstrip('0').rstrip('.')


def calculate(
    base: Calculation, inputs: Mapping[str, str], rules: dict, facts: Mapping[str, bool] | None = None
) -> Calculation:
    rows: list[InputRow] = []
    hours: dict[str, Fraction] = {}
    workdays = None
    try:
        for key, source in inputs.items():
            if FIELDS.get(key) == 'hours':
                hours[key] = value = parse_hours(source)
                rows.append(
                    {'label': _LABELS[key], 'value': _shown(value), 'unit': '시간', 'source': f'질문: {source}'}
                )
            elif key == 'weekly_workdays':
                workdays = parse_count(source, '일')
                rows.append({'label': _LABELS[key], 'value': workdays, 'unit': '일', 'source': f'질문: {source}'})
            elif key == 'applicability':
                rows.append({'label': _LABELS[key], 'value': source, 'source': f'질문: {source}'})
    except ValueError:
        return replace(
            base, reason='ambiguous_input', message='소정근로시간·일수와 월 적용기준시간을 하나의 값으로 알려 주세요.'
        )
    base = replace(base, inputs=(*base.inputs, *rows))

    def guidance(message: str, reason: str = 'missing_required') -> Calculation:
        return replace(base, reason=reason, message=message)

    if facts is not None and 'applicability' in facts:
        special = facts['applicability']
    else:
        special = 'applicability' in inputs and not _ordinary(inputs['applicability'])
    if special:
        return guidance(_CONDITION_MESSAGE, 'ambiguous_input')
    wage_rule = rules['hourly_wage']
    month_rule = rules['standard_monthly_basis_hours']
    standard_weekly = rules['standard_weekly_hours']['value']
    standard_holiday = rules['standard_paid_holiday_hours']['value']
    standard_workdays = Fraction(standard_weekly, standard_holiday)
    hourly_wage = wage_rule['value']
    standard_month = month_rule['value']
    daily = hours.get('daily_scheduled_hours')
    weekly = hours.get('weekly_scheduled_hours')
    paid_holiday = hours.get('weekly_paid_holiday_hours')
    month = hours.get('monthly_basis_hours')
    if daily is not None and workdays is not None:
        calculated_weekly = daily * workdays
        if weekly is not None and weekly != calculated_weekly:
            return guidance('일·주 소정근로시간과 주 소정근로일수를 확인해 주세요.', 'ambiguous_input')
        weekly = calculated_weekly
    standard = weekly == standard_weekly and (
        paid_holiday == standard_holiday or (paid_holiday is None and workdays == standard_workdays)
    )
    if standard and month is not None and month != standard_month:
        return guidance('고시의 표준 월 적용기준시간과 질문에 적힌 시간을 확인해 주세요.', 'ambiguous_input')
    if month is None and not standard:
        return guidance('월 최저임금 적용기준시간 또는 표준 근무의 소정근로시간·일수를 알려 주세요.')
    if not standard and 'year' not in inputs:
        return guidance('최저임금 적용연도를 알려 주세요.')
    if month is not None and month <= 0:
        return guidance('월 최저임금 적용기준시간을 확인해 주세요.', 'ambiguous_input')
    basis = standard_month if standard else month
    assert basis is not None
    pay = basis * hourly_wage
    basis_formula = (
        f'주 소정 {standard_weekly}시간 + 유급주휴 {standard_holiday}시간 기준 고시값'
        if standard
        else '월 최저임금 적용기준시간 입력값'
    )
    display = ' (소수점 4자리 표시)' if pay.denominator != 1 else ''
    steps: tuple[Step, ...] = (
        {
            'label': '시간급 최저임금',
            'formula': f'{base.year}년 적용 최저임금 고시',
            'value': hourly_wage,
            'unit': '원',
            'article': wage_rule['article'],
        },
        {
            'label': '월 최저임금 적용기준시간',
            'formula': basis_formula,
            'value': _shown(basis),
            'unit': '시간',
            'article': month_rule['article'],
        },
        {
            'label': '월 최저임금',
            'formula': f'시간급 최저임금 × 월 적용기준시간{display}',
            'value': _shown(pay),
            'unit': '원',
            'article': _CONVERSION_ARTICLE,
        },
    )
    return replace(
        base, status='calculated', steps=steps, results=({'label': '월 최저임금', 'value': _shown(pay), 'unit': '원'},)
    )
