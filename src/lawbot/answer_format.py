# src/lawbot/answer_format.py  →  모델 원문(4섹션)을 화면용 간결형으로 바꾸는 후처리 (gradio 미사용)
# 모델 입력([참고 조문])과 원문 출력은 바꾸지 않는다. 평가는 원문과 간결형을 따로 잰다 (eval_concise)
import re

from lawbot.citation import OK, check, hit_law, parse, refused

SECTION = re.compile(r'【(결론|근거 조문|설명|참고)】')


def sections(raw):
    """{'결론': ..., '근거 조문': ...}. 생성 중이면 마지막 섹션은 아직 쓰는 중"""
    marks = list(SECTION.finditer(raw))
    return {
        m[1]: raw[m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(raw)].strip()
        for i, m in enumerate(marks)
    }


def hit_label(h):
    return f'{hit_law(h)} {h["article_id"]}({h["title"]})'


def cited_hits(cite_text, hits):
    """【근거 조문】 글에서 실제로 인용한 조문: (검색 결과의 조문, 검색 결과에 없거나 법령을 특정할 수 없는 번호)
    인용 파싱·판정은 eval_v4 채점과 같은 citation 모듈: 줄임말('퇴직급여법')을 인정하고, 법령명 없이 이어 쓴 번호는
    앞의 법령으로 본다. 법령명이 전혀 없으면 그 번호의 검색 결과가 하나뿐일 때만 인정"""
    used, unknown = [], []
    for _, no, verdict, h in check(parse(cite_text), hits):
        if verdict == OK:
            if h not in used:
                used.append(h)
        elif no not in unknown:
            unknown.append(no)
    return used, unknown


def concise(raw, hits, done=True):
    """간결형: 결론 + '근거: ' 실제 인용 조문만. 4섹션 표시가 없으면 원문 그대로 (형식을 벗어난 답변을 숨기지 않음)"""
    sec = sections(raw)
    if '결론' not in sec:
        if not done and '【결론】'.startswith(raw.strip()):  # 표시 기호를 아직 쓰는 중
            return ''
        return raw.strip()
    out = sec['결론']
    if refused(raw):  # 거절은 근거 줄 없이 결론만 (튜닝 전 27B의 거절 표현 포함)
        return out
    if (
        done or '설명' in sec or '참고' in sec
    ):  # 근거 조문 섹션이 끝난 뒤에 한 번에 붙인다 (쓰는 중인 번호를 표시하지 않음)
        used, unknown = cited_hits(sec.get('근거 조문', ''), hits)
        if used:
            out += '\n\n근거: ' + ', '.join(hit_label(h) for h in used)
        if unknown:
            out += f'\n\n⚠ 검색된 조문에서 확인되지 않은 인용: {", ".join(unknown)}'
        if not used and not unknown:
            out += '\n\n⚠ 인용 조문 없음: 오른쪽 조문을 직접 확인하세요.'
    return out


def answer_citations(raw, hits):
    """생성이 끝난 답변의 (인용한 검색 결과 조문, 검색 결과에서 확인되지 않은 번호, 거절 여부). 화면의 조문 카드 구분에 씀
    【결론】 형식이면 【근거 조문】만, 형식을 벗어난 답변은 전체에서 인용을 찾는다 (간결형과 같은 기준)"""
    if refused(raw):
        return [], [], True
    sec = sections(raw)
    used, unknown = cited_hits(sec.get('근거 조문', '') if '결론' in sec else raw, hits)
    return used, unknown, False


def render(raw, hits, fmt, done=True):
    """fmt: 'concise'(간결형) / 'full'(모델 원문 4섹션)
    전체 형식도 생성이 끝나면 검색 결과에서 확인되지 않은 인용을 원문 아래에 경고한다 (간결형과 같은 문구)"""
    if fmt == 'concise':
        return concise(raw, hits, done)
    if done and not refused(raw):
        _, unknown, _ = answer_citations(raw, hits)
        if unknown:
            return raw + f'\n\n⚠ 검색된 조문에서 확인되지 않은 인용: {", ".join(unknown)}'
    return raw
