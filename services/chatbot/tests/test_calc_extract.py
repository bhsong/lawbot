import json

import httpx
import openai
import pytest

from lawbot.api_client import request_timeout
from lawbot.calc_extract import (
    SELECT_MAX_TOKENS,
    SELECT_SYSTEM,
    compose_route,
    label_system,
    marked_question,
    quantity_tokens,
)
from lawbot.calc_router import route_calculation
from lawbot.llm import LLM


def route(question, selection, labels, calculator=None):
    raw = compose_route(question, selection, labels)
    return route_calculation(question, lambda _: raw, {calculator or selection: ()}, reference_year=2026)


def test_tokens_keep_whole_quantities_with_units():
    question = '월급 342만 7,500원, 2026년 3월 1일 입사, 주 14시간 54분, 1주년, 4주, 상시 5명, 출근율 86.5%'
    assert [text for text, _ in quantity_tokens(question)] == [
        '342만 7,500원',
        '2026년 3월 1일',
        '14시간 54분',
        '1주년',
        '4주',
        '5명',
        '86.5%',
    ]
    assert marked_question('주 5일 근무') == '주 [1:5일] 근무\n\n(숫자 1개: 1~1번)'
    assert marked_question('연차는 언제 생기나요?').endswith('(숫자 없음)')


@pytest.mark.parametrize(
    ('selection', 'expected'),
    [
        ('lookup', {'calculator': 'none', 'kind': 'lookup'}),
        ('unsupported', {'calculator': 'none', 'kind': 'calculation'}),
    ],
)
def test_non_calculation_selection(selection, expected):
    assert json.loads(compose_route('질문 1개', selection)) == expected


def test_unusable_selection_returns_none():
    assert compose_route('질문', None) is None


def test_every_number_needs_a_label():
    question = '통상시급 12,000원, 주 소정 40시간, 비교 근로자 주 5일입니다.'
    composed = json.loads(compose_route(question, 'weekly_holiday_pay', '1=hourly_wage\n2=weekly_scheduled_hours'))
    assert composed['ambiguity'] == '5일'
    composed = json.loads(compose_route(question, 'weekly_holiday_pay', '1=hourly_wage\n2=?\n3=x'))
    assert composed['ambiguity'] == '40시간'


def test_label_whose_unit_cannot_fill_the_field_is_not_an_input():
    question = '4주 제 소정근로시간 합계 80시간, 통상시급 10,000원'
    composed = json.loads(
        compose_route(
            question, 'weekly_holiday_pay', '1=four_week_scheduled_hours\n2=four_week_scheduled_hours\n3=hourly_wage'
        )
    )
    assert composed['inputs'] == {'four_week_scheduled_hours': '80시간', 'hourly_wage': '10,000원'}
    assert 'ambiguity' not in composed


def test_number_labeled_with_fact_field_is_ignored():
    question = '7일 동안 근로관계가 유지됐습니다. 통상시급 10,000원'
    labels = '1=employment\n2=hourly_wage\nemployment=예|[1:7일] 동안 근로관계가 유지됐습니다'
    composed = json.loads(compose_route(question, 'weekly_holiday_pay', labels))
    assert composed['inputs'] == {'employment': '7일 동안 근로관계가 유지됐습니다', 'hourly_wage': '10,000원'}
    assert composed['facts'] == {'employment': True}


def test_two_different_numbers_for_one_field_are_ambiguous():
    question = '상시 근로자가 4명이라는 자료와 6명이라는 자료가 있습니다.'
    composed = json.loads(compose_route(question, 'premium_pay', '1=staff_count\n2=staff_count'))
    assert composed['ambiguity'] == '6명'


def test_fact_lines_map_polarity_and_drop_evidence_not_in_question():
    question = '이번 주 개근했고 근로관계 유지 여부는 확인 중입니다. 통상시급 9,000원'
    labels = '1=hourly_wage\nattendance=예|이번 주 개근했고\nemployment=불명|근로관계 유지 여부는 확인 중\nemployment=예|없는 구절'
    composed = json.loads(compose_route(question, 'weekly_holiday_pay', labels))
    assert composed['facts'] == {'attendance': True, 'employment': None}


def test_unknown_numeric_value_becomes_ambiguity():
    question = '총급여 3500만원, 원천징수액 50만원이 소득세인지 몰라요.'
    labels = '1=gross_pay\n2=?\nwithheld_income_tax=불명|원천징수액 [2:50만원]이 소득세인지 몰라요'
    composed = json.loads(compose_route(question, 'midyear_income_tax', labels))
    assert '50만원' in composed['ambiguity']
    assert 'facts' not in composed


def test_label_prompt_lists_numeric_and_fact_fields_separately():
    prompt = label_system('weekly_holiday_pay')
    numbers, facts = prompt.split('(예/아니오) 항목:')
    assert 'hourly_wage: 통상시급' in numbers and 'attendance' not in numbers.split('숫자 항목:')[1]
    assert 'attendance: 해당 주 소정근로일을 개근했는지' in facts


def test_model_polarity_replaces_regex_reading_of_colloquial_employment():
    question = (
        '이번 입사기념일로 근속 7년을 채웠습니다. 연차가 생기는 날에도 계속 회사에 다니고 있어요. '
        '직전 1년 출근율은 86.5%, 상시 직원은 정확히 23명이고 4주 평균 주 소정근로시간은 37시간입니다. '
        '이번에 발생하는 법정 연차는 며칠이죠?'
    )
    labels = (
        '1=service_period\n2=x\n3=attendance_rate\n4=workplace_size\n5=x\n6=weekly_hours\n'
        'employed_at_accrual=예|연차가 생기는 날에도 계속 회사에 다니고 있어요'
    )
    routed = route(question, 'annual_leave_days', labels)
    assert routed.reason == 'selected'
    assert routed.payload['results'][0]['value'] == 18


def test_clause_with_opposite_certain_polarity_vetoes_model_fact():
    question = '이번 주 소정근로일을 개근했고 7일간 재직했습니다. 통상시급 10,000원, 유급주휴 8시간, 주 40시간.'
    labels = '1=x\n2=hourly_wage\n3=paid_holiday_hours\n4=weekly_scheduled_hours\nattendance=아니오|이번 주 소정근로일을 개근했고\nemployment=예|7일간 재직했습니다'
    routed = route(question, 'weekly_holiday_pay', labels)
    assert routed.payload['status'] == 'guidance'
    assert routed.reason == 'ambiguous_input'
    assert '개근' in routed.payload['message']


def test_unknown_fact_asks_for_that_fact():
    question = '통상시급 12,786원, 주 32시간, 유급주휴 6시간 24분, 7일 재직. 결근 처리인지 아직 모릅니다.'
    labels = '1=hourly_wage\n2=weekly_scheduled_hours\n3=paid_holiday_hours\n4=x\nattendance=불명|결근 처리인지 아직 모릅니다\nemployment=예|7일 재직'
    routed = route(question, 'weekly_holiday_pay', labels)
    assert routed.payload['status'] == 'guidance'
    assert routed.payload['message'] == '해당 주 소정근로일 개근 여부를 확인해 주세요.'


def test_facts_must_name_fact_fields_with_evidence():
    question = '통상시급 10,000원'
    raw = json.dumps(
        {'calculator': 'weekly_holiday_pay', 'inputs': {'hourly_wage': '10,000원'}, 'facts': {'hourly_wage': True}}
    )
    routed = route_calculation(question, lambda _: raw, {'weekly_holiday_pay': ()}, reference_year=2026)
    assert routed.reason == 'invalid_schema'


def test_dates_decide_severance_eligibility_over_an_inferred_fact():
    question = (
        '2025-11-02 입사, 마지막 재직일 2026-10-31, 주 소정 40시간입니다. 이 근속기간의 법정 퇴직금은 얼마인가요?'
    )
    labels = '1=hire_date\n2=last_employed_date\n3=weekly_hours\nservice_qualifies=예|이 근속기간'
    routed = route(question, 'severance_pay', labels)
    assert routed.reason == 'selected'
    assert routed.payload['results'] == [{'label': '법정 최소 퇴직금', 'value': 0}]


def test_explicit_one_year_statement_conflicting_with_dates_is_ambiguous():
    question = '1년 이상 일했어요. 2025-11-02 입사, 마지막 재직일 2026-10-31, 주 40시간, 하한 반영 일급 10만원.'
    labels = '1=x\n2=hire_date\n3=last_employed_date\n4=weekly_hours\n5=adjusted_daily_wage\nservice_qualifies=예|1년 이상 일했어요'
    routed = route(question, 'severance_pay', labels)
    assert routed.payload['status'] == 'guidance'
    assert routed.reason == 'ambiguous_input'


def test_monthly_basis_described_apart_from_its_label_is_accepted():
    question = '147.25시간이 유급주휴까지 반영한 월 최저임금 적용기준시간입니다. 2026년 월 최저임금은 얼마인가요?'
    routed = route(question, 'monthly_minimum_wage', '1=monthly_basis_hours\n2=year')
    assert routed.reason == 'selected'
    assert routed.payload['results'][0]['value'] == 1519620


def test_monthly_basis_described_as_hours_worked_is_rejected():
    question = '2026년에 월 100시간 실제 근무했습니다. 월 최저임금은 얼마인가요?'
    routed = route(question, 'monthly_minimum_wage', '1=year\n2=monthly_basis_hours')
    assert routed.payload['status'] == 'guidance'


def test_premium_only_request_judged_by_model():
    question = '상시근로자 9명이고 통상시급은 12,000원입니다. 휴일 하루에 휴게 제외 10시간 일했고 그중 야간은 4시간입니다. 가산수당을 구해 주세요.'
    labels = '1=staff_count\n2=ordinary_hourly_wage\n3=holiday_hours\n4=night_hours\ninclude_base=아니오|가산수당을 구해 주세요'
    routed = route(question, 'premium_pay', labels)
    assert routed.reason == 'selected'
    assert routed.payload['results'] == [{'label': '법정 가산수당', 'value': 96000, 'unit': '원'}]


def test_llm_router_makes_select_then_label_requests():
    requests = []
    replies = iter(['weekly_holiday_pay', '1=hourly_wage\n2=x'])

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                'id': 'route',
                'object': 'chat.completion',
                'created': 0,
                'model': 'pro',
                'choices': [
                    {'index': 0, 'message': {'role': 'assistant', 'content': next(replies)}, 'finish_reason': 'stop'}
                ],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12},
            },
        )

    metrics = {}
    with openai.OpenAI(
        base_url='http://127.0.0.1:9/v1',
        api_key='fixture',
        max_retries=0,
        timeout=request_timeout(90),  # pyright: ignore[reportArgumentType]
        http_client=httpx.Client(transport=httpx.MockTransport(reply)),  # pyright: ignore[reportArgumentType]
    ) as client:
        raw = LLM('pro', client, 'system').route_calculation('시급 10,000원, 주 5일', metrics=metrics)

    assert json.loads(raw) == {'calculator': 'weekly_holiday_pay', 'inputs': {'hourly_wage': '10,000원'}}
    assert [body['messages'][0]['content'] for body in requests] == [SELECT_SYSTEM, label_system('weekly_holiday_pay')]
    assert requests[1]['messages'][1]['content'] == '시급 [1:10,000원], 주 [2:5일]\n\n(숫자 2개: 1~2번)'
    assert [body['max_tokens'] for body in requests] == [SELECT_MAX_TOKENS, 96]
    assert metrics == {'router_calls': 2, 'router_prompt_tokens': 20, 'router_completion_tokens': 4}


def test_amount_without_won_keeps_its_korean_unit():
    question = '총급여 5000만, 본인 의료비 300만 썼어요. 의료비 세액공제는?'
    assert [text for text, _ in quantity_tokens(question)] == ['5000만', '300만']
    routed = route(question, 'medical_expense_credit', '1=gross_pay\n2=unlimited_expenses')
    assert routed.reason == 'selected'


def test_unknown_label_is_ambiguous_not_dropped():
    question = '상시 10명, 통상시급 10,000원, 평일 연장 2시간, 밤에 일한 3시간(야간근로입니다). 가산분만 계산해 주세요.'
    labels = '1=staff_count\n2=ordinary_hourly_wage\n3=overtime_hours\n4=night_hour'
    assert json.loads(compose_route(question, 'premium_pay', labels))['ambiguity'] == '3시간'
    assert route(question, 'premium_pay', labels).payload['status'] == 'guidance'


def test_decimal_hours_described_as_worked_are_rejected():
    question = '2026년에 월 100.5시간 실제 근무했습니다. 월 최저임금은 얼마인가요?'
    routed = route(question, 'monthly_minimum_wage', '1=year\n2=monthly_basis_hours')
    assert routed.payload['status'] == 'guidance'
