import csv
from pathlib import Path

import pytest

from lawbot.calc_corpus import calculator_compatibility
from lawbot.paths import DATA

MEDICAL = 'medical_expense_credit'
SETTLEMENT = 'midyear_income_tax'
DECREE = '소득세법 시행령 제118조의5'
COMBINED = DATA / 'v5' / 'law_articles_v5_decree.csv'


def test_pinned_hashes_match_v5_csv_and_db_release_bytes():
    assert calculator_compatibility(COMBINED) == {MEDICAL: (), SETTLEMENT: ()}
    assert calculator_compatibility(COMBINED.read_bytes()) == {MEDICAL: (), SETTLEMENT: ()}
    assert calculator_compatibility(DATA / 'v5' / 'articles_v5.csv') == {MEDICAL: (DECREE,), SETTLEMENT: ()}


@pytest.mark.parametrize('change', ['text', 'delete', 'duplicate', 'unrelated'])
def test_only_the_calculator_with_an_affected_dependency_is_disabled(tmp_path: Path, change: str):
    path = tmp_path / 'changed.csv'
    with (
        COMBINED.open(encoding='utf-8-sig', newline='') as original,
        path.open('w', encoding='utf-8-sig', newline='') as edited,
    ):
        rows = csv.DictReader(original)
        writer = csv.DictWriter(edited, fieldnames=rows.fieldnames)
        writer.writeheader()
        for row in rows:
            if row['article_id'] == DECREE:
                if change == 'text':
                    row['text'] += '\n'
                elif change == 'delete':
                    row['deleted'] = '1'
                elif change == 'duplicate':
                    writer.writerow(row)
            if change == 'unrelated' and row['article_id'] == '근로기준법 제26조':
                row['text'] += '\n'
            writer.writerow(row)
    assert calculator_compatibility(path) == {MEDICAL: () if change == 'unrelated' else (DECREE,), SETTLEMENT: ()}
