import json
from pathlib import Path

import pytest

from lawbot.calc_router import check_conclusion, prompt_suffix, route_calculation
from lawbot.calculators import MEDICAL, MIDYEAR

_CASES = {
    case['id']: case
    for case in json.loads(Path(__file__).with_name('fixtures').joinpath('calc_router_val.json').read_text())
}


def route(case_id, calculator, inputs):
    raw = json.dumps({'calculator': calculator, 'inputs': inputs})
    return route_calculation(_CASES[case_id]['question'], lambda _: raw, {calculator: ()}, reference_year=2026)


@pytest.mark.parametrize('case_id, gross_pay, year', [('CR023', '1,200만원', '2024'), ('CR030', '1000만원', '2025')])
def test_tax_year_from_question_survives_hallucinated_model_year(case_id, gross_pay, year):
    output = route(case_id, MIDYEAR, {'gross_pay': gross_pay, 'year': year})
    assert output.payload['status'] == 'calculated'
    assert output.payload['year'] == 2026
    assert all(result['value'] == 0 for result in output.payload['results'])


def test_hallucinated_year_does_not_mask_missing_gross_pay():
    output = route('CR088', MIDYEAR, {'year': '2025'})
    assert output.payload['status'] == 'guidance'
    assert output.reason == 'missing_required'
    assert '총급여' in output.payload['message']


def test_different_excerpts_of_the_same_amount_are_not_independent():
    output = route(
        'CR092', MEDICAL, {'gross_pay': '5000만원', 'general_expenses': '700만원', 'infertility_expenses': '700만'}
    )
    assert output.payload['status'] == 'guidance'
    assert output.reason == 'ambiguous_input'


def test_amount_fields_can_use_distinct_overlapping_candidates():
    question = '2026년 총급여 5000만원, 일반 의료비 300만원, 난임시술비 300만원일 때 의료비 공제액은?'
    raw = json.dumps(
        {
            'calculator': MEDICAL,
            'inputs': {'gross_pay': '5000만원', 'general_expenses': '300만', 'infertility_expenses': '300만원'},
        }
    )
    output = route_calculation(question, lambda _: raw, {MEDICAL: ()}, reference_year=2026)
    assert output.payload['results'] == [{'label': '의료비 세액공제', 'value': 1_125_000}]


def test_day_result_is_checked_with_its_unit():
    payload = {
        'status': 'calculated',
        'results': [{'label': '연차유급휴가 일수', 'value': 15, 'unit': '일'}],
        'inputs': [],
        'steps': [],
    }
    assert check_conclusion('【결론】연차는 15일입니다.', payload) == 'match'
    assert check_conclusion('【결론】연차는 16일입니다.', payload) == 'mismatch'
    assert check_conclusion('【결론】연차는 -15일입니다.', payload) == 'mismatch'
    assert check_conclusion('【결론】연차가 발생합니다.', payload) == 'missing'
    assert '단위' in prompt_suffix(payload)


def test_calculation_suffix_avoids_json_whitespace_in_the_generation_prompt():
    payload = {'status': 'calculated', 'results': [{'label': '퇴직금', 'value': 9_000_000}]}
    encoded = prompt_suffix(payload).splitlines()[3]
    assert json.loads(encoded) == payload
    assert len(encoded) < len(json.dumps(payload, ensure_ascii=False))


def test_evaluation_keeps_pr1_labels_and_counts_pr2_unsupported():
    from lawbot_tools.eval_calc_router import _evaluation_cases

    groups, samples = _evaluation_cases('labor-pr2')
    assert {name: len(cases) for name, cases in groups.items()} == {'pr1': 100, 'pr2_regression': 18, 'labor': 120}
    original = {case['id']: case for case in groups['pr1']}
    regression = {case['id']: case for case in groups['pr2_regression']}
    assert original['CR041']['calculator'] == 'none'
    assert regression['CR041']['expected_results'] == {'평균임금 기준 퇴직금 추정': 9_000_000}
    assert regression['CR050']['status'] == 'guidance'
    assert len([case for case in groups['labor'] if case['kind'] == 'unsupported']) == 20
    assert len(samples) == 13 and '2,700만원' in next(case for case in samples if case['id'] == 'CR041')['prior_answer']


def test_pr2_val_replay_matches_the_independent_results():
    from lawbot.calculators import ARTICLE_HASHES
    from lawbot_tools.eval_calc_router import _evaluation_cases, _quality, _route_case

    groups, _ = _evaluation_cases('labor-pr2')
    compatibility = dict.fromkeys(ARTICLE_HASHES, ())

    class Stub:
        def route_calculation(self, question, **_):
            case = cases[question]
            if 'inputs' in case:
                return json.dumps({'calculator': case['calculator'], 'inputs': case['inputs']})
            kind = 'calculation' if case['kind'] == 'unsupported' else 'lookup'
            return json.dumps({'calculator': 'none', 'kind': kind})

    cases = {row['question']: row for row in groups['labor']}
    routes = [_route_case(case, Stub(), compatibility, 'labor') for case in cases.values()]
    assert len(cases) == 120
    assert [row['id'] for row in routes if not row['classification_match']] == []
    assert _quality(routes)['wrong_calculations'] == []


@pytest.mark.parametrize(
    ('question', 'name', 'inputs'),
    [
        (
            '월급 300만원으로 13년 일했습니다. 퇴직금을 얼마 받나요?',
            'severance_pay',
            {'monthly_wage': '300만원', 'service_period': '3년'},
        ),
        (
            '하루 8시간 30분 주 5일이면 최저임금을 월급으로 환산하면 얼마인가요?',
            'monthly_minimum_wage',
            {'daily_scheduled_hours': '8시간', 'weekly_workdays': '5일'},
        ),
        (
            '월급 300만원으로 2년하고 삼개월 일했습니다. 퇴직금을 얼마 받나요?',
            'severance_pay',
            {'monthly_wage': '300만원', 'service_period': '2년'},
        ),
        (
            '상시 10명이고 주 소정 40시간, 3년 근속, 연차 발생일에 재직 중이며 출근율 -95%입니다. 연차는 며칠인가요?',
            'annual_leave_days',
            {
                'workplace_size': '10명',
                'weekly_hours': '40시간',
                'service_period': '3년',
                'employed_at_accrual': '연차 발생일에 재직 중',
                'attendance_rate': '95%',
            },
        ),
    ],
)
def test_labor_quantity_must_include_its_complete_digits_and_units(question, name, inputs):
    raw = json.dumps({'calculator': name, 'inputs': inputs})
    outcome = route_calculation(question, lambda _: raw, {name: ()}, reference_year=2026)
    assert outcome.payload['status'] != 'calculated'


def test_one_night_span_cannot_also_supply_overtime():
    question = '상시 9명, 통상시급 12,000원입니다. 휴게 제외 야간근로 4시간의 가산수당은 얼마인가요?'
    inputs = {
        'staff_count': '9명',
        'ordinary_hourly_wage': '12,000원',
        'night_hours': '4시간',
        'overtime_hours': '4시간',
    }
    raw = json.dumps({'calculator': 'premium_pay', 'inputs': inputs})
    outcome = route_calculation(question, lambda _: raw, {'premium_pay': ()}, reference_year=2026)
    assert outcome.payload['status'] == 'guidance' and outcome.reason == 'ambiguous_input'


@pytest.mark.parametrize('hire_date', ['2025-11-01', '2025년 11월 1일'])
def test_hire_date_is_not_the_minimum_wage_application_year(hire_date):
    question = f'{hire_date} 입사했습니다. 주 소정 40시간과 유급주휴 8시간이면 최저임금 월급을 얼마로 환산하나요?'
    raw = json.dumps(
        {
            'calculator': 'monthly_minimum_wage',
            'inputs': {'weekly_scheduled_hours': '40시간', 'weekly_paid_holiday_hours': '8시간', 'year': '2025'},
        }
    )
    outcome = route_calculation(question, lambda _: raw, {'monthly_minimum_wage': ()}, reference_year=2026)
    assert outcome.payload['results'][0]['value'] == 2_156_880
