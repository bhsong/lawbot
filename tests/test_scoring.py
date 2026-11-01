# tests/test_scoring.py  →  채점기(score_answers)와 거절 판정(refusal) 회귀 테스트
# 저장소에 든 data/·results/만 읽고 수정하지 않음 (모델 불필요)
import json

import pandas as pd
import pytest

from lawbot.paths import EVAL, RESULTS
from lawbot.refusal import cited_articles, conclusion, is_refusal
from lawbot.score_answers import cite_ok, cite_strict, norm, score_one, term_in

ANSWER = ('【결론】\n수습 기간에도 해고 예고를 해야 합니다.\n\n'
          '【근거 조문】\n근로기준법 제26조(해고의 예고)\n\n'
          '【설명】\n- 30일 전에 예고해야 합니다.\n\n【참고】\n- 1350 상담')
REFUSAL = ('【결론】\n제공된 조문만으로는 질문에 대한 근거를 확인할 수 없습니다.\n\n'
           '【근거 조문】\n해당 없음 (제공된 조문: 제69조(근로시간), 제11조(적용 범위))\n\n'
           '【설명】\n- 관련 규정이 아닙니다.')

@pytest.fixture(scope='module')
def ev():
    return pd.read_csv(EVAL, encoding='utf-8-sig', dtype=str)

def results(name):
    return pd.read_csv(RESULTS / name, encoding='utf-8-sig')

# 거절 판정
def test_refusal_detected_in_conclusion():
    assert is_refusal(REFUSAL)
    assert not is_refusal(ANSWER)

def test_refusal_phrase_outside_conclusion_is_not_refusal():
    answer = ANSWER.replace('- 1350 상담', '- 사실관계는 확인할 수 없으므로 상담하세요.')
    assert not is_refusal(answer)

def test_conclusion_falls_back_to_first_150_chars():
    plain = '가' * 200 + '확인할 수 없습니다'
    assert conclusion(plain) == '가' * 150
    assert not is_refusal(plain)
    assert is_refusal('근거를 확인할 수 없습니다.')

def test_cited_articles():
    assert cited_articles(ANSWER) == ['제26조']
    assert cited_articles('【근거 조문】\n제43조, 제43조의2\n【설명】') == ['제43조', '제43조의2']
    assert cited_articles(REFUSAL) == []          # '해당 없음 (제공된 조문: …)'은 인용 아님
    assert cited_articles('근거 조문 표시 없음 제26조') == []

@pytest.mark.parametrize('name', ['refusal_v1_4bit.csv', 'oos_v1_4bit.csv'])
def test_refusal_rule_matches_recorded_results(name):
    # README 거절률 수치의 근거가 된 판정을 지금 규칙으로 그대로 재현해야 함
    o = results(name)
    assert (o['answer'].fillna('').map(is_refusal) == o['refusal']).all()

# 채점기 단위
def test_norm():
    assert norm(' 1,000 원 ㆍ 수당\n') == '1000원·수당'

def test_term_in_number_boundary():
    assert term_in('3년', norm('3년간 보존'))
    assert not term_in('3년', norm('13년간 보존'))
    assert not term_in('3년', norm('1.3년'))
    assert not term_in('', '아무거나')

@pytest.mark.parametrize('article,text,ok', [
    ('제43조', '근로기준법 제43조(임금 지급)', True),
    ('제43조', '근로기준법 제43조의2(체불사업주 명단 공개)', False),
    ('제43조', '제143조', False),
    ('제43조의2', '제43조의2', True),
    ('제43조의2', '제43조의20', False),
    ('제43조의2', '제43조', False),
    # 띄어쓰기·항·조사가 붙어도 같은 조문, '의'+숫자가 붙으면 다른 조문
    ('제43조', '제 43 조 ② 임금은', True),
    ('제43조', '제43조제2항', True),
    ('제43조', '제43조의 규정에 따라', True),
    ('제43조', '제43조 의2', False),
    ('제43조', '제43조의2제1항', False),
    ('제76조의2', '제76조의 2(직장 내 괴롭힘의 금지)', True),
    ('제76조의2', '제76조의3', False),
    ('제2조', '제12조, 제20조, 제2조의2', False),
    ('제43조', '43조', False),                   # '제' 없는 번호는 인용으로 보지 않음
])
def test_cite_ok_distinguishes_similar_numbers(article, text, ok):
    assert cite_ok(article, norm(text)) is ok

def test_cite_ok_known_limit_other_law_same_number():
    # 알려진 한계: 다른 법의 같은 번호도 정답으로 본다 (법 이름을 보지 않음)
    assert cite_ok('제34조', norm('근로자퇴직급여 보장법 제34조'))

def test_refusal_listing_gold_article_is_not_citation(ev):
    # 거절 답변의 '해당 없음 (제공된 조문: …)'에 정답 번호가 있어도 인용이 아니다
    r = ev.set_index('id').loc['Q0818'].copy()   # 정답 제43조
    answer = ('【결론】\n제공된 조문만으로는 질문에 대한 근거를 확인할 수 없습니다.\n\n'
              '【근거 조문】\n해당 없음 (제공된 조문: 제40조(취업 방해의 금지), 제43조(임금 지급))\n\n'
              '【설명】\n- 임금 지급 관련 규정이 아닙니다.')
    s = score_one(r, answer)
    assert s['cite_ok'] == 0 and s['cite_strict'] == 0 and s['total'] == 0.0

@pytest.mark.parametrize('gold,basis,conf,ok', [
    ('제43조', '제43조(임금 지급)', None, True),
    ('제43조', '제43조, 제17조', None, True),           # 관련 없는 조문을 함께 인용하는 것은 허용
    ('제43조', '제43조, 제43조의2', None, False),       # 번호가 비슷한 조문을 함께 인용
    ('제43조', '제43조의2', None, False),
    ('제56조', '제55조, 제60조', '제55조;제60조', False),  # 정답 없이 헷갈리는 조문만
    ('제56조', '제56조, 제55조', '제55조', False),      # 정답 + 헷갈리는 조문
    ('제56조', '제56조', '제55조', True),
])
def test_cite_strict(gold, basis, conf, ok):
    answer = f'【결론】\n결론\n\n【근거 조문】\n{basis}\n\n【설명】\n- 제43조의2와 제55조는 다른 조문입니다.'
    assert cite_strict(gold, conf, answer) is ok

def test_cite_strict_ignores_mentions_outside_basis():
    # 【설명】에서만 정답을 언급하면 cite_ok는 맞지만 cite_strict는 아니다
    answer = '【결론】\n결론\n\n【근거 조문】\n제43조의2\n\n【설명】\n- 제43조와 다릅니다.'
    assert cite_ok('제43조', norm(answer))
    assert not cite_strict('제43조', None, answer)

def test_score_one_penalties(ev):
    r = ev.iloc[0].copy()                      # 제1조(목적)
    ref = r['reference_answer']
    assert score_one(r, ref)['total'] == 1.0
    no_cite = score_one(r, ref.replace('제1조', '제2조'))
    assert no_cite['cite_ok'] == 0 and no_cite['total'] == 0.0
    r['must_not_include'] = json.dumps(['국민경제'], ensure_ascii=False)
    banned = score_one(r, ref)
    assert banned['must_not_ok'] == 0 and banned['total'] == 0.5
    empty = score_one(ev.iloc[0], '')
    assert empty['total'] == 0.0 and empty['groups_hit'] == 0

# 데이터·결과 회귀
def test_reference_answers_score_full_marks(ev):
    # README: 평가 파일의 모범 답변은 650문항 모두 1.0점
    assert len(ev) == 650
    scores = [score_one(r, r['reference_answer'])['total'] for _, r in ev.iterrows()]
    assert min(scores) == 1.0
    assert not ev['reference_answer'].map(is_refusal).any()

@pytest.mark.parametrize('name', ['scored_sllm_mlx_4bit.csv', 'scored_oracle_4bit.csv',
                                  'scored_sllm_mlx_4bit_hybrid.csv'])
def test_scorer_reproduces_recorded_scores(ev, name):
    o = results(name).fillna({'answer': ''})
    gold = ev.set_index('id')
    for _, r in o.iterrows():
        s = score_one(gold.loc[r['id']], r['answer'])
        assert s['total'] == pytest.approx(r['total']), r['id']
        assert s['cite_ok'] == r['cite_ok'], r['id']
        assert s['cite_strict'] == r['cite_strict'], r['id']

@pytest.mark.parametrize('name,qid,cite,strict,total', [
    ('scored_sllm_mlx_4bit.csv', 'Q1730', 0, 0, 0.0),    # 정답 제76조의2, 제76조의3 인용
    ('scored_oracle_4bit.csv', 'Q1730', 0, 0, 0.0),
    ('scored_oracle_4bit.csv', 'Q0989', 0, 0, 0.0),      # 거절 + 제공 조문 목록에 정답 (수정 전 0.33)
    ('scored_oracle_4bit.csv', 'Q0818', 0, 0, 0.0),      # 거절 + 제공 조문 목록에 정답 (수정 전 0.5)
    ('scored_oracle_4bit.csv', 'Q2464', 1, 0, 1.0),      # 정답 제110조 + 헷갈리는 제53조 함께 인용
])
def test_recorded_citation_edge_cases(ev, name, qid, cite, strict, total):
    # 저장된 답변을 지금 채점 코드로 다시 채점해 경계 사례의 점수를 고정한다
    answer = results(name).fillna({'answer': ''}).set_index('id').loc[qid, 'answer']
    s = score_one(ev.set_index('id').loc[qid], answer)
    assert (s['cite_ok'], s['cite_strict'], s['total']) == (cite, strict, total)
