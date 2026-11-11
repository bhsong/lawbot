import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from lawbot.api_client import ModelServerError, ModelServerTimeout, RouterTimeout
from lawbot.calc_gate import is_calculation_question
from lawbot.calc_numbers import amount_spans, parse_amount, parse_quantity, parse_year
from lawbot.calc_router import ROUTER_SYSTEM, check_conclusion, route_calculation
from lawbot_common.api import SERVER_BUSY

MEDICAL = 'medical_expense_credit'
QUESTION = '2026년 의료비 300만 원, 실손보험금 100만 원, 총급여 5천만 원입니다. 의료비 세액공제액은 얼마인가요?'
INPUTS = {'gross_pay': '5천만 원', 'medical_expenses': '300만 원', 'insurance_reimbursement': '100만 원'}


@pytest.mark.parametrize(
    ('source', 'value'),
    [
        ('5천만 원', 50_000_000),
        ('3백만', 3_000_000),
        ('2억 3천 5백만 6,800 원', 235_006_800),
        ('1.25억원', 125_000_000),
        ('삼천오백만 원', 35_000_000),
        ('０원', 0),
        ('37,500원', 37_500),
        ('천원', 1000),
        ('3만 5천 원', 35_000),
        ('1만 7원', 10_007),
    ],
)
def test_parse_amount(source, value):
    assert parse_amount(source) == value


@pytest.mark.parametrize(
    'source',
    [
        '5,00원',
        '300~500만원',
        '-10만원',
        '3만원~4만원',
        '1만2',
        '5%',
        '1.25원',
        '총 3만원',
        '100,000만원 + 20원',
        '만억',
        '1억원 미만',
    ],
)
def test_invalid_amount(source):
    with pytest.raises(ValueError):
        parse_amount(source)


def test_other_units_and_conclusion_amount_spans():
    assert parse_quantity('３.５％', '%') == Decimal('3.5')
    assert parse_quantity('6개월', '개월') == 6
    assert parse_year('2026년도') == 2026
    assert list(amount_spans('결정세액은 **75,000원**이고 지방소득세는 7천 5백 원입니다.')) == [
        ('75,000원', 75_000),
        ('7천 5백 원', 7_500),
    ]


def test_gate_on_synthetic_val():
    data = Path(__file__).with_name('fixtures') / 'calc_router_val.json'
    cases = json.loads(data.read_text(encoding='utf-8'))
    assert len(cases) == 100 and len({case['question'] for case in cases}) == len(cases)
    assert {case['kind'] for case in cases} == {
        'medical',
        'midyear',
        'unsupported',
        'general',
        'lookup',
        'missing',
        'ambiguous',
    }
    misses = [case['id'] for case in cases if is_calculation_question(case['question']) != (case['kind'] != 'general')]
    assert misses == []


def test_router_prompt_distinguishes_computation_from_looking_up_a_value():
    assert (
        '사용자가 준 금액·기간·인원수로 금액·일수·세액을 구해야 답할 수 있는 질문은 계산이다. '
        '지원하는 계산기가 없으면(일반 연말정산, 퇴직소득세 등) {"calculator":"none","kind":"calculation"}.'
    ) in ROUTER_SYSTEM
    assert (
        '법령에 적힌 값·기한·요건을 묻는 질문은 조회다. '
        '질문에 숫자가 있어도 그 숫자로 계산하지 않으면 {"calculator":"none","kind":"lookup"}.'
    ) in ROUTER_SYSTEM


def test_gate_positive_lookup_cases_route_to_rag():
    cases = json.loads((Path(__file__).with_name('fixtures') / 'calc_router_val.json').read_text(encoding='utf-8'))
    lookups = [case for case in cases if case['kind'] == 'lookup']
    assert len(lookups) == 6

    def classify_lookup(_):
        return '{"calculator":"none","kind":"lookup"}'

    for case in lookups:
        assert is_calculation_question(case['question'])
        routed = route_calculation(case['question'], classify_lookup, {})
        assert routed.payload == {'calculator': None, 'status': 'lookup'}
        assert routed.reason == 'lookup_question'


def classify(inputs, calculator=MEDICAL):
    return lambda _: json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)


def test_routing_uses_original_spans_and_explicit_year():
    routed = route_calculation(QUESTION, classify(INPUTS), {MEDICAL: ()}, reference_year=2027)
    assert routed.payload['status'] == 'calculated'
    assert routed.payload['results'] == [{'label': '의료비 세액공제', 'value': 75_000}]
    assert {row['source'] for row in routed.payload['inputs']} >= {'질문: 5천만 원', '질문: 2026년'}
    assert routed.reason == 'selected'
    assert routed.elapsed_ms >= 0


@pytest.mark.parametrize(
    ('response', 'reason'),
    [
        ('{"calculator":', 'invalid_json'),
        ('{"calculator":"none","calculator":"none","inputs":{}}', 'invalid_json'),
        ('{"calculator":"none","kind":"calculation"}', 'unsupported_question'),
        ('{"calculator":"none","inputs":{}}', 'invalid_schema'),
        ('{"calculator":"none","kind":"lookup","inputs":{"gross_pay":"5천만 원"}}', 'invalid_schema'),
        ('{"calculator":"other","inputs":{}}', 'invalid_schema'),
        ('{"calculator":"medical_expense_credit","inputs":{"gross_pay":50000000}}', 'invalid_schema'),
        (classify(INPUTS | {'gross_pay': '50000000'})(None), 'span_not_in_question'),
        (classify(INPUTS | {'gross_pay': '5'})(None), 'incomplete_amount_span'),
        (classify(INPUTS | {'gross_pay': '5천만 원입니다'})(None), 'conversion_failed'),
    ],
)
def test_router_fails_closed_once(response, reason):
    calls = []

    def complete(question):
        calls.append(question)
        return response

    route = route_calculation(QUESTION, complete, {MEDICAL: ()})
    assert route.payload['status'] == 'none'
    assert route.reason == reason
    assert calls == [QUESTION]


@pytest.mark.parametrize(
    ('error', 'reason'),
    [
        (ModelServerTimeout('test'), 'timeout'),
        (RouterTimeout(httpx.ReadTimeout('test')), 'read_timeout'),
        (ModelServerError('test', 503, SERVER_BUSY), 'queue_full'),
        (ModelServerError('test', 500, 'server_error'), 'model_error'),
    ],
)
def test_router_timeout_and_queue_rejection_without_retry(error, reason):
    calls = []

    def timeout(question):
        calls.append(question)
        raise error

    result = route_calculation(QUESTION, timeout, {MEDICAL: ()})
    assert result.reason == reason and result.payload['status'] == 'none'
    assert result.payload['message'].startswith('계산 판정을 일시적으로 하지 못했습니다.')
    assert calls == [QUESTION]


def test_disabled_and_missing_and_ambiguous():
    missing = route_calculation(
        QUESTION.replace('총급여 5천만 원', '총급여 미상'), classify({'medical_expenses': '300만 원'}), {MEDICAL: ()}
    )
    assert missing.payload['status'] == 'guidance'
    assert '총급여' in missing.payload['message']
    unavailable = route_calculation(QUESTION, classify(INPUTS), {MEDICAL: ('소득세법 제59조의4',)})
    assert unavailable.payload['status'] == 'none' and unavailable.reason == 'corpus_mismatch'
    ambiguous = route_calculation(QUESTION.replace('2026년', '2025년 또는 2026년'), classify(INPUTS), {MEDICAL: ()})
    assert ambiguous.payload['status'] == 'guidance' and ambiguous.reason == 'ambiguous_input'


def test_router_cannot_treat_omitted_special_medical_cost_as_general():
    question = '2026년 총급여 5천만 원, 난임시술비 300만 원의 의료비 세액공제액은 얼마인가요?'
    inputs = {'gross_pay': '5천만 원', 'medical_expenses': '300만 원'}
    omitted = route_calculation(question, classify(inputs), {MEDICAL: ()})
    assert omitted.payload['status'] == 'guidance'
    included = route_calculation(question, classify(inputs | {'medical_category': '난임'}), {MEDICAL: ()})
    assert included.payload['results'][0]['value'] == 450_000


@pytest.mark.parametrize(('total', 'status'), [('700만원', 'guidance'), ('100만원', 'calculated')])
def test_combined_medical_cost_with_different_rates(total, status):
    question = f'2026년 총급여 5000만원, 난임시술비와 일반 의료비를 합쳐서 {total}인데 세액공제액은 얼마인가요?'
    inputs = {'gross_pay': '5000만원', 'medical_expenses': total, 'medical_category': '난임시술비'}
    result = route_calculation(question, classify(inputs), {MEDICAL: ()})
    assert result.payload['status'] == status
    if status == 'calculated':
        assert result.payload['results'][0]['value'] == 0


def test_optional_reimbursement_stated_in_question_cannot_default_to_zero():
    unaccounted = route_calculation(
        QUESTION, classify({'gross_pay': '5천만 원', 'medical_expenses': '300만 원'}), {MEDICAL: ()}
    )
    assert unaccounted.reason == 'unresolved_input' and unaccounted.payload['status'] == 'guidance'


def test_amount_cannot_be_a_year_or_month_excerpt():
    question = '2026\n년 중 12개월 의료비 300만원의 세액공제액은 얼마인가요?'
    result = route_calculation(question, classify({'gross_pay': '12', 'medical_expenses': '300만원'}), {MEDICAL: ()})
    assert result.reason == 'incomplete_amount_span'


@pytest.mark.parametrize(('amount', 'excerpt'), [('삼백만 원', '백만 원'), ('삼백오십만 원', '삼백')])
def test_hangul_amount_must_not_be_truncated(amount, excerpt):
    question = f'2026년 총급여 오천만 원, 의료비 {amount}입니다. 의료비 세액공제액은 얼마인가요?'
    inputs = {'gross_pay': '오천만 원', 'medical_expenses': excerpt}
    incomplete = route_calculation(question, classify(inputs), {MEDICAL: ()})
    assert incomplete.payload['status'] == 'none'
    assert incomplete.reason == 'incomplete_amount_span'
    complete = route_calculation(question, classify(inputs | {'medical_expenses': amount}), {MEDICAL: ()})
    assert complete.payload['status'] == 'calculated'


@pytest.mark.parametrize('care', ['5만원', '오만 원'])
def test_separate_long_term_care_premium_must_be_extracted(care):
    calculator = 'midyear_income_tax'
    question = (
        f'2026년 중도퇴사까지 총급여 2000만원, 건강보험료 50만원, 장기요양보험료 {care}입니다. 소득세는 얼마인가요?'
    )
    inputs = {'gross_pay': '2000만원', 'health_premium': '50만원'}
    incomplete = route_calculation(question, classify(inputs, calculator), {calculator: ()})
    assert incomplete.payload['status'] == 'guidance'
    assert '장기요양보험료' in incomplete.payload['message']
    complete = route_calculation(
        question, classify(inputs | {'long_term_care_premium': care}, calculator), {calculator: ()}
    )
    assert complete.payload['status'] == 'calculated'
    assert any(
        row['label'] == '장기요양보험료' and row['value'] == 50_000 and row['source'] == f'질문: {care}'
        for row in complete.payload['inputs']
    )


@pytest.mark.parametrize(
    'label', ['건강·장기요양보험료', '건강 / 장기요양 보험료', '건강 및 장기요양보험료 합계는', '건강장기요양보험료']
)
def test_combined_health_and_long_term_care_premium_must_be_extracted_once(label):
    calculator = 'midyear_income_tax'
    question = f'올해 총급여 4천만원, {label} 200만원일 때 중도퇴사 근로소득세 계산해줘'
    inputs = {'gross_pay': '4천만원'}
    incomplete = route_calculation(question, classify(inputs, calculator), {calculator: ()}, reference_year=2026)
    assert incomplete.payload['status'] == 'guidance' and incomplete.reason == 'unresolved_input'
    assert '건강·장기요양보험료' in incomplete.payload['message']
    complete = route_calculation(
        question, classify(inputs | {'health_premium': '200만원'}, calculator), {calculator: ()}, reference_year=2026
    )
    assert complete.payload['status'] == 'calculated'
    # At ₩40M gross, A deducts the single ₩2M premium under §52 and determines ₩1,843,500.
    assert complete.payload['results'][0] == {'label': '결정세액', 'value': 1_843_500}


@pytest.mark.parametrize(
    ('text', 'status'),
    [
        ('【결론】 공제액은 7만 5천 원입니다.\n【설명】의료비는 300만원입니다.', 'match'),
        ('【결론】 공제액은 225,000원입니다.\n【설명】75,000원으로 계산됩니다.', 'mismatch'),
        ('【결론】 75,000원 또는 0원입니다.', 'mismatch'),
        ('【결론】 공제액이 있습니다.', 'missing'),
        ('의료비 세액공제액은 **75,000원**입니다.', 'match'),
        ('【결론】\n- 75,000원', 'match'),
    ],
)
def test_conclusion_check_ignores_other_sections(text, status):
    payload = {'results': [{'label': '의료비 세액공제', 'value': 75_000}], 'inputs': [], 'steps': []}
    assert check_conclusion(text, payload) == status
