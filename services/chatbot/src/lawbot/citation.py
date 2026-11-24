# src/lawbot/citation.py  →  조문 키·인용 파싱·인용 검증·거절 판정 (웹앱 화면과 eval_v4 채점이 같이 쓴다)
# 웹 서버(fastapi)를 import하지 않는다 (평가에서도 씀, tests/test_citation.py가 검사)
# 검색 결과 dict 형식은 그대로 쓴다: 조문 키는 웹앱 쪽에서 law + ' ' + article_id로 만든다 (retriever·서버 API 변경 없음)
# 평가 도구(lawbot_tools)의 refusal.is_refusal(【결론】에 '확인할 수 없')은 키워드 채점과 v1 기록 재현 테스트가 쓴다
import re
from dataclasses import dataclass

from lawbot.hits import Hit

SUB_LAWS = ('시행령', '시행규칙')  # 법령명 뒤에 붙으면 법령명의 일부 ('근로기준법 시행령')


def norm(s: str) -> str:
    """비교용 정규화: 가운뎃점 통일, 숫자 사이 천 단위 쉼표·공백 제거 (키워드 채점 score_answers와 같은 규칙)"""
    s = str(s)
    s = s.replace('ㆍ', '·').replace('・', '·')
    s = re.sub(r'(?<=\d),(?=\d{3})', '', s)
    s = re.sub(r'\s+', '', s)
    return s


def conclusion_part(answer: str) -> str:
    """【결론】 부분. 표시가 없으면 답변 앞 150자"""
    a = str(answer)
    m = re.search(r'【결론】(.*?)(?=【|$)', a, re.S)
    return m.group(1) if m else a[:150]


# 줄임말로 인용해도 인정할 법령 이름 (공백·가운뎃점은 norm으로 맞춤)
LAW_ALIASES = {
    '근로기준법': ('근로기준법',),
    '근로자퇴직급여 보장법': ('근로자퇴직급여 보장법', '근로자퇴직급여보장법', '퇴직급여법'),
    '최저임금법': ('최저임금법',),
    '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률': (
        '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률',
        '남녀고용평등법',
        '고평법',
    ),
    '기간제 및 단시간근로자 보호 등에 관한 법률': ('기간제 및 단시간근로자 보호 등에 관한 법률', '기간제법'),
    '파견근로자 보호 등에 관한 법률': ('파견근로자 보호 등에 관한 법률', '파견법'),
    '임금채권보장법': ('임금채권보장법',),
    # v5 세금·보험 (docs/archives/v5-tax-scope.md): 소득세법 전체 및 나머지 선택 조문
    '소득세법': ('소득세법',),
    '조세특례제한법': ('조세특례제한법', '조특법'),
    '지방세법': ('지방세법',),
    '국세기본법': ('국세기본법',),
    '농어촌특별세법': ('농어촌특별세법', '농특세법'),
    '국민연금법': ('국민연금법',),
    '국민건강보험법': ('국민건강보험법', '건강보험법'),
    '노인장기요양보험법': ('노인장기요양보험법', '장기요양보험법'),
    '고용보험법': ('고용보험법',),
    '고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률': (
        '고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률',
        '보험료징수법',
        '고용산재보험료징수법',
    ),
}

# 거절 판정: v1 규칙(【결론】에 '확인할 수 없')은 4B 학습 문구만 잡는다. 튜닝 전 27B는 같은 뜻을 다른 말로 쓰므로
# 근거·답변이 없다는 표현을 함께 본다 (in-scope 200문항 결과에서 이 확장으로 새로 잡힌 거절 0건 확인)
REFUSAL = re.compile(
    r'확인할 수 없|근거를 찾을 수 없|근거를 찾지 못|근거가 (?:없|되지 않|포함되어 있지 않)|포함되어 있지 않'
    r'|답변할 수 없|답변드릴 수 없|답할 수 없|판단할 수 없'
)

# 단서 문장: 답을 한 뒤 '다만, 제공된 조문에는 … 절차가 포함되어 있지 않아 확인할 수 없습니다'처럼 덧붙인 한계는 거절이 아님
# (val 200문항에서 거짓 거절로 잡힌 9건 중 1건이 이 경우. test·범위 밖·정답 없는 참고 조문 결과는 바뀌지 않음)
SENTENCE = re.compile(r'(?<=[.!?])\s+|\n+')
CAVEAT = re.compile(r'\s*(?:다만|단,)')


def refused(answer):
    """【결론】(없으면 앞 150자)에서 단서 문장('다만', '단,'으로 시작)을 뺀 문장에 근거·답변이 없다는 표현이 있으면 거절"""
    return any(REFUSAL.search(s) for s in SENTENCE.split(conclusion_part(answer)) if not CAVEAT.match(s))


# ---- 조문 키 ----
def key(law, no):
    return f'{law} {no}'


def hit_law(h: Hit) -> str:
    return h['law']


def hit_key(h: Hit) -> str:
    """검색 결과 dict → '근로기준법 제26조' (eval_v4 정답 조문과 같은 형식)"""
    return key(hit_law(h), h['article_id'])


def article_keys(retriever):
    """검색기(로컬 Retriever·RemoteRetriever)의 전체 조문 → {(법령, 제N조)}. 삭제 조문은 검색기에 없다
    하위법령을 켠 검색기는 하위법령 397개도 포함 (법령 '근로기준법 시행령')"""
    return set(zip(map(str, retriever.laws), map(str, retriever.ids)))


# ---- 인용 파싱 ----
_ALIAS = sorted(((norm(a), law) for law, al in LAW_ALIASES.items() for a in al), key=lambda x: -len(x[0]))
_TOKEN = re.compile(
    '(?P<law>' + '|'.join(re.escape(a) for a, _ in _ALIAS) + ')(?P<sub>' + '|'.join(SUB_LAWS) + ')?'
    r'|(?P<add>부칙(?:[<(（\[]?(?P<num>제\d+호)[>)）\]]?)?)?(?P<no>제\d+조(?:의\d+)?)'
)


def article_no(m: re.Match) -> str:
    """'제26조', 부칙 특례 '부칙<제20903호>제4조' (데이터 article_no 형식). 공포번호 없는 '부칙 제4조'는 어느 부칙인지
    몰라 '부칙제4조' (본칙 제4조와 섞이지 않게)"""
    if not m['add']:
        return m['no']
    return f'부칙<{m["num"]}>{m["no"]}' if m['num'] else f'부칙{m["no"]}'


def cite_section(answer):
    """【근거 조문】 부분. 표시가 없으면 답변 전체"""
    m = re.search(r'【근거 조문】(.*?)(?=【|$)', str(answer), re.S)
    return m.group(1) if m else str(answer)


def parse(text):
    """글에서 인용한 (법령, 제N조) 목록 (나온 순서, 중복 없음). 부칙 특례는 (법령, '부칙<제N호>제M조'). 법령명 없이 이어 쓴 번호는 앞의 법령으로 본다
    ('근로기준법 제26조, 제27조' → 둘 다 근로기준법). 앞에 법령명이 전혀 없으면 (None, 번호)
    '근로기준법 시행령 제5조'는 ('근로기준법 시행령', '제5조'): 같은 번호의 법률 조문과 구분한다
    거절 형식('해당 없음 (제공된 조문: …)')은 빈 목록"""
    if str(text).strip().startswith('해당 없음'):
        return []
    alias = dict(_ALIAS)
    law, out = None, []
    for m in _TOKEN.finditer(norm(text)):
        if m['law']:
            law = alias[m['law']] + (' ' + m['sub'] if m['sub'] else '')
        else:
            no = article_no(m)
            if (law, no) not in out:
                out.append((law, no))
    return out


def citations(answer):
    """답변의 【근거 조문】(없으면 답변 전체)에서 인용한 (법령, 제N조) 목록"""
    return parse(cite_section(answer))


# ---- 인용 검증 ----
OK, OUTSIDE, WRONG_LAW, NONEXISTENT, AMBIGUOUS = 'ok', 'outside', 'wrong_law', 'nonexistent', 'ambiguous'


def check(cites, hits: list[Hit], articles=None):
    """인용마다 (법령, 제N조, 판정, 맞는 검색 결과 dict 또는 None)
    판정 (위에서부터 먼저 맞는 것):
      ok         : 검색 결과(답변에 준 조문)에 있음. 법령명이 없으면 그 번호의 검색 결과가 하나뿐일 때
      ambiguous  : 법령명이 없고, 그 번호가 검색 결과(없으면 전체 조문)에서 여러 법령에 있음
      wrong_law  : 같은 번호의 다른 법령 조문이 검색 결과에 있음 (법령을 헷갈림)
      outside    : 전체 조문에는 있지만 검색 결과 밖
      nonexistent: 전체 조문에 없음 (삭제 조문 포함). 전체 조문은 검색기 기준: 하위법령을 끄면 법률 415개라
                   하위법령 인용도 nonexistent, 켜면 812개('근로기준법 시행령', '제2조')라 참고 조문 밖이면 outside
    articles: 전체 조문 {(법령, 제N조)} (article_keys). None이면 존재 여부를 모르므로 검색 결과 밖은 모두 outside
    hits에 첨부한 하위법령 dict(law '근로기준법 시행령')도 법률 조문과 같은 규칙으로 판정한다"""
    by_key = {(hit_law(h), h['article_id']): h for h in hits}
    out = []
    for law, no in cites:
        same_no = [h for (l, a), h in by_key.items() if a == no]
        if law is None:
            if len(same_no) == 1:
                verdict, h = OK, same_no[0]
            elif same_no or (articles is not None and len({l for l, a in articles if a == no}) > 1):
                verdict, h = AMBIGUOUS, None
            elif articles is not None and not any(a == no for _, a in articles):
                verdict, h = NONEXISTENT, None
            else:
                verdict, h = OUTSIDE, None
        elif (law, no) in by_key:
            verdict, h = OK, by_key[(law, no)]
        elif same_no:
            verdict, h = WRONG_LAW, None
        elif articles is not None and (law, no) not in articles:
            verdict, h = NONEXISTENT, None
        else:
            verdict, h = OUTSIDE, None
        out.append((law, no, verdict, h))
    return out


@dataclass(frozen=True)
class CitationSpan:
    """One citation occurrence, with a half-open span in the original text."""

    start: int
    end: int
    law: str | None
    article_id: str
    verdict: str
    hit: Hit | None
    attribution: str


_SPAN_ALIASES = dict(_ALIAS)
_KNOWN_NAME = '|'.join(r'[ \t]*'.join('[ㆍ·・]' if ch == '·' else re.escape(ch) for ch in alias) for alias, _ in _ALIAS)
_LONG_NAME = r'[가-힣0-9ㆍ·・]+(?:[ \t]+[가-힣0-9ㆍ·・]+){0,16}?[ \t]*에[ \t]*(?:관한|따른)[ \t]*법률'
_SUB = r'시행령|시행규칙'
_SUBS = rf'(?:{_SUB})(?:[ \t]*[·ㆍ・/][ \t]*(?:{_SUB}))*'
_PARTICLE = r'에서는|에서|으로는|으로|에는|에|은|는|이|가|을|를|의|과|와|도'
_NAME_END = rf'(?:(?:{_PARTICLE})?(?![가-힣])|(?={_SUBS}|제\s*\d|부칙))'
_KNOWN = re.compile(rf'(?<![가-힣])(?:{_KNOWN_NAME})(?={_NAME_END})')
_LAW_SUFFIX = re.compile(rf'(?:[ \t]*(?P<sub>{_SUBS}))?(?:{_PARTICLE})?(?=[^가-힣]|제\s*\d|부칙|$)')
_RELATIVE = re.compile(rf'(?<![가-힣])(?:(?:같은[ \t]*법|동법|법)(?:[ \t]*(?P<sub>{_SUBS}))?|(?P<only>{_SUBS}))\s*$')
_PART = r'(?:제\s*\d+\s*[항호목](?:\s*의\s*\d+)?|[가-하]목)'
_ADDENDUM_NOTE = r'(?:\([^()\n]{0,50}\)|（[^（）\n]{0,50}）|<[^<>\n]{0,50}>|\[[^\[\]\n]{0,50}\])'
_ADDENDUM = rf'부칙(?:\s*{_ADDENDUM_NOTE}|[^.!?。！？\n]{{0,50}}?)'
_ARTICLE_HEAD = r'제\s*\d+\s*조'
_OTHER = re.compile(
    rf'(?<![가-힣])(?:{_LONG_NAME}(?={_NAME_END})'
    rf'|[가-힣0-9ㆍ·・]+?(?:법률|법|조례)(?=(?:{_PARTICLE})?\s*(?:'
    rf'(?:{_SUBS})(?={_NAME_END})|(?:{_ADDENDUM}\s*)?{_ARTICLE_HEAD})))'
)
# Nearby article numbers also occur after ordinary nouns and statute categories.
_NON_NAMES = frozenset('동법 같은법 방법 적법 위법 불법 편법 문법 해법 용법 수법 기법 세법 과세법 노동법'.split())
_NUMBER = re.compile(
    rf'(?:(?P<add>{_ADDENDUM})\s*)?(?P<no>{_ARTICLE_HEAD}(?:\s*의\s*\d+)?)'
    rf'(?:\s*{_PART})*(?:\s*[·ㆍ・,]\s*{_PART}(?:\s*{_PART})*)*'
)
_GAZETTE_NO = re.compile(r'부칙(?:제(\d+)호|\(제(\d+)호\)|<제(\d+)호>|\[제(\d+)호\]|（제(\d+)호）)')
_SENTENCE_END = re.compile(r'[!?。！？]+|(?<!\d)\.+|\.+(?!\d)')
_LINES = re.compile(r'\r\n|\r|\n')
_PARENT = re.compile(rf' (?:{_SUB})$')
_QUOTES = {'「': '」', '『': '』'}
_QUOTED_NAME = re.compile(r'「(?P<corner>[^」\n]+(?:법률|법|조례))」|『(?P<double>[^』\n]+(?:법률|법|조례))』')


@dataclass(frozen=True)
class _LawMention:
    start: int
    end: int
    names: tuple[str, ...]


def _with_subs(laws: tuple[str, ...], subs: str | None) -> tuple[str, ...]:
    if not subs:
        return laws
    return tuple(dict.fromkeys(f'{_PARENT.sub("", law)} {sub}' for law in laws for sub in re.findall(_SUB, subs)))


def _law_mentions(text: str) -> list[_LawMention]:
    known = list(_KNOWN.finditer(text))
    # A complete registered name wins over an overlapping long generic match.
    known_ends = {m.end() for m in known}
    other = (m for m in _OTHER.finditer(text) if m.end() not in known_ends and m[0] not in _NON_NAMES)
    out = []
    for token in sorted([*known, *other, *_QUOTED_NAME.finditer(text)], key=lambda m: m.start()):
        start, end = token.span()
        if out and start < out[-1].end:
            continue
        name = (token['corner'] or token['double']) if token.re is _QUOTED_NAME else token[0]
        name = _SPAN_ALIASES.get(norm(name), name)
        if start and text[start - 1] in _QUOTES and text[end : end + 1] == _QUOTES[text[start - 1]]:
            start, end = start - 1, end + 1
        suffix = _LAW_SUFFIX.match(text, end)
        if suffix is not None:
            end = suffix.end()
        out.append(_LawMention(start, end, _with_subs((name,), suffix['sub'] if suffix else None)))
    return out


def _article_id(token: re.Match[str]) -> str:
    no = norm(token['no'])
    if token['add'] is None:
        return no
    gazette = _GAZETTE_NO.fullmatch(norm(token['add']))
    if gazette:
        return f'부칙<제{next(n for n in gazette.groups() if n)}호>{no}'
    return '부칙' + no


def _citation_scopes(text: str, line_scoped: bool):
    numbers = iter(_NUMBER.finditer(text))
    number = next(numbers, None)
    endings = sorted(
        [(m.end(), False) for m in _SENTENCE_END.finditer(text)]
        + ([(m.end(), True) for m in _LINES.finditer(text)] if line_scoped else [])
    )
    for end, line_break in endings:
        while number and number.end() < end:
            number = next(numbers, None)
        if not number or not number.start() < end <= number.end():
            yield end, line_break
    yield len(text), False


def _attributed_numbers(text: str, line_scoped: bool):
    previous: tuple[str, ...] = ()
    start = 0
    for end, line_break in _citation_scopes(text, line_scoped):
        if start == end:
            continue
        sentence = text[start:end]
        mentions = _law_mentions(sentence)
        law_index = 0
        named = ()
        active = ()
        attribution = 'unresolved'
        last_end = 0
        for number in _NUMBER.finditer(sentence):
            left, right = number.span()
            while law_index < len(mentions) and mentions[law_index].end <= left:
                named = active = mentions[law_index].names
                attribution = 'same_sentence'
                law_index += 1
            mention = mentions[law_index - 1] if law_index else None
            relative = _RELATIVE.search(sentence, last_end, left)
            if relative and mention and relative.start() < mention.end:
                relative = None
            if relative:
                active = _with_subs(named or previous, relative['sub'] or relative['only'])
                attribution = 'same_sentence' if named else 'previous_sentence' if previous else 'unresolved'
                left = relative.start()
            elif mention and not sentence[mention.end : left].strip():
                left = mention.start
            names = active or (previous if relative is None else ())
            source = attribution if active else 'previous_sentence' if names else 'unresolved'
            quote_start = len(sentence[:left].rstrip(' \t')) - 1
            quote_end = right + len(sentence[right:]) - len(sentence[right:].lstrip(' \t'))
            if (
                quote_start >= 0
                and (close := _QUOTES.get(sentence[quote_start]))
                and sentence[quote_end : quote_end + 1] == close
            ):
                left, right = quote_start, quote_end + 1
            yield start + left, start + right, names, _article_id(number), source, relative is not None
            last_end = right
        previous = mentions[-1].names if mentions and not line_break else ()
        start = end


def check_spans(
    text: str,
    hits: list[Hit],
    articles: set[tuple[str, str]] | None = None,
    *,
    verified: frozenset[tuple[str, str]] = frozenset(),
    line_scoped: bool = False,
) -> list[CitationSpan]:
    """Validate each original citation, with explicit attribution and no synthetic search hit."""
    provided = {(hit_law(h), h['article_id']): h for h in hits}
    keys = provided.keys() | verified
    registered = LAW_ALIASES.keys() | {law for law, _ in keys}
    out = []
    for start, end, names, no, attribution, relative in _attributed_numbers(text, line_scoped):
        matching = {law for law, article in keys if article == no}
        if not names and not relative and len(matching) == 1:
            names = tuple(matching)
            attribution = 'unique_provided'
        for law in names or (None,):
            hit = provided.get((law, no)) if law else None
            if hit is not None or (law, no) in verified:
                verdict = OK
            elif law and law not in registered and _PARENT.sub('', law) not in registered:
                verdict = OUTSIDE
            elif law and matching:
                verdict = WRONG_LAW
            elif not law and (relative or matching or (articles and sum(a == no for _, a in articles) > 1)):
                verdict = AMBIGUOUS
            elif articles is not None and not any(a == no and (law is None or name == law) for name, a in articles):
                verdict = NONEXISTENT
            else:
                verdict = OUTSIDE
            out.append(CitationSpan(start, end, law, no, verdict, hit, attribution))
    return out
