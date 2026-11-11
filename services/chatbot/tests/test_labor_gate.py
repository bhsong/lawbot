import json
from pathlib import Path

from lawbot.calc_gate import is_calculation_question


def test_gate_covers_labor_val_and_preserves_the_pr1_val():
    fixtures = Path(__file__).with_name('fixtures')
    paths = [fixtures / 'calc_router_val.json', *sorted(fixtures.glob('*labor*val.json'))]
    cases = [row for path in paths for row in json.loads(path.read_text(encoding='utf-8'))]
    misses = [row['id'] for row in cases if is_calculation_question(row['question']) != (row['kind'] != 'general')]
    assert misses == []


def test_article_numbers_are_not_amounts_for_new_labor_intents():
    assert not is_calculation_question('근로기준법 제60조에서 정한 연차유급휴가를 알려 주세요.')
    assert not is_calculation_question('근로기준법 제55조의 주휴수당을 알려 주세요.')
