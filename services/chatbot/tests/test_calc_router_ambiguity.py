import json
from pathlib import Path

import pytest

from lawbot.calc_router import route_calculation
from lawbot.calculators import ARTICLE_HASHES, MEDICAL

_VAL_PATH = Path(__file__).with_name('fixtures') / 'calc_router_val.json'
_VAL_CASES = {case['id']: case for case in json.loads(_VAL_PATH.read_text(encoding='utf-8'))}


# Verbatim router responses recorded in docs/evals/2026-11-11-calc-router-live.md.
@pytest.mark.parametrize(
    ('case_id', 'response'),
    [
        (
            'CR091',
            '{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5000만원","medical_expenses":"1300만원"},'
            '"ambiguity":"의료비 1300만원을 썼는데 누가 쓴 비용인지 확인 전입니다"}',
        ),
        (
            'CR093',
            '{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5000만원","medical_expenses":"300만원"},'
            '"ambiguity":"2025년 또는 2026년 귀속인지 모르겠어요"}',
        ),
        (
            'CR094',
            '{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5천만원","medical_expenses":"200~400만원"},'
            '"ambiguity":"200~400만원"}',
        ),
        (
            'CR096',
            '{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5000만원","medical_expenses":"50만원",'
            '"insurance_reimbursement":"100만원"},"kind":"calculation","ambiguity":"50만원"}',
        ),
        (
            'CR097',
            '{"calculator":"midyear_income_tax","kind":"calculation","inputs":{"gross_pay":"3000만원",'
            '"ambiguity":"총급여가 비과세 급여를 빼기 전인지 모르겠는데"}}',
        ),
        (
            'CR100',
            '{"calculator":"midyear_income_tax","inputs":{"gross_pay":"3500만원","year":"2024"},'
            '"ambiguity":"원천징수액 50만원이 소득세인지 지방소득세 포함인지 몰라요"}',
        ),
    ],
    ids=['CR091', 'CR093', 'CR094', 'CR096', 'CR097', 'CR100'],
)
def test_replay_ambiguous_live_responses(case_id, response):
    case = _VAL_CASES[case_id]
    calculator = case['calculator']
    route = route_calculation(case['question'], lambda _: response, {calculator: ()}, reference_year=2026)
    assert route.payload['calculator'] == calculator
    assert route.payload['status'] == case['status']
    assert route.payload['articles'] == list(ARTICLE_HASHES[calculator])
    assert route.reason == 'ambiguous_input'


# The first evaluation retained only the failed responses. These four responses use the frozen
# questions' original spans to check that the previously successful paths still give guidance.
@pytest.mark.parametrize(
    ('case_id', 'inputs', 'reason'),
    [
        ('CR092', {'gross_pay': '5000만원', 'medical_expenses': '700만원'}, 'ambiguous_input'),
        (
            'CR095',
            {
                'gross_pay': '5000만원',
                'unlimited_expenses': '500만원',
                'infertility_expenses': '300만원',
                'insurance_reimbursement': '200만원',
            },
            'ambiguous_input',
        ),
        ('CR098', {'gross_pay': '2300만원'}, 'ambiguous_input'),
        ('CR099', {'gross_pay': '4000만원', 'year': '2024년'}, 'unsupported_year'),
    ],
)
def test_previously_successful_ambiguous_cases_with_synthetic_responses(case_id, inputs, reason):
    case = _VAL_CASES[case_id]
    calculator = case['calculator']
    response = json.dumps({'calculator': calculator, 'inputs': inputs})
    route = route_calculation(case['question'], lambda _: response, {calculator: ()}, reference_year=2026)
    assert route.payload['calculator'] == calculator
    assert route.payload['status'] == case['status']
    assert route.reason == reason


@pytest.mark.parametrize('top_level', [True, False])
@pytest.mark.parametrize('has_original_span', [True, False])
def test_only_verbatim_ambiguity_can_take_priority_over_an_unverified_input(top_level, has_original_span):
    question = '2026년 의료비는 300만원이지만 누구의 비용인지 확인 전입니다. 의료비 세액공제액은 얼마인가요?'
    ambiguity = '누구의 비용인지 확인 전입니다' if has_original_span else '아직 환급액을 모르겠습니다'
    inputs = {'gross_pay': '5000만원', 'medical_expenses': '300만원'}
    response = {'calculator': MEDICAL, 'inputs': inputs}
    (response if top_level else inputs)['ambiguity'] = ambiguity

    route = route_calculation(question, lambda _: json.dumps(response), {MEDICAL: ()})
    if has_original_span:
        assert route.payload['status'] == 'guidance'
        assert route.reason == 'ambiguous_input'
    else:
        assert route.payload['status'] == 'none'
        assert route.reason == 'span_not_in_question'


@pytest.mark.parametrize(
    'extra',
    [
        {'kind': 'lookup'},
        {'kind': None},
        {'ambiguity': ''},
        {'ambiguity': None},
        {'ambiguity': 3000000},
        {'extra': '의료비'},
    ],
)
def test_supported_router_response_rejects_malformed_metadata(extra):
    question = '2026년 총급여 5000만원, 의료비 300만원인 경우 세액공제액은 얼마인가요?'
    response = {'calculator': MEDICAL, 'inputs': {'gross_pay': '5000만원', 'medical_expenses': '300만원'}, **extra}
    route = route_calculation(question, lambda _: json.dumps(response), {MEDICAL: ()})
    assert route.payload['status'] == 'none'
    assert route.reason == 'invalid_schema'


def test_supported_router_response_can_calculate_with_kind_calculation():
    question = '2026년 총급여 5000만원, 의료비 300만원인 경우 세액공제액은 얼마인가요?'
    response = {
        'calculator': MEDICAL,
        'kind': 'calculation',
        'inputs': {'gross_pay': '5000만원', 'medical_expenses': '300만원'},
    }
    route = route_calculation(question, lambda _: json.dumps(response), {MEDICAL: ()})
    assert route.payload['status'] == 'calculated'
    assert route.payload['results'] == [{'label': '의료비 세액공제', 'value': 225_000}]


def test_amount_range_in_question_takes_priority_over_an_unverified_input():
    question = '총급여 5천만원에 의료비는 200~400만원 정도인데 공제액을 계산해 주세요.'
    response = '{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5천만원","medical_expenses":"300만원"}}'
    route = route_calculation(question, lambda _: response, {MEDICAL: ()})
    assert route.payload['status'] == 'guidance'
    assert route.reason == 'ambiguous_input'


def test_verified_ambiguity_respects_disabled_calculators():
    question = '총급여 5000만원, 의료비 300만원이 누구의 비용인지 확인 전입니다. 공제액은 얼마인가요?'
    response = (
        '{"calculator":"medical_expense_credit","inputs":{"gross_pay":"5000만원","medical_expenses":"300만원"},'
        '"ambiguity":"누구의 비용인지 확인 전입니다"}'
    )
    route = route_calculation(question, lambda _: response, {MEDICAL: ('article',)})
    assert route.payload['status'] == 'none'
    assert route.reason == 'corpus_mismatch'
