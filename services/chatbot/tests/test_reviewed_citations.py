import pytest

from lawbot.answer_format import UNVERIFIED, mark_unverified, review_answer
from lawbot.citation import check_spans
from lawbot.web import answer_display

LABOR = '근로기준법'
FIXED = '기간제 및 단시간근로자 보호 등에 관한 법률'
EQUAL = '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률'


def hit(law, no):
    return {'law': law, 'article_id': no, 'title': '제목', 'doc': '제목\n본문'}


HITS = [
    hit(LABOR, '제26조'),
    hit(LABOR, '제4조'),
    hit(FIXED, '제4조'),
    hit(EQUAL, '제19조'),
    hit(LABOR + ' 시행규칙', '제26조'),
]


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        (
            '근로기준법 제26조. 사회복지사업법의 제26조입니다.',
            [
                ('근로기준법 제26조', LABOR, '제26조', 'ok', 'same_sentence'),
                ('사회복지사업법의 제26조', '사회복지사업법', '제26조', 'outside', 'same_sentence'),
            ],
        ),
        (
            '근로기준법 제26조. 사회복지사업법에서는 제26조입니다.',
            [
                ('근로기준법 제26조', LABOR, '제26조', 'ok', 'same_sentence'),
                ('사회복지사업법에서는 제26조', '사회복지사업법', '제26조', 'outside', 'same_sentence'),
            ],
        ),
        (
            '근로기준법 제26조. 별개입니다. (제26조)',
            [
                ('근로기준법 제26조', LABOR, '제26조', 'ok', 'same_sentence'),
                ('제26조', None, '제26조', 'ambiguous', 'unresolved'),
            ],
        ),
        ('근로기준법에 따릅니다. 동법 제26조', [('동법 제26조', LABOR, '제26조', 'ok', 'previous_sentence')]),
        ('(제19조)', [('제19조', EQUAL, '제19조', 'ok', 'unique_provided')]),
        (
            '「사회복지사업법 제999조」',
            [('「사회복지사업법 제999조」', '사회복지사업법', '제999조', 'outside', 'same_sentence')],
        ),
        ('제\n999조', [('제\n999조', None, '제999조', 'nonexistent', 'unresolved')]),
        (
            '근로기준법 시행령·시행규칙 제26조',
            [
                ('근로기준법 시행령·시행규칙 제26조', LABOR + ' 시행령', '제26조', 'wrong_law', 'same_sentence'),
                ('근로기준법 시행령·시행규칙 제26조', LABOR + ' 시행규칙', '제26조', 'ok', 'same_sentence'),
            ],
        ),
        ('제999조 제1항ㆍ제2항', [('제999조 제1항ㆍ제2항', None, '제999조', 'nonexistent', 'unresolved')]),
        (f'이는 {FIXED} 제4조에 따릅니다.', [(f'{FIXED} 제4조', FIXED, '제4조', 'ok', 'same_sentence')]),
        (f'이는 {EQUAL} 제19조에 따릅니다.', [(f'{EQUAL} 제19조', EQUAL, '제19조', 'ok', 'same_sentence')]),
        (
            '근로기준법 부칙 제20903호 제4조',
            [('근로기준법 부칙 제20903호 제4조', LABOR, '부칙<제20903호>제4조', 'nonexistent', 'same_sentence')],
        ),
        (
            '근로기준법 부칙(제20903호) 제4조',
            [('근로기준법 부칙(제20903호) 제4조', LABOR, '부칙<제20903호>제4조', 'nonexistent', 'same_sentence')],
        ),
        (
            '근로기준법 부칙(공포번호 불명) 제4조',
            [('근로기준법 부칙(공포번호 불명) 제4조', LABOR, '부칙제4조', 'nonexistent', 'same_sentence')],
        ),
        (
            '근로기준법 부칙(2020. 1. 1.) 제4조',
            [('근로기준법 부칙(2020. 1. 1.) 제4조', LABOR, '부칙제4조', 'nonexistent', 'same_sentence')],
        ),
    ],
)
def test_review_cases_have_original_positions_and_attribution(text, expected):
    spans = check_spans(text, HITS, {(h['law'], h['article_id']) for h in HITS})
    assert [(c.start, c.end, c.law, c.article_id, c.verdict, c.attribution) for c in spans] == [
        (offset, offset + len(label), law, no, verdict, attribution)
        for label, law, no, verdict, attribution in expected
        for offset in (text.rindex(label) if label == '제26조' else text.index(label),)
    ]
    marked = text
    for label in reversed(list(dict.fromkeys(label for label, _, _, verdict, _ in expected if verdict != 'ok'))):
        end = (text.rindex(label) if label == '제26조' else text.index(label)) + len(label)
        marked = marked[:end] + UNVERIFIED + marked[end:]
    assert mark_unverified(text, spans) == marked


def test_ground_lines_do_not_inherit_law_from_conclusion_or_prior_line():
    raw = '【결론】근로기준법 제26조. 무관한 문장입니다.\n【근거 조문】제26조\n근로기준법 제26조\n제26조'
    result = review_answer(raw, HITS)
    assert [(c.attribution, c.verdict) for c in result.ground_cites] == [
        ('unresolved', 'ambiguous'),
        ('same_sentence', 'ok'),
        ('unresolved', 'ambiguous'),
    ]
    wrapped = review_answer('【결론】답\n【근거 조문】사회복지사업법 제\n999조\n제26조', HITS)
    assert [(c.start, c.end, c.verdict) for c in wrapped.ground_cites] == [
        (0, len('사회복지사업법 제\n999조'), 'outside'),
        (len('사회복지사업법 제\n999조\n'), len(wrapped.grounds), 'ambiguous'),
    ]


def test_calculator_citations_are_verified_without_manufacturing_cards():
    raw = '【결론】소득세법 제20조에 따라 산정한 결과입니다.\n【근거 조문】소득세법 제20조'
    st = {
        '_calculation': {'status': 'calculated', 'articles': ['소득세법 제20조']},
        'calculation_metrics': {'calculation_check': 'match'},
    }
    verdict, _, text, cards, _ = answer_display(raw, HITS, st, calculated=True)
    assert verdict == 'shown' and '소득세법 제20조' in text and UNVERIFIED not in text
    assert not cards
