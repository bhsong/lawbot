import json

import pytest

from lawbot.calc_router import route_calculation
from lawbot.calculators import calculate

_ANNUAL = 'annual_leave_days'
_WEEKLY = 'weekly_holiday_pay'
_ANNIVERSARY_INPUTS = {
    'workplace_size': '14명',
    'weekly_hours': '36시간',
    'hire_date': '2025-03-15',
    'as_of_date': '2026-03-15',
    'attendance_rate': '87%',
}
_WEEKLY_INPUTS = {
    'hourly_wage': '11,500원',
    'weekly_scheduled_hours': '24시간',
    'paid_holiday_hours': '6시간',
    'attendance': '개근했습니다',
}


def _route(calculator, question, inputs):
    raw = json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)
    return route_calculation(question, lambda _: raw, {calculator: ()}, reference_year=2026)


def test_annual_first_year_attendance_does_not_request_a_combined_total():
    question = (
        '상시 14명인 곳에서 주 소정근로시간은 36시간입니다. 2025-03-15 입사했고 2026-03-15에도 다니고 있습니다. '
        '입사 첫해 출근율이 87%였는데 이 날 새로 받는 연차는 며칠인가요?'
    )
    result = _route(_ANNUAL, question, {**_ANNIVERSARY_INPUTS, 'include_first_year': '입사 첫해'})

    assert result.payload['results'] == [{'label': '연차유급휴가 일수', 'value': 15, 'unit': '일'}]
    assert all(row['label'] != '첫해 월단위 연차 합산' for row in result.payload['inputs'])


def test_annual_monthly_accrual_is_independent_of_an_unrequested_boolean_flag():
    question = (
        '상시 13명인 곳에서 주 소정 35시간으로 근무했습니다. 입사 후 4개월 동안 결근 없이 출근했고 '
        '각 월을 마친 다음 날에도 재직했습니다. 지금까지 발생한 법정 연차가 며칠인가요?'
    )
    result = _route(
        _ANNUAL,
        question,
        {
            'workplace_size': '13명',
            'weekly_hours': '35시간',
            'service_period': '4개월',
            'full_attendance_months': '4개월',
            'include_first_year': 'true',
        },
    )

    assert result.payload['results'] == [{'label': '연차유급휴가 일수', 'value': 4, 'unit': '일'}]
    assert all(row['source'] != '질문: true' for row in result.payload['inputs'])


def test_annual_combined_total_requires_an_explicit_scope_excerpt():
    scope = '입사 첫해 월별 연차까지 더하면'
    question = (
        '상시 14명, 주 소정 36시간입니다. 2025-03-15에 입사했고 2026-03-15에도 재직했습니다. '
        f'첫해 출근율 87%, 월 말 다음 날까지 재직한 개근월은 10개월입니다. 1주년 발생분에 {scope} 며칠인가요?'
    )
    inputs = {**_ANNIVERSARY_INPUTS, 'full_attendance_months': '10개월'}
    result = _route(_ANNUAL, question, {**inputs, 'include_first_year': scope})
    missing = _route(_ANNUAL, question, {**inputs, 'include_first_year': 'true'})

    assert result.payload['results'] == [{'label': '연차유급휴가 일수', 'value': 25, 'unit': '일'}]
    assert any(row['source'] == f'질문: {scope}' for row in result.payload['inputs'])
    assert missing.payload['status'] == 'guidance'
    assert not missing.payload.get('results')


def test_annual_accrual_day_employment_is_confirmed_by_the_question():
    question = (
        '상시 16명이고 주 소정 38시간입니다. 4년을 채웠고 직전 1년 출근율은 91%였습니다. '
        '연차 발생일에도 근로계약이 이어졌습니다. 이번 연차는 며칠인가요?'
    )
    result = _route(
        _ANNUAL,
        question,
        {
            'workplace_size': '16명',
            'weekly_hours': '38시간',
            'service_period': '4년',
            'attendance_rate': '91%',
            'employed_at_accrual': '근로계약이 이어졌습니다',
        },
    )

    assert result.payload['results'] == [{'label': '연차유급휴가 일수', 'value': 16, 'unit': '일'}]


def test_annual_uncertain_accrual_day_fact_produces_guidance():
    result = calculate(
        _ANNUAL,
        {
            'workplace_size': '16명',
            'weekly_hours': '38시간',
            'service_period': '4년',
            'attendance_rate': '91%',
            'employed_at_accrual': '발생일에 재직했는지 확인이 어렵습니다',
        },
        year=2026,
    )

    assert result.status == 'guidance'
    assert result.reason == 'ambiguous_input'
    assert not result.results


def test_annual_does_not_use_a_positive_fragment_from_a_negative_fact():
    question = (
        '상시 16명, 주 소정 38시간인 곳에서 4년을 채웠고 직전 1년 출근율은 91%입니다. '
        '연차 발생일에는 재직하지 않았습니다. 이번 연차는 며칠인가요?'
    )
    result = _route(
        _ANNUAL,
        question,
        {
            'workplace_size': '16명',
            'weekly_hours': '38시간',
            'service_period': '4년',
            'attendance_rate': '91%',
            'employed_at_accrual': '재직',
        },
    )

    assert result.payload['status'] == 'guidance'
    assert result.reason == 'ambiguous_input'
    assert not result.payload.get('results')


@pytest.mark.parametrize(
    ('employment', 'pay'),
    [
        ('일주일 동안 고용계약이 이어졌습니다', 69_000),
        ('7일 동안 고용관계가 이어지지 않았습니다', 0),
        ('7일 동안 고용관계를 유지한 듯합니다', None),
    ],
)
def test_weekly_holiday_checks_the_whole_employment_fact(employment, pay):
    question = (
        '통상시급 11,500원, 주 소정근로시간 24시간, 유급주휴시간 6시간입니다. '
        f'개근했습니다. {employment}. 이 주의 주휴수당은 얼마인가요?'
    )
    result = _route(_WEEKLY, question, {**_WEEKLY_INPUTS, 'employment': employment})

    if pay is None:
        assert result.payload['status'] == 'guidance'
        assert not result.payload.get('results')
    else:
        assert result.payload['results'] == [{'label': '주휴수당', 'value': pay, 'unit': '원'}]


def test_weekly_holiday_accepts_hours_with_their_question_labels():
    question = (
        '통상시급 11,500원, 주 소정근로시간 24시간, 유급주휴시간 6시간입니다. '
        '개근했습니다. 7일간 근로관계를 유지했습니다. 이 주의 주휴수당은 얼마인가요?'
    )
    result = _route(
        _WEEKLY,
        question,
        {
            **_WEEKLY_INPUTS,
            'weekly_scheduled_hours': '주 소정근로시간 24시간',
            'paid_holiday_hours': '유급주휴시간 6시간',
            'employment': '7일간 근로관계를 유지했습니다',
        },
    )

    assert result.payload['results'] == [{'label': '주휴수당', 'value': 69_000, 'unit': '원'}]
    assert {row['source'] for row in result.payload['inputs']} >= {
        '질문: 주 소정근로시간 24시간',
        '질문: 유급주휴시간 6시간',
    }


def test_weekly_holiday_accepts_an_hourly_wage_with_its_label():
    wage = '통상시급 11,500원'
    employment = '7일간 근로관계를 유지했습니다'
    question = (
        f'{wage}, 주 소정근로시간 24시간, 유급주휴시간 6시간입니다. '
        f'개근했습니다. {employment}. 이 주의 주휴수당은 얼마인가요?'
    )
    result = _route(_WEEKLY, question, {**_WEEKLY_INPUTS, 'hourly_wage': wage, 'employment': employment})

    assert result.payload['results'] == [{'label': '주휴수당', 'value': 69_000, 'unit': '원'}]
    assert any(row['source'] == f'질문: {wage}' for row in result.payload['inputs'])


def test_annual_working_status_is_evidence_only_for_the_accrual_date():
    prefix = '상시 16명, 주 소정 38시간입니다. 4년을 채웠고 직전 1년 출근율은 91%입니다. '
    status = '근무 중이었습니다'
    inputs = {
        'workplace_size': '16명',
        'weekly_hours': '38시간',
        'service_period': '4년',
        'attendance_rate': '91%',
        'employed_at_accrual': status,
    }
    result = _route(_ANNUAL, prefix + f'연차 발생일에도 {status}. 이번 연차는 며칠인가요?', inputs)
    unscoped = _route(_ANNUAL, prefix + f'출근율을 조회한 당시 {status}. 이번 연차는 며칠인가요?', inputs)

    assert result.payload['results'] == [{'label': '연차유급휴가 일수', 'value': 16, 'unit': '일'}]
    assert unscoped.payload['status'] == 'guidance'
    assert not unscoped.payload.get('results')


def test_weekly_holiday_excludes_confirmed_employment_of_less_than_seven_days():
    employment = '이번 주 근로관계는 7일 미만이었습니다'
    question = (
        f'통상시급 11,500원, 주 소정근로시간 24시간, 유급주휴시간 6시간입니다. 개근했습니다. '
        f'{employment}. 이 주의 주휴수당은 얼마인가요?'
    )
    result = _route(_WEEKLY, question, {**_WEEKLY_INPUTS, 'employment': employment})

    assert result.payload['results'] == [{'label': '주휴수당', 'value': 0, 'unit': '원'}]
