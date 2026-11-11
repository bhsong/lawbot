"""Statutory overtime, night and holiday premium pay."""

import re
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from math import ceil
from typing import TYPE_CHECKING, Any

from lawbot.calc_numbers import can_assign_spans, complete_labor_spans, parse_amount, parse_count, parse_hours

if TYPE_CHECKING:
    from lawbot.calculators import Calculation, InputRow, Result, Step


FIELDS = {
    'staff_count': 'count',
    'ordinary_hourly_wage': 'money',
    'overtime_hours': 'hours',
    'night_hours': 'hours',
    'holiday_hours': 'hours',
    'holiday_within_hours': 'hours',
    'holiday_excess_hours': 'hours',
    'total_hours': 'hours',
    'include_base': 'fact',
    'applicability': 'fact',
}
SCHEMA = (
    '연장·야간·휴일 가산수당. staff_count 상시 근로자 수, ordinary_hourly_wage 통상시급. 휴게 제외 시간: '
    'overtime_hours 평일 연장, night_hours 야간(다른 유형과 중첩 가능), holiday_hours 단일 휴일의 실근로. '
    '복수 휴일은 당일 기준 8시간 이내분 holiday_within_hours와 초과분 holiday_excess_hours가 각각 명시된 경우만 복사. '
    'include_base 기본임금 포함 또는 가산분만 요청한 원문, total_hours 중복 없는 총 실근로, '
    'applicability 제56조 적용 또는 감시·단속 등 별도 적용 조건 원문.'
)

_HOURS_LABELS = {
    'overtime_hours': '평일 연장 실근로시간',
    'night_hours': '야간 실근로시간',
    'holiday_hours': '휴일 실근로시간',
    'holiday_within_hours': '휴일 기준시간 이내 실근로 합계',
    'holiday_excess_hours': '휴일 기준시간 초과 실근로 합계',
    'total_hours': '중복 없는 총 실근로시간',
}
_HOLIDAY_SPLIT = {'holiday_within_hours', 'holiday_excess_hours'}
_HOURS_PER_DAY = 24
_BASE_REQUEST = re.compile(
    r'기본\s*임금(?:을)?\s*(?:까지|포함(?!하지\s*(?:않|말))|합산)|총\s*(?:지급액|임금)|임금\s*총액'
)
_PREMIUM_ONLY = re.compile(r'(?:가산|추가)\s*(?:수당|분|액)\s*만(?!\s*아니)|기본\s*임금\s*(?:제외|빼고)')
_SPECIAL_SCOPE = re.compile(
    r'(?:제\s*56\s*조|근로시간|휴일)\s*(?:의\s*)?적용\s*제외'
    r'|감시\s*(?:[·ㆍ/]\s*|및\s*)?단속|감시적|단속적|관리\s*[·ㆍ/]?\s*감독\s*(?:업무|근로)'
    r'|(?:농업|임업|축산|양잠|수산|기밀)\s*(?:종사|근로|업무)|별도\s*가산\s*(?:제도|규정|적용)'
    r'|단시간[^.!?\n]{0,24}(?:소정(?:근로)?시간[^.!?\n]{0,12}초과|초과근로)'
)
_ORDINARY_SCOPE = re.compile(r'제\s*56\s*조(?:가|를|이)?\s*(?:전부\s*)?적용|통상\s*근로자|일반\s*근로자')
_SCOPE_NEGATION = re.compile(
    r'\s*(?:적)?\s*(?:(?:근로자?|업무|승인|대상|규정|제도)(?:이|가|을|은|는)?\s*)*(?:아니|아닙|아님|없)'
)
_STAFF_UNCERTAIN = re.compile(
    r'상시[^.!?\n]{0,24}(?:모르|불명확|미정|추정|가량|정도|사이)'
    r'|상시[^.!?\n]{0,16}(?:명|인)\s*(?:이상|이하|미만|초과|(?:인지\s*)?또는)'
    r'|상시[^.!?\n]{0,14}[0-9０-９]\s*[~～∼–-]\s*[0-9０-９]+(?:명|인)'
)
_MULTIPLE_HOLIDAYS = re.compile(
    r'휴일\s*(?:의\s*)?(?:이틀|사흘|나흘|여러\s*날|[두세네]\s*(?:날|번)|(?:[2-9]|[1-9][0-9]+)\s*(?:일|번))'
    r'|휴일\s*마다|휴일\s*(?:근로\s*)?(?:전체|합산|합계|총)|(?:이틀|사흘|나흘)\s*(?:의\s*)?휴일'
)
_HOLIDAY_OVERTIME = re.compile(r'휴일\s*(?:근로(?:의)?\s*)?연장|휴일[^.!?\n]{0,40}그\s*중\s*연장')
_BREAK_INCLUDED = re.compile(r'휴게(?:시간)?[^.!?\n]{0,15}(?:포함|안\s*뺀)')
_BREAK_EXCLUDED = re.compile(r'휴게(?:시간)?[^.!?\n]{0,8}(?:제외|빼고|(?<!안 )뺀)')
_UNCERTAIN_TIME = re.compile(r'(야간|연장|휴일)[^.!?\n]{0,24}(?:인지|는지)\s*(?:모르|몰라|불명확)')
_HOURS_TOKEN = r'(?:[0-9０-９]+(?:[.．][0-9０-９]+)?|[영공일이삼사오육칠팔구십백천]+)\s*(?:시간|분)'
_HOUR_EVIDENCE = {
    'overtime_hours': re.compile(rf'(?:연장|초과)\s*(?:실?근로|근무)?\s*(?:은|는|이|가|를|을|에|:)?\s*{_HOURS_TOKEN}'),
    'night_hours': re.compile(rf'야간\s*(?:실?근로|근무)?\s*(?:은|는|이|가|를|을|에|:)?\s*{_HOURS_TOKEN}'),
    'holiday_hours': re.compile(rf'휴일(?:(?!야간|연장)[^.!?\n,]){{0,22}}?{_HOURS_TOKEN}'),
}


def _special_scope(text: str) -> bool:
    return any(not _SCOPE_NEGATION.match(text, match.end()) for match in _SPECIAL_SCOPE.finditer(text))


def question_issue(question: str, inputs: Mapping[str, str]) -> str | None:
    """Find facts in the question that cannot safely be inferred from excerpts alone."""
    if _special_scope(question):
        return '별도 가산 기준이 적용되는 근로인지 확인해 주세요.'
    if _STAFF_UNCERTAIN.search(question):
        return '법정 가산 적용 여부를 판단할 상시 근로자 수를 확인해 주세요.'
    if _BREAK_INCLUDED.search(question) and not _BREAK_EXCLUDED.search(question):
        return '휴게시간을 뺀 유형별 실근로시간을 확인해 주세요.'
    if match := _UNCERTAIN_TIME.search(question):
        return f'{match[1]}에 해당하는 실근로시간을 확인해 주세요.'
    if _MULTIPLE_HOLIDAYS.search(question) and not _HOLIDAY_SPLIT <= inputs.keys():
        return '휴일별 실근로시간 또는 각 휴일의 기준시간 이내분과 초과분을 합산한 시간을 알려 주세요.'
    holiday_overtime = _HOLIDAY_OVERTIME.search(question) is not None
    if holiday_overtime and ('overtime_hours' in inputs or not {'holiday_hours', *_HOLIDAY_SPLIT} & inputs.keys()):
        return '휴일 근로에 포함된 연장시간과 별도의 평일 연장시간을 구분해 주세요.'
    if _BASE_REQUEST.search(question) and 'include_base' not in inputs and 'total_hours' not in inputs:
        return '기본임금을 포함하려면 중복 없는 총 실근로시간을 확인해 주세요.'
    if not can_assign_spans(
        [
            tuple(complete_labor_spans(question, source, 'hours'))
            for field, source in inputs.items()
            if field in _HOURS_LABELS
        ]
    ):
        return '연장·야간·휴일에 해당하는 시간을 각각 확인해 주세요.'
    for field, pattern in _HOUR_EVIDENCE.items():
        if field in inputs or (field == 'overtime_hours' and holiday_overtime):
            continue
        if field == 'holiday_hours' and _HOLIDAY_SPLIT & inputs.keys():
            continue
        if pattern.search(question):
            return f'{_HOURS_LABELS[field]}을 확인해 주세요.'
    return None


def _shown(value: Fraction | int) -> int | str:
    return int(value) if value.denominator == 1 else f'{value:.4f}'.rstrip('0').rstrip('.')


def _input(label: str, value: Fraction | int, source: str | None, unit: str) -> InputRow:
    return {
        'label': label,
        'value': _shown(value),
        'source': f'질문: {source}' if source else '미입력(0)',
        'unit': unit,
    }


def _step(label: str, formula: str, value: Fraction | int, unit: str, article: str) -> Step:
    if 10000 % value.denominator:
        formula += ' (소수점 이하 최대 네 자리 표시)'
    return {'label': label, 'formula': formula, 'value': _shown(value), 'unit': unit, 'article': article}


def _result(label: str, value: Fraction | int) -> Result:
    return {'label': label, 'value': _shown(value), 'unit': '원'}


def _base_requested(inputs: Mapping[str, str]) -> bool | None:
    if (flag := inputs.get('include_base')) is None:
        return 'total_hours' in inputs
    has_base, only_premium = _BASE_REQUEST.search(flag) is not None, _PREMIUM_ONLY.search(flag) is not None
    if has_base == only_premium or re.search(r'인지|모르|불명확|또는', flag):
        return None
    return has_base


def calculate(base: Calculation, inputs: Mapping[str, str], rules: Mapping[str, Any]) -> Calculation:
    """Calculate premium pay from confirmed ordinary wage and actual working hours."""
    if 'staff_count' not in inputs:
        return replace(base, reason='missing_required', message='사업장의 상시 근로자 수를 알려 주세요.')
    if 'ordinary_hourly_wage' not in inputs:
        return replace(base, reason='missing_required', message='가산수당 산정에 쓸 통상시급을 알려 주세요.')
    if not (_HOURS_LABELS.keys() - {'total_hours'}) & inputs.keys():
        return replace(
            base, reason='missing_required', message='평일 연장·야간·휴일 중 해당하는 실근로시간을 알려 주세요.'
        )
    if (scope := inputs.get('applicability')) is not None and (
        _special_scope(scope) or not _ORDINARY_SCOPE.search(scope) or re.search(r'인지|모르|불명확', scope)
    ):
        return replace(
            base, reason='ambiguous_input', message='근로기준법 제56조와 별도 가산 기준의 적용을 확인해 주세요.'
        )

    include_base = _base_requested(inputs)
    if include_base is None:
        return replace(base, reason='ambiguous_input', message='기본임금을 포함한 총액이 필요한지 확인해 주세요.')
    if include_base and 'total_hours' not in inputs:
        return replace(
            base, reason='missing_required', message='기본임금 산정에 필요한 중복 없는 총 실근로시간을 알려 주세요.'
        )

    staff_source = inputs['staff_count']
    staff = parse_count(staff_source, '인' if staff_source.strip().endswith('인') else '명')
    wage = parse_amount(inputs['ordinary_hourly_wage'])
    if staff <= 0 or wage <= 0:
        return replace(base, reason='ambiguous_input', message='상시 근로자 수와 통상시급을 확인해 주세요.')

    holiday_rule = rules['holiday']
    threshold = holiday_rule['threshold_hours']
    split_fields = _HOLIDAY_SPLIT & inputs.keys()
    if split_fields and 'holiday_hours' in inputs:
        return replace(base, reason='ambiguous_input', message='단일 휴일과 여러 휴일의 근로시간을 구분해 주세요.')
    if split_fields and split_fields != _HOLIDAY_SPLIT:
        return replace(base, reason='missing_required', message='휴일의 기준시간 이내분과 초과분을 모두 알려 주세요.')

    hours = {field: parse_hours(inputs[field]) if field in inputs else Fraction(0) for field in _HOURS_LABELS}
    overtime, night, holiday = (hours[field] for field in ('overtime_hours', 'night_hours', 'holiday_hours'))
    if holiday > _HOURS_PER_DAY:
        return replace(base, reason='ambiguous_input', message='휴일별 실제 근로시간을 확인해 주세요.')
    if split_fields:
        within, excess = hours['holiday_within_hours'], hours['holiday_excess_hours']
        if within < ceil(excess / (_HOURS_PER_DAY - threshold)) * threshold:
            return replace(base, reason='ambiguous_input', message='휴일의 기준시간 이내분과 초과분을 확인해 주세요.')
    else:
        within, excess = min(holiday, threshold), max(holiday - threshold, Fraction(0))
    total = hours['total_hours']
    if 'total_hours' in inputs and total < max(overtime + within + excess, night):
        return replace(
            base, reason='ambiguous_input', message='유형별 시간과 중복 없는 총 실근로시간이 맞는지 확인해 주세요.'
        )

    rows = [
        _input('상시 근로자 수', staff, staff_source, '명'),
        _input('통상시급', wage, inputs['ordinary_hourly_wage'], '원'),
    ]
    if scope is not None:
        rows.append({'label': '가산 적용 조건', 'value': scope, 'source': f'질문: {scope}'})
    if (flag := inputs.get('include_base')) is not None:
        rows.append({'label': '기본임금 포함 여부', 'value': flag, 'source': f'질문: {flag}'})
    hour_fields = ['overtime_hours', 'night_hours']
    hour_fields += ['holiday_within_hours', 'holiday_excess_hours'] if split_fields else ['holiday_hours']
    if 'total_hours' in inputs:
        hour_fields.append('total_hours')
    rows.extend(_input(_HOURS_LABELS[field], hours[field], inputs.get(field), '시간') for field in hour_fields)

    coverage = rules['coverage']
    covered = staff >= coverage['minimum_staff']
    steps: list[Step] = [
        {
            'label': '제56조 가산 적용',
            'formula': f'상시 근로자 {coverage["minimum_staff"]}명 이상 여부',
            'value': '적용' if covered else '미적용',
            'article': coverage['article'],
        },
    ]
    components = (
        ('평일 연장 가산', overtime, rules['overtime']['rate'], rules['overtime']['article']),
        ('야간 가산', night, rules['night']['rate'], rules['night']['article']),
        (f'휴일 {threshold}시간 이내 가산', within, holiday_rule['within_rate'], holiday_rule['article']),
        (f'휴일 {threshold}시간 초과 가산', excess, holiday_rule['above_rate'], holiday_rule['article']),
    )
    premium = Fraction(0)
    if covered:
        for label, duration, rate_source, article in components:
            rate = Fraction(rate_source)
            part = wage * duration * rate
            steps.append(_step(f'{label} 적용 시간', '휴게 제외 실근로시간', duration, '시간', article))
            steps.append(
                _step(label, f'{wage:,}원 × {_shown(duration)}시간 × {_shown(rate * 100)}%', part, '원', article)
            )
            premium += part
    premium_formula = '평일 연장·야간·휴일 가산의 합' if covered else '상시 근로자 수 기준에 따라 제56조 가산 미적용'
    steps.append(_step('법정 가산수당', premium_formula, premium, '원', holiday_rule['article']))
    results = [_result('법정 가산수당', premium)]
    if include_base:
        wage_only = wage * total
        base_article = rules['base_wage']['article']
        steps.append(_step('기본임금', f'{wage:,}원 × {_shown(total)}시간', wage_only, '원', base_article))
        steps.append(_step('기본임금 포함 합계', '기본임금 + 법정 가산수당', wage_only + premium, '원', base_article))
        results += [_result('기본임금', wage_only), _result('기본임금 포함 합계', wage_only + premium)]
    return replace(base, status='calculated', inputs=(*base.inputs, *rows), steps=tuple(steps), results=tuple(results))
