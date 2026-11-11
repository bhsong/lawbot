"""Route gated questions and verify the model's excerpts before using them."""

import json
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from lawbot.answer_format import sections
from lawbot.api_client import ModelLoadError, ModelServerError, ModelServerTimeout, RouterTimeout
from lawbot.calc_numbers import (
    amount_spans,
    can_assign_spans,
    complete_amount_spans,
    complete_labor_spans,
    parse_year,
)
from lawbot.calculators import (
    ARTICLE_HASHES,
    INPUT_FIELDS,
    LABOR_CALCULATORS,
    MEDICAL,
    MEDICAL_GROUPING,
    MIDYEAR,
    TAX_CALCULATORS,
    calculate,
)
from lawbot.labor_dates import parse_labor_date
from lawbot_common.api import SERVER_BUSY

PR1_ROUTER_SYSTEM = """계산 질문 분류기. JSON 한 개만 출력한다.
사용자가 준 금액·기간·인원수로 금액·일수·세액을 구해야 답할 수 있는 질문은 계산이다. 지원하는 계산기가 없으면(일반 연말정산, 퇴직소득세 등) {"calculator":"none","kind":"calculation"}.
법령에 적힌 값·기한·요건을 묻는 질문은 조회다. 질문에 숫자가 있어도 그 숫자로 계산하지 않으면 {"calculator":"none","kind":"lookup"}.
개인 조건으로 구하는 월액·휴가일수도 계산이다. 실업·육아휴직 급여, 4대보험료, 임대료 환산 등 목록 밖 산출은 미지원이다.
지원 계산의 형식은 {"calculator":"계산기 이름","inputs":{}}.
지원 계산이면 필수 값이 없어도 계산기 이름을 선택한다. inputs 값은 질문에 있는 정확한 연속 문자열이다.
숫자·단위 전체와 부정 표현을 복사한다. 추론한 값은 생략한다. 합계 한 곳은 둘로 복사하지 않는다.
세금 공통 gross_pay=근무기간의 총급여(월급·계약연봉 제외). 세금 귀속연도는 생략.
medical_expense_credit 의료비: medical_expenses(실손 전), net_medical_expenses(실손 후), insurance_reimbursement(총실손), medical_category(대상·치료명).
구분별 *_expenses, *_reimbursement의 접두어 general(일반), unlimited(본인·65세이상·장애인·6세이하), premature(미숙아·선천성이상), infertility(난임).
midyear_income_tax 중도퇴사: pension_premium 연금, health_premium 건강, long_term_care_premium 장기요양, employment_premium 고용, withheld_income_tax 기납부소득세, withheld_local_tax 기납부지방세. 건강·요양 합계는 health_premium 한 번.
범위나 구분이 불명확하면 ambiguity에 원문을 복사한다."""

ROUTER_SYSTEM = (
    PR1_ROUTER_SYSTEM + '\n' + '\n'.join(f'{name} {module.SCHEMA}' for name, module in LABOR_CALCULATORS.items())
)

CALCULATED_INSTRUCTION = '계산 결과의 숫자를 그대로 쓰고 다시 계산하지 말 것. 결론에는 결과 항목과 금액(원)을 표의 순서대로 쓰고 중간 금액은 설명에 둘 것.'
CALCULATED_UNITS_INSTRUCTION = '표의 결과 항목과 숫자·단위를 결론에 그대로 쓰고 다시 계산하지 말 것.'
GUIDANCE_INSTRUCTION = '직접 계산하지 말고 계산에 필요한 정보와 근거 조문만 안내할 것.'
NO_CALCULATOR_MESSAGE = '이 질문은 계산 결과를 제공할 수 없습니다. 계산에 필요한 정보와 근거 조문을 확인해 주세요.'
TEMPORARY_ROUTER_MESSAGE = '계산 판정을 일시적으로 하지 못했습니다. 계산에 필요한 정보와 근거 조문을 확인해 주세요.'
_AMBIGUOUS_INPUT_MESSAGE = '계산에 사용할 금액과 그 금액이 어떤 항목에 해당하는지 확인해 주세요.'
_KST = ZoneInfo('Asia/Seoul')
_RELATIVE_YEARS = {'재작년': -2, '작년': -1, '지난해': -1, '올해': 0, '내년': 1}
_YEARS = re.compile(
    r'(?<![0-9０-９])([0-9０-９]{4}\s*년도?|[0-9０-９]{4}(?=\s*귀속)|'
    r'(?<=귀속연도 )[0-9０-９]{4}|재작년|작년|지난해|올해|내년)'
)
_DATE_TEXT = r'\d{4}(?:\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일|[./-]\d{1,2}[./-]\d{1,2})'
_HIRE_DATES = re.compile(
    rf'(?:(?:입사|채용)(?:일)?\s*(?:은|는|이|가|:)?\s*{_DATE_TEXT}|{_DATE_TEXT}\s*(?:에|부터)?\s*(?:입사|채용))'
)
_AMOUNT_RANGE = re.compile(
    r'\d[\d,.조억만천백십원 \t]*\s*(?:[~～∼–−-]|또는)\s*\d[\d,.조억만천백십 \t]*[조억만천백십원]'
)
_AMBIGUOUS_AMOUNT = re.compile(
    r'(?:비과세|실손보험금|지방소득세)[^.!?\n]{0,30}(?:인지|는지)\s*(?:모르|몰라|불명확|확실하지)'
)
_UNSUPPORTED_DETAILS = {
    'medical_expense_credit': re.compile(r'미용\s*성형|안경|콘택트렌즈|산후조리원'),
    'midyear_income_tax': re.compile(r'(?:배우자|부양가족|자녀)\s*(?:기본|인적)?\s*공제|세액\s*감면'),
}
_COMBINED_HEALTH_LABEL = '건강·장기요양보험료'
_COMBINED_HEALTH_LABEL_VARIANTS = re.compile(r'건강[ \t]*(?:[·/／]|및)?[ \t]*장기\s*요양\s*보험료')
_OPTIONAL_EVIDENCE = {
    'medical_expense_credit': (
        (
            r'실손\s*(?:보험(?:금)?)?',
            (
                'insurance_reimbursement',
                'net_medical_expenses',
                'general_reimbursement',
                'unlimited_reimbursement',
                'premature_reimbursement',
                'infertility_reimbursement',
            ),
            '실손보험금 수령액',
        ),
    ),
    MIDYEAR: (
        (r'국민연금|연금보험료', ('pension_premium',), '연금보험료'),
        (rf'{_COMBINED_HEALTH_LABEL}(?:\s*(?:합계|합산액|총액))?', ('health_premium',), _COMBINED_HEALTH_LABEL),
        (r'건강\s*보험료', ('health_premium',), '건강보험료'),
        (r'(?<!건강·)장기\s*요양\s*보험료', ('long_term_care_premium',), '장기요양보험료'),
        (r'고용보험료', ('employment_premium',), '고용보험료'),
        (r'기납부\s*(?:근로)?소득세|소득세\s*기납부', ('withheld_income_tax',), '기납부 소득세'),
        (r'기납부\s*지방소득세|지방소득세\s*기납부', ('withheld_local_tax',), '기납부 지방소득세'),
    ),
}
_STATED_WON = r'(?:[0-9０-９][0-9０-９,.]*\s*[조억만천백십원]|[영공일이삼사오육칠팔구십백천만억조]+\s*원)'
_SPECIAL_MEDICAL = {
    'infertility_expenses': re.compile(r'난임|보조생식술|시험관|인공수정'),
    'premature_expenses': re.compile(r'미숙아|선천성\s*이상아'),
}
_ORDINARY_MEDICAL = re.compile(r'(?:일반|본인)\s*(?:부양가족\s*)?(?:의료비|치료비|병원비)')
_MEDICAL_BREAKDOWN = {*_SPECIAL_MEDICAL, 'general_expenses', 'unlimited_expenses'}


@dataclass(frozen=True)
class Routing:
    payload: dict[str, Any]
    reason: str
    elapsed_ms: int | None


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate JSON key')
    return result


def _year_value(source: str, reference: int) -> int:
    offset = _RELATIVE_YEARS.get(source)
    return reference + offset if offset is not None else parse_year(source)


def _unaccounted_optional(question: str, calculator: str, inputs: dict[str, str]) -> str | None:
    if calculator == MIDYEAR:
        # Match a combined label once even if the original includes spaces around the separator.
        question = _COMBINED_HEALTH_LABEL_VARIANTS.sub(_COMBINED_HEALTH_LABEL, question)
    for label_pattern, fields, label in _OPTIONAL_EVIDENCE[calculator]:
        if any(field in inputs for field in fields):
            continue
        amount_after_label = rf'(?:{label_pattern})\s*(?:은|는|이|가|을|를|로|:)?\s*{_STATED_WON}'
        if re.search(amount_after_label, question):
            return label
    return None


def _missing_medical_category(question: str, inputs: dict[str, str]) -> bool:
    category = inputs.get('medical_category', '')
    return any(
        marker.search(question) and field not in inputs and not marker.search(category)
        for field, marker in _SPECIAL_MEDICAL.items()
    )


def _combined_medical_span(question: str, inputs: dict[str, str]) -> str | None:
    if _MEDICAL_BREAKDOWN.intersection(inputs):
        return None
    for sentence in re.findall(r'[^.!?\n]+', question):
        grouping = MEDICAL_GROUPING.search(sentence)
        if grouping is None:
            continue
        categories = [
            found
            for pattern in (*_SPECIAL_MEDICAL.values(), _ORDINARY_MEDICAL)
            if (found := pattern.search(sentence)) is not None
        ]
        if len(categories) > 1:
            spans = [grouping, *categories]
            return sentence[min(span.start() for span in spans) : max(span.end() for span in spans)]
    return None


def route_calculation(
    question: str,
    classify: Callable[[str], str | None] | None,
    compatibility: Mapping[str, tuple[str, ...]] | Callable[[], Mapping[str, tuple[str, ...]]],
    *,
    reference_year: int | None = None,
    tax_only: bool = False,
) -> Routing:
    elapsed_ms = None
    calculator = None

    def none(reason: str, *, message: str = NO_CALCULATOR_MESSAGE) -> Routing:
        return Routing({'calculator': calculator, 'status': 'none', 'message': message}, reason, elapsed_ms)

    def guidance(message: str, reason='ambiguous_input') -> Routing:
        assert calculator is not None
        return Routing(
            {
                'calculator': calculator,
                'status': 'guidance',
                'message': message,
                'articles': list(ARTICLE_HASHES[calculator]),
            },
            reason,
            elapsed_ms,
        )

    if classify is None:
        return none('pro_unavailable')
    started = time.perf_counter()
    error = None
    raw = None
    try:
        raw = classify(question)
    except RouterTimeout as exc:
        error = exc.reason
    except ModelServerTimeout:
        error = 'timeout'
    except ModelServerError as exc:
        error = 'queue_full' if exc.status == 503 and exc.kind == SERVER_BUSY else 'model_error'
    except ModelLoadError, ConnectionError:
        error = 'model_error'
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if error is not None:
        return none(error, message=TEMPORARY_ROUTER_MESSAGE)
    if not isinstance(raw, str) or len(raw) > 4096:
        return none('invalid_json')
    try:
        obj = json.loads(raw, object_pairs_hook=_unique_object)
    except ValueError, RecursionError:
        return none('invalid_json')
    if not isinstance(obj, dict):
        return none('invalid_schema')
    if obj.get('calculator') == 'none' and obj.keys() == {'calculator', 'kind'}:
        if obj['kind'] == 'lookup':
            return Routing({'calculator': None, 'status': 'lookup'}, 'lookup_question', elapsed_ms)
        if obj['kind'] == 'calculation':
            return none('unsupported_question')
    required = {'calculator', 'inputs'}
    if (
        not required <= obj.keys() <= required | {'ambiguity', 'kind'}
        or obj.get('kind', 'calculation') != 'calculation'
    ):
        return none('invalid_schema')
    name, inputs = obj['calculator'], obj['inputs']
    if not isinstance(name, str) or name not in INPUT_FIELDS or not isinstance(inputs, dict):
        return none('invalid_schema')
    if tax_only and name not in TAX_CALCULATORS:
        return none('invalid_schema')
    calculator = name
    labor = LABOR_CALCULATORS.get(calculator)
    allowed = set(INPUT_FIELDS[calculator]) | {'year', 'ambiguity'}
    top_ambiguity = obj.get('ambiguity')
    spans = [*inputs.values()]
    if 'ambiguity' in obj:
        spans.append(top_ambiguity)
    if not inputs.keys() <= allowed or any(not isinstance(span, str) or not span.strip() for span in spans):
        return none('invalid_schema')
    if labor is None:
        # The tax year is derived from the question. Older responses may still carry a year.
        inputs.pop('year', None)
        spans = [*inputs.values()]
        if top_ambiguity is not None:
            spans.append(top_ambiguity)

    ambiguous = (
        _AMOUNT_RANGE.search(question) is not None
        or _AMBIGUOUS_AMOUNT.search(question) is not None
        or any(span is not None and span in question for span in (top_ambiguity, inputs.get('ambiguity')))
    )
    # Guidance for an ambiguous question does not use the extracted inputs.
    if not ambiguous and any(span not in question for span in spans):
        return none('span_not_in_question')
    active = compatibility() if callable(compatibility) else compatibility
    if calculator not in active or active[calculator]:
        return none('corpus_mismatch')

    if ambiguous:
        return guidance(_AMBIGUOUS_INPUT_MESSAGE)
    if calculator in _UNSUPPORTED_DETAILS and _UNSUPPORTED_DETAILS[calculator].search(question):
        return guidance(
            '계산기의 적용 범위를 벗어난 항목이 있습니다. 해당 항목의 공제 요건과 공제 대상 금액을 확인해 주세요.',
            'unsupported_input',
        )
    if calculator == MEDICAL and (combined := _combined_medical_span(question, inputs)):
        inputs['medical_category'] = combined
    reference = reference_year if reference_year is not None else datetime.now(_KST).year
    if labor is not None:
        check_question = getattr(labor, 'question_issue', None)
        if check_question is not None and (issue := check_question(question, inputs)):
            return guidance(issue)
        for field, source in inputs.items():
            kind = labor.FIELDS.get(field)
            if (
                kind is not None
                and kind not in ('money', 'fact')
                and not any(complete_labor_spans(question, source, kind))
            ):
                return none('incomplete_quantity_span')
    try:
        years = _calculation_years(question, inputs, reference, calculator)
    except ValueError, OverflowError:
        return none('conversion_failed')
    if len(set(years.values())) > 1:
        return guidance('어느 귀속연도를 기준으로 계산할지 알려 주세요.')
    year = next(iter(years.values()), reference)
    year_source = next(iter(years), None)
    if labor is not None and 'year' in labor.FIELDS:
        inputs.pop('year', None)
        if year_source is not None:
            inputs['year'] = year_source

    candidates = [
        tuple(complete_amount_spans(question, source))
        for field, source in inputs.items()
        if (labor.FIELDS.get(field) == 'money' if labor else field != 'medical_category')
    ]
    if not all(candidates):
        return none('incomplete_amount_span')
    if not can_assign_spans(candidates):
        return guidance(_AMBIGUOUS_INPUT_MESSAGE)
    try:
        calculation = calculate(calculator, inputs, year=year, year_source=year_source)
    except ValueError:
        return none('conversion_failed')
    payload = calculation.as_dict()
    if labor is None and payload['status'] == 'calculated':
        if label := _unaccounted_optional(question, calculator, inputs):
            return guidance(f'계산에 사용할 {label} 항목을 확인해 주세요.', 'unresolved_input')
        if calculator == MEDICAL and _missing_medical_category(question, inputs):
            return guidance('의료비의 대상 구분과 구분별 금액을 알려 주시면 계산할 수 있습니다.', 'unresolved_input')
    return Routing(payload, calculation.reason or 'selected', elapsed_ms)


def _calculation_years(question: str, inputs: dict[str, str], reference: int, calculator: str) -> dict[str, int]:
    if calculator in LABOR_CALCULATORS:
        event_fields = ('as_of_date', 'retirement_date', 'last_employed_date')
        for field in event_fields:
            if source := inputs.get(field):
                event = parse_labor_date(source)
                if calculator == 'severance_pay' and field == 'last_employed_date':
                    event += timedelta(days=1)
                return {source: event.year}
        year_context = _HIRE_DATES.sub(' ', question)
        explicit = {match[0]: _year_value(match[0], reference) for match in _YEARS.finditer(year_context)}
        if explicit:
            return explicit
        if (source := inputs.get('year')) and re.fullmatch(r'[0-9０-９]{4}', source.strip()):
            # A bare year is accepted only outside a longer number.
            if re.search(rf'(?<!\d){re.escape(source.strip())}(?!\d|[./-]\d)', year_context):
                return {source: int(source)}
        return {}
    return {match[0]: _year_value(match[0], reference) for match in _YEARS.finditer(question)}


def prompt_suffix(payload: dict[str, Any]) -> str:
    instruction = CALCULATED_INSTRUCTION if payload['status'] == 'calculated' else GUIDANCE_INSTRUCTION
    if payload['status'] == 'calculated' and any(row.get('unit', '원') != '원' for row in payload['results']):
        instruction = CALCULATED_UNITS_INSTRUCTION
    return f'\n\n[계산 결과]\n{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}\n{instruction}'


def _display_values(row: dict[str, Any]) -> set[tuple[str, Decimal]]:
    value = row['value']
    if isinstance(value, int) or 'unit' in row:
        try:
            return {(row.get('unit', '원'), Decimal(str(value).replace(',', '')))}
        except InvalidOperation:
            return set()
    return {('원', Decimal(amount)) for _, amount in amount_spans(value)}


def check_conclusion(raw: str, payload: dict[str, Any]) -> Literal['match', 'mismatch', 'missing']:
    marked = sections(raw)
    conclusion = marked.get('결론', '' if marked else raw)
    conclusion = re.sub(r'(?m)^[ \t]*-[ \t]+', '', conclusion)
    required = {item for result in payload['results'] for item in _display_values(result)}
    observed = {('원', Decimal(value)) for _, value in amount_spans(conclusion)}
    if any(unit == '일' for unit, _ in required):
        days_text = re.sub(r'\d{1,2}\s*월\s*\d{1,2}\s*일', '', unicodedata.normalize('NFKC', conclusion))
        day_amounts = re.findall(r'(?<![\d.,])[-−]?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?=일)', days_text)
        observed.update(
            ('일', Decimal(''.join(value.split()).replace('−', '-').replace(',', ''))) for value in day_amounts
        )
    if not observed:
        return 'missing'
    allowed = required.copy()
    for row in (*payload['inputs'], *payload['steps']):
        allowed.update(_display_values(row))
    return 'match' if required <= observed <= allowed else 'mismatch'
