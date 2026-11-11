import json
from pathlib import Path

import pytest

from lawbot import labor_minimum, labor_weekly
from lawbot.calc_gate import is_calculation_question
from lawbot.calc_router import route_calculation
from lawbot.calculators import Calculation

_FIXTURES = Path(__file__).with_name('fixtures')
_CASES = {
    'weekly_holiday_pay': json.loads((_FIXTURES / 'calc_labor_weekly_val.json').read_text(encoding='utf-8')),
    'monthly_minimum_wage': json.loads((_FIXTURES / 'calc_labor_minimum_val.json').read_text(encoding='utf-8')),
}
_MODULES = {'weekly_holiday_pay': labor_weekly, 'monthly_minimum_wage': labor_minimum}
_STATUTES = json.loads(
    (Path(__file__).parents[1] / 'src' / 'lawbot' / 'calc_statutes.json').read_text(encoding='utf-8')
)


@pytest.mark.parametrize('case', [case for cases in _CASES.values() for case in cases], ids=lambda case: case['id'])
def test_weekly_holiday_and_minimum_wage_cases(case):
    name, year = case['calculator'], case.get('year', 2026)
    inputs = case['inputs']
    assert all(source in case['question'] for source in inputs.values())
    module = _MODULES[name]
    assert set(inputs) <= module.FIELDS.keys()
    base = Calculation(name, year, tuple(_STATUTES['article_hashes'][name]))
    result = module.calculate(base, inputs, _STATUTES['years'][str(year)][name])
    assert result.status == case['status']
    if case['status'] == 'calculated':
        assert {row['label']: row['value'] for row in result.results} == case['expected_results']
        assert all(row.get('unit') == '원' for row in result.results)
        assert any(row.get('unit') == '시간' for row in result.steps)
        assert all(row['article'] for row in result.steps)
        assert all(row['source'].startswith('질문: ') for row in result.inputs)
    else:
        assert result.message and not result.results

    assert is_calculation_question(case['question'])
    raw = json.dumps({'calculator': name, 'inputs': inputs}, ensure_ascii=False)
    routed = route_calculation(case['question'], lambda _: raw, {name: ()}, reference_year=2026)
    assert routed.payload['status'] == case['status']
    assert {row['label']: row['value'] for row in routed.payload.get('results', [])} == case.get('expected_results', {})


def test_weekly_holiday_question_checks_negation_and_missing_evidence():
    question = '소정근로일에 개근하지 않았습니다. 7일간 근로관계 유지했습니다.'
    assert labor_weekly.question_issue(question, {'attendance': '개근', 'employment': '7일간 근로관계 유지'})
    assert not labor_weekly.question_issue(
        question, {'attendance': '개근하지 않았습니다', 'employment': '7일간 근로관계 유지'}
    )
    question = '개근했고 7일간 근로관계 유지했는지 모릅니다.'
    assert labor_weekly.question_issue(question, {'attendance': '개근', 'employment': '7일간 근로관계 유지'})
    question = '개근했습니다. 7일간 근로관계 유지했습니다. 유급주휴 4시간입니다.'
    assert labor_weekly.question_issue(question, {'attendance': '개근', 'employment': '7일간 근로관계 유지'})


def test_minimum_wage_question_checks_basis_and_exemption():
    question = '수습 감액 대상인지 모릅니다. 하루 8시간 주 5일입니다.'
    inputs = {'daily_scheduled_hours': '8시간', 'weekly_workdays': '5일'}
    assert labor_minimum.question_issue(question, inputs)
    assert labor_minimum.question_issue('월 100시간 실제 근무했습니다.', {'monthly_basis_hours': '100시간'})
    assert not labor_minimum.question_issue(
        '월 최저임금 적용기준시간이 100시간입니다.', {'monthly_basis_hours': '100시간'}
    )

    question = '하루 8시간 주 5일인데 식대 포함 월급 200만원이면 최저임금에 미달하나요?'
    assert labor_minimum.question_issue(question, inputs)


def test_weekly_holiday_question_detects_conflicting_attendance_and_wrong_hours():
    question = '개근했습니다. 그런데 이번 주 소정근로일에 결근했습니다.'
    assert labor_weekly.question_issue(question, {'attendance': '개근'})
    question = '유급주휴 4시간에 실제로 6시간 더 일했습니다.'
    assert labor_weekly.question_issue(question, {'paid_holiday_hours': '6시간'})
    question = '이번 주 실제로 20시간 일했지만 4주 평균 주 소정근로시간은 14시간입니다.'
    assert labor_weekly.question_issue(question, {'weekly_scheduled_hours': '20시간'})
    assert not labor_weekly.question_issue(question, {'weekly_scheduled_hours': '14시간'})


def test_minimum_wage_question_requires_hours_tied_to_the_monthly_basis():
    question = '월 최저임금 적용기준시간은 104시간이고 실제로 120시간 일했습니다.'
    assert labor_minimum.question_issue(question, {'monthly_basis_hours': '120시간'})
    assert not labor_minimum.question_issue(question, {'monthly_basis_hours': '104시간'})
    question = '월 최저임금 적용기준시간 104시간 또는 106시간인 것 같습니다.'
    assert labor_minimum.question_issue(question, {'monthly_basis_hours': '104시간'})
