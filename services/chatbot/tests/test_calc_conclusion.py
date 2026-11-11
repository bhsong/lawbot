import json
from pathlib import Path

import pytest

from lawbot.calc_router import check_conclusion
from lawbot.calculators import calculate

FIXTURES = Path(__file__).with_name('fixtures')
VAL = {case['id']: case for case in json.loads((FIXTURES / 'calc_router_val.json').read_text(encoding='utf-8'))}
# Routed answers from docs/evals/2026-11-11-calc-router-live.md.
REPLAY = json.loads((FIXTURES / 'calc_conclusion_replay.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case_id', REPLAY)
def test_conclusion_check_replays_evaluation_answers(case_id):
    case = REPLAY[case_id]
    calculation = calculate(VAL[case_id]['calculator'], case['inputs'], year=2026)
    assert calculation.status == 'calculated'
    assert check_conclusion(case['answer'], calculation.as_dict()) == 'match'


def test_conclusion_check_reads_subtraction_operands_as_positive_amounts():
    case = REPLAY['CR001']
    calculation = calculate(VAL['CR001']['calculator'], case['inputs'], year=2026)
    answer = '【결론】 의료비 세액공제는 75,000원입니다. 직접 부담 의료비는 200만 원(300만 원 - 100만 원)입니다.'

    assert check_conclusion(answer, calculation.as_dict()) == 'match'


@pytest.mark.parametrize(
    ('text', 'status'),
    [
        ('【결론】 환급액 10,000원, 결정세액 75,000원입니다. 입력은 5,000만원, 산출세액은 90,000원입니다.', 'match'),
        ('【결론】 결정세액 75,000원, 환급액 10,000원, 70,000원입니다.', 'mismatch'),
        ('【결론】 결정세액 75,000원입니다. 【설명】 환급액은 10,000원입니다.', 'mismatch'),
        ('【결론】 입력은 5,000만원입니다.', 'mismatch'),
        ('【결론】 금액을 확인하세요. 【설명】 결정세액 75,000원, 환급액 10,000원입니다.', 'missing'),
        ('【설명】 결정세액 75,000원, 환급액 10,000원입니다.', 'missing'),
        ('【결론】 결정세액 75,000원, 환급액 10,000원입니다. 반올림 전 금액은 75,000.45원입니다.', 'match'),
        ('【결론】 결정세액 75,000원, 환급액 10,000원입니다. 반올림 전 금액은 75,000.54원입니다.', 'mismatch'),
        ('【결론】 반올림 전 금액은 75,000.45원입니다.', 'mismatch'),
    ],
)
def test_conclusion_check_requires_all_results_and_allows_only_table_amounts(text, status):
    payload = {
        'results': [{'label': '결정세액', 'value': 75_000}, {'label': '환급액', 'value': 10_000}],
        'inputs': [{'label': '총급여액', 'value': 50_000_000, 'source': '질문: 5,000만원'}],
        'steps': [
            {'label': '산출세액', 'value': 90_000, 'formula': '70,000원 + 20,000원', 'article': '소득세법 제55조'},
            {'label': '반올림 전 금액', 'value': '75,000.45원', 'formula': '', 'article': '소득세법 제55조'},
        ],
    }
    assert check_conclusion(text, payload) == status
