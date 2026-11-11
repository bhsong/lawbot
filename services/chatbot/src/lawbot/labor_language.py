"""Read employment facts without treating uncertain or negated fragments as evidence."""

import re
import unicodedata
from typing import Literal

_UNCERTAIN = re.compile(
    r'인지|는지|여부|모르|몰라|불명확|불확실|미정|또는|혹은|것같|보기어렵|듯(?:합|하|해)|'
    r'알수없|확실하지|애매|확인(?:이)?어렵'
)
_WEEK = r'(?:(?<!\d)7일|일주일|(?:한|해당|이번|그)주(?:간|동안|내내))'
_RELATION = re.compile(r'근로관계|고용관계|재직|근로계약|고용계약')
_NOT_EMPLOYED = re.compile(
    r'미재직|재직(?:중|상태)?(?:(?:하지|되지)(?:않|못)|(?:이|은|가)?(?:아니|아닙)|안(?:했|하))|'
    r'미유지|(?:유지|존속|계속)(?:하지|되지)?(?:않|못|안)|이어지지(?:않|못)'
)
_CONTINUED = re.compile(
    r'재직(?=$|[,.!?]|중|했|합|하며|하고|하였|한|상태(?:였|이었|입))|'
    r'(?:유지|존속|계속)(?=$|[,.!?]|시|했|합|한|하고|하며|하였|됐|되었|되는|되고|되어)|'
    r'이어(?:졌|지고|지며)|끝까지'
)
_WORKING = re.compile(r'근무중(?!(?:이|은)?(?:아니|아닙|아님))')
_WORK_WEEK = re.compile(r'(?:(?<!\d)7일|일주일)(?:간|동안|내내|계속)|(?:한|해당|이번|그)주(?:간|동안|내내)')
_ENDED = re.compile(r'종료|끝났|해지|해고|퇴사|만료')
_BEFORE_WEEK_END = re.compile(rf'{_WEEK}(?:이)?(?:되기)?(?:이전|전에|전)')
_SHORT_WEEK = re.compile(rf'{_WEEK}(?:이|을|은)?(?:미만(?![은이가]?(?:아니|아닙|아님))|(?:다)?채우지못)')
_BEFORE_ACCRUAL = re.compile(r'(?:발생일|다음날|1주년)(?:이)?(?:되기)?(?:이전|전에|전|직전)')


def normalize_fact(text: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', text).split())


def is_uncertain(text: str) -> bool:
    return _UNCERTAIN.search(normalize_fact(text)) is not None


_QUANTITY_UNIT = r'(?:명|인|년|개월|일|시간|분|%|원)'
_QUANTITY_QUALIFIER = re.compile(
    r'[~～∼]|\d[-–—]\d|(?:대략|약|대충)(?=[\d영공일이삼사오육칠팔구십백천만억조])|'
    rf'{_QUANTITY_UNIT}(?:이상|이하|미만|초과|가량|정도|안팎|내외|전후)|최소|최대'
)
_QUANTITY_NEGATION = re.compile(
    rf'{_QUANTITY_UNIT}(?:간)?(?:[이가은는도]?(?:아니|아닙|아님)'
    r'|[^\d.!?;\n]{0,28}(?:(?:것|게)[은는이가도]?(?:아니|아닙|아님)|지(?:않|못)|안(?:했|합|하)))'
)
_QUANTITY_COMPARISON = re.compile(
    rf'{_QUANTITY_UNIT}(?:보다|에|을|를|이|가|은|는)?(?:더|훨씬|조금|채)?'
    r'(?:적|많|작|크|짧|길|넘|못미치|미치지|안되|되지않)'
)


def is_uncertain_quantity(text: str) -> bool:
    """Do not remove uncertainty, approximation or bounds along with quantity context."""
    value = normalize_fact(text)
    return any(
        pattern.search(value) is not None
        for pattern in (_UNCERTAIN, _QUANTITY_QUALIFIER, _QUANTITY_NEGATION, _QUANTITY_COMPARISON)
    )


def classify_employment(source: str, *, weekly: bool = False) -> bool | None:
    """Read a verified employment fact for an accrual date or an entire week."""
    text = normalize_fact(source)
    if _UNCERTAIN.search(text) or (weekly and not re.search(_WEEK, text)):
        return None
    relation = _RELATION.search(text) is not None
    before_end = _BEFORE_WEEK_END if weekly else _BEFORE_ACCRUAL
    if before_end.search(text) and _ENDED.search(text) and (relation or re.search(r'해고|퇴사', text)):
        return False
    if weekly and relation and _SHORT_WEEK.search(text):
        return False
    working = _WORKING.search(text) is not None and (not weekly or _WORK_WEEK.search(text) is not None)
    if not relation and not working:
        return None
    text, negative = _NOT_EMPLOYED.subn(' ', text)
    positive = working or _CONTINUED.search(text) is not None
    if positive == bool(negative):
        return None
    return positive


_PAY_SCOPE_UNCERTAIN = re.compile(r'\s*(?:인|는)지|\s*여부|\s*일지')
_BASE_WAGE_NAME = r'기본\s*(?:임금|급)'
_BASE_WAGE = rf'{_BASE_WAGE_NAME}\s*(?:(?:을|은|도|과|에|까지)\s*)?'
_BASE_INCLUDED = re.compile(
    rf'{_BASE_WAGE}(?:포함(?!\s*하지\s*(?:않|말|못))|합산|더(?:한|해|하|할|함))'
    rf'|{_BASE_WAGE_NAME}\s*까지(?!\s*(?:포함|제외|빼))'
)
_TOTAL_PAY = re.compile(r'총\s*(?:지급액|임금)|임금\s*총액')
_PREMIUM_ONLY = re.compile(
    r'(?:법정\s*)?(?:가산|추가)\s*(?:수당|분|액)\s*만(?!\s*(?:큼|(?:은|이)?\s*아니))'
    rf'|{_BASE_WAGE}(?:제외|빼고|포함\s*하지\s*(?:않|말|못))'
)
_PREMIUM_AMOUNT = re.compile(r'(?:법정\s*)?(?:가산|추가)\s*(?:분|액)|법정\s*가산\s*수당')


def premium_pay_scope(text: str) -> tuple[Literal['base', 'premium', 'ambiguous', 'unspecified'], str | None]:
    """Identify the requested amounts and preserve their exact wording."""
    base = _BASE_INCLUDED.search(text)
    exclusive = _PREMIUM_ONLY.search(text)
    if base and exclusive:
        return 'ambiguous', None

    if exclusive:
        scope, evidence = 'premium', exclusive
    elif evidence := base or _TOTAL_PAY.search(text):
        scope = 'base'
    else:
        scope, evidence = 'premium', _PREMIUM_AMOUNT.search(text)
    if not evidence:
        return 'unspecified', None
    if _PAY_SCOPE_UNCERTAIN.match(text, evidence.end()):
        return 'ambiguous', None
    return scope, evidence[0]
