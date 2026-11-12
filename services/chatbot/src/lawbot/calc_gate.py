"""One deterministic eligibility rule for calculation routing and all caches."""

import re
import unicodedata

_QUANTITY = re.compile(
    r'\d[\d,.]*\s*(?:[조억만천백십]?[ \t]*원|[조억만천백십]|%|개월|시간|일|년|명|세|주)'
    r'|[영공일이삼사오육칠팔구십백천만억조]+\s*원'
    r'|\d[\d,.]*\s*~\s*\d'
    r'|(?:올해|작년|지난해|재작년|내년)'
    r'|(?:총급여|의료비|병원비|급여|월급|시급)\s*(?:가|는|이|을|를|:)?\s*\d'
)
_INTENT = re.compile(
    r'얼마|며칠|몇\s*(?:원|일|시간|개월)|계산\s*(?:해|하면|할\s*수|부탁|좀)|'
    r'산출\s*(?:해|하면|할\s*수|부탁)|환산\s*(?:해|하면)|'
    r'(?:액|금액|세액)\s*(?:을|이|은|는|가)?\s*(?:알고|궁금|구해|알려|부탁|나오|있나|어떻게)|'
    r'(?:공제액|공제금액|결정세액|환급액|납부액|정산액|실수령액|지급액)\s*은?\s*[?？]|'
    r'(?:얼마|금액|세금|소득세)\s*(?:을|를)?\s*(?:구해|계산)'
)
_LABOR_INTENT = re.compile(
    r'(?:퇴직금|주휴수당|연차(?:유급휴가)?(?:\s*일수)?|가산(?:수당|분)|월\s*환산액|기본임금\s*포함\s*총액)'
    r'\s*(?:을|를|은|는|이|가)?\s*(?:구해|알려|발생하나요|요?\s*[?？])'
)
_ARTICLE_NUMBER = re.compile(r'제\s*\d+\s*조(?:의\s*\d+)?')


def is_calculation_question(question: str) -> bool:
    normalized = unicodedata.normalize('NFKC', question)
    if _INTENT.search(normalized):
        return _QUANTITY.search(normalized) is not None
    return bool(_LABOR_INTENT.search(normalized) and _QUANTITY.search(_ARTICLE_NUMBER.sub(' ', normalized)))
