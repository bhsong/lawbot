"""Two-stage calculation routing prompts and parsers.

Stage 1 asks the model for one calculator name (or ``lookup``/``unsupported``).
Stage 2 shows the question with every quantity numbered by deterministic code and
asks the model to label each number with an input field. Because every number must
receive a label, a quantity the model ignores becomes an explicit ambiguity instead
of a silent omission. The composed JSON keeps the router protocol of
``calc_router.route_calculation`` and adds judged fact polarities under ``facts``.
"""

import json
import re

from lawbot.calculators import LABOR_CALCULATORS, TAX_CALCULATORS

SELECT_SYSTEM = """계산 질문 분류기. 아래 이름 중 하나만 출력한다.
질문자가 준 금액·기간·인원수 같은 개인 조건으로 금액·일수·세액을 구해야 답할 수 있으면 계산이다. 계산이면 맞는 계산기 이름을 고른다. 필요한 값이 빠졌거나 모호해도 계산기 이름을 고른다.
medical_expense_credit: 근로소득자의 의료비 세액공제액
midyear_income_tax: 중도퇴사자의 근로소득 결정세액·환급액·추가 납부액
severance_pay: 법정 퇴직금
annual_leave_days: 연차유급휴가 발생 일수
weekly_holiday_pay: 주휴수당
monthly_minimum_wage: 최저임금 월 환산액
premium_pay: 연장·야간·휴일근로 가산수당
unsupported: 목록에 없는 계산(일반 연말정산, 다른 공제, 퇴직소득세, 연차미사용수당, 실업·육아휴직 급여, 4대보험료, 임대료 환산 등)
lookup: 계산이 아닌 질문. 법령에 적힌 값·기한·요건·절차를 묻는다. 질문에 숫자가 있어도 그 숫자로 계산하지 않는다."""
SELECT_MAX_TOKENS = 12
NON_CALCULATOR_CHOICES = ('lookup', 'unsupported')

_YN = '(예/아니오) '
_TAX_PAY = '근무기간의 총급여(월급·계약연봉 제외)'
FIELD_DESCRIPTIONS: dict[str, tuple[str, dict[str, str], str]] = {
    'medical_expense_credit': (
        '의료비 세액공제',
        {
            'gross_pay': _TAX_PAY,
            'medical_expenses': '실손보험금을 빼기 전 의료비',
            'net_medical_expenses': '실손보험금을 뺀 뒤 의료비',
            'insurance_reimbursement': '받은 실손보험금 전체',
            'medical_category': '의료비 대상·치료명 구절',
            'general_expenses': '일반 부양가족 의료비',
            'unlimited_expenses': '본인·65세 이상·장애인·6세 이하 의료비',
            'premature_expenses': '미숙아·선천성이상아 의료비',
            'infertility_expenses': '난임시술비',
            'general_reimbursement': '일반 의료비에 대한 실손보험금',
            'unlimited_reimbursement': '본인·65세 이상·장애인·6세 이하 의료비에 대한 실손보험금',
            'premature_reimbursement': '미숙아·선천성이상아 의료비에 대한 실손보험금',
            'infertility_reimbursement': '난임시술비에 대한 실손보험금',
        },
        '합계 금액 하나는 두 항목으로 나누지 않는다. 귀속연도는 x.',
    ),
    'midyear_income_tax': (
        '중도퇴사자 근로소득세 정산',
        {
            'gross_pay': _TAX_PAY,
            'pension_premium': '국민연금 등 연금보험료',
            'health_premium': '건강보험료(건강·장기요양 합계 금액이면 여기에 한 번만)',
            'long_term_care_premium': '장기요양보험료',
            'employment_premium': '고용보험료',
            'withheld_income_tax': '이미 원천징수된 소득세',
            'withheld_local_tax': '이미 원천징수된 지방소득세',
        },
        '귀속연도는 x.',
    ),
    'severance_pay': (
        '법정 퇴직금',
        {
            'hire_date': '입사일',
            'retirement_date': '첫 비재직일(근로관계가 끝난 다음 날) 또는 구분 없이 적힌 퇴직일',
            'last_employed_date': '마지막 재직일',
            'service_period': '인정 계속근로기간(년·개월·일수로 적힌 전체)',
            'service_qualifies': _YN + '계속근로기간이 1년 이상이라고 밝혔는지',
            'weekly_hours': '4주 평균 1주 소정근로시간',
            'wage_total': '퇴직 전 3개월 임금 총액',
            'wage_days': '그 3개월의 달력 일수',
            'ordinary_daily_wage': '1일 통상임금',
            'adjusted_daily_wage': '통상임금 하한까지 반영한 퇴직금 산정용 일급',
            'annual_bonus': '임금 총액과 별도로 산입할 연간 상여금',
            'annual_unused_leave_pay': '임금 총액과 별도로 산입할 연간 미사용 연차수당',
            'excluded_wages': '평균임금 산정에서 제외할 기간의 임금',
            'excluded_days': '평균임금 산정에서 제외할 일수',
            'monthly_wage': '월급(위 임금 항목이 없고 월급만 있을 때)',
        },
        '',
    ),
    'annual_leave_days': (
        '연차유급휴가 발생 일수',
        {
            'workplace_size': '사업장의 상시 근로자 수',
            'weekly_hours': '4주 평균 1주 소정근로시간',
            'hire_date': '입사일',
            'as_of_date': '재직 중인 계산 기준일',
            'last_employed_date': '마지막 재직일',
            'service_period': '근속기간(년·개월로 적힌 전체)',
            'attendance_rate': '연차 산정 대상 1년의 출근율',
            'full_attendance_months': '개근하고 그 다음 날에도 재직한 달의 수',
            'employed_at_accrual': _YN + '연차 발생일(1년 또는 각 1개월 근로를 마친 다음 날)에 재직했는지',
            'include_first_year': _YN + '첫해 월 단위 연차와 1주년 발생분을 합친 누적 일수를 달라고 명시했는지',
        },
        '',
    ),
    'weekly_holiday_pay': (
        '주휴수당',
        {
            'hourly_wage': '통상시급',
            'weekly_scheduled_hours': '1주 소정근로시간(고정값 또는 "4주 평균 주 소정근로시간")',
            'four_week_scheduled_hours': '4주 동안의 본인 소정근로시간 총합(평균이 아닌 합계)',
            'paid_holiday_hours': '유급주휴시간',
            'reference_days_per_week': '비교 대상 통상근로자의 1주 소정근로일수',
            'four_week_reference_days': '같은 4주간 비교 대상 통상근로자의 소정근로일수 합계',
            'attendance': _YN + '해당 주 소정근로일을 개근했는지',
            'employment': _YN + '해당 주 7일 동안 근로관계가 유지됐는지',
        },
        '실제 일한 시간이나 본인의 출근일수는 x.',
    ),
    'monthly_minimum_wage': (
        '최저임금 월 환산액',
        {
            'year': '적용 연도',
            'weekly_scheduled_hours': '1주 소정근로시간(한 주 합계)',
            'daily_scheduled_hours': '1일(하루) 소정근로시간',
            'weekly_workdays': '1주 소정근로일수',
            'weekly_paid_holiday_hours': '1주 유급주휴시간',
            'monthly_basis_hours': '월 최저임금 적용기준시간(유급주휴 포함 월 환산 시간)',
            'applicability': _YN + '수습 감액·적용 제외 같은 특례가 적용되는지',
        },
        '실제 월 근로시간은 x.',
    ),
    'premium_pay': (
        '연장·야간·휴일근로 가산수당',
        {
            'staff_count': '사업장의 상시 근로자 수',
            'ordinary_hourly_wage': '통상시급',
            'overtime_hours': '평일 연장근로 시간',
            'night_hours': '야간근로 시간(다른 유형과 겹쳐도 씀)',
            'holiday_hours': '휴일근로 시간(하루 휴일의 전체 실근로)',
            'holiday_within_hours': '여러 휴일의 8시간 이내분 합계(질문에 그렇게 나눠 적힌 경우만)',
            'holiday_excess_hours': '여러 휴일의 8시간 초과분 합계(질문에 그렇게 나눠 적힌 경우만)',
            'total_hours': '중복 없이 센 총 실근로시간',
            'include_base': _YN + '가산분뿐 아니라 기본임금까지 포함한 총액을 요청했는지',
            'applicability': _YN + '감시·단속적 근로, 적용 제외 등 별도 기준이 적용되는지',
        },
        '시간은 휴게시간을 뺀 실근로시간이다.',
    ),
}

_LABEL_RULES = """{title} 계산 입력 분류기. 질문 속 숫자마다 [번호:값] 표시가 있다.
1) 번호마다 `번호=항목이름` 한 줄을 빠짐없이, 번호당 한 번만 쓴다. 항목이름은 아래 목록의 영문 이름이다. 값은 다시 쓰지 않는다.
- 계산 입력이 아닌 숫자(산정 기간·단위·법정 기준·조문 번호)나 목록에 맞는 항목이 없는 숫자는 `번호=x`.
- 질문자가 값을 확실히 모르거나(약·안팎·~인지 모름) 범위로 적었거나, 서로 다른 값 중 어느 것이 맞는지 정하지 못한 숫자는 `번호=?`.
2) 그다음 (예/아니오) 항목 중 질문에 그 사실이 적힌 것만 `항목이름=예|근거` 또는 `항목이름=아니오|근거`로 쓴다. 근거는 질문의 해당 구절을 짧게 그대로 복사한다. 질문자가 모르거나 확인 중이라고 한 사실은 `항목이름=불명|근거`. 질문에 언급이 없는 사실은 줄을 쓰지 않는다.
추론하거나 계산하지 않는다. 마지막 번호와 사실 줄까지 쓰면 끝낸다.
출력 예(숫자가 3개일 때):
{example}
숫자 항목:
{numbers}"""

_N = r'[0-9０-９]+(?:[,，][0-9０-９]{3})*(?:[.．][0-9０-９]+)?'
_KOREAN_UNITS = '조억만천백십'
_KOREAN_DIGITS = '영공일이삼사오육칠팔구'
_JOIN = r'\s*(?:하고|과|및)?\s*'
_QUANTITY = re.compile(
    '|'.join(
        (
            r'(?P<date>[0-9]{4}\s*년\s*[0-9]{1,2}\s*월\s*[0-9]{1,2}\s*일'
            r'|[0-9]{4}([-./])\s*[0-9]{1,2}\2\s*[0-9]{1,2}(?![0-9]))',
            rf'(?P<money>{_N}(?:\s*[{_KOREAN_UNITS}])*(?:\s*(?:{_N}|[{_KOREAN_DIGITS}])(?:\s*[{_KOREAN_UNITS}])+)*'
            rf'(?:\s*{_N})?\s*원|(?<![가-힣])[{_KOREAN_DIGITS}{_KOREAN_UNITS}]{{2,}}\s*원'
            # Amounts may omit 원 when a large Korean unit marks them as money.
            rf'|{_N}(?:\s*[천백십]?\s*[억만])+(?!\s*(?:명|인|개|일|시간|주|회|번)))',
            rf'(?P<period>{_N}\s*년(?:{_JOIN}{_N}\s*개월)?(?:{_JOIN}{_N}\s*일)?(?!\s*[0-9]+\s*월)'
            rf'|{_N}\s*개월(?:{_JOIN}{_N}\s*일)?)',
            rf'(?P<hours>{_N}\s*시간(?:\s*{_N}\s*분)?)',
            rf'{_N}\s*(?P<unit>%|％|퍼센트|분|명|인(?![가-힣])|일|주년|주|회|세|배|개|번|차|월|시|년)?',
        )
    )
)
# Field kinds each explicit unit can fill; a bare number may fill any kind.
_UNIT_KINDS: dict[str, frozenset[str]] = {
    'date': frozenset({'date'}),
    'money': frozenset({'money'}),
    'hours': frozenset({'hours'}),
    'period': frozenset({'duration', 'count', 'year'}),
    '%': frozenset({'percent'}),
    '％': frozenset({'percent'}),
    '퍼센트': frozenset({'percent'}),
    '분': frozenset({'hours'}),
    '명': frozenset({'count'}),
    '인': frozenset({'count'}),
    '일': frozenset({'duration', 'count'}),
    '주년': frozenset({'duration'}),
    '년': frozenset({'duration', 'count', 'year'}),
    '개': frozenset({'count'}),
    '회': frozenset({'count'}),
    '번': frozenset({'count'}),
}
_MARKER = re.compile(r'\[[0-9]+:([^\]]*)\]')
_POLARITY = {'예': True, '아니오': False, '불명': None}


def quantity_tokens(question: str) -> list[tuple[str, frozenset[str] | None]]:
    """Return each number in reading order with the field kinds its unit allows."""
    tokens = []
    for match in _QUANTITY.finditer(question):
        group = match.lastgroup if match.lastgroup in ('date', 'money', 'period', 'hours') else None
        unit = group or match['unit']
        kinds = _UNIT_KINDS.get(unit, frozenset()) if unit else None
        tokens.append((match[0], kinds))
    return tokens


def marked_question(question: str) -> str:
    """Number every quantity inline and state how many labels are expected."""
    matches = list(_QUANTITY.finditer(question))
    if not matches:
        return question + '\n\n(숫자 없음)'
    parts, last = [], 0
    for index, match in enumerate(matches, 1):
        parts += [question[last : match.start()], f'[{index}:{match[0]}]']
        last = match.end()
    return ''.join(parts) + question[last:] + f'\n\n(숫자 {len(matches)}개: 1~{len(matches)}번)'


def label_max_tokens(question: str) -> int:
    return min(512, 8 * len(quantity_tokens(question)) + 80)


def field_kinds(calculator: str) -> dict[str, str]:
    labor = LABOR_CALCULATORS.get(calculator)
    if labor is not None:
        return dict(labor.FIELDS)
    return {field: 'phrase' if field == 'medical_category' else 'money' for field in FIELD_DESCRIPTIONS[calculator][1]}


def label_system(calculator: str) -> str:
    """Build the stage-2 prompt from the calculator's field descriptions."""
    title, fields, note = FIELD_DESCRIPTIONS[calculator]
    kinds = field_kinds(calculator)
    numbers = [f'{field}: {text}' for field, text in fields.items() if kinds.get(field) not in ('fact', 'phrase')]
    facts = [f'{field}: {text.removeprefix(_YN)}' for field, text in fields.items() if kinds.get(field) == 'fact']
    example = f'1={numbers[0].split(":")[0]}\n2=x\n3=?'
    if facts:
        example += f'\n{facts[0].split(":")[0]}=예|질문 구절'
    text = _LABEL_RULES.format(title=title, numbers='\n'.join(numbers), example=example)
    if facts:
        text += '\n(예/아니오) 항목:\n' + '\n'.join(facts)
    if 'medical_category' in fields:
        text += '\n구절 항목(질문 구절을 그대로 복사해 `항목=구절`):\nmedical_category: ' + fields['medical_category']
    return text + ('\n' + note if note else '')


def parse_selection(text: str | None) -> str | None:
    word = (text or '').strip().strip('`"\'. ')
    return word if word in FIELD_DESCRIPTIONS or word in NON_CALCULATOR_CHOICES else None


def _evidence(question: str, text: str) -> str | None:
    span = _MARKER.sub(r'\1', text).strip().strip('"\'`')
    return span if span and span in question else None


def compose_route(question: str, selection: str | None, labels: str | None = None) -> str | None:
    """Convert stage outputs to the router JSON, or None when stage 1 was unusable.

    Unlabeled numbers, numbers judged uncertain, numbers given an unknown field
    and fields given two different numbers become the ``ambiguity`` span so the
    router asks instead of guessing.
    A number labeled with a field its unit cannot fill is treated as not an input.
    """
    if selection is None:
        return None
    if selection == 'lookup':
        return json.dumps({'calculator': 'none', 'kind': 'lookup'})
    if selection == 'unsupported':
        return json.dumps({'calculator': 'none', 'kind': 'calculation'})
    kinds = field_kinds(selection)
    tokens = quantity_tokens(question)
    assigned: dict[int, str] = {}
    inputs: dict[str, str] = {}
    facts: dict[str, bool | None] = {}
    ambiguity = None
    for line in (labels or '').splitlines():
        key, sep, value = (part.strip() for part in line.strip().strip('`').partition('='))
        if not sep or not value:
            continue
        if key.isdigit():
            index = int(key) - 1
            if 0 <= index < len(tokens) and index not in assigned:
                assigned[index] = value
            continue
        kind = kinds.get(key)
        if kind == 'phrase':
            if (span := _evidence(question, value)) is not None:
                inputs[key] = span
            continue
        polarity, bar, evidence = (part.strip() for part in value.partition('|'))
        if not bar or polarity not in _POLARITY or (span := _evidence(question, evidence)) is None:
            continue
        if kind == 'fact':
            inputs[key], facts[key] = span, _POLARITY[polarity]
        elif kind is not None and _POLARITY[polarity] is None:
            ambiguity = ambiguity or span
    for index, (token, allowed) in enumerate(tokens):
        label = assigned.get(index)
        if label is None or label == '?':
            ambiguity = ambiguity or token
            continue
        kind = kinds.get(label)
        if kind is None and label != 'x':
            ambiguity = ambiguity or token
            continue
        if kind is None or kind in ('fact', 'phrase') or (allowed is not None and kind not in allowed):
            continue
        if label in inputs and re.sub(r'\s', '', inputs[label]) != re.sub(r'\s', '', token):
            ambiguity = ambiguity or token
            continue
        inputs.setdefault(label, token)
    route: dict[str, object] = {'calculator': selection, 'inputs': inputs}
    if facts and selection not in TAX_CALCULATORS:
        route['facts'] = facts
    if ambiguity is not None:
        route['ambiguity'] = ambiguity
    return json.dumps(route, ensure_ascii=False)
