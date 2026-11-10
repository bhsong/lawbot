"""Deterministic calculations using fiscal-year rules pinned to the v5 articles."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Literal, TypedDict

from lawbot.calc_numbers import parse_amount

MEDICAL = 'medical_expense_credit'
MIDYEAR = 'midyear_income_tax'
_STATUTES = json.loads(Path(__file__).with_name('calc_statutes.json').read_text(encoding='utf-8'))
ARTICLE_HASHES: dict[str, dict[str, str]] = _STATUTES['article_hashes']
_RULES = _STATUTES['years']
GROSS_PAY_ARTICLE = '소득세법 제20조'
MEDICAL_DECREE = '소득세법 시행령 제118조의5'
SETTLEMENT_ARTICLE = '소득세법 제137조'

_CATEGORY_IDS = ('general', 'unlimited', 'premature', 'infertility')
_EXPENSE_FIELDS = tuple(f'{name}_expenses' for name in _CATEGORY_IDS)
_REIMBURSEMENT_FIELDS = tuple(f'{name}_reimbursement' for name in _CATEGORY_IDS)
_MEDICAL_LABELS = {
    'gross_pay': '총급여액',
    'medical_expenses': '실손 차감 전 적격 의료비',
    'net_medical_expenses': '실손 차감 후 적격 의료비',
    'medical_category': '의료비 대상 구분',
    'insurance_reimbursement': '실손의료보험금',
}
_MIDYEAR_LABELS = {
    'gross_pay': '총급여액',
    'pension_premium': '연금보험료',
    'health_premium': '건강보험료',
    'employment_premium': '고용보험료',
    'withheld_income_tax': '기납부 소득세',
    'withheld_local_tax': '기납부 개인지방소득세',
}
INPUT_FIELDS = {
    MEDICAL: (*_MEDICAL_LABELS, *_EXPENSE_FIELDS, *_REIMBURSEMENT_FIELDS),
    MIDYEAR: tuple(_MIDYEAR_LABELS),
}


class InputRow(TypedDict):
    label: str
    value: int | str
    source: str


class Step(TypedDict):
    label: str
    formula: str
    value: int | str
    article: str


class Result(TypedDict):
    label: str
    value: int


@dataclass(frozen=True)
class Calculation:
    calculator: str
    year: int
    articles: tuple[str, ...]
    status: Literal['calculated', 'guidance'] = 'guidance'
    inputs: tuple[InputRow, ...] = ()
    steps: tuple[Step, ...] = ()
    results: tuple[Result, ...] = ()
    message: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        payload = {
            'calculator': self.calculator,
            'year': self.year,
            'status': self.status,
            'inputs': list(self.inputs),
            'steps': list(self.steps),
            'results': list(self.results),
            'articles': list(self.articles),
        }
        if self.message is not None:
            payload['message'] = self.message
        return payload


class _AmbiguousMedicalInput(Exception):
    """The supplied amounts permit multiple results or disagree with each other."""


def _input(label: str, value: int | str, source: str | None, default: str = '미입력(0)') -> InputRow:
    return {'label': label, 'value': value, 'source': f'질문: {source}' if source is not None else default}


def _won(value: Fraction | int) -> str:
    return (f'{int(value):,}' if value == int(value) else f'{value:,.4f}'.rstrip('0').rstrip('.')) + '원'


def _percent(rate: Fraction) -> str:
    return f'{100 * rate:.3f}'.rstrip('0').rstrip('.') + '%'


def _step(label: str, formula: str, value: Fraction | int, article: str) -> Step:
    shown = int(value) if value == int(value) else _won(value)
    return {'label': label, 'formula': formula, 'value': shown, 'article': article}


def _possible_categories(span: str, limits: dict) -> tuple[str, ...]:
    text = re.sub(r'\s+', '', span)
    explicit = []
    if re.search(r'난임|보조생식술|시험관|인공수정', text):
        explicit.append('infertility')
    if re.search(r'미숙아|선천성이상아', text):
        explicit.append('premature')
    # A procedure-specific rate takes precedence over the patient's category.
    uncertain = re.search(r'또는|혹은|인지|아니|여부|미정', text)
    if explicit and not uncertain:
        return tuple(explicit)
    if re.search(r'본인|제의료비|제병원비|장애인|중증질환|희귀난치|결핵환자|한도없', text):
        explicit.append('unlimited')
    if re.search(r'일반|한도적용', text):
        explicit.append('general')
    if explicit and not uncertain:
        return tuple(explicit)
    for age in re.finditer(r'(?<!\d)(\d{1,3})(?:세|살)(이하|이상)?', text):
        number, boundary = int(age[1]), age[2]
        if (number <= limits['young_max'] and boundary != '이상') or (
            number >= limits['senior_min'] and boundary != '이하'
        ):
            explicit.append('unlimited')
        elif boundary is None:
            explicit.append('general')
    if not explicit or uncertain:
        explicit.extend(('general', 'unlimited'))
    return tuple(dict.fromkeys(explicit))


def _medical_inputs(inputs: Mapping[str, str], amounts: dict[str, int], categories: list[dict]) -> tuple[InputRow, ...]:
    rows = []
    aggregate_insurance = amounts.get('insurance_reimbursement', 0)
    for key, label in _MEDICAL_LABELS.items():
        if key in inputs:
            rows.append(_input(label, amounts[key] if key in amounts else inputs[key], inputs[key]))
        elif key == 'insurance_reimbursement' and not any(k in amounts for k in _REIMBURSEMENT_FIELDS):
            rows.append(_input(label, 0, None))
    for category in categories:
        key = f'{category["id"]}_expenses'
        insurance_key = f'{category["id"]}_reimbursement'
        if key in amounts:
            rows.append(_input(f'{category["label"]} 의료비', amounts[key], inputs[key]))
        if insurance_key in amounts or (key in amounts and not aggregate_insurance):
            rows.append(
                _input(f'{category["label"]} 실손의료보험금', amounts.get(insurance_key, 0), inputs.get(insurance_key))
            )
    return tuple(rows)


def _reimburse_in_order(amounts: tuple[int, ...], remaining: int, eligible: list[int]) -> tuple[int, ...]:
    result = list(amounts)
    for idx in eligible:
        reimbursement = min(remaining, result[idx])
        result[idx] -= reimbursement
        remaining -= reimbursement
    return tuple(result)


def _medical_scenarios(
    amounts: dict[str, int],
    categories: tuple[str, ...],
) -> tuple[list[tuple[int, ...]], str]:
    gross, net = amounts.get('medical_expenses'), amounts.get('net_medical_expenses')
    reimbursement = amounts.get('insurance_reimbursement')
    if any(key in amounts for key in _EXPENSE_FIELDS):
        by_category = tuple(amounts.get(key, 0) for key in _EXPENSE_FIELDS)
        if gross is not None and gross != sum(by_category):
            raise _AmbiguousMedicalInput('전체 의료비와 구분별 의료비 합계가 다릅니다. 구분별 금액을 확인해 주세요.')
        if any(value and key not in categories for key, value in zip(_CATEGORY_IDS, by_category)):
            raise _AmbiguousMedicalInput('의료비 대상 구분과 구분별 의료비가 다릅니다. 각 대상의 금액을 알려 주세요.')
        detailed = tuple(amounts.get(key, 0) for key in _REIMBURSEMENT_FIELDS)
        net_max = tuple(cost - paid for cost, paid in zip(by_category, detailed))
        if min(net_max) < 0:
            raise _AmbiguousMedicalInput(
                '구분별 실손의료보험금이 의료비를 초과합니다. 같은 의료비에 대한 금액을 확인해 주세요.'
            )
        if net is not None and reimbursement is None:
            reimbursement = sum(by_category) - net
        remaining = (reimbursement - sum(detailed)) if reimbursement is not None else 0
        eligible = [index for index, key in enumerate(_REIMBURSEMENT_FIELDS) if key not in amounts]
        if not 0 <= remaining <= sum(net_max[index] for index in eligible):
            raise _AmbiguousMedicalInput('실손의료보험금의 총액과 구분별 금액을 확인해 주세요.')
        if net is not None and net != sum(net_max) - remaining:
            raise _AmbiguousMedicalInput('실손보험금 차감 후 의료비와 구분별 의료비가 일치하는지 확인해 주세요.')
        # Keeping net expenses in earlier categories minimizes the credit. Rates
        # are nondecreasing and the general category alone has a cap.
        low = _reimburse_in_order(net_max, remaining, list(reversed(eligible)))
        high = _reimburse_in_order(net_max, remaining, eligible)
        gross_total = sum(by_category)
        return list(dict.fromkeys((low, high))), f'{_won(gross_total)} − {_won(gross_total - sum(low))}'

    if any(key in amounts for key in _REIMBURSEMENT_FIELDS):
        raise _AmbiguousMedicalInput('실손의료보험금에 대응하는 구분별 의료비를 알려 주세요.')
    if net is not None:
        if gross is not None and (gross < net or (reimbursement is not None and gross - reimbursement != net)):
            raise _AmbiguousMedicalInput('실손의료보험금 차감 전·후 의료비를 확인해 주세요.')
        return [
            tuple(net if key == selected else 0 for key in _CATEGORY_IDS) for selected in categories
        ], '질문의 실손 차감 후 의료비'

    assert gross is not None
    reimbursement = reimbursement or 0
    if reimbursement > gross:
        raise _AmbiguousMedicalInput('실손의료보험금이 의료비를 초과합니다. 같은 의료비에 대한 금액을 확인해 주세요.')
    own = gross - reimbursement
    return [
        tuple(own if key == selected else 0 for key in _CATEGORY_IDS) for selected in categories
    ], f'{_won(gross)} − {_won(reimbursement)}'


def _medical_parts(
    net: tuple[int, ...],
    threshold: Fraction,
    categories: list[dict],
    article: str,
) -> tuple[Fraction, Fraction, tuple[Step, ...]]:
    total_base = Fraction()
    credit = Fraction()
    steps = []
    for amount, category in zip(net, categories):
        if amount == 0:
            continue
        base = max(Fraction(amount) - threshold, Fraction())
        formula = f'max({_won(amount)} − {_won(threshold)}, 0원)'
        threshold = max(threshold - amount, Fraction())
        if (limit := category['limit']) is not None:
            base = min(base, limit)
            formula = f'min({formula}, {_won(limit)})'
        rate = Fraction(category['rate'])
        component = base * rate
        total_base += base
        credit += component
        steps.append(_step(f'{category["label"]} 공제대상 의료비', formula, base, article))
        steps.append(_step(f'{category["label"]} 세액공제', f'{_won(base)} × {_percent(rate)}', component, article))
    return total_base, credit, tuple(steps)


def _medical(result: Calculation, inputs: Mapping[str, str], amounts: dict[str, int], rules: dict) -> Calculation:
    categories = rules['categories']['values']
    result = replace(result, inputs=(*result.inputs, *_medical_inputs(inputs, amounts, categories)))
    if not {'medical_expenses', 'net_medical_expenses', *_EXPENSE_FIELDS}.intersection(amounts):
        return replace(result, reason='missing_required', message='적격 의료비 금액을 알려 주시면 계산할 수 있습니다.')
    rate = Fraction(rules['threshold']['rate'])
    threshold = amounts['gross_pay'] * rate
    article = rules['categories']['article']
    possible = (
        _possible_categories(inputs.get('medical_category', ''), rules['recipient_age'])
        if 'medical_category' in inputs or not any(key in amounts for key in _EXPENSE_FIELDS)
        else _CATEGORY_IDS
    )
    try:
        scenarios, formula = _medical_scenarios(amounts, possible)
    except _AmbiguousMedicalInput as exc:
        return replace(result, reason='ambiguous_input', message=str(exc))
    parts = [_medical_parts(net, threshold, categories, article) for net in scenarios]
    possible_results = {int(credit) for _, credit, _ in parts}
    if len(possible_results) != 1:
        return replace(
            result, reason='ambiguous_input', message='의료비 대상 구분과 구분별 실손의료보험금을 알려 주세요.'
        )
    credit = possible_results.pop()
    steps = [
        _step('직접 부담 의료비', formula, sum(scenarios[0]), MEDICAL_DECREE),
        _step(
            '총급여액 기준 문턱',
            f'{_won(amounts["gross_pay"])} × {_percent(rate)}',
            threshold,
            rules['threshold']['article'],
        ),
    ]
    bases = {base for base, _, _ in parts}
    if len(set(scenarios)) == 1 and parts[0][2]:
        steps.extend(parts[0][2])
    elif len(bases) == 1:
        steps.append(
            _step('공제대상 의료비', '직접 부담 의료비에 총급여 기준 문턱과 구분별 한도 적용', parts[0][0], article)
        )
    rates = {Fraction(category['rate']) for net in scenarios for amount, category in zip(net, categories) if amount}
    if len(rates) == 1 and len(bases) == 1:
        formula = f'{_won(parts[0][0])} × {_percent(rates.pop())}, 원 미만 절사'
    else:
        formula = '구분별 공제대상 의료비 × 해당 공제율의 합, 원 미만 절사'
    steps.append(_step('의료비 세액공제', formula, credit, article))
    return replace(
        result, status='calculated', steps=tuple(steps), results=({'label': '의료비 세액공제', 'value': credit},)
    )


def _progressive(amount: Fraction | int, brackets: list[dict]) -> tuple[Fraction, str]:
    lower = 0
    for bracket in brackets:
        if (upper := bracket['up_to']) is None or amount <= upper:
            base = bracket['base']
            rate = Fraction(bracket['rate'])
            delta = f'({_won(amount)} − {_won(lower)})' if lower else _won(amount)
            formula = f'{delta} × {_percent(rate)}'
            return base + (amount - lower) * rate, f'{_won(base)} + {formula}' if base else formula
        lower = upper
    raise ValueError('Missing unbounded bracket')


def _credit_cap(pay: int, brackets: list[dict]) -> tuple[Fraction | int, str]:
    lower = 0
    for bracket in brackets:
        if (upper := bracket['up_to']) is None or pay <= upper:
            base, floor = bracket['base'], bracket['floor']
            rate = Fraction(bracket['rate'])
            if not rate:
                return base, _won(base)
            formula = f'max({_won(base)} − ({_won(pay)} − {_won(lower)}) × {_percent(rate)}, {_won(floor)})'
            return max(base - (pay - lower) * rate, floor), formula
        lower = upper
    raise ValueError('Missing unbounded credit cap bracket')


def _settle(label: str, tax: int, withheld: int, article: str) -> tuple[Step, Result]:
    difference = tax - withheld
    if difference > 0:
        item = f'{label} 추가납부액'
    elif difference < 0:
        item = f'{label} 환급액'
    else:
        item = f'{label} 차감징수세액'
    value = abs(difference)
    formula = f'{_won(withheld)} − {_won(tax)}' if difference < 0 else f'{_won(tax)} − {_won(withheld)}'
    return _step(item, formula, value, article), {'label': item, 'value': value}


def _midyear(result: Calculation, amounts: dict[str, int], rules: dict) -> Calculation:
    pay = amounts['gross_pay']
    deduction_rule = rules['income_deduction']
    deduction, formula = _progressive(pay, deduction_rule['brackets'])
    deduction = min(deduction, deduction_rule['limit'], pay)
    personal_rule = rules['personal_deduction']
    personal = personal_rule['value']
    steps = [
        _step('총급여액', '비과세소득을 제외한 퇴직일까지의 과세급여 합계', pay, GROSS_PAY_ARTICLE),
        _step(
            '근로소득공제',
            f'min({formula}, {_won(deduction_rule["limit"])}, {_won(pay)})',
            deduction,
            deduction_rule['article'],
        ),
        _step('본인 기본공제', f'본인 1명 × {_won(personal)}', personal, personal_rule['article']),
    ]
    for key, article in (
        ('pension_premium', '소득세법 제51조의3'),
        ('health_premium', '소득세법 제52조'),
        ('employment_premium', '소득세법 제52조'),
    ):
        if key in amounts:
            steps.append(
                _step(f'{_MIDYEAR_LABELS[key]} 공제', '해당 과세기간의 근로자 본인 부담 납입액', amounts[key], article)
            )
    pension = amounts.get('pension_premium', 0)
    special = amounts.get('health_premium', 0) + amounts.get('employment_premium', 0)
    basis = max(int(pay - deduction - personal - pension - special), 0)
    formula = 'max(총급여액 − 근로소득공제 − 본인 기본공제 − 연금·건강·고용보험료, 0원), 원 미만 절사'
    steps.append(_step('과세표준', formula, basis, '소득세법 제14조'))

    tax_rule, credit_rule = rules['income_tax'], rules['earned_income_credit']
    tax, formula = _progressive(basis, tax_rule['brackets'])
    steps.append(_step('산출세액', formula, tax, tax_rule['article']))
    earned, formula = _progressive(tax, credit_rule['brackets'])
    cap, cap_formula = _credit_cap(pay, credit_rule['caps'])
    earned = min(earned, cap)
    steps.append(_step('근로소득세액공제 한도', cap_formula, cap, credit_rule['article']))
    steps.append(_step('근로소득세액공제', f'min({formula}, {_won(cap)})', earned, credit_rule['article']))

    standard_rule = rules['standard_credit']
    standard = 0 if special else standard_rule['value']
    formula = '특별소득공제 신청 시 적용 제외' if special else '특별소득공제·특별세액공제·월세액 세액공제 미신청'
    steps.append(_step('표준세액공제', formula, standard, standard_rule['article']))
    national = max(int(tax - earned - standard), 0)
    formula = 'max(산출세액 − 근로소득세액공제 − 표준세액공제, 0원), 원 미만 절사'
    steps.append(_step('결정세액', formula, national, '소득세법 제61조'))
    local_rule = rules['local_tax']
    local_rate = Fraction(local_rule['rate'])
    local = int(national * local_rate)
    steps.append(
        _step(
            '개인지방소득세', f'{_won(national)} × {_percent(local_rate)}, 원 미만 절사', local, local_rule['article']
        )
    )

    results: list[Result] = [{'label': '결정세액', 'value': national}, {'label': '개인지방소득세', 'value': local}]
    for key, label, value, article in (
        ('withheld_income_tax', '소득세', national, SETTLEMENT_ARTICLE),
        ('withheld_local_tax', '개인지방소득세', local, local_rule['article']),
    ):
        if key in amounts:
            step, item = _settle(label, value, amounts[key], article)
            steps.append(step)
            results.append(item)
    return replace(result, status='calculated', steps=tuple(steps), results=tuple(results))


def calculate(
    calculator: str,
    inputs: Mapping[str, str],
    *,
    year: int,
    year_source: str | None = None,
) -> Calculation:
    """Calculate from source spans or explain which input or fiscal year is missing.

    The caller verifies every supplied span against the question. Invalid numeric
    spans raise ValueError so the router can record a conversion failure.
    """
    year_row = _input('귀속연도', f'{year}년', year_source, f'기본값({year}년)')
    result = Calculation(calculator, year, tuple(ARTICLE_HASHES[calculator]), inputs=(year_row,))
    if (rules := _RULES.get(str(year), {}).get(calculator)) is None:
        supported = ', '.join(f'{value}년' for value, entries in _RULES.items() if calculator in entries)
        return replace(
            result,
            reason='unsupported_year',
            message=f'{year}년 귀속은 지원하지 않습니다. 지원 귀속연도는 {supported}입니다.',
        )
    amounts = {
        key: parse_amount(inputs[key])
        for key in INPUT_FIELDS[calculator]
        if key in inputs and key != 'medical_category'
    }
    if 'gross_pay' not in amounts:
        return replace(result, reason='missing_required', message='총급여액을 알려 주시면 계산할 수 있습니다.')
    if calculator == MEDICAL:
        return _medical(result, inputs, amounts, rules)
    rows = tuple(_input(label, amounts.get(key, 0), inputs.get(key)) for key, label in _MIDYEAR_LABELS.items())
    return _midyear(replace(result, inputs=(*result.inputs, *rows)), amounts, rules)
