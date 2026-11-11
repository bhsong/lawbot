"""Calculate statutory annual-leave accrual in days for ordinary work schedules."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from lawbot.calc_numbers import parse_count, parse_hours, parse_quantity
from lawbot.labor_dates import (
    advance_days,
    calendar_month_boundary,
    completed_calendar_months,
    parse_labor_date,
    parse_service_period,
)

if TYPE_CHECKING:
    from lawbot.calculators import Calculation, InputRow, Step

FIELDS = {
    'workplace_size': 'count',
    'weekly_hours': 'hours',
    'service_period': 'duration',
    'hire_date': 'date',
    'as_of_date': 'date',
    'last_employed_date': 'date',
    'employed_at_accrual': 'fact',
    'attendance_rate': 'percent',
    'full_attendance_months': 'count',
    'include_first_year': 'fact',
    'year': 'year',
}
SCHEMA = (
    '입사일 기준 연차 발생일수. workplace_size(상시 근로자수), weekly_hours(4주 평균 주 소정시간), '
    'service_period(근속기간 전체) 또는 hire_date와 as_of_date(재직 기준일)/last_employed_date(마지막 재직일), '
    'employed_at_accrual(발생일 재직 여부가 명시된 구간), attendance_rate(해당 1년 출근율), '
    'full_attendance_months(개근하고 다음날에도 재직한 달의 수), include_first_year(최초 1년 월단위 발생분도 합산 요청).'
)

_ANNUAL = '근로기준법 제60조'
_LABEL = '연차유급휴가 일수'
_LABELS = {
    'workplace_size': '상시 근로자수',
    'weekly_hours': '4주 평균 주 소정근로시간',
    'service_period': '계속근로기간',
    'hire_date': '입사일',
    'as_of_date': '재직 기준일',
    'last_employed_date': '마지막 재직일',
    'employed_at_accrual': '연차 발생일 재직 여부',
    'attendance_rate': '해당 1년 출근율',
    'full_attendance_months': '다음 날까지 재직한 개근월 수',
    'include_first_year': '첫해 월단위 연차 합산',
}
_UNSUPPORTED = re.compile(
    r'회계\s*연도|단시간\s*(?:근로|근무)|시간제|파트타임|교대근무|근로시간\s*단축|'
    r'잔여\s*(?:연차|일수)|남은\s*연차|연차\s*(?:미사용\s*)?수당|사용\s*촉진|연차\s*대체'
)
_AFFIRMATIVE = re.compile(r'(?:연차)?(?:발생일|다음날)(?:에|에도|도)?(?:계속)?(?:재직|근로관계존속)(?:중|합니다|했다)?')
_NEGATIVE = re.compile(
    r'(?:연차)?(?:발생일|다음날)(?:(?:전에|전)(?:근로관계)?(?:종료|퇴사)|(?:에|에도|도)?(?:미재직|재직하지않음))'
)
_INPUT_RANGE = re.compile(r'\d+(?:\.\d+)?\s*(?:%|시간|명)?\s*(?:[~～∼–-]|또는)\s*\d+(?:\.\d+)?\s*(?:%|시간|명)')


def question_issue(question: str, inputs: Mapping[str, str]) -> str | None:
    if _UNSUPPORTED.search(question):
        return '입사일 기준 통상근로자의 법정 연차 발생 일수에 필요한 조건을 확인해 주세요.'
    if re.search(r'(?:상시\s*(?:근로자|인원)|소정\s*근로시간)[^.!?\n]{0,24}(?:변동|달라|바뀌)', question):
        return '해당 기간에 적용되는 상시 근로자수와 주 소정근로시간을 확인해 주세요.'
    if _INPUT_RANGE.search(question) or re.search(
        r'출근율\s*(?:은|이|:)?\s*(?:약|대략|[\d.]+\s*%\s*(?:정도|가량))', question
    ):
        return '상시 근로자수, 주 소정근로시간과 출근율을 확인해 주세요.'
    if 'full_attendance_months' in inputs and not re.search(r'개근|빠짐없이\s*출근|결근\s*없이', question):
        return '근속기간 중 개근하고 그 다음 날에도 재직한 월 수를 알려 주세요.'
    if re.search(r'누적|첫해[^.!?\n]{0,20}(?:합산|더해|포함)', question) and 'include_first_year' not in inputs:
        return '발생일별 연차와 합산할 기간을 확인해 주세요.'
    return None


def _compact(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def _employed_fact(inputs: Mapping[str, str]) -> bool | None:
    if (source := inputs.get('employed_at_accrual')) is None:
        return None
    source = _compact(source)
    if _AFFIRMATIVE.fullmatch(source):
        return True
    if _NEGATIVE.fullmatch(source):
        return False
    raise ValueError('uncertain employment on accrual day')


def _guidance(base: Calculation, message: str, reason: str = 'missing_required') -> Calculation:
    return replace(base, message=message, reason=reason)


def _step(label: str, formula: str, value: int, article: str = _ANNUAL, unit: str = '일') -> Step:
    return {'label': label, 'formula': formula, 'value': value, 'unit': unit, 'article': article}


def _done(base: Calculation, steps: tuple[Step, ...], days: int, message: str | None = None) -> Calculation:
    return replace(
        base,
        status='calculated',
        steps=steps,
        results=({'label': _LABEL, 'value': days, 'unit': '일'},),
        message=message,
    )


def calculate(base: Calculation, inputs: Mapping[str, str], rules: Mapping[str, Any]) -> Calculation:
    workers = parse_count(inputs['workplace_size'], '명') if 'workplace_size' in inputs else None
    hours = parse_hours(inputs['weekly_hours']) if 'weekly_hours' in inputs else None
    full_months = parse_count(inputs['full_attendance_months'], '개월') if 'full_attendance_months' in inputs else None
    attendance = parse_quantity(inputs['attendance_rate'], '%') if 'attendance_rate' in inputs else None
    if (hours is not None and hours > 168) or (attendance is not None and not 0 <= attendance <= 100):
        raise ValueError('invalid annual leave input')
    count_rows = {'workplace_size': (workers, '명'), 'full_attendance_months': (full_months, '개월')}
    rows: list[InputRow] = []
    for field, label in _LABELS.items():
        if (source := inputs.get(field)) is None:
            continue
        row: InputRow = {'label': label, 'value': source, 'source': f'질문: {source}'}
        if field in count_rows:
            count, unit = count_rows[field]
            assert count is not None
            row['value'] = count
            row['unit'] = unit
        rows.append(row)
    base = replace(base, inputs=(*base.inputs, *rows))

    minimum_workers = rules['minimum_workers']
    minimum_hours = rules['minimum_weekly_hours']
    if workers is not None and workers < int(minimum_workers['value']):
        step = _step(_LABEL, f'상시 근로자 {minimum_workers["value"]}명 기준 미충족', 0, minimum_workers['article'])
        return _done(base, (step,), 0, '근로기준법 제60조의 법정 연차 적용 기준을 충족하지 않습니다.')
    if hours is not None and hours < int(minimum_hours['value']):
        step = _step(
            _LABEL, f'4주 평균 주 소정근로시간 {minimum_hours["value"]}시간 기준 미충족', 0, minimum_hours['article']
        )
        return _done(base, (step,), 0, '근로기준법 제60조의 법정 연차 적용 기준을 충족하지 않습니다.')
    if workers is None or hours is None:
        return _guidance(base, '상시 근로자수와 4주 평균 주 소정근로시간을 알려 주세요.')

    year_months = int(rules['annual_cycle_months']['value'])
    start = parse_labor_date(inputs['hire_date']) if 'hire_date' in inputs else None
    active_on = parse_labor_date(inputs['as_of_date']) if 'as_of_date' in inputs else None
    last = parse_labor_date(inputs['last_employed_date']) if 'last_employed_date' in inputs else None
    end = active_on or last
    fact = _employed_fact(inputs)
    if (active_on is not None and last is not None and active_on > last) or (
        start is not None and end is not None and start > end
    ):
        return _guidance(base, '입사일과 재직 기준일 또는 마지막 재직일을 확인해 주세요.', 'ambiguous_input')
    period = parse_service_period(inputs['service_period']) if 'service_period' in inputs else None
    if start is not None and end is not None:
        months = completed_calendar_months(start, end)
        if period is not None:
            years, extra_months, extra_days = period
            expected_end = advance_days(calendar_month_boundary(start, years * year_months + extra_months), extra_days)
            if expected_end != (advance_days(end, 1) if active_on is None else end):
                return _guidance(base, '입사·종료일과 기재된 근속기간을 확인해 주세요.', 'ambiguous_input')
        ended_at_boundary = (
            active_on is None
            and (months + 1) % year_months == 0
            and advance_days(end, 1) == calendar_month_boundary(start, months + 1)
        )
        completed_years = (months + int(ended_at_boundary)) // year_months
        employed_at_accrual = not ended_at_boundary
        if fact is not None and fact != employed_at_accrual:
            return _guidance(base, '날짜와 연차 발생일 재직 여부가 일치하는지 확인해 주세요.', 'ambiguous_input')
    elif period is not None:
        years, extra_months, extra_days = period
        if (extra_days and not (years or extra_months)) or extra_days >= 28:
            return _guidance(base, '개월·연수의 경계를 확인할 수 있도록 입사일과 기준일을 알려 주세요.')
        months = years * year_months + extra_months
        completed_years = months // year_months
        employed_at_accrual = fact
        if months % year_months or extra_days:
            if fact is False and completed_years:
                return _guidance(base, '근속기간과 연차 발생일 재직 여부를 확인해 주세요.', 'ambiguous_input')
            employed_at_accrual = True
    else:
        return _guidance(base, '입사일과 기준일 또는 완료한 근속기간을 알려 주세요.')

    threshold = int(rules['qualifying_attendance_percent']['value'])
    first_max = int(rules['first_year_max_months']['value'])
    annual_completed = completed_years > 0
    first_year = not annual_completed or (completed_years == 1 and employed_at_accrual is False)
    low_attendance = attendance is not None and attendance < threshold
    include_first = 'include_first_year' in inputs
    if include_first:
        requested = _compact(inputs['include_first_year'])
        if not re.search(r'(?:첫해|첫1년|최초1년).*?(?:합산|포함|더해)', requested) or completed_years != 1:
            return _guidance(base, '합산할 최초 1년 월단위 연차와 1주년 발생분을 확인해 주세요.', 'ambiguous_input')
    if not first_year and attendance is None:
        return _guidance(base, '연차 산정 대상 1년의 출근율을 알려 주세요.')
    if not first_year and not low_attendance and employed_at_accrual is None:
        return _guidance(base, '해당 1년의 근로를 마친 다음 날에도 재직했는지 알려 주세요.')
    if first_year or low_attendance or include_first:
        if full_months is None:
            return _guidance(base, '개근하고 그 다음 날까지 재직한 월 수를 알려 주세요.')
        if first_year or include_first:
            bound = min(first_max, months)
        else:
            bound = year_months - int(employed_at_accrual is False)
        if full_months > bound:
            return _guidance(base, '근속기간과 개근 후 다음 날까지 재직한 월 수를 확인해 주세요.', 'ambiguous_input')
        days_per_month = int(rules['monthly_days']['value'])
        accrued_monthly = full_months * days_per_month
        monthly_step = _step('월단위 발생 연차', f'적격 개근 {full_months}개월 × {days_per_month}일', accrued_monthly)
        if first_year or low_attendance:
            if include_first and low_attendance and not first_year:
                return _guidance(base, '첫해와 출근율 80% 미만 기간의 개근월을 각각 확인해 주세요.', 'ambiguous_input')
            return _done(base, (monthly_step,), accrued_monthly)
    else:
        accrued_monthly = 0
        monthly_step = None

    if employed_at_accrual is False:
        step = _step(_LABEL, '산정기간을 마친 다음 날 근로관계 종료', 0)
        return _done(base, (step,), 0)

    regular = int(rules['regular_days']['value'])
    extra_after = int(rules['extra_after_years']['value'])
    extra_every = int(rules['extra_every_years']['value'])
    limit = int(rules['maximum_days']['value'])
    extra = max(0, (completed_years - extra_after) // extra_every)
    annual_days = min(limit, regular + extra)
    formula = f'min({limit}일, {regular}일 + ⌊({completed_years}년 − {extra_after}년) ÷ {extra_every}년⌋일)'
    annual_step = _step('해당 1년 발생 연차', formula, annual_days)
    if monthly_step is None:
        return _done(base, (annual_step,), annual_days)
    combined = annual_days + accrued_monthly
    total_step = _step('최초 1년 월단위 및 1주년 발생 합계', f'{accrued_monthly}일 + {annual_days}일', combined)
    return _done(
        base,
        (monthly_step, annual_step, total_step),
        combined,
        '최초 1년 월단위 발생분과 1주년 발생분의 합계입니다. 현재 사용 가능한 일수는 별도로 확인해 주세요.',
    )
