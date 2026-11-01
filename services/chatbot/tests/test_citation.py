# tests/test_citation.py  →  인용·거절 판정 공통 모듈 (citation): 파싱·형식 검증·화면 간결형과 eval_v4가 같은 판정을 쓰는지
import subprocess
import sys
import types

import pandas as pd
import pytest

import lawbot.eval_v4 as ev
from lawbot import citation as ci
from lawbot.answer_format import cited_hits, concise
from lawbot.paths import V4_ARTICLES


def hit(law, no, title='제목'):
    return {'law': law, 'article_id': no, 'title': title, 'doc': f'{law} {no}({title})\n본문', 'score': 0.5}


def v4_articles():
    """실제 v4 조문 CSV를 검색기처럼 읽은 전체 조문 키 (비삭제 415개)"""
    d = pd.read_csv(V4_ARTICLES, encoding='utf-8-sig')
    d = d[d['deleted'] == 0]
    return ci.article_keys(types.SimpleNamespace(ids=d['article_no'].to_numpy(), laws=d['law'].to_numpy()))


# ---- 파싱 ----
@pytest.mark.parametrize(
    'text,expected',
    [
        (
            '근로기준법 제26조(해고의 예고), 최저임금법 제26조',
            [('근로기준법', '제26조'), ('최저임금법', '제26조')],
        ),  # 번호가 겹치는 법령
        ('근로기준법 제43조, 제43조의2', [('근로기준법', '제43조'), ('근로기준법', '제43조의2')]),  # 이어 쓴 번호
        (
            '퇴직급여법 제8조, 고평법 제19조',
            [('근로자퇴직급여 보장법', '제8조'), ('남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률', '제19조')],
        ),  # 줄임말
        (
            '근로기준법 시행령 제5조, 근로기준법 제5조',
            [('근로기준법 시행령', '제5조'), ('근로기준법', '제5조')],
        ),  # 하위법령 구분
        ('최저임금법 시행규칙 제2조, 제3조', [('최저임금법 시행규칙', '제2조'), ('최저임금법 시행규칙', '제3조')]),
        ('근로 기준법 제 26 조', [('근로기준법', '제26조')]),  # 띄어쓰기
        ('제26조, 제27조', [(None, '제26조'), (None, '제27조')]),  # 법령명 없음
        ('해당 없음 (제공된 조문: 근로기준법 제34조)', []),  # 거절 형식
        ('  해당 없음', []),
    ],
)
def test_parse(text, expected):
    assert ci.parse(text) == expected


def test_citations_reads_cite_section():
    ans = '【결론】근로기준법 제1조\n【근거 조문】근로기준법 시행령 제5조\n【설명】근로기준법 제99조'
    assert ci.citations(ans) == [('근로기준법 시행령', '제5조')]
    assert not ev.cite_gold(ans, '근로기준법 제5조')  # 시행령 조문은 법률 조문의 정답이 아님


def test_eval_v4_uses_citation_module():
    assert ev.citations is ci.citations and ev.refused is ci.refused and ev.LAW_ALIASES is ci.LAW_ALIASES
    assert ev.key(hit('최저임금법', '제6조')) == '최저임금법 제6조'


# ---- 거절 ----
@pytest.mark.parametrize(
    'answer,expected',
    [
        ('【결론】\n제공된 조문만으로는 확인할 수 없습니다.', True),  # 4B 학습 문구
        ('【결론】\n노동법 내용이라 날씨에 대한 근거를 찾을 수 없습니다.\n【근거 조문】\n근거 조문 없음', True),  # 27B
        ('【결론】\n해당 질문에 대해 답변할 수 없습니다.', True),
        (
            '【결론】\n제공된 [참고 조문]은 근로기준법 관련 내용으로, 김치찌개 조리법에 대한 근거를 찾을 수 없습니다.\n【근거 조문】\n근거 조문 없음',
            True,
        ),  # 27B (튜닝 전)
        ('【결론】\n노동법 관련 내용만 포함되어 있어 해당 질문에 대해 답변할 수 없습니다.', True),  # 27B (튜닝 전)
        ('【결론】\n사용자는 정당한 이유 없이 해고할 수 없습니다.', False),  # 법 내용의 '할 수 없'
        ('【결론】\n예고해야 합니다.\n【설명】\n- 확인할 수 없는 경우도 있음', False),  # 결론 밖
    ],
)
def test_refused(answer, expected):
    assert ci.refused(answer) is expected


# ---- 형식 검증 ----
HITS = [
    hit('근로기준법', '제26조', '해고의 예고'),
    hit('최저임금법', '제6조', '최저임금의 효력'),
    hit('근로기준법', '제43조', '임금 지급'),
]


def verdicts(text, hits=HITS, articles=None):
    return [(law, no, v) for law, no, v, _ in ci.check(ci.parse(text), hits, articles)]


def test_check_verdicts():
    arts = v4_articles()
    assert len(arts) == 415
    assert verdicts('근로기준법 제26조, 최저임금법 제6조', articles=arts) == [
        ('근로기준법', '제26조', 'ok'),
        ('최저임금법', '제6조', 'ok'),
    ]
    assert verdicts('최저임금법 제26조', articles=arts) == [
        ('최저임금법', '제26조', 'wrong_law')
    ]  # 같은 번호 다른 법령
    assert verdicts('근로기준법 제43조의2', articles=arts) == [('근로기준법', '제43조의2', 'outside')]  # 제43조와 구분
    assert verdicts('근로기준법 제36조', articles=arts) == [('근로기준법', '제36조', 'outside')]
    assert verdicts('근로기준법 제35조', articles=arts) == [('근로기준법', '제35조', 'nonexistent')]  # 삭제 조문
    assert verdicts('근로기준법 제999조', articles=arts) == [('근로기준법', '제999조', 'nonexistent')]
    assert verdicts('근로기준법 시행령 제36조', articles=arts) == [('근로기준법 시행령', '제36조', 'nonexistent')]
    assert verdicts('제6조', articles=arts) == [(None, '제6조', 'ok')]  # 번호가 검색 결과에 하나
    assert verdicts('제2조', articles=arts) == [(None, '제2조', 'ambiguous')]  # 7개 법령에 있음
    assert verdicts('제999조', articles=arts) == [(None, '제999조', 'nonexistent')]
    assert verdicts('최저임금법 제7조', articles=None) == [
        ('최저임금법', '제7조', 'outside')
    ]  # 전체 목록 없음: 존재 미확인


def test_check_ambiguous_in_hits():
    hits = [hit('근로기준법', '제2조', '정의'), hit('최저임금법', '제2조', '정의')]
    assert verdicts('제2조(정의)', hits) == [(None, '제2조', 'ambiguous')]
    assert verdicts('최저임금법 제2조(정의)', hits) == [('최저임금법', '제2조', 'ok')]


# ---- 화면 간결형 (answer_format) ----
def test_concise_hides_27b_refusal():
    raw = (
        '【결론】\n제공된 [참고 조문]은 노동법 내용으로, 영화 추천에 대한 근거를 찾을 수 없습니다.\n\n'
        '【근거 조문】\n근로기준법 제26조(해고의 예고)'
    )
    assert concise(raw, HITS) == '제공된 [참고 조문]은 노동법 내용으로, 영화 추천에 대한 근거를 찾을 수 없습니다.'


def test_concise_alias_citation():
    hits = [hit('근로자퇴직급여 보장법', '제8조', '퇴직금제도의 설정 등'), hit('근로기준법', '제8조', '폭행의 금지')]
    raw = '【결론】\n퇴직금을 줘야 합니다.\n\n【근거 조문】\n퇴직급여법 제8조(퇴직금제도의 설정 등)'
    assert concise(raw, hits).endswith('근거: 근로자퇴직급여 보장법 제8조(퇴직금제도의 설정 등)')
    assert cited_hits('퇴직급여법 제8조', hits) == ([hits[0]], [])


def test_cited_hits_wrong_law_and_order():
    assert cited_hits('최저임금법 제26조', HITS) == ([], ['제26조'])  # 다른 법령의 같은 번호는 인정하지 않음
    used, unknown = cited_hits('근로기준법 제43조, 제26조', HITS)
    assert used == [HITS[2], HITS[0]] and unknown == []  # 인용한 순서


def test_no_model_imports():
    """인용 판정·화면 후처리는 torch·mlx·gradio를 올리지 않는다"""
    code = (
        'import sys, lawbot.citation, lawbot.answer_format; '
        "print(sorted(m for m in ('torch', 'mlx', 'mlx_lm', 'sentence_transformers', 'gradio') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == '[]'


def test_chatbot_never_imports_model_libraries():
    """모델은 모두 모델 서버에: 웹앱·평가·배포 검사는 torch·mlx·sentence_transformers·모델 서버 패키지를 올리지 않는다"""
    code = (
        'import sys, lawbot.app, lawbot.service, lawbot.eval_v4, lawbot.check_deploy, lawbot.smoke_decree; '
        "print(sorted(m for m in ('torch', 'mlx', 'mlx_lm', 'sentence_transformers', 'lawbot_model_server') "
        'if m in sys.modules))'
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == '[]'


@pytest.mark.parametrize(
    'answer,expected',
    [
        # val Q0110: 답한 뒤 단서 문장에서 한계를 밝힘 → 거절 아님
        (
            '【결론】\n국적을 이유로 차별하는 것은 근로기준법에 따라 금지됩니다. 다만, 제공된 조문에는 신고 절차가 포함되어 있지 않아, '
            '구제 절차는 확인할 수 없습니다.\n\n【근거 조문】\n근로기준법 제6조',
            False,
        ),
        ('【결론】\n금지됩니다.\n단, 구제 절차는 확인할 수 없습니다.', False),
        # 첫 문장이 거절이면 뒤에 '다만'이 있어도 거절 (val Q4935·Q0023 형식)
        (
            '【결론】\n참고 조문만으로는 차별에 해당하는지 판단할 수 없습니다.\n다만, 고용형태에 따른 차별 금지 규정은 있습니다.',
            True,
        ),
        # 맥락 문장 뒤 '따라서 …'로 거절 (범위 밖 답변 형식)
        (
            '【결론】\n제공된 [참고 조문]은 노동관계 내용만 다룹니다.\n따라서 월세 인상 한도에 대해 답변할 수 없습니다.',
            True,
        ),
        ('【결론】\n제공된 조문에는 처벌 규정이 포함되어 있지 않아 확인할 수 없습니다.', True),
        (
            '【결론】\n단순히 지각했다는 이유만으로는 해고할 수 없습니다.',
            False,
        ),  # '단순히'는 단서가 아님 (거절 표현도 없음)
    ],
)
def test_refused_caveat_sentence(answer, expected):
    assert ci.refused(answer) is expected
