"""Context words must not obscure exact quantities or make uncertain ones exact."""

import json
from fractions import Fraction

import pytest

from lawbot.calc_numbers import parse_count, parse_hours, parse_quantity
from lawbot.calc_router import GUIDANCE_INSTRUCTION, prompt_suffix, route_calculation
from lawbot.labor_dates import parse_service_period


@pytest.mark.parametrize('source', ['직원 30명', '근로자 30명', '사무직원 30명입니다', '관리직 인원은 30명'])
def test_count_uses_the_unique_complete_quantity_without_a_noun_allowlist(source):
    assert parse_count(source, '명') == 30


@pytest.mark.parametrize('source', ['만 5년', '5년간', '계약을 유지한 기간은 5년', '만 5년 7개월간'])
def test_duration_preserves_a_single_compound_quantity(source):
    assert parse_service_period(source) == (5, 7 if '7개월' in source else 0, 0)


@pytest.mark.parametrize(
    ('source', 'parser', 'expected'),
    [
        ('유급으로 정한 휴일시간이 6시간 45분입니다', parse_hours, Fraction(27, 4)),
        ('채용공고의 인원수는 ４２인입니다', lambda s: parse_count(s, '명'), 42),
        ('근태대장에서 확인한 비율은 ９１％입니다', lambda s: parse_quantity(s, '%'), 91),
        ('유급휴가를 받은 기간은 7개월입니다', lambda s: parse_count(s, '개월'), 7),
    ],
)
def test_context_extraction_is_shared_across_units(source, parser, expected):
    assert parser(source) == expected


def _annual(staff='직원 30명', period='만 5년', *, question_staff=None, question_period=None):
    question = (
        f'{question_staff or staff}인 사업장입니다. {question_period or period} 근무했습니다. '
        '주 소정시간은 40시간이고 발생일에도 재직 중입니다. 출근율은 93%였습니다. 올해 연차가 며칠인가요?'
    )
    inputs = {
        'workplace_size': staff,
        'service_period': period,
        'weekly_hours': '40시간',
        'employed_at_accrual': '발생일에도 재직 중',
        'attendance_rate': '93%',
    }
    return _route(question, 'annual_leave_days', inputs)


def _route(question, calculator, inputs):
    raw = json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)
    return route_calculation(question, lambda _: raw, {calculator: ()}, reference_year=2026)


def test_router_accepts_exact_source_quantities_and_preserves_provenance():
    result = _annual()
    assert result.payload['status'] == 'calculated'
    assert result.payload['results'] == [{'label': '연차유급휴가 일수', 'value': 17, 'unit': '일'}]
    sources = {row['source'] for row in result.payload['inputs']}
    assert {'질문: 직원 30명', '질문: 만 5년'} <= sources


@pytest.mark.parametrize(
    ('staff', 'period'),
    [
        ('사무직 12명과 현장직 18명', '5년'),
        ('직원 30명 이상', '5년'),
        ('직원 28~32명', '5년'),
        ('직원 약 30명', '5년'),
        ('직원 30명', '3년 또는 5년'),
        ('직원 30명', '5년인지 아직 모릅니다'),
        ('직원 30명', '정규직 5년 및 계약직 2년'),
    ],
)
def test_multiple_ranged_or_uncertain_quantities_request_clarification(staff, period):
    result = _annual(staff, period)
    assert (result.payload['status'], result.reason) == ('guidance', 'ambiguous_input')
    assert 'results' not in result.payload


@pytest.mark.parametrize(
    ('staff', 'period', 'extra'),
    [
        ('30명', '5년', {'question_staff': '직원 130명'}),
        ('직원 30명', '5년', {'question_period': '15년'}),
        ('직원 30명', '5년', {'question_period': '5년하고 7개월'}),
        ('직원 30명', '5년', {'question_period': '4~5년'}),
    ],
)
def test_numeric_extraction_cannot_use_a_partial_source_span(staff, period, extra):
    assert _annual(staff, period, **extra).payload['status'] != 'calculated'


@pytest.mark.parametrize(
    ('question', 'calculator', 'inputs'),
    [
        ('근로자 수가 서른명일 때 연차는 며칠인가요?', 'annual_leave_days', {'workplace_size': '서른명'}),
        (
            '2026년 총급여 4천만원입니다. 의료비 공제액은 얼마인가요?',
            'medical_expense_credit',
            {'gross_pay': '4천만원입니다'},
        ),
        (
            '2026년 2월 30일에 퇴직했는데 퇴직금은 얼마인가요?',
            'severance_pay',
            {'retirement_date': '2026년 2월 30일'},
        ),
    ],
)
def test_selected_calculator_conversion_errors_return_actionable_guidance(question, calculator, inputs):
    result = _route(question, calculator, inputs)
    assert (result.payload['status'], result.reason) == ('guidance', 'unresolved_input')
    assert result.payload['calculator'] == calculator and result.payload['articles']
    assert '다시' in result.payload['message']
    assert 'results' not in result.payload
    assert prompt_suffix(result.payload).endswith(GUIDANCE_INSTRUCTION)


@pytest.mark.parametrize('prefix', ['', '계약서에 적힌 '])
def test_extracting_money_does_not_change_an_hourly_wage_into_a_monthly_wage(prefix):
    source = f'{prefix}시급 12,300원'
    result = _route(
        f'{source}으로 4년 일했습니다. 퇴직금은 얼마인가요?',
        'severance_pay',
        {'monthly_wage': source, 'service_period': '4년'},
    )
    assert result.payload['status'] == 'guidance'
    assert result.reason == 'ambiguous_input'


@pytest.mark.parametrize('prefix', ['이번 ', '근로계약에서 정한 '])
def test_extracting_hours_retains_the_quantity_role_after_context_words(prefix):
    source = f'{prefix}유급 주휴시간은 6시간'
    question = (
        f'직원 19명이며 근속 4년입니다. {source}입니다. 연차 발생일 재직 중이고 출근율 91%라면 연차는 며칠인가요?'
    )
    result = _route(
        question,
        'annual_leave_days',
        {
            'workplace_size': '19명',
            'service_period': '4년',
            'weekly_hours': source,
            'employed_at_accrual': '연차 발생일 재직 중',
            'attendance_rate': '91%',
        },
    )
    assert (result.payload['status'], result.reason) == ('guidance', 'ambiguous_input')


def test_two_money_fields_cannot_reassign_one_labeled_source_to_an_unrelated_equal_amount():
    source = '별도 상여금 110만원'
    question = f'월급 280만원으로 4년 일했습니다. {source}, 개인 생활비 110만원입니다. 퇴직금은요?'
    result = _route(
        question,
        'severance_pay',
        {'monthly_wage': '280만원', 'service_period': '4년', 'annual_bonus': source, 'annual_unused_leave_pay': source},
    )
    assert (result.payload['status'], result.reason) == ('guidance', 'ambiguous_input')


def test_two_hour_fields_cannot_reassign_one_labeled_source_to_an_unrelated_equal_duration():
    source = '야간근로 3시간'
    question = f'직원 17명이고 통상시급 13,700원입니다. {source}, 개인 여가시간 3시간입니다. 가산분만 얼마인가요?'
    result = _route(
        question,
        'premium_pay',
        {
            'staff_count': '17명',
            'ordinary_hourly_wage': '13,700원',
            'night_hours': source,
            'overtime_hours': source,
            'include_base': '가산분만',
        },
    )
    assert (result.payload['status'], result.reason) == ('guidance', 'ambiguous_input')
