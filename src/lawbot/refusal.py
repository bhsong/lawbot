# src/lawbot/refusal.py
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
