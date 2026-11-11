"""Require evidence for material source quantities without conflating numeric roles."""

import json

import pytest

from lawbot.calc_router import route_calculation


def _route(question, calculator, inputs):
    raw = json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)
    return route_calculation(question, lambda _: raw, {calculator: ()}, reference_year=2026)


@pytest.mark.parametrize(
    ('calculator', 'question', 'inputs'),
    [
        (
            'severance_pay',
            '월급 245만원을 받으며 4년 근속했습니다. 근속 합계는 1320일로도 적혀 있습니다. 퇴직금은 얼마인가요?',
            {'monthly_wage': '245만원', 'service_period': '4년'},
        ),
        (
            'annual_leave_days',
            '상시 근로자 17명인 곳입니다. 별도 인원표의 상시 근로자는 29명입니다. '
            '주 소정 35시간, 근속 5년이고 연차 발생일 재직 중입니다. 출근율 94%일 때 연차 일수를 구해 주세요.',
            {
                'workplace_size': '17명',
                'weekly_hours': '35시간',
                'service_period': '5년',
                'employed_at_accrual': '연차 발생일 재직 중',
                'attendance_rate': '94%',
            },
        ),
        (
            'weekly_holiday_pay',
            '통상시급은 14700원이고 급여명세서의 통상시급은 15100원입니다. '
            '주 소정 28시간, 유급주휴 5시간입니다. 개근했고 7일간 근로관계 유지했습니다. 주휴수당은요?',
            {
                'hourly_wage': '14700원',
                'weekly_scheduled_hours': '28시간',
                'paid_holiday_hours': '5시간',
                'attendance': '개근',
                'employment': '7일간 근로관계 유지',
            },
        ),
        (
            'monthly_minimum_wage',
            '2026년 주 소정근로시간 40시간과 유급주휴 8시간으로 최저임금 월액을 알고 싶습니다. '
            '근로계약서의 주 소정근로시간은 36시간입니다.',
            {'year': '2026년', 'weekly_scheduled_hours': '40시간', 'weekly_paid_holiday_hours': '8시간'},
        ),
        (
            'premium_pay',
            '상시 11명, 통상시급 16200원입니다. 야간 실근로 2시간이고 야간 시간 집계표에는 5시간으로 '
            '적혀 있습니다. 가산분만 얼마인가요?',
            {
                'staff_count': '11명',
                'ordinary_hourly_wage': '16200원',
                'night_hours': '2시간',
                'include_base': '가산분만',
            },
        ),
    ],
    ids=['severance-service', 'annual-workforce', 'weekly-wage', 'minimum-hours', 'premium-night'],
)
def test_unextracted_second_quantity_of_material_role_requires_guidance(calculator, question, inputs):
    outcome = _route(question, calculator, inputs)
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


@pytest.mark.parametrize(
    ('calculator', 'question', 'inputs', 'expected'),
    [
        (
            'severance_pay',
            '2026년 근로자퇴직급여 보장법 제8조의 퇴직금을 구해주세요. '
            '직전 3개월 동안 고정 월급 245만원을 받았고 근속은 4년입니다.',
            {'monthly_wage': '245만원', 'service_period': '4년'},
            9800000,
        ),
        (
            'annual_leave_days',
            '직원은 상시 22명입니다. 하루 소정근로 7시간, 주 소정근로 35시간이고 근속 5년입니다. '
            '직전 1년 출근율은 94%이며 연차 발생일 재직 중입니다. 제60조 연차는 며칠인가요?',
            {
                'workplace_size': '22명',
                'weekly_hours': '35시간',
                'service_period': '5년',
                'employed_at_accrual': '연차 발생일 재직 중',
                'attendance_rate': '94%',
            },
            17,
        ),
        (
            'weekly_holiday_pay',
            '상시 근로자 3명인 매장입니다. 통상시급 14700원, 주 소정 28시간과 유급주휴 5시간이 정해져 '
            '있습니다. 개근했고 7일간 근로관계 유지했습니다. 제55조 주휴수당을 구해 주세요.',
            {
                'hourly_wage': '14700원',
                'weekly_scheduled_hours': '28시간',
                'paid_holiday_hours': '5시간',
                'attendance': '개근',
                'employment': '7일간 근로관계 유지',
            },
            73500,
        ),
        (
            'monthly_minimum_wage',
            '2026년 최저임금법 제6조를 기준으로 월 환산액이 궁금합니다. 입사는 2026-04-01이고 '
            '상시 인원은 6명입니다. 주 소정근로 40시간, 유급주휴 8시간입니다.',
            {'year': '2026년', 'weekly_scheduled_hours': '40시간', 'weekly_paid_holiday_hours': '8시간'},
            2156880,
        ),
        (
            'premium_pay',
            '상시 11명, 주 소정근로 35시간입니다. 통상시급 16200원에 야간 실근로 2시간이면 '
            '근로기준법 제56조의 가산분만 얼마인가요?',
            {
                'staff_count': '11명',
                'ordinary_hourly_wage': '16200원',
                'night_hours': '2시간',
                'include_base': '가산분만',
            },
            16200,
        ),
    ],
    ids=['severance-lookback', 'annual-assessment', 'weekly-workforce', 'minimum-calendar', 'premium-schedule'],
)
def test_other_quantity_roles_do_not_block_supported_calculation(calculator, question, inputs, expected):
    outcome = _route(question, calculator, inputs)
    assert outcome.payload['status'] == 'calculated', outcome.reason
    # Independent primary results: monthly wage × years; statutory annual days;
    # hourly wage × holiday hours; 2026 hourly minimum × 209; night hours × wage / 2.
    assert outcome.payload['results'][0]['value'] == expected


def test_identical_amounts_at_separate_source_positions_remain_covered():
    question = '월급 240만원, 별도 산입 연간 상여금 240만원이며 근속 2년입니다. 퇴직금 추정액을 구해 주세요.'
    outcome = _route(
        question,
        'severance_pay',
        {'monthly_wage': '240만원', 'annual_bonus': '240만원', 'service_period': '2년'},
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    # ((2,400,000 × 3 + 2,400,000 × 3 / 12) / 90) × 30 × 2.
    assert outcome.payload['results'][0]['value'] == 5200000


def test_long_fact_source_does_not_account_for_an_unrelated_numeric_role():
    question = (
        '상시 인원 17명, 주 소정 35시간, 근속 5년, 출근율 94%입니다. '
        '연차 발생일 재직 중이며 상시 인원 집계는 29명입니다. 연차 일수를 계산해 주세요.'
    )
    outcome = _route(
        question,
        'annual_leave_days',
        {
            'workplace_size': '17명',
            'weekly_hours': '35시간',
            'service_period': '5년',
            'attendance_rate': '94%',
            'employed_at_accrual': '연차 발생일 재직 중이며 상시 인원 집계는 29명',
        },
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


@pytest.mark.parametrize(
    ('calculator', 'question', 'inputs'),
    [
        (
            'severance_pay',
            '월급은 265만원입니다. 임금대장의 월급도 265만원입니다. 근속 6년의 퇴직금은 얼마인가요?',
            {'monthly_wage': '265만원', 'service_period': '6년'},
        ),
        (
            'annual_leave_days',
            '상시 직원 14명이고 인원 집계의 상시 직원도 14명입니다. 주 소정 38시간, 근속 7년, '
            '출근율 96%이며 연차 발생일 재직 중입니다. 연차를 계산해 주세요.',
            {
                'workplace_size': '14명',
                'weekly_hours': '38시간',
                'service_period': '7년',
                'attendance_rate': '96%',
                'employed_at_accrual': '연차 발생일 재직 중',
            },
        ),
        (
            'monthly_minimum_wage',
            '주 소정근로 40시간이고 계약서에도 주 소정근로 40시간으로 적혀 있습니다. '
            '유급주휴 8시간을 포함한 2026년 최저임금 월액을 구해주세요.',
            {'year': '2026년', 'weekly_scheduled_hours': '40시간', 'weekly_paid_holiday_hours': '8시간'},
        ),
    ],
    ids=['same-monthly-wage', 'same-workforce', 'same-weekly-hours'],
)
def test_one_input_cannot_cover_repeated_source_occurrences(calculator, question, inputs):
    outcome = _route(question, calculator, inputs)
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


@pytest.mark.parametrize(
    'statement',
    [
        '평균임금 산정기간은 87일입니다.',
        '산정 제외 일수 집계는 13일입니다.',
    ],
    ids=['unextracted-wage-days', 'unextracted-excluded-days'],
)
def test_unextracted_wage_period_operand_is_not_a_measurement_window(statement):
    question = f'월급 275만원을 받고 6년 근속했습니다. {statement} 퇴직금 추정액을 구해 주세요.'
    outcome = _route(question, 'severance_pay', {'monthly_wage': '275만원', 'service_period': '6년'})
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


def test_wage_lookback_and_daily_denominator_are_not_extra_operands():
    question = (
        '입사 2024-03-02, 첫 비재직일 2026-03-02입니다. 직전 3개월 임금 총액은 810만원이고 '
        '산정기간은 90일입니다. 1일 통상임금 92000원, 주 소정 37시간입니다. '
        '근로기준법 제2조제1항제6호를 적용하면 퇴직금은 얼마인가요?'
    )
    outcome = _route(
        question,
        'severance_pay',
        {
            'hire_date': '2024-03-02',
            'retirement_date': '2026-03-02',
            'wage_total': '810만원',
            'wage_days': '90일',
            'ordinary_daily_wage': '92000원',
            'weekly_hours': '37시간',
        },
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    # max(8,100,000 / 90, 92,000) × 30 × 730 / 365.
    assert outcome.payload['results'][0]['value'] == 5520000


def test_weekly_averaging_window_is_not_a_second_service_period():
    question = (
        '4주 평균 주 소정근로 24시간이고 유급주휴 6시간입니다. 통상시급 15800원입니다. '
        '개근했고 7일 근로관계 유지했습니다. 주휴수당을 계산해 주세요.'
    )
    outcome = _route(
        question,
        'weekly_holiday_pay',
        {
            'weekly_scheduled_hours': '24시간',
            'paid_holiday_hours': '6시간',
            'hourly_wage': '15800원',
            'attendance': '개근',
            'employment': '7일 근로관계 유지',
        },
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    assert outcome.payload['results'][0]['value'] == 94800


def test_clock_readings_calendar_parts_and_statute_numbers_are_not_operands():
    question = (
        '2026년 8월 19일에 상담했습니다. 야간 시각은 22:00부터 01:00까지이고 야간 실근로 3시간입니다. '
        '상시 16명, 통상시급 17400원입니다. 근로기준법 제56조제3항의 가산분만 계산해 주세요.'
    )
    outcome = _route(
        question,
        'premium_pay',
        {
            'night_hours': '3시간',
            'staff_count': '16명',
            'ordinary_hourly_wage': '17400원',
            'include_base': '가산분만',
        },
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    assert outcome.payload['results'][0]['value'] == 26100


def test_lookback_months_do_not_hide_an_unextracted_day_count():
    question = '직전 3개월 87일 임금은 월급 230만원으로 일정했습니다. 근속 4년의 퇴직금 추정액을 계산해 주세요.'
    outcome = _route(question, 'severance_pay', {'monthly_wage': '230만원', 'service_period': '4년'})
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


def test_explicit_day_count_alongside_lookback_months_can_be_used():
    question = '직전 3개월 87일 임금은 월급 230만원으로 일정했습니다. 근속 4년의 퇴직금 추정액을 계산해 주세요.'
    outcome = _route(
        question,
        'severance_pay',
        {'monthly_wage': '230만원', 'service_period': '4년', 'wage_days': '87일'},
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    # 2,300,000 × 3 / 87 × 30 × 4, rounded once to the nearest won.
    assert outcome.payload['results'][0]['value'] == 9517241


def test_four_week_total_preserves_the_period_role_of_hours_and_days():
    question = (
        '4주간 소정근로시간 합계 112시간입니다. 같은 4주간 비교 통상근로자 소정근로일수 합계는 '
        '20일입니다. 통상시급 14600원이고 개근했습니다. 7일 근로관계 유지했습니다. 주휴수당은요?'
    )
    outcome = _route(
        question,
        'weekly_holiday_pay',
        {
            'four_week_scheduled_hours': '112시간',
            'four_week_reference_days': '20일',
            'hourly_wage': '14600원',
            'attendance': '개근',
            'employment': '7일 근로관계 유지',
        },
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    assert outcome.payload['results'][0]['value'] == 81760


def test_four_week_average_preserves_the_weekly_role_of_hours():
    question = (
        '4주간 평균 주 소정근로시간은 26시간입니다. 유급주휴 6시간 30분이며 통상시급 16400원입니다. '
        '개근했고 7일간 근로관계 유지했습니다. 주휴수당은 얼마인가요?'
    )
    outcome = _route(
        question,
        'weekly_holiday_pay',
        {
            'weekly_scheduled_hours': '26시간',
            'paid_holiday_hours': '6시간 30분',
            'hourly_wage': '16400원',
            'attendance': '개근',
            'employment': '7일간 근로관계 유지',
        },
    )
    assert outcome.payload['status'] == 'calculated', outcome.reason
    assert outcome.payload['results'][0]['value'] == 106600


def test_unextracted_period_total_is_not_hidden_by_its_weekly_average():
    question = (
        '4주간 총 144시간을 소정근로했습니다. 주 소정시간은 36시간이며 유급주휴 6시간입니다. '
        '통상시급 15150원이고 개근했습니다. 7일 근로관계 유지했습니다. 주휴수당을 계산해 주세요.'
    )
    outcome = _route(
        question,
        'weekly_holiday_pay',
        {
            'weekly_scheduled_hours': '36시간',
            'paid_holiday_hours': '6시간',
            'hourly_wage': '15150원',
            'attendance': '개근',
            'employment': '7일 근로관계 유지',
        },
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


def test_unextracted_service_weeks_are_not_an_averaging_window():
    question = (
        '입사일은 2023-09-07이고 첫 비재직일은 2026-09-07입니다. 월급 257만원입니다. '
        '근속 기간은 142주라고 기재되어 있습니다. 퇴직금 추정액을 계산해 주세요.'
    )
    outcome = _route(
        question,
        'severance_pay',
        {
            'hire_date': '2023-09-07',
            'retirement_date': '2026-09-07',
            'monthly_wage': '257만원',
        },
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')


def test_daily_scope_does_not_hide_an_unextracted_actual_night_duration():
    question = (
        '야간 3시간에 더해 하루 5시간 야간근로도 했습니다. 상시 근로자는 18명이고 통상시급은 '
        '18750원입니다. 가산분만 계산해 주세요.'
    )
    outcome = _route(
        question,
        'premium_pay',
        {
            'night_hours': '3시간',
            'staff_count': '18명',
            'ordinary_hourly_wage': '18750원',
            'include_base': '가산분만',
        },
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason in {'unresolved_input', 'ambiguous_input'}
    assert not outcome.payload.get('results')
