import csv
from pathlib import Path

import pytest

from lawbot.calc_corpus import calculator_compatibility
from lawbot.paths import DATA

MEDICAL = 'medical_expense_credit'
SETTLEMENT = 'midyear_income_tax'
DECREE = '소득세법 시행령 제118조의5'
COMBINED = DATA / 'v5' / 'law_articles_v5_decree.csv'
ENABLED = dict.fromkeys(
    (
        MEDICAL,
        SETTLEMENT,
        'severance_pay',
        'annual_leave_days',
        'weekly_holiday_pay',
        'monthly_minimum_wage',
        'premium_pay',
    ),
    (),
)


def test_pinned_hashes_match_v5_csv_and_db_release_bytes():
    assert calculator_compatibility(COMBINED) == ENABLED
    assert calculator_compatibility(COMBINED.read_bytes()) == ENABLED
    law_only = calculator_compatibility(DATA / 'v5' / 'articles_v5.csv')
    assert law_only[MEDICAL] == (DECREE,)
    assert law_only[SETTLEMENT] == ()
    assert all(law_only[name] for name in ENABLED if name not in (MEDICAL, SETTLEMENT))


@pytest.mark.parametrize(
    ('change', 'article', 'affected'),
    [
        ('text', DECREE, MEDICAL),
        ('delete', DECREE, MEDICAL),
        ('duplicate', DECREE, MEDICAL),
        ('text', '최저임금법 시행령 제5조', 'monthly_minimum_wage'),
        ('text', '근로기준법 제26조', None),
    ],
)
def test_only_the_calculator_with_an_affected_dependency_is_disabled(tmp_path: Path, change, article, affected):
    path = tmp_path / 'changed.csv'
    with (
        COMBINED.open(encoding='utf-8-sig', newline='') as original,
        path.open('w', encoding='utf-8-sig', newline='') as edited,
    ):
        rows = csv.DictReader(original)
        writer = csv.DictWriter(edited, fieldnames=rows.fieldnames)
        writer.writeheader()
        for row in rows:
            if row['article_id'] == article:
                if change == 'text':
                    row['text'] += '\n'
                elif change == 'delete':
                    row['deleted'] = '1'
                elif change == 'duplicate':
                    writer.writerow(row)
            writer.writerow(row)
    assert calculator_compatibility(path) == (ENABLED | {affected: (article,)} if affected else ENABLED)
