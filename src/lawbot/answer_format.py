# src/lawbot/answer_format.py  →  모델 원문(4섹션)을 화면용 간결형으로 바꾸는 후처리 (gradio 미사용)
# 모델 입력([참고 조문])과 원문 출력은 바꾸지 않는다. 평가는 원문과 간결형을 따로 잰다 (eval_concise)
import re
from lawbot.refusal import is_refusal

SECTION = re.compile(r'【(결론|근거 조문|설명|참고)】')
ARTICLE = re.compile(r'제\d+조(?:의\d+)?')

def sections(raw):
    """{'결론': ..., '근거 조문': ...}. 생성 중이면 마지막 섹션은 아직 쓰는 중"""
    marks = list(SECTION.finditer(raw))
    return {m[1]: raw[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(raw)].strip()
            for i, m in enumerate(marks)}

def hit_label(h):
    return f"{h.get('law', '근로기준법')} {h['article_id']}({h['title']})"

def cited_hits(cite_text, hits):
    """【근거 조문】 글에서 실제로 인용한 조문: (검색 결과의 조문, 검색 결과에 없거나 법령을 특정할 수 없는 번호)
    v4는 법령마다 번호가 겹치므로 '법령명 제N조'가 맞는 조문을 고르고, 법령명이 없으면 번호가 하나뿐일 때만 인정"""
    if cite_text.startswith('해당 없음'):           # 거절 형식의 제공 조문 목록은 인용이 아님 (refusal.cited_articles와 같음)
        return [], []
    flat = re.sub(r'\s+', '', cite_text)
    used, unknown = [], []
    for a in dict.fromkeys(ARTICLE.findall(cite_text)):
        cands = [h for h in hits if h['article_id'] == a]
        named = [h for h in cands
                 if re.search(re.escape(re.sub(r'\s+', '', h.get('law', ''))) + re.escape(a) + r'(?!의\d|\d)', flat)]
        pick = named or (cands if len(cands) == 1 else [])
        used += [h for h in pick if h not in used]
        if not pick:
            unknown.append(a)
    return used, unknown

def concise(raw, hits, done=True):
    """간결형: 결론 + '근거: ' 실제 인용 조문만. 4섹션 표시가 없으면 원문 그대로 (형식을 벗어난 답변을 숨기지 않음)"""
    sec = sections(raw)
    if '결론' not in sec:
        if not done and '【결론】'.startswith(raw.strip()):   # 표시 기호를 아직 쓰는 중
            return ''
        return raw.strip()
    out = sec['결론']
    if is_refusal(raw):                            # 거절은 근거 줄 없이 결론만
        return out
    if done or '설명' in sec or '참고' in sec:     # 근거 조문 섹션이 끝난 뒤에 한 번에 붙인다 (쓰는 중인 번호를 표시하지 않음)
        used, unknown = cited_hits(sec.get('근거 조문', ''), hits)
        if used:
            out += '\n\n근거: ' + ', '.join(hit_label(h) for h in used)
        if unknown:
            out += f"\n\n⚠ 검색된 조문에서 확인되지 않은 인용: {', '.join(unknown)}"
        if not used and not unknown:
            out += '\n\n⚠ 인용 조문 없음: 오른쪽 조문을 직접 확인하세요.'
    return out

def render(raw, hits, fmt, done=True):
    """fmt: 'concise'(간결형) / 'full'(모델 원문 4섹션)"""
    return concise(raw, hits, done) if fmt == 'concise' else raw
