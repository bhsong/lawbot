import json
from pathlib import Path

import pytest

from lawbot.calculators import Calculation
from lawbot.labor_premium import FIELDS, SCHEMA, calculate, question_issue

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
        ('휴게 1시간을 포함해 야간 근무가 3시간입니다.', {'night_hours': '3시간'}, '휴게'),
        ('상시 4명인지 5명인지 모르겠어요.', {'staff_count': '5명'}, '상시'),
        ('단시간근로자의 소정근로시간을 초과한 2시간입니다.', {'overtime_hours': '2시간'}, '적용'),
    ],
)
def test_question_issue_blocks_information_lost_during_extraction(question, extra, guidance):
    assert guidance in (question_issue(question, extra) or '')


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
    assert all(field in SCHEMA for field in FIELDS)
