"""Quantity context must assert a value before surrounding prose can be removed."""

import json
from fractions import Fraction

import pytest

from lawbot.calc_numbers import AmbiguousQuantity, parse_count, parse_hours, quantity_excerpt
from lawbot.calc_router import route_calculation
from lawbot.labor_dates import parse_service_period


@pytest.mark.parametrize(
    ('parser', 'source'),
    [
        (lambda text: parse_count(text, '명'), '직원 4명이 아닙니다'),
        (parse_hours, '주 소정근로는 12시간이 아닙니다'),
        (parse_service_period, '5년간 근무한 것은 아닙니다'),
        (parse_service_period, '5년간 근무하지 않았습니다'),
        (parse_hours, '12시간을 근무하지 못했습니다'),
        (lambda text: parse_count(text, '명'), '근무 인원은 30명보다 적습니다'),
        (lambda text: parse_count(text, '명'), '사업장 인원이 30명보다 많습니다'),
        (parse_hours, '실근로가 9시간을 넘습니다'),
        (parse_service_period, '14개월에 못 미치는 기간'),
        (lambda text: quantity_excerpt(text, 'money'), '약 삼백만원'),
        (parse_service_period, '대략 칠년 근속'),
    ],
)
def test_nonexact_quantity_context_is_not_discarded(parser, source):
    with pytest.raises(AmbiguousQuantity):
        parser(source)


@pytest.mark.parametrize(
    ('parser', 'source', 'expected'),
    [
        (parse_hours, '휴게 제외 8시간', Fraction(8)),
        (parse_hours, '휴게를 포함하지 않은 실근로 8시간', Fraction(8)),
        (lambda text: parse_count(text, '명'), '근무 인원은 23명으로 확인', 23),
        (parse_service_period, '만 6년간 근속', (6, 0, 0)),
        (parse_service_period, '퇴사하지 않고 근무한 기간은 7년', (7, 0, 0)),
    ],
)
def test_exact_quantity_context_still_parses(parser, source, expected):
    assert parser(source) == expected


@pytest.mark.parametrize('workforce', ['직원 4명이 아닙니다', '사업장 인원이 30명보다 많습니다'])
def test_nonexact_workforce_routes_to_guidance(workforce):
    question = (
        f'{workforce}. 주 소정근로 37시간, 근속기간 4년을 채웠고 연차 발생일 재직 중입니다. '
        '출근율 93%이면 연차는 며칠인가요?'
    )
    inputs = {
        'workplace_size': workforce,
        'weekly_hours': '37시간',
        'service_period': '4년',
        'employed_at_accrual': '연차 발생일 재직 중',
        'attendance_rate': '93%',
    }
    raw = json.dumps({'calculator': 'annual_leave_days', 'inputs': inputs})
    outcome = route_calculation(question, lambda _: raw, {'annual_leave_days': ()}, reference_year=2026)
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason == 'ambiguous_input'
    assert not outcome.payload.get('results')


def test_approximate_written_amount_routes_to_guidance():
    question = '월급 약 삼백만원으로 4년 일했습니다. 퇴직금을 계산해 주세요.'
    inputs = {'monthly_wage': '약 삼백만원', 'service_period': '4년'}
    raw = json.dumps({'calculator': 'severance_pay', 'inputs': inputs})
    outcome = route_calculation(question, lambda _: raw, {'severance_pay': ()}, reference_year=2026)
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason == 'ambiguous_input'
    assert not outcome.payload.get('results')
