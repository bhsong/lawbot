"""Calculate statutory pay for one eligible weekly holiday."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from typing import TYPE_CHECKING

from lawbot.calc_numbers import parse_amount, parse_count, parse_hours

if TYPE_CHECKING:
    from lawbot.calculators import Calculation, InputRow, Step

FIELDS = {
    'hourly_wage': 'money',
    'weekly_scheduled_hours': 'hours',
    'four_week_scheduled_hours': 'hours',
    'paid_holiday_hours': 'hours',
    'reference_days_per_week': 'count',
    'four_week_reference_days': 'count',
    'attendance': 'fact',
    'employment': 'fact',
}
SCHEMA = (
    '주휴수당: hourly_wage(통상시급), weekly_scheduled_hours(4주 또는 4주 미만 해당 기간의 주 평균·고정 소정근로시간), '
    'four_week_scheduled_hours(4주간 본인 소정근로시간 합계), paid_holiday_hours(유급주휴시간), '
    'reference_days_per_week(비교 통상근로자의 고정 주 소정근로일수), '
    'four_week_reference_days(동일한 4주간 비교 통상근로자의 소정근로일수 합계), '
    'attendance(해당 주 개근·결근 사실 전체), employment(해당 주 7일 근로관계 유지·종료 사실 전체). '
    '실근로시간이나 본인의 출근일수를 소정시간이나 비교 근로자의 일수로 쓰지 않는다.'
)

_ARTICLE_HOLIDAY = '근로기준법 제55조'
_ARTICLE_ATTENDANCE = '근로기준법 시행령 제30조'
_ARTICLE_PART_TIME = '근로기준법 시행령 제9조'
_ARTICLE_WAGE = '근로기준법 시행령 제6조'
_LABELS = {
    'hourly_wage': '통상시급',
    'weekly_scheduled_hours': '4주 평균 주 소정근로시간',
    'four_week_scheduled_hours': '4주간 소정근로시간 합계',
    'paid_holiday_hours': '유급주휴시간',
    'reference_days_per_week': '비교 통상근로자의 주 소정근로일수',
    'four_week_reference_days': '같은 4주간 비교 통상근로자의 소정근로일수',
    'attendance': '해당 주 소정근로일 개근 여부',
    'employment': '해당 주 근로관계 존속 여부',
}
_UNCERTAIN = re.compile(r'인지|는지|여부|모르|불명확|미정|또는|혹은')
_NO_ABSENCE = re.compile(r'결근(?:하지않|이?없)')
_ABSENCE = re.compile(r'개근(?:하지않|못|이?아니)|미개근|결근')
_ATTENDED = re.compile(r'개근|소정근로일(?:을|에)?(?:모두|전부)출근')
_FULL_WEEK = re.compile(r'(?<!\d)7일|일주일|한주(?:간|동안)')
_EARLY_END = re.compile(r'되기전|되기이전|미만|채우지못|유지(?:하지않|되지않|못)|유지안|미유지')
_RELATION = re.compile(r'근로관계|고용관계|재직|근로계약')
_KEPT = re.compile(r'유지|존속|계속|재직|끝까지')
_HOUR_TEXT = r'\d+(?:\.\d+)?\s*시간(?:\s*\d+(?:\.\d+)?\s*분)?'
_HOUR_EVIDENCE = {
    'paid_holiday_hours': re.compile(rf'(?:유급\s*)?주휴(?:\s*시간)?\s*(?:은|는|이|:)?\s*({_HOUR_TEXT})'),
    'weekly_scheduled_hours': re.compile(
        rf'주\s*(?:평균\s*)?소정(?:\s*근로)?(?:\s*시간)?\s*(?:은|는|이|:)?\s*({_HOUR_TEXT})'
    ),
}


def _normalized(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def _attendance(source: str) -> bool | None:
    text = _normalized(source)
    if _UNCERTAIN.search(text):
        return None
    if _NO_ABSENCE.search(text):
        return True
    if _ABSENCE.search(text):
        return False
    return True if _ATTENDED.search(text) else None


def _employment(source: str) -> bool | None:
    text = _normalized(source)
    if _UNCERTAIN.search(text) or not _FULL_WEEK.search(text) or not _RELATION.search(text):
        return None
    if _EARLY_END.search(text):
        return False
    return True if _KEPT.search(text) else None


def question_issue(question: str, inputs: Mapping[str, str]) -> str | None:
    """Reject excerpts that omit material qualifications to an extracted fact."""
    clauses = re.split(r'[!?\n,;]|(?<!\d)\.|\.(?!\d)', question)
    for field, parse in (('attendance', _attendance), ('employment', _employment)):
        if source := inputs.get(field):
            parsed = parse(source)
            for clause in clauses:
                in_context = parse(clause)
                if (source in clause or in_context is not None) and parsed != in_context:
                    return f'{_LABELS[field]}를 확인해 주세요.'
    normalized_question = unicodedata.normalize('NFKC', question)
    for field, evidence in _HOUR_EVIDENCE.items():
        stated = {_normalized(match[1]) for match in evidence.finditer(normalized_question)}
        if stated and stated != {_normalized(inputs.get(field, ''))}:
            return f'질문에 적힌 {_LABELS[field]}을 확인해 주세요.'
    return None


def _shown(value: Fraction | int) -> int | str:
    return int(value) if value == int(value) else f'{value:.4f}'.rstrip('0').rstrip('.')


def calculate(base: Calculation, inputs: Mapping[str, str], rules: dict) -> Calculation:
    rows: list[InputRow] = []
    hours, days, facts = {}, {}, {}
    hourly_wage = None
    try:
        for key, source in inputs.items():
            field_type = FIELDS.get(key)
            if field_type == 'hours':
                hours[key] = value = parse_hours(source)
                rows.append(
                    {'label': _LABELS[key], 'value': _shown(value), 'unit': '시간', 'source': f'질문: {source}'}
                )
            elif field_type == 'count':
                days[key] = value = parse_count(source, '일')
                rows.append({'label': _LABELS[key], 'value': value, 'unit': '일', 'source': f'질문: {source}'})
            elif key == 'hourly_wage':
                hourly_wage = parse_amount(source)
                rows.append({'label': _LABELS[key], 'value': hourly_wage, 'unit': '원', 'source': f'질문: {source}'})
            elif field_type == 'fact':
                facts[key] = (_attendance if key == 'attendance' else _employment)(source)
                rows.append({'label': _LABELS[key], 'value': source, 'source': f'질문: {source}'})
    except ValueError:
        return replace(
            base, reason='ambiguous_input', message='통상시급과 소정근로시간·일수를 하나의 값으로 알려 주세요.'
        )
    base = replace(base, inputs=(*base.inputs, *rows))

    def guidance(message: str, reason: str = 'missing_required') -> Calculation:
        return replace(base, reason=reason, message=message)

    assessment_weeks = rules['assessment_weeks']['value']
    minimum_hours = rules['minimum_weekly_hours']['value']
    week_days = rules['qualifying_week_days']['value']
    weekly_hours = hours.get('weekly_scheduled_hours')
    period_hours = hours.get('four_week_scheduled_hours')
    if period_hours is not None:
        average = period_hours / assessment_weeks
        if weekly_hours is not None and weekly_hours != average:
            return guidance('같은 기간의 주 평균 소정근로시간과 소정근로시간 합계를 확인해 주세요.', 'ambiguous_input')
        weekly_hours = average

    steps: list[Step] = []
    if weekly_hours is not None:
        steps.append(
            {
                'label': '주 평균 소정근로시간',
                'formula': f'{assessment_weeks}주 소정근로시간 합계 ÷ {assessment_weeks}'
                if period_hours is not None
                else '주 평균 소정근로시간 입력값',
                'value': _shown(weekly_hours),
                'unit': '시간',
                'article': rules['minimum_weekly_hours']['article'],
            }
        )
    disqualifiers = (
        (
            weekly_hours is not None and weekly_hours < minimum_hours,
            f'주 평균 {minimum_hours}시간 미만',
            rules['minimum_weekly_hours']['article'],
        ),
        (facts.get('attendance') is False, '해당 주 소정근로일에 결근', _ARTICLE_ATTENDANCE),
        (
            facts.get('employment') is False,
            f'해당 주 {week_days}일 동안 근로관계 미유지',
            rules['qualifying_week_days']['article'],
        ),
    )
    for disqualified, formula, article in disqualifiers:
        if disqualified:
            steps.append({'label': '유급주휴시간', 'formula': formula, 'value': 0, 'unit': '시간', 'article': article})
            return replace(
                base,
                status='calculated',
                steps=tuple(steps),
                results=({'label': '주휴수당', 'value': 0, 'unit': '원'},),
            )

    missing = []
    if weekly_hours is None:
        missing.append('4주 평균 주 소정근로시간')
    missing.extend(_LABELS[key] for key in ('attendance', 'employment') if facts.get(key) is None)
    if hourly_wage is None:
        missing.append('통상시급')
    if missing:
        reason = (
            'ambiguous_input'
            if any(key in facts and facts[key] is None for key in ('attendance', 'employment'))
            else 'missing_required'
        )
        return guidance(f'{", ".join(missing)}를 알려 주시면 계산할 수 있습니다.', reason)
    assert weekly_hours is not None and hourly_wage is not None
    if hourly_wage <= 0:
        return guidance('통상시급을 확인해 주세요.', 'ambiguous_input')

    weekly_days = days.get('reference_days_per_week')
    period_days = days.get('four_week_reference_days')
    if weekly_days is not None and not 0 < weekly_days <= week_days:
        return guidance('비교 통상근로자의 주 소정근로일수를 확인해 주세요.', 'ambiguous_input')
    if period_days is not None and not 0 < period_days <= week_days * assessment_weeks:
        return guidance('비교 통상근로자의 소정근로일수 합계를 확인해 주세요.', 'ambiguous_input')
    if weekly_days is not None and period_days is not None and weekly_days * assessment_weeks != period_days:
        return guidance('같은 기간의 비교 통상근로자 소정근로일수를 확인해 주세요.', 'ambiguous_input')
    if period_days is None and weekly_days is not None:
        period_days = weekly_days * assessment_weeks
    proportional = weekly_hours * assessment_weeks / period_days if period_days is not None else None
    paid_hours = hours.get('paid_holiday_hours')
    if paid_hours is not None and proportional is not None and paid_hours != proportional:
        return guidance('유급주휴시간과 비교 통상근로자의 소정근로일수를 확인해 주세요.', 'ambiguous_input')
    if paid_hours is None:
        paid_hours = proportional
    if paid_hours is None:
        return guidance('유급주휴시간 또는 비교 통상근로자의 소정근로일수를 알려 주시면 계산할 수 있습니다.')
    if not 0 < paid_hours <= weekly_hours:
        return guidance('유급주휴시간을 확인해 주세요.', 'ambiguous_input')

    steps.extend(
        (
            {
                'label': '주휴 적용 요건',
                'formula': f'소정근로일 개근, 해당 주 {week_days}일 근로관계 유지',
                'value': '충족',
                'article': _ARTICLE_ATTENDANCE,
            },
            {
                'label': '유급주휴시간',
                'formula': f'{assessment_weeks}주 본인 소정근로시간 합계 ÷ 같은 기간 비교 통상근로자 소정근로일수'
                if proportional is not None
                else '유급주휴시간 입력값',
                'value': _shown(paid_hours),
                'unit': '시간',
                'article': _ARTICLE_PART_TIME if proportional is not None else _ARTICLE_HOLIDAY,
            },
        )
    )
    pay = paid_hours * hourly_wage
    display = ' (소수점 4자리 표시)' if pay.denominator != 1 else ''
    steps.append(
        {
            'label': '주휴수당',
            'formula': f'유급주휴시간 × 통상시급{display}',
            'value': _shown(pay),
            'unit': '원',
            'article': _ARTICLE_WAGE,
        }
    )
    return replace(
        base,
        status='calculated',
        steps=tuple(steps),
        results=({'label': '주휴수당', 'value': _shown(pay), 'unit': '원'},),
    )
