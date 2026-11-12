import json
from pathlib import Path

import pytest

from lawbot.calc_extract import FIELD_DESCRIPTIONS
from lawbot.calc_router import route_calculation
from lawbot.calculators import Calculation
from lawbot.labor_language import premium_pay_scope
from lawbot.labor_premium import FIELDS, calculate, question_issue

RULES = {
    'coverage': {'article': '근로기준법 제11조', 'minimum_staff': 5},
    'base_wage': {'article': '근로기준법 시행령 제6조'},
    'overtime': {'article': '근로기준법 제56조', 'rate': '0.5'},
    'night': {'article': '근로기준법 제56조', 'rate': '0.5'},
    'holiday': {'article': '근로기준법 제56조', 'threshold_hours': 8, 'within_rate': '0.5', 'above_rate': '1.0'},
}
ARTICLES = ('근로기준법 제11조', '근로기준법 제56조', '근로기준법 제63조', '근로기준법 시행령 제6조')
BASE = Calculation('premium_pay', 2026, ARTICLES)
CASES = json.loads((Path(__file__).with_name('fixtures') / 'labor_premium_val.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_premium_cases_with_independent_arithmetic(case):
    inputs = case['inputs']
    question = case['question']
    assert all(source in question for source in inputs.values())
    assert inputs.keys() <= FIELDS.keys()
    issue = question_issue(question, inputs)
    if case['id'] == 'LP016':
        assert issue and '휴일별' in issue
        return

    assert not issue or case['kind'] == 'ambiguous'
    result = calculate(BASE, inputs, RULES)
    assert result.status == case['status']
    assert {item['label']: item['value'] for item in result.results} == case['expected_results'], case['basis']
    if result.status == 'calculated':
        assert result.steps
        assert all(step['article'] in ARTICLES for step in result.steps)
        assert all(
            row['source'] in {'미입력(0)', *(f'질문: {source}' for source in inputs.values())} for row in result.inputs
        )
    else:
        assert result.message
        assert result.reason == ('missing_required' if case['kind'] == 'missing' else 'ambiguous_input')


def test_premium_explicit_zero_and_default_zero_have_different_sources():
    case = next(case for case in CASES if case['id'] == 'LP011')
    result = calculate(BASE, case['inputs'], RULES)
    hours = {row['label']: row for row in result.inputs if row.get('unit') == '시간'}
    assert hours['야간 실근로시간']['source'] == '질문: 0시간'
    assert hours['평일 연장 실근로시간']['source'] == '미입력(0)'
    assert hours['휴일 실근로시간']['source'] == '미입력(0)'


def test_premium_source_and_result_carry_explicit_units():
    result = calculate(BASE, CASES[9]['inputs'], RULES)
    assert result.results == ({'label': '법정 가산수당', 'value': '2750.25', 'unit': '원'},)
    assert any(step.get('unit') == '시간' for step in result.steps)


@pytest.mark.parametrize(
    ('question', 'extra', 'guidance'),
    [
        ('야간 실근로는 2시간인지 모르겠습니다.', {}, '야간'),
        (
            '휴일 하루에 10시간 일했고 그중 연장근로는 2시간입니다.',
            {'holiday_hours': '10시간', 'overtime_hours': '2시간'},
            '휴일',
        ),
        ('휴일 이틀에 총 10시간 일했습니다.', {'holiday_hours': '10시간'}, '휴일별'),
        ('기본임금 포함 총액이 얼마인가요?', {}, '총 실근로시간'),
        ('초과근무가 2시간이고 야간은 3시간입니다.', {'overtime_hours': '2시간'}, '야간'),
        ('초과근무 시간은 2시간이고 야간은 3시간입니다.', {'night_hours': '3시간'}, '평일 연장'),
        ('초과근무는 2시간이고 야간 실근로 시간은 3시간입니다.', {'overtime_hours': '2시간'}, '야간'),
        ('휴게 1시간을 포함해 야간 근무가 3시간입니다.', {'night_hours': '3시간'}, '휴게'),
        ('상시 4명인지 5명인지 모르겠어요.', {'staff_count': '5명'}, '상시'),
        ('단시간근로자의 소정근로시간을 초과한 2시간입니다.', {'overtime_hours': '2시간'}, '적용'),
    ],
)
def test_question_issue_blocks_information_lost_during_extraction(question, extra, guidance):
    assert guidance in (question_issue(question, extra) or '')


@pytest.mark.parametrize(
    ('omitted', 'guidance'),
    [('overtime_hours', '평일 연장 실근로시간'), ('night_hours', '야간 실근로시간'), (None, None)],
)
def test_premium_route_requires_each_explicit_work_duration(omitted, guidance):
    question = (
        '상시 6명, 통상시급 12,000원, 평일 연장근로시간은 2시간이고 야간근로시간은 2시간입니다. 가산수당은 얼마인가요?'
    )
    inputs = {
        'staff_count': '6명',
        'ordinary_hourly_wage': '12,000원',
        'overtime_hours': '2시간',
        'night_hours': '2시간',
    }
    if omitted is not None:
        del inputs[omitted]
    raw = json.dumps({'calculator': 'premium_pay', 'inputs': inputs})
    outcome = route_calculation(question, lambda _: raw, {'premium_pay': ()}, reference_year=2026)

    if guidance is None:
        assert outcome.payload['status'] == 'calculated'
        assert outcome.payload['results'] == [{'label': '법정 가산수당', 'value': 24_000, 'unit': '원'}]
    else:
        assert outcome.payload['status'] == 'guidance'
        assert outcome.reason == 'ambiguous_input'
        assert guidance in outcome.payload['message']
        assert not outcome.payload.get('results')


def _route_premium(question, **spans):
    question = f'상시 7명이며 통상시급은 13,000원입니다. {question}'
    inputs = {'staff_count': '7명', 'ordinary_hourly_wage': '13,000원'} | spans
    raw = json.dumps({'calculator': 'premium_pay', 'inputs': inputs})
    return route_calculation(question, lambda _: raw, {'premium_pay': ()}, reference_year=2026)


def test_premium_only_request_does_not_use_the_available_total_for_base_pay():
    outcome = _route_premium(
        '야간 근무가 3시간, 중복 없는 전체 실근로시간은 5시간입니다. 법정 가산분이 얼마인가요?',
        night_hours='3시간',
        total_hours='5시간',
        include_base='법정 가산분',
    )
    assert outcome.payload['status'] == 'calculated'
    assert outcome.payload['results'] == [{'label': '법정 가산수당', 'value': 19_500, 'unit': '원'}]


def test_total_pay_recovers_only_explicit_total_hours():
    outcome = _route_premium(
        '야간 근무가 3시간, 중복 없는 전체 실근로시간은 5시간입니다. 기본임금까지 더한 실제 지급액은 얼마인가요?',
        night_hours='3시간',
    )
    assert outcome.payload['status'] == 'calculated'
    assert {row['label']: row['value'] for row in outcome.payload['results']} == {
        '법정 가산수당': 19_500,
        '기본임금': 65_000,
        '기본임금 포함 합계': 84_500,
    }
    assert any(row['source'] == '질문: 5시간' for row in outcome.payload['inputs'])


@pytest.mark.parametrize(
    'work',
    ['실제 일한 시간은 중복 없이 총 6시간 15분입니다.', '전체 실제 근무시간은 중복 없이 6시간 15분입니다.'],
)
def test_total_hours_accepts_a_modifier_after_the_particle(work):
    outcome = _route_premium(
        f'야간근무 3시간입니다. {work} 기본임금을 합산한 지급액이 얼마인가요?', night_hours='3시간'
    )
    assert outcome.payload['status'] == 'calculated'
    assert outcome.payload['results'][-1]['value'] == 100_750
    assert any(row['source'] == '질문: 6시간 15분' for row in outcome.payload['inputs'])


@pytest.mark.parametrize(
    'work',
    [
        '야간 근무 3시간입니다.',
        '총 실근로시간은 3시간이고 이 시간이 전부 야간입니다.',
        '야간 근무 3시간이며 첫 기록의 총 실근로시간은 5시간, 둘째 기록의 총 근무시간은 6시간입니다.',
    ],
)
def test_total_pay_requires_an_independent_unique_total(work):
    outcome = _route_premium(f'{work} 기본임금까지 더한 실제 지급액은 얼마인가요?', night_hours='3시간')
    assert outcome.payload['status'] == 'guidance'
    assert '총 실근로시간' in outcome.payload['message']


@pytest.mark.parametrize(
    'pay_question',
    [
        '가산분만 계산해 주시되 기본임금도 더한 총지급액이 얼마인가요?',
        '가산분만인지 기본임금 포함 지급액인지 정하지 못했습니다. 얼마인가요?',
    ],
)
def test_premium_request_with_conflicting_pay_scope_guides(pay_question):
    outcome = _route_premium(
        f'야간 근무 3시간, 총 실근로시간 5시간입니다. {pay_question}', night_hours='3시간', total_hours='5시간'
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason == 'ambiguous_input'


@pytest.mark.parametrize(
    ('source', 'scope', 'evidence'),
    [
        ('기본급을 포함한 지급액을 알려 주세요.', 'base', '기본급을 포함'),
        ('기본급까지 계산해 주세요.', 'base', '기본급까지'),
        ('기본급은 제외하고 산출해 주세요.', 'premium', '기본급은 제외'),
        ('기본급을 포함하지 말고 계산해 주세요.', 'premium', '기본급을 포함하지 말'),
        ('기본급 포함 지급액과 가산분만을 함께 요구했습니다.', 'ambiguous', None),
        ('기본급 포함인지 먼저 확인해야 합니다.', 'ambiguous', None),
    ],
)
def test_basic_salary_uses_the_shared_base_wage_scope(source, scope, evidence):
    assert premium_pay_scope(source) == (scope, evidence)


@pytest.mark.parametrize(
    ('pay_request', 'expected'),
    [
        ('기본급을 포함해서 받을 금액을 계산해 주세요.', 107_250),
        ('기본급은 제외하고 추가로 받을 금액을 계산해 주세요.', 16_250),
        ('가산분만 계산하되 기본급을 포함해 주세요.', None),
    ],
)
def test_basic_salary_scope_controls_the_calculation(pay_request, expected):
    outcome = _route_premium(
        f'야간 근무 2시간 30분, 중복 없는 총 실근로시간 7시간입니다. {pay_request}',
        night_hours='2시간 30분',
        total_hours='7시간',
    )
    if expected is None:
        assert outcome.payload['status'] == 'guidance'
        assert outcome.reason == 'ambiguous_input'
        assert not outcome.payload.get('results')
    else:
        # 13,000 × 2.5 × 50%; including base adds 13,000 × 7 (Article 56).
        assert outcome.payload['status'] == 'calculated'
        assert outcome.payload['results'][-1]['value'] == expected


def test_single_holiday_uses_the_actual_original_duration_instead_of_invented_splits():
    outcome = _route_premium(
        '휴일 하루에 휴게를 빼고 실제 9시간 30분 근무했는데 휴일 가산분은 얼마인가요?',
        holiday_within_hours='8시간',
        holiday_excess_hours='1.5시간',
    )
    assert outcome.payload['status'] == 'calculated'
    assert outcome.payload['results'] == [{'label': '법정 가산수당', 'value': 71_500, 'unit': '원'}]
    holiday = next(row for row in outcome.payload['inputs'] if row['label'] == '휴일 실근로시간')
    assert holiday['source'] == '질문: 9시간 30분'


def test_a_holiday_duration_can_be_qualified_by_a_single_day():
    outcome = _route_premium(
        '휴일 한 날의 근무시간은 10시간 45분입니다. 법정 가산분이 얼마나 되나요?',
        holiday_within_hours='8시간',
        holiday_excess_hours='2.75시간',
    )
    assert outcome.payload['status'] == 'calculated'
    assert outcome.payload['results'] == [{'label': '법정 가산수당', 'value': 87_750, 'unit': '원'}]


@pytest.mark.parametrize(
    'work',
    ['휴일 사흘에 합쳐 9시간 30분 일했습니다.', '휴일에 9시간 30분 일했습니다.'],
)
def test_absent_single_holiday_does_not_convert_the_split_to_one_day(work):
    outcome = _route_premium(
        f'{work} 휴일 가산분은 얼마인가요?', holiday_within_hours='8시간', holiday_excess_hours='1.5시간'
    )
    assert outcome.payload['status'] != 'calculated'


@pytest.mark.parametrize(
    'extra',
    [
        {'holiday_hours': '25시간'},
        {'holiday_within_hours': '4시간', 'holiday_excess_hours': '1시간'},
        {'holiday_hours': '8시간', 'holiday_within_hours': '8시간', 'holiday_excess_hours': '0시간'},
        {'night_hours': '6시간', 'total_hours': '5시간'},
        {'night_hours': '2시간', 'include_base': '기본임금 포함인지 모름'},
    ],
)
def test_premium_rejects_inconsistent_inputs(extra):
    result = calculate(BASE, {'staff_count': '6명', 'ordinary_hourly_wage': '13000원'} | extra, RULES)
    assert result.status == 'guidance'
    assert result.reason == 'ambiguous_input'
    assert not result.results


def test_prompt_documents_all_premium_inputs():
    assert FIELD_DESCRIPTIONS['premium_pay'][1].keys() == FIELDS.keys()
