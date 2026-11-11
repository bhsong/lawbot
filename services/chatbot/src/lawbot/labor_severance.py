"""Calculate the statutory minimum severance or a labeled monthly-wage estimate."""

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from lawbot.calc_numbers import _MAX_SAFE_INTEGER, parse_amount, parse_count, parse_hours
from lawbot.labor_dates import advance_days, calendar_month_boundary, parse_labor_date, parse_service_period

if TYPE_CHECKING:
    from lawbot.calculators import Calculation, InputRow, Result, Step

FIELDS = {
    'monthly_wage': 'money',
    'service_period': 'duration',
    'hire_date': 'date',
    'retirement_date': 'date',
    'last_employed_date': 'date',
    'weekly_hours': 'hours',
    'service_qualifies': 'fact',
    'wage_total': 'money',
    'wage_days': 'count',
    'ordinary_daily_wage': 'money',
    'adjusted_daily_wage': 'money',
    'annual_bonus': 'money',
    'annual_unused_leave_pay': 'money',
    'excluded_wages': 'money',
    'excluded_days': 'count',
    'year': 'year',
}
SCHEMA = (
    '퇴직금. service_period(인정 계속근로기간 전체) 또는 hire_date와 '
    'retirement_date(첫 비재직일)/last_employed_date(마지막 재직일), weekly_hours(4주 평균 주 소정시간), '
    'service_qualifies(달력상 1년 이상임이 명시된 구간), wage_total(직전 3개월 임금 총액), '
    'wage_days(그 역일수), ordinary_daily_wage(1일 통상임금) 또는 adjusted_daily_wage(하한 반영 산정용 일급). '
    'annual_bonus/annual_unused_leave_pay(임금 총액에 별도 산입할 적격 연간 상여/미사용연차수당), '
    'excluded_wages/excluded_days(제외 임금/일수). 월급만 제시되면 monthly_wage.'
)

_QUALIFICATION = '근로자퇴직급여 보장법 제4조'
_SEVERANCE = '근로자퇴직급여 보장법 제8조'
_AVERAGE = '근로기준법 제2조'
_EXCLUSION = '근로기준법 시행령 제2조'
_LABELS = {
    'monthly_wage': '월급',
    'service_period': '계속근로기간',
    'hire_date': '입사일',
    'retirement_date': '첫 비재직일',
    'last_employed_date': '마지막 재직일',
    'weekly_hours': '4주 평균 주 소정근로시간',
    'service_qualifies': '달력상 1년 충족',
    'wage_total': '퇴직 직전 3개월 임금 총액',
    'wage_days': '퇴직 직전 3개월 역일수',
    'ordinary_daily_wage': '1일 통상임금',
    'adjusted_daily_wage': '통상임금 하한 반영 산정용 일급',
    'annual_bonus': '별도 산입 대상 연간 상여금',
    'annual_unused_leave_pay': '별도 산입 대상 연간 미사용 연차수당',
    'excluded_wages': '산정 제외 임금',
    'excluded_days': '산정 제외 일수',
}
_OPTIONAL = ('annual_bonus', 'annual_unused_leave_pay', 'excluded_wages', 'excluded_days')
_OUTSIDE = re.compile(
    r'퇴직\s*소득세|실수령|세후|중간\s*정산|퇴직\s*연금|(?:확정급여|확정기여)형|\b(?:DB|DC|IRP)\b', re.I
)
_VARIABLE_HOURS = re.compile(r'(?:소정\s*(?:근로\s*)?시간|근무\s*시간)[^.!?\n]{0,25}(?:변동|달라|바뀌|늘었|줄었|교대)')
_HOURS_RANGE = re.compile(r'\d+(?:\.\d+)?\s*(?:시간)?\s*(?:[~～∼–-]|또는)\s*\d+(?:\.\d+)?\s*시간')
_UNQUALIFIED_END = re.compile(r'(?:퇴직|퇴사)\s*일(?:자)?\s*(?:은|는|이|가|:)?\s*[0-9]')
_QUALIFIED_END = re.compile(r'첫\s*비재직일|마지막\s*(?:재직|근무)일|근로관계\s*종료\s*다음\s*날')


def question_issue(question: str, inputs: Mapping[str, str]) -> str | None:
    if _OUTSIDE.search(question):
        return '법정 세전 퇴직금제도의 산정 범위에 해당하는지 확인해 주세요.'
    if _VARIABLE_HOURS.search(question) or _HOURS_RANGE.search(question):
        return '4주 평균 주 소정근로시간과 인정 계속근로기간을 확인해 주세요.'
    if _UNQUALIFIED_END.search(question) and not _QUALIFIED_END.search(question):
        return '퇴직일이 마지막 재직일인지 첫 비재직일인지 알려 주세요.'
    if (source := inputs.get('wage_days')) and re.search(
        rf'(?:실제|소정)\s*(?:근무|근로)\s*일수\s*(?:은|는|이|가|:)?\s*{re.escape(source)}', question
    ):
        return '실제 근무일수가 아닌 평균임금 산정기간의 달력 일수를 알려 주세요.'
    for field, label in (
        ('annual_bonus', r'상여금'),
        ('annual_unused_leave_pay', r'(?:미사용\s*)?연차\s*수당'),
        ('excluded_days', r'(?:평균임금\s*산정\s*)?제외\s*(?:기간|일수)'),
    ):
        if not re.search(rf'{label}\s*(?:은|는|이|가|:)?\s*[0-9]', question):
            continue
        included = field != 'excluded_days' and re.search(
            rf'(?:포함된|포함한)\s*{label}|{label}[^.!?\n]{{0,25}}포함(?!하지\s*않|되지\s*않)', question
        )
        if (included and field in inputs) or (not included and field not in inputs):
            return f'{_LABELS[field]}의 포함 여부와 값을 확인해 주세요.'
    return None


def _inputs(inputs: Mapping[str, str], money: Mapping[str, int], days: Mapping[str, int]) -> tuple[InputRow, ...]:
    rows: list[InputRow] = []
    for field, label in _LABELS.items():
        source = inputs.get(field)
        if source is None and field not in _OPTIONAL:
            continue
        value: str | int = source if source is not None else 0
        if field in money:
            value = money[field]
        if field in days:
            value = days[field]
        row: InputRow = {'label': label, 'value': value, 'source': f'질문: {source}' if source else '미입력(0)'}
        if FIELDS[field] == 'count':
            row['unit'] = '일'
        rows.append(row)
    return tuple(rows)


def _read_dates(inputs: Mapping[str, str]) -> tuple[date | None, date | None, bool]:
    start = parse_labor_date(inputs['hire_date']) if 'hire_date' in inputs else None
    end = parse_labor_date(inputs['retirement_date']) if 'retirement_date' in inputs else None
    if 'last_employed_date' in inputs:
        from_last_day = advance_days(parse_labor_date(inputs['last_employed_date']), 1)
        if end and end != from_last_day:
            return start, end, False
        end = from_last_day
    return start, end, not (start and end and end <= start)


def _format_value(value: Fraction | int) -> int | str:
    return int(value) if value == int(value) else format(value, '.4f').rstrip('0').rstrip('.')


def _step(label: str, formula: str, value: Fraction | int, article: str, unit: str = '원') -> Step:
    return {'label': label, 'formula': formula, 'value': _format_value(value), 'unit': unit, 'article': article}


def _guidance(base: Calculation, message: str, reason: str = 'missing_required') -> Calculation:
    return replace(base, message=message, reason=reason)


def calculate(base: Calculation, inputs: Mapping[str, str], rules: Mapping[str, Any]) -> Calculation:
    money = {
        field: parse_amount(inputs[field]) for field, kind in FIELDS.items() if kind == 'money' and field in inputs
    }
    days = {field: parse_count(inputs[field], '일') for field in ('wage_days', 'excluded_days') if field in inputs}
    base = replace(base, inputs=(*base.inputs, *_inputs(inputs, money, days)))
    start, end, consistent_dates = _read_dates(inputs)
    if not consistent_dates:
        return _guidance(base, '입사일과 마지막 재직일 또는 첫 비재직일을 확인해 주세요.', 'ambiguous_input')

    lookback = int(rules['lookback_months']['value'])
    minimum_years = int(rules['minimum_service_years']['value'])
    minimum_hours = Fraction(rules['minimum_weekly_hours']['value'])
    year_days = int(rules['proration_days_per_year']['value'])
    pay_days = int(rules['pay_days_per_year']['value'])
    months_per_year = int(rules['estimate_months_per_year']['value'])
    monthly = money.get('monthly_wage')
    adjusted = money.get('adjusted_daily_wage')
    ordinary = money.get('ordinary_daily_wage')
    wage_total = money.get('wage_total')
    estimate = monthly is not None and wage_total is None and adjusted is None
    period = parse_service_period(inputs['service_period']) if 'service_period' in inputs else None
    hours = parse_hours(inputs['weekly_hours']) if 'weekly_hours' in inputs else None
    if hours is not None and hours > 168:
        raise ValueError('invalid weekly hours')

    qualifies = None
    if 'service_qualifies' in inputs:
        if not re.search(rf'(?<!\d){minimum_years}\s*년\s*이상', inputs['service_qualifies']) or re.search(
            r'아니|미만|불명|모르|않', inputs['service_qualifies']
        ):
            raise ValueError('ambiguous service qualification')
        qualifies = True

    steps: list[Step] = []
    if start is not None and end is not None:
        service_days = Fraction((end - start).days)
        if period is not None:
            years, months, extra_days = period
            period_end = advance_days(calendar_month_boundary(start, years * months_per_year + months), extra_days)
            if period_end != end:
                return _guidance(base, '날짜에서 산정한 재직일수와 제시된 계속근로기간이 다릅니다.', 'ambiguous_input')
        service_eligible = end >= calendar_month_boundary(start, minimum_years * months_per_year)
        service_formula = f'{end.isoformat()} − {start.isoformat()}'
    elif period is not None:
        years, months, extra_days = period
        if years or months:
            if not estimate:
                return _guidance(base, '입사·종료일 또는 인정 계속근로일수를 알려 주세요.')
            service_days = Fraction(year_days * (years * months_per_year + months), months_per_year) + extra_days
            service_formula = f'{year_days}일 × ({years}년 + {months}개월 ÷ {months_per_year}) + {extra_days}일 (추정)'
            service_eligible = years * months_per_year + months >= minimum_years * months_per_year or qualifies
        else:
            service_days = Fraction(extra_days)
            service_eligible = True if service_days > year_days * minimum_years else None
            if service_days < year_days * minimum_years:
                service_eligible = False
            service_formula = '질문에 명시된 인정 계속근로일수'
    else:
        return _guidance(base, '계속근로기간 또는 입사일과 종료일을 알려 주세요.')

    if qualifies and service_eligible is False:
        return _guidance(base, '계속근로기간과 달력상 1년 충족 여부를 확인해 주세요.', 'ambiguous_input')
    service_eligible = qualifies if service_eligible is None else service_eligible
    steps.append(_step('인정 계속근로일수', service_formula, service_days, _SEVERANCE, '일'))

    if service_eligible is False or (hours is not None and hours < minimum_hours):
        steps.append(_step('법정 최소 퇴직금', '계속근로기간 또는 주 소정근로시간 기준 미충족', 0, _QUALIFICATION))
        return replace(
            base,
            status='calculated',
            steps=tuple(steps),
            results=({'label': '법정 최소 퇴직금', 'value': 0},),
            message='제시된 근로기간 또는 주 소정근로시간은 법정 퇴직급여 요건을 충족하지 않습니다.',
        )
    if not estimate and (service_eligible is None or hours is None):
        return _guidance(base, '달력상 1년 이상 계속근로 여부와 4주 평균 주 소정근로시간을 알려 주세요.')

    has_excluded_days = 'excluded_days' in days
    has_excluded_wages = 'excluded_wages' in money
    if has_excluded_days != has_excluded_wages:
        return _guidance(base, '평균임금에서 제외할 기간과 그 기간의 임금을 함께 알려 주세요.', 'ambiguous_input')
    if estimate and has_excluded_days:
        return _guidance(base, '산정 제외 기간이 있으면 퇴직 직전 3개월 임금 총액을 알려 주세요.')
    if (
        adjusted is not None
        and wage_total is None
        and (has_excluded_days or money.get('annual_bonus') or money.get('annual_unused_leave_pay'))
    ):
        return _guidance(
            base, '산정용 일급에 제외 기간과 별도 산입 금액이 반영되었는지 확인해 주세요.', 'ambiguous_input'
        )

    if wage_total is None and monthly is not None and estimate:
        wage_total = monthly * lookback
        steps.append(_step('퇴직 직전 3개월 임금 총액 추정', f'{monthly:,}원 × {lookback}개월', wage_total, _AVERAGE))
    if wage_total is not None:
        wage_days = days.get('wage_days')
        if end is not None:
            calendar_days = (end - calendar_month_boundary(end, -lookback)).days
            if wage_days is not None and wage_days != calendar_days:
                return _guidance(base, '퇴직 직전 3개월의 달력 일수를 확인해 주세요.', 'ambiguous_input')
            wage_days = calendar_days
            day_formula = '첫 비재직일 이전 3개월의 달력 일수'
        elif wage_days is not None:
            day_formula = '질문에 명시된 직전 3개월 역일수'
        elif estimate:
            estimate_days = int(rules['estimate_days_per_month']['value'])
            wage_days = lookback * estimate_days
            day_formula = f'{lookback}개월 × {estimate_days}일 (추정)'
        else:
            return _guidance(base, '퇴직 직전 3개월의 달력 일수를 알려 주세요.')
        steps.append(_step('평균임금 산정 전 역일수', day_formula, wage_days, _AVERAGE, '일'))
        excluded_days = days.get('excluded_days', 0)
        excluded_wages = money.get('excluded_wages', 0)
        if wage_days <= excluded_days or excluded_wages > wage_total or (not excluded_days and excluded_wages):
            return _guidance(base, '평균임금 산정에 남는 기간과 임금을 확인해 주세요.', 'ambiguous_input')
        if excluded_days:
            steps.append(
                _step(
                    '제외 후 산정일수',
                    f'{wage_days}일 − {excluded_days}일',
                    wage_days - excluded_days,
                    _EXCLUSION,
                    '일',
                )
            )
            steps.append(
                _step(
                    '제외 후 임금 총액',
                    f'{wage_total:,}원 − {excluded_wages:,}원',
                    wage_total - excluded_wages,
                    _EXCLUSION,
                )
            )
        bonus = money.get('annual_bonus', 0) + money.get('annual_unused_leave_pay', 0)
        bonus_months = int(rules['bonus_months_per_year']['value'])
        allocation = Fraction(bonus * lookback, bonus_months)
        if allocation:
            steps.append(
                _step('별도 상여·연차수당 산입액', f'{bonus:,}원 × {lookback} ÷ {bonus_months}', allocation, _AVERAGE)
            )
        average = (wage_total - excluded_wages + allocation) / (wage_days - excluded_days)
        average_formula = '제외 후 임금과 별도 산입액의 합 ÷ 제외 후 역일수'
        steps.append(_step('1일 평균임금', average_formula, average, _AVERAGE))
    else:
        average = None

    if adjusted is not None:
        if (ordinary is not None and adjusted < ordinary) or (average is not None and adjusted < average):
            return _guidance(base, '산정용 일급과 평균임금·통상임금 하한을 다시 확인해 주세요.', 'ambiguous_input')
        daily = Fraction(adjusted)
        daily_formula = '질문에 명시된 통상임금 하한 반영 산정용 일급'
    elif average is None:
        return _guidance(base, '퇴직 직전 3개월 적격 임금 총액과 역일수를 알려 주세요.')
    elif ordinary is None:
        if not estimate:
            return _guidance(base, '퇴직일 기준 1일 통상임금을 알려 주세요.')
        daily = average
        daily_formula = '추정 1일 평균임금 (통상임금 하한 미확인)'
    else:
        daily = max(average, Fraction(ordinary))
        daily_formula = '1일 평균임금과 1일 통상임금 중 큰 금액'

    steps.append(_step('퇴직금 산정용 일급', daily_formula, daily, _AVERAGE))
    amount = daily * pay_days * service_days / year_days
    if amount > _MAX_SAFE_INTEGER:
        raise ValueError('amount exceeds the supported precision')
    label = (
        '평균임금 기준 퇴직금 추정'
        if estimate and ordinary is None
        else '퇴직금 추정'
        if estimate
        else '법정 최소 퇴직금'
    )
    formula = f'산정용 일급 × {pay_days}일 × 인정 계속근로일수 ÷ {year_days}일'
    shown = _format_value(amount)
    result: Result = {'label': label, 'value': shown}
    if isinstance(shown, str):
        formula += ' (소수 넷째 자리까지 표시)'
        result['unit'] = '원'
    steps.append(_step(label, formula, amount, _SEVERANCE))
    message = (
        '최근 3개월 월급이 일정하다고 보고 계산한 추정입니다. 실제 역일수, 계속근로·주 소정근로시간 요건과 '
        '통상임금 하한이 결과를 바꿀 수 있습니다.'
        if estimate
        else None
    )
    return replace(base, status='calculated', steps=tuple(steps), results=(result,), message=message)
