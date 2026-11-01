# src/lawbot_tools/refusal.py
# 거절 판정 (lawbot_2 false_refusal, lawbot_5 refusal_rate와 같은 규칙)
import re


def conclusion(answer):
    """【결론】 부분. 표시가 없으면 답변 앞 150자"""
    m = re.search(r'【결론】(.*?)(?=【|$)', answer, re.S)
    return m.group(1) if m else answer[:150]


def is_refusal(answer):
    return '확인할 수 없' in conclusion(answer)


def cited_articles(answer):
    """【근거 조문】에 든 조문 번호. 거절 형식('해당 없음 …')이면 빈 목록"""
    m = re.search(r'【근거 조문】(.*?)(?=【|$)', answer, re.S)
    if not m or m.group(1).strip().startswith('해당 없음'):
        return []
    return re.findall(r'제\d+조(?:의\d+)?', m.group(1))


def oos_verdict(answer, refer_lsa=None):
    """범위 밖 질문 답변 판정
    거절: 【결론】에 '확인할 수 없'
    안내: 다른 법에 맡기는 조문(refer_lsa, 예: '제34조')만 인용 (예: 퇴직금 → 근로자퇴직급여 보장법)
    억지적용: 그 밖의 근로기준법 조문을 근거로 답함
    미인용: 거절 문구도 인용 조문도 없음 (형식을 벗어난 답변, 직접 확인 필요)"""
    if is_refusal(answer):
        return '거절'
    cited = set(cited_articles(answer))
    if not cited:
        return '미인용'
    allowed = set(refer_lsa.split(';')) if isinstance(refer_lsa, str) and refer_lsa else set()
    return '안내' if cited <= allowed else '억지적용'
