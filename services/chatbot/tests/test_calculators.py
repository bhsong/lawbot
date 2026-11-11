import csv
import hashlib
from pathlib import Path

import pytest

from lawbot.calculators import ARTICLE_HASHES, calculate

MEDICAL = 'medical_expense_credit'
MIDYEAR = 'midyear_income_tax'
YEAR = 2026


@pytest.mark.parametrize(
    'extra,expected',
    [
        # §59-4(2), 시행령 §118-5(1): (300만 − 100만 − 150만) × 15% = 75,000원.
        ({'medical_expenses': '300만 원', 'insurance_reimbursement': '100만 원'}, 75_000),
        # §59-4(2)1: 의료비와 총급여 × 3%가 같은 경계.
        ({'medical_expenses': '150만원'}, 0),
        # 시행령 §118-5(1): 실손보험금으로 전액 보전하면 직접 부담 0원.
        ({'medical_expenses': '300만원', 'insurance_reimbursement': '300만원'}, 0),
        # §59-4(2)1: min(1200만 − 150만, 700만) × 15%.
        ({'medical_expenses': '1200만원', 'medical_category': '일반 부양가족'}, 1_050_000),
        # §59-4(2)2가: (1200만 − 150만) × 15%, 본인은 700만 원 한도 없음.
        ({'medical_expenses': '1200만원', 'medical_category': '본인'}, 1_575_000),
        # §59-4(2)2다: 70세 부양가족, (1000만 − 150만) × 15%.
        ({'medical_expenses': '1000만원', 'medical_category': '70세'}, 1_275_000),
        # §59-4(2)4 및 시행령 §118-5(6): (500만 − 50만 − 150만) × 30%.
        ({'medical_expenses': '500만원', 'medical_category': '난임시술', 'insurance_reimbursement': '50만원'}, 900_000),
        # §59-4(2)3 및 시행령 §118-5(5): (400만 − 150만) × 20%.
        ({'medical_expenses': '400만원', 'medical_category': '미숙아', 'insurance_reimbursement': '0원'}, 500_000),
        # §59-4(2)1→2→3→4: 문턱 150만은 일반 80만, 본인 30만, 미숙아 40만을 소진.
        # 남은 미숙아 20만 × 20% + 난임 100만 × 30% = 34만.
        (
            {
                'general_expenses': '80만원',
                'unlimited_expenses': '30만원',
                'premature_expenses': '60만원',
                'infertility_expenses': '100만원',
            },
            340_000,
        ),
        # §59-4(2)1→2→3→4: 일반 min(1000만 − 150만, 700만) × 15%,
        # 본인 400만 × 15%, 미숙아 100만 × 20%, 난임 50만 × 30%의 합은 200만.
        (
            {
                'general_expenses': '1000만원',
                'unlimited_expenses': '400만원',
                'premature_expenses': '100만원',
                'infertility_expenses': '50만원',
            },
            2_000_000,
        ),
        # §59-4(2)1,2: 일반·본인 모두 15%, 700만원 한도 미도달. (170만 − 10만 − 150만) × 15%.
        ({'general_expenses': '80만원', 'unlimited_expenses': '90만원', 'insurance_reimbursement': '10만원'}, 15_000),
        # §59-4(2)1,4: 배분과 무관하게 전체 순지출 140만이 문턱 150만보다 작다.
        ({'general_expenses': '70만원', 'infertility_expenses': '80만원', 'insurance_reimbursement': '10만원'}, 0),
        # 시행령 §118-5(1): 명시된 차감 후 금액에서 보험금을 다시 빼지 않는다.
        ({'net_medical_expenses': '200만원', 'insurance_reimbursement': '100만원'}, 75_000),
        # §59-4(2)2나: 과세기간 개시일 현재 6세 이하는 한도 없음.
        ({'medical_expenses': '1200만원', 'medical_category': '6세 이하'}, 1_575_000),
        # §59-4(2)1,4 및 시행령 §118-5(1): (100만 − 30만) 일반, (160만 − 10만) 난임.
        # 150만 문턱의 나머지 80만을 난임에서 차감하면 (150만 − 80만) × 30%.
        (
            {
                'general_expenses': '100만원',
                'general_reimbursement': '30만원',
                'infertility_expenses': '160만원',
                'infertility_reimbursement': '10만원',
            },
            210_000,
        ),
    ],
)
def test_medical_credit_hand_calculated(extra, expected):
    result = calculate(MEDICAL, {'gross_pay': '5천만 원', **extra}, year=YEAR).as_dict()
    assert result['status'] == 'calculated'
    assert result['results'] == [{'label': '의료비 세액공제', 'value': expected}]
    assert all(step['article'] in ARTICLE_HASHES[MEDICAL] for step in result['steps'])


@pytest.mark.parametrize(
    'pay,extra,national,local',
    [
        # §47 70% (350만), §50 150만을 빼면 과표 0, 결정세액 0.
        ('500만원', {}, 0, 0),
        # §47 350만 + 500만 × 40% = 550만. §55 과표 300만 × 6% = 18만.
        # §59 99,000원과 §59-4(9) 13만원 공제 후 0원.
        ('1000만원', {}, 0, 0),
        # §47 750만, §50 150만. §55 600만 × 6% = 36만, §59 19.8만, §59-4(9) 13만.
        ('1500만원', {}, 32_000, 3_200),
        # §47 825만, §50 150만. §55 1025만 × 6% = 615,000 − §59 338,250 − 표준 130,000.
        ('2000만원', {}, 146_750, 14_675),
        # §51-3 연금 100만. §55 과표 925만 × 6% = 555,000 − §59 305,250 − 표준 130,000.
        ('2000만원', {'pension_premium': '100만원'}, 119_750, 11_975),
        # §51-3 100만, §52 70만. 보험료 공제 시 과표 855만 × 6% = 513,000 − §59 282,150.
        # 표준세액공제 적용 시 과표 925만 × 6% = 555,000 − §59 305,250 − §59-4(9) 130,000.
        (
            '2000만원',
            {'pension_premium': '100만원', 'health_premium': '50만원', 'employment_premium': '20만원'},
            119_750,
            11_975,
        ),
        # §47 1020만. §55 과표 2130만: 84만 + 730만 × 15% = 193.5만 − §59 한도 74만 − 표준 13만.
        ('3300만원', {}, 1_065_000, 106_500),
        # §47 1200만. §55 과표 3150만: 84만 + 1750만 × 15% = 346.5만 − §59 최저 66만 − 표준 13만.
        ('4500만원', {}, 2_675_000, 267_500),
        # §47 1225만. §55 과표 3625만: 84만 + 2225만 × 15% = 417.75만 − §59 66만 − 표준 13만.
        ('5000만원', {}, 3_387_500, 338_750),
        # §47 1325만. §55 과표 5525만: 624만 + 525만 × 24% = 750만 − §59 66만 − 표준 13만.
        ('7000만원', {}, 6_710_000, 671_000),
        # §47 1325.5만. §55 624만 + 534.5만 × 24% = 752.28만 − §59 (66만 − 10만 × 50%) − 표준 13만.
        ('7010만원', {}, 6_782_800, 678_280),
        # §47 1475만. §55 624만 + 3375만 × 24% = 1434만 − §59 50만 − 표준 13만.
        ('1억원', {}, 13_710_000, 1_371_000),
        # §47 1515만. §55 1536만 + 1535만 × 35% = 2073.25만 − §59 50만 − 표준 13만.
        ('1억 2천만원', {}, 20_102_500, 2_010_250),
        # §47 1515.4만. §55 1536만 + 1546만 × 35% = 2080.11만 − §59 (50만 − 20만 × 50%) − 표준 13만.
        ('1억 2020만원', {}, 20_271_100, 2_027_110),
        # §47 2000만 한도. §55 9406만 + 7850만 × 40% = 12546만 − §59 20만 − 표준 13만.
        ('4억원', {}, 125_130_000, 12_513_000),
        # §47 2000만 한도. §55 3억8406만 + 7850만 × 45% = 4억1938.5만 − §59 20만 − 표준 13만.
        ('11억원', {}, 419_055_000, 41_905_500),
    ],
)
def test_midyear_tax_hand_calculated(pay, extra, national, local):
    result = calculate(MIDYEAR, {'gross_pay': pay, **extra}, year=YEAR).as_dict()
    assert result['status'] == 'calculated'
    # 지방세법 §103-15(1): 지방세는 결정세액 × 10%.
    assert result['results'] == [{'label': '결정세액', 'value': national}, {'label': '개인지방소득세', 'value': local}]
    assert all(step['article'] in ARTICLE_HASHES[MIDYEAR] for step in result['steps'])


@pytest.mark.parametrize(
    'inputs,a,b,selected',
    [
        # CR025, §52 120만. §55 84만 + (과표 − 1400만) × 15%. §59 한도 684,000원.
        # A 2,647,500 − 684,000. B 2,827,500 − 684,000 − §59-4(9) 130,000.
        (
            {'gross_pay': '4000만원', 'health_premium': '120만원'},
            (26_050_000, 2_647_500, 1_963_500),
            (27_250_000, 2_827_500, 2_013_500),
            'A',
        ),
        # CR026, §52 19만. §55 84만 + (과표 − 1400만) × 15%. §59 산출세액의 55% 공제.
        # A 886,500 − 487,575. B 915,000 − 503,250 − §59-4(9) 130,000.
        (
            {'gross_pay': '2500만원', 'employment_premium': '19만원'},
            (14_310_000, 886_500, 398_925),
            (14_500_000, 915_000, 281_750),
            'B',
        ),
        # CR029, §51-3 250만 양쪽 공제, §52 250만은 A에 공제.
        # §55 624만 + (과표 − 5000만) × 24%. §59 한도 50만, B만 §59-4(9) 13만 추가 공제.
        (
            {
                'gross_pay': '8000만원',
                'pension_premium': '250만원',
                'health_premium': '200만원',
                'employment_premium': '50만원',
            },
            (59_750_000, 8_580_000, 8_080_000),
            (62_250_000, 9_180_000, 8_550_000),
            'A',
        ),
        # §55 40% 구간. §52 325,000원 공제로 산출세액이 130,000원 감소.
        # §59 한도 20만. §59-4(9) 13만원과 절세액이 같아 A를 선택한다.
        (
            {'gross_pay': '4억원', 'health_premium': '325,000원'},
            (378_175_000, 125_330_000, 125_130_000),
            (378_500_000, 125_460_000, 125_130_000),
            'A',
        ),
    ],
)
def test_midyear_compares_both_claims(inputs, a, b, selected):
    result = calculate(MIDYEAR, inputs, year=YEAR).as_dict()
    steps = {step['label']: step for step in result['steps']}
    for option, expected in (('A 보험료 공제 적용', a), ('B 표준세액공제 적용', b)):
        assert tuple(steps[f'{option} · {name}']['value'] for name in ('과세표준', '산출세액', '결정세액')) == expected
    detail = '세액이 적은 쪽' if a[2] != b[2] else '결정세액 동일'
    assert steps['선택']['value'] == f'{selected} ({detail})'
    national = min(a[2], b[2])
    assert result['results'] == [
        {'label': '결정세액', 'value': national},
        {'label': '개인지방소득세', 'value': national // 10},
    ]


def test_midyear_separate_long_term_care_premium_and_selected_settlement():
    result = calculate(
        MIDYEAR,
        {
            'gross_pay': '2000만원',
            'pension_premium': '100만원',
            'health_premium': '50만원',
            'long_term_care_premium': '5만원',
            'employment_premium': '20만원',
            'withheld_income_tax': '15만원',
            'withheld_local_tax': '1만원',
        },
        year=YEAR,
    ).as_dict()
    rows = {row['label']: row for row in result['inputs']}
    assert rows['장기요양보험료'] == {'label': '장기요양보험료', 'value': 50_000, 'source': '질문: 5만원'}
    steps = {step['label']: step for step in result['steps']}
    # §51-3 연금 100만 양쪽 공제. §52 건강 50만 + 장기요양 5만 + 고용 20만.
    # A: 과표 850만 × 6% = 510,000원 − §59 280,500원.
    # B: 과표 925만 × 6% = 555,000원 − §59 305,250원 − §59-4(9) 130,000원.
    assert steps['A 보험료 공제 적용 · 과세표준']['value'] == 8_500_000
    assert steps['A 보험료 공제 적용 · 결정세액']['value'] == 229_500
    assert steps['B 표준세액공제 적용 · 과세표준']['value'] == 9_250_000
    # §137 15만 − 119,750원. 지방세법 §103-15의 결정세액 × 10%에서 기납부세액을 뺀다.
    assert result['results'] == [
        {'label': '결정세액', 'value': 119_750},
        {'label': '개인지방소득세', 'value': 11_975},
        {'label': '소득세 환급액', 'value': 30_250},
        {'label': '개인지방소득세 추가납부액', 'value': 1_975},
    ]


def test_settlement_refund_and_additional_payment_have_separate_tax_bases():
    result = calculate(
        MIDYEAR,
        {'gross_pay': '1500만원', 'withheld_income_tax': '4만원', 'withheld_local_tax': '3천원'},
        year=YEAR,
    ).as_dict()
    # §137: 32,000 − 40,000 = −8,000. 지방세법 §103-15: 3,200 − 3,000 = 200.
    assert result['results'][2:] == [
        {'label': '소득세 환급액', 'value': 8_000},
        {'label': '개인지방소득세 추가납부액', 'value': 200},
    ]


def test_missing_values_and_explicit_zero_have_distinct_provenance():
    result = calculate(MIDYEAR, {'gross_pay': '1500만원', 'pension_premium': '0원'}, year=YEAR, year_source='2026년')
    rows = {row['label']: row for row in result.as_dict()['inputs']}
    assert rows['총급여액'] == {'label': '총급여액', 'value': 15_000_000, 'source': '질문: 1500만원'}
    assert rows['귀속연도'] == {'label': '귀속연도', 'value': '2026년', 'source': '질문: 2026년'}
    assert rows['연금보험료']['source'] == '질문: 0원'
    assert rows['건강보험료']['source'] == '미입력(0)'
    assert rows['건강보험료']['value'] == 0


@pytest.mark.parametrize(
    'name,inputs,year,reason,fragment',
    [
        (MEDICAL, {'medical_expenses': '200만원'}, YEAR, 'missing_required', '총급여액'),
        (MEDICAL, {'gross_pay': '5000만원'}, YEAR, 'missing_required', '의료비'),
        (MIDYEAR, {'pension_premium': '10만원'}, YEAR, 'missing_required', '총급여액'),
        (MIDYEAR, {'gross_pay': '5000만원'}, 2010, 'unsupported_year', '2010년'),
        (MEDICAL, {'gross_pay': '5000만원', 'medical_expenses': '1200만원'}, YEAR, 'ambiguous_input', '구분'),
        (
            MEDICAL,
            {'gross_pay': '5000만원', 'medical_expenses': '300만원', 'insurance_reimbursement': '400만원'},
            YEAR,
            'ambiguous_input',
            '실손의료보험금',
        ),
        (
            MEDICAL,
            {
                'gross_pay': '5000만원',
                'general_expenses': '250만원',
                'infertility_expenses': '250만원',
                'insurance_reimbursement': '100만원',
            },
            YEAR,
            'ambiguous_input',
            '구분',
        ),
        (
            MEDICAL,
            {
                'gross_pay': '5000만원',
                'medical_expenses': '300만원',
                'net_medical_expenses': '200만원',
                'insurance_reimbursement': '50만원',
            },
            YEAR,
            'ambiguous_input',
            '차감',
        ),
    ],
)
def test_guidance_has_concrete_request_and_articles(name, inputs, year, reason, fragment):
    calculation = calculate(name, inputs, year=year)
    payload = calculation.as_dict()
    assert calculation.reason == reason
    assert payload['status'] == 'guidance'
    assert fragment in payload['message']
    assert payload['articles']
    assert not payload['results']


def test_amount_conversion_failures_are_left_for_router():
    with pytest.raises(ValueError):
        calculate(MIDYEAR, {'gross_pay': '퇴사 예정'}, year=YEAR)


def test_article_hashes_are_for_the_exact_v5_bodies():
    root = Path(__file__).resolve().parents[3] / 'data' / 'v5'
    articles = {}
    for path in ('law_articles_v5_decree.csv', 'articles_v5.csv'):
        with (root / path).open(encoding='utf-8-sig', newline='') as file:
            for row in csv.DictReader(file):
                if row['deleted'] == '0':
                    articles[row['article_id']] = hashlib.sha256(row['text'].encode()).hexdigest()
    assert set(ARTICLE_HASHES) == {MEDICAL, MIDYEAR}
    for hashes in ARTICLE_HASHES.values():
        assert hashes
        assert hashes == {article: articles[article] for article in hashes}
