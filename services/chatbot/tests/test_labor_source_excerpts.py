import json
from fractions import Fraction

import pytest

from lawbot.calc_numbers import parse_count, parse_hours, parse_quantity
from lawbot.calc_router import route_calculation
from lawbot.labor_dates import parse_service_period


def _route(question, calculator, inputs):
    raw = json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)
    return route_calculation(question, lambda _: raw, {calculator: ()}, reference_year=2026)


@pytest.mark.parametrize(
    ('parser', 'source', 'expected'),
    [
        (parse_hours, '매주 소정근로 29시간 20분', Fraction(88, 3)),
        (parse_hours, '8주 동안의 평균 주 31시간', Fraction(31)),
        (parse_hours, '유급 주휴시간은 5시간', Fraction(5)),
        (lambda text: parse_count(text, '명'), '상시 근로자 23인', 23),
        (lambda text: parse_count(text, '일'), '매주 6일', 6),
        (lambda text: parse_count(text, '개월'), '개근 9개월', 9),
        (lambda text: parse_quantity(text, '%'), '출근율 86%', 86),
        (parse_service_period, '4년 7개월 근속', (4, 7, 0)),
    ],
)
def test_shared_labor_parsers_accept_exact_quantities_with_context(parser, source, expected):
    assert parser(source) == expected


@pytest.mark.parametrize(
    ('question', 'extracted', 'result'),
    [
        ('2025년 월 최저임금 적용기준시간이 173시간 30분이라고 합니다. 월 환산액을 구해주세요.', '173', 1_740_205),
        (
            '2025년 월 최저임금 적용기준시간이 172시간이고 별도로 172분이라는 시간도 적혀 있네요. 월 환산액은?',
            '172',
            None,
        ),
        ('2025년 월 최저임금 적용기준시간은 113시간입니다. 월 환산액은요?', '13', None),
    ],
)
def test_restore_a_missing_time_unit_from_a_unique_complete_question_span(question, extracted, result):
    outcome = _route(question, 'monthly_minimum_wage', {'monthly_basis_hours': extracted})
    if result is None:
        assert outcome.payload['status'] == 'guidance'
        assert outcome.reason == 'ambiguous_input'
    else:
        assert outcome.payload['results'] == [{'label': '월 최저임금', 'value': result, 'unit': '원'}]
        assert any(row['source'] == '질문: 173시간 30분' for row in outcome.payload['inputs'])


def test_restoring_the_original_unit_preserves_a_missing_required_fact():
    question = '주당 소정은 27시간이고 5년째 근무 중입니다. 연차유급휴가를 얼마나 받나요?'
    outcome = _route(question, 'annual_leave_days', {'weekly_hours': '27', 'service_period': '5년'})
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason == 'missing_required'


def test_known_auxiliary_workplace_size_does_not_affect_weekly_holiday_pay():
    question = (
        '상시 3인 매장입니다. 시급 12,650원, 주 소정 36시간, 유급주휴 6시간이며 '
        '해당 주 개근했고 7일간 근로관계 유지했습니다. 주휴수당이 궁금합니다.'
    )
    outcome = _route(
        question,
        'weekly_holiday_pay',
        {
            'workplace_size': '상시 3인',
            'hourly_wage': '시급 12,650원',
            'weekly_scheduled_hours': '36시간',
            'paid_holiday_hours': '6시간',
            'attendance': '개근',
            'employment': '7일간 근로관계 유지',
        },
    )
    assert outcome.payload['results'] == [{'label': '주휴수당', 'value': 75_900, 'unit': '원'}]


def test_auxiliary_workforce_alias_cannot_hide_conflicting_required_values():
    question = '상시 인원이 3명인지 9명인지 아직 확인 전입니다. 주 33시간이면 연차는 생기나요?'
    outcome = _route(
        question, 'annual_leave_days', {'workplace_size': '3명', 'staff_count': '9명', 'weekly_hours': '33시간'}
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason == 'ambiguous_input'


def test_unknown_out_of_schema_field_still_rejects_the_extraction():
    outcome = _route('2026년 월 기준 180시간이면 최저임금 월액은?', 'monthly_minimum_wage', {'unrelated': '180시간'})
    assert outcome.payload['status'] == 'none'
    assert outcome.reason == 'invalid_schema'


def test_contextual_weekly_holiday_hours_are_not_a_weekly_schedule():
    outcome = _route(
        '유급 주휴 7시간이 잡혀 있고 주 소정시간은 아직 모릅니다. 주휴수당은 어떻게 되나요?',
        'weekly_holiday_pay',
        {'weekly_scheduled_hours': '유급 주휴 7시간'},
    )
    assert outcome.payload['status'] == 'guidance'
    assert outcome.reason == 'ambiguous_input'
