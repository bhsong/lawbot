# src/lawbot/answer_format.py  →  모델 원문을 화면용 간결형(결론 + 근거)으로 바꾸는 후처리
# 모델 입력([참고 조문])과 원문 출력은 바꾸지 않는다. 화면 형식만 바꾼다
import re
from dataclasses import dataclass

from lawbot.citation import OK, CitationSpan, check, check_spans, hit_key, hit_law, parse, refused
from lawbot.hits import Hit

SECTION = re.compile(r'【(결론|근거 조문|설명|참고)】')
UNVERIFIED = '⚠확인되지 않은 인용'
UNVERIFIED_GROUNDS_HEADER = '\n\n확인되지 않은 근거:\n'


def sections(raw):
    """{'결론': ..., '근거 조문': ...}. 생성 중이면 마지막 섹션은 아직 쓰는 중"""
    marks = list(SECTION.finditer(raw))
    return {
        m[1]: raw[m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(raw)].strip()
        for i, m in enumerate(marks)
    }


def hit_label(h: Hit) -> str:
    return f'{hit_law(h)} {h["article_id"]}({h["title"]})'


def cited_hits(cite_text, hits: list[Hit]):
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


def concise(raw, hits: list[Hit], done=True):
    """Render the conclusion and evidence, annotating only the completed answer."""
    if done:
        return review_answer(raw, hits).render()
    sec = sections(raw)
    if '결론' not in sec:
        if not done and '【결론】'.startswith(raw.strip()):  # 표시 기호를 아직 쓰는 중
            return ''
        return raw.strip()
    out = sec['결론']
    if refused(raw):  # 거절은 근거 줄 없이 결론만 (튜닝 전 27B의 거절 표현 포함)
        return out
    if '설명' in sec or '참고' in sec:  # Wait for the whole evidence section.
        used, unknown = cited_hits(sec.get('근거 조문', ''), hits)
        if used:
            out += '\n\n근거: ' + ', '.join(hit_label(h) for h in used)
        if unknown:
            out += f'\n\n⚠ 검색된 조문에서 확인되지 않은 인용: {", ".join(unknown)}'
        if not used and not unknown:
            out += '\n\n⚠ 인용 조문 없음: 오른쪽 조문을 직접 확인하세요.'
    return out


def answer_citations(raw, hits: list[Hit]):
    """Return verified hits, unverified article numbers and refusal for the completed answer."""
    review = review_answer(raw, hits)
    unknown = (
        []
        if review.refused
        else list(dict.fromkeys(c.article_id for c in review.ground_cites + review.conclusion_cites if c.verdict != OK))
    )
    return review.used(), unknown, review.refused


def mark_unverified(text, citations):
    parts = []
    start = 0
    for cite in citations:
        if cite.verdict != OK:
            parts.extend((text[start : cite.end], UNVERIFIED))
            start = cite.end
    parts.append(text[start:])
    return ''.join(parts)


@dataclass(frozen=True)
class AnswerReview:
    conclusion: str
    grounds: str
    conclusion_cites: list[CitationSpan]
    ground_cites: list[CitationSpan]
    refused: bool
    structured: bool

    def used(self) -> list[Hit]:
        if self.refused:
            return []
        hits: dict[str, Hit] = {}
        for cite in self.ground_cites + self.conclusion_cites:
            if cite.hit is not None:
                hits.setdefault(hit_key(cite.hit), cite.hit)
        return list(hits.values())

    def unverified(self) -> list[tuple[str, CitationSpan]]:
        return [(self.grounds[c.start : c.end], c) for c in self.ground_cites if c.verdict != OK]

    def render(self) -> str:
        text = mark_unverified(self.conclusion, self.conclusion_cites)
        if self.refused or not self.structured:
            return text
        used, unknown = self.used(), self.unverified()
        if used:
            text += '\n\n근거: ' + ', '.join(hit_label(h) for h in used)
        if unknown:
            text += UNVERIFIED_GROUNDS_HEADER + '\n'.join(label + UNVERIFIED for label, _ in unknown)
        if not used and not unknown:
            text += '\n\n⚠ 인용 조문 없음: 오른쪽 조문을 직접 확인하세요.'
        return text


def review_answer(raw: str, hits: list[Hit], articles: set[tuple[str, str]] | None = None) -> AnswerReview:
    """Check all displayed model text; preserve law context from conclusion into grounds."""
    sec = sections(raw)
    structured = '결론' in sec
    text = sec['결론'] if structured else raw.strip()
    grounds = sec.get('근거 조문', '') if structured else ''
    if grounds.startswith('해당 없음'):
        grounds = ''
    offset = len(text) + 1
    main, basis = [], []
    for cite in check_spans(f'{text}\n{grounds}' if grounds else text, hits, articles):
        if cite.start < offset:
            main.append(cite)
        else:
            basis.append(
                CitationSpan(cite.start - offset, cite.end - offset, cite.law, cite.article_id, cite.verdict, cite.hit)
            )
    return AnswerReview(text, grounds, main, basis, refused(raw), structured)
