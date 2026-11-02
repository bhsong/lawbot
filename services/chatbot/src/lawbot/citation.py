# src/lawbot/citation.py  →  조문 키·인용 파싱·인용 검증·거절 판정 (웹앱 화면과 eval_v4 채점이 같이 쓴다)
# gradio를 import하지 않는다 (평가에서도 씀, tests/test_citation.py가 검사)
# 검색 결과 dict 형식은 그대로 쓴다: 조문 키는 웹앱 쪽에서 law + ' ' + article_id로 만든다 (retriever·서버 API 변경 없음)
# 평가 도구(lawbot_tools)의 refusal.is_refusal(【결론】에 '확인할 수 없')은 키워드 채점과 v1 기록 재현 테스트가 쓴다
import re

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
    r'|(?P<no>제\d+조(?:의\d+)?)'
)


def cite_section(answer):
    """【근거 조문】 부분. 표시가 없으면 답변 전체"""
    m = re.search(r'【근거 조문】(.*?)(?=【|$)', str(answer), re.S)
    return m.group(1) if m else str(answer)


def parse(text):
    """글에서 인용한 (법령, 제N조) 목록 (나온 순서, 중복 없음). 법령명 없이 이어 쓴 번호는 앞의 법령으로 본다
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
        elif (law, m['no']) not in out:
            out.append((law, m['no']))
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
