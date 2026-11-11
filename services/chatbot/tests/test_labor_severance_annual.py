import json
from pathlib import Path

import pytest

from lawbot.calc_router import check_conclusion, route_calculation
from lawbot.calculators import calculate

FIXTURES = Path(__file__).with_name('fixtures')
CASES = [
    case
    for filename in ('labor_severance_val.json', 'labor_annual_val.json')
    for case in json.loads((FIXTURES / filename).read_text(encoding='utf-8'))
]


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_labor_calculation_cases(case):
    assert all(span in case['question'] for span in case['inputs'].values())
    result = calculate(case['calculator'], case['inputs'], year=2026)
    assert result.status == case['status']
    assert [{key: value for key, value in row.items() if key != 'condition'} for row in result.results] == case.get(
        'results', []
    )
    if result.status == 'guidance':
        assert result.message


def test_cr041_estimates_from_monthly_wages_and_flags_historical_answer():
    case = json.loads((FIXTURES / 'labor_severance_cr041.json').read_text(encoding='utf-8'))
    result = calculate('severance_pay', case['inputs'], year=2026)
    assert result.status == 'calculated'
    assert {row['label']: row['value'] for row in result.results} == case['expected_results']
    assert all(row.get('condition') for row in result.results)
    assert {row['source'] for row in result.inputs} >= {'질문: 300만원', '질문: 3년'}
    assert sum(row['source'] == '미입력(0)' for row in result.inputs) >= 2
    assert {row['value'] for row in result.steps} >= {9_000_000, 10_334_928, 100_000, '114832.5359', 1095, 90}
    ordinary = next(step for step in result.steps if '1일 통상임금' in step['label'])
    assert ordinary['article'] == '근로기준법 시행령 제6조'
    assert '209시간' in ordinary['formula'] and '8시간' in ordinary['formula']
    assert '반올림' in result.steps[-1]['formula']
    message = result.message or ''
    assert '월급이 기본급만으로 구성돼 모두 통상임금이라면' in message
    assert '10,334,928원' in message and '9,000,000원 이상' in message
    assert '209시간' in message and '8시간' in message and '법정 지급 요건' in message
    assert check_conclusion(case['answer'], result.as_dict()) == 'mismatch'
    assert check_conclusion(message, result.as_dict()) == 'match'


def test_monthly_estimate_uses_supplied_ordinary_wage_without_hypothetical_result():
    result = calculate(
        'severance_pay',
        {'monthly_wage': '300만원', 'service_period': '3년', 'ordinary_daily_wage': '12만원'},
        year=2026,
    )

    assert list(result.results) == [{'label': '퇴직금 추정', 'value': 10_800_000}]
    assert any(row['label'] == '1일 통상임금' and row['source'] == '질문: 12만원' for row in result.inputs)
    assert all('209시간' not in step['formula'] for step in result.steps)


def test_monthly_estimate_never_lowers_the_average_wage_to_the_conditional_ordinary_wage():
    result = calculate(
        'severance_pay',
        {'monthly_wage': '300만원', 'service_period': '3년', 'annual_bonus': '1200만원'},
        year=2026,
    )

    assert [row['value'] for row in result.results] == [12_000_000, 12_000_000]


def test_monthly_estimate_rounds_the_conditional_amount_after_proration():
    result = calculate('severance_pay', {'monthly_wage': '3,000,086원', 'service_period': '3년'}, year=2026)

    # 2,160,061,920 / 209 is below the half-won boundary. Rounding the displayed
    # daily wage 114,835.8278 first would move the total above that boundary.
    assert result.results[-1]['value'] == 10_335_224


@pytest.mark.parametrize(
    ('calculator', 'question', 'inputs'),
    [
        (
            'severance_pay',
            '2024-11-01 입사, 퇴사일은 2026-11-01입니다. 주 소정 40시간이고 통상임금 하한 반영 일급은 10만원입니다.',
            {
                'hire_date': '2024-11-01',
                'retirement_date': '2026-11-01',
                'weekly_hours': '40시간',
                'adjusted_daily_wage': '10만원',
            },
        ),
        (
            'severance_pay',
            '인정 계속근로 730일, 주 소정 40시간, 상여금 184만원을 포함한 직전 3개월 임금 총액이 920만원입니다. '
            '직전 역일수 92일, 1일 통상임금 9만원인데 퇴직금을 계산해 주세요.',
            {
                'service_period': '730일',
                'weekly_hours': '40시간',
                'wage_total': '920만원',
                'wage_days': '92일',
                'annual_bonus': '184만원',
                'ordinary_daily_wage': '9만원',
            },
        ),
        (
            'severance_pay',
            '인정 계속근로 730일, 주 소정 40시간, 직전 3개월 실제 근무일수 61일에 임금 920만원입니다. '
            '1일 통상임금 9만원인데 퇴직금은 얼마인가요?',
            {
                'service_period': '730일',
                'weekly_hours': '40시간',
                'wage_total': '920만원',
                'wage_days': '61일',
                'ordinary_daily_wage': '9만원',
            },
        ),
        (
            'annual_leave_days',
            '상시 10명, 주 소정 40시간인 직장에서 근속 6개월입니다. 연차가 며칠 발생하나요?',
            {
                'workplace_size': '10명',
                'weekly_hours': '40시간',
                'service_period': '6개월',
                'full_attendance_months': '6개월',
            },
        ),
        (
            'annual_leave_days',
            '상시 10명, 주 소정 40시간, 2년 근속했고 연차 발생일에도 재직합니다. 출근율이 79~81%일 때 연차는요?',
            {
                'workplace_size': '10명',
                'weekly_hours': '40시간',
                'service_period': '2년',
                'employed_at_accrual': '연차 발생일에도 재직',
                'attendance_rate': '81%',
            },
        ),
    ],
)
def test_labor_route_checks_ambiguous_source_meanings(calculator, question, inputs):
    raw = json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)
    routed = route_calculation(question, lambda _: raw, {calculator: ()}, reference_year=2026)
    assert routed.payload['status'] == 'guidance'
    assert routed.reason == 'ambiguous_input'


@pytest.mark.parametrize(
    'extra',
    [
        {'hire_date': '2024-11-01', 'retirement_date': '2026-11-01', 'service_period': '9,000,000,000년'},
        {'hire_date': '2024-11-01', 'retirement_date': '2026-11-01', 'service_period': '2년 9,000,000,000일'},
        {'last_employed_date': '9999-12-31', 'service_period': '730일'},
        {'service_period': '9,000,000,000일'},
    ],
)
def test_severance_rejects_inputs_beyond_supported_date_or_value_ranges(extra):
    inputs = {'weekly_hours': '40시간', 'adjusted_daily_wage': '1억원', **extra}
    with pytest.raises(ValueError):
        calculate('severance_pay', inputs, year=2026)
