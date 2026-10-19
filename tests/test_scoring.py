# tests/test_scoring.py  →  채점기(score_answers)와 거절 판정(refusal) 회귀 테스트
# 저장소에 든 data/·results/만 읽고 수정하지 않음 (모델 불필요)
import json
import pandas as pd
import pytest
from lawbot.paths import EVAL, RESULTS
from lawbot.refusal import conclusion, is_refusal, cited_articles
from lawbot.score_answers import norm, term_in, cite_ok, score_one

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
])
def test_cite_ok_distinguishes_similar_numbers(article, text, ok):
    assert cite_ok(article, norm(text)) is ok

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

@pytest.mark.parametrize('name', ['scored_sllm_mlx_4bit.csv', 'scored_oracle_4bit.csv'])
def test_scorer_reproduces_recorded_scores(ev, name):
    o = results(name).fillna({'answer': ''})
    gold = ev.set_index('id')
    for _, r in o.iterrows():
        s = score_one(gold.loc[r['id']], r['answer'])
        assert s['total'] == pytest.approx(r['total']), r['id']
        assert s['cite_ok'] == r['cite_ok'], r['id']
