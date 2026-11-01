# tests/test_eval_data.py  →  범위 밖 답변 판정(oos_verdict)과 평가 데이터(oos_eval·scope_controls·refusal_hard_cases) 검증
# 저장소에 든 data/·results/만 읽고 수정하지 않음 (모델 불필요)
import pandas as pd
import pytest

from lawbot.paths import ARTICLES, DATA, EVAL, RESULTS
from lawbot.refusal import is_refusal, oos_verdict

E = dict(encoding='utf-8-sig')
REFUSAL = (
    '【결론】\n제공된 조문만으로는 질문에 대한 근거를 확인할 수 없습니다.\n\n'
    '【근거 조문】\n해당 없음 (제공된 조문: 제34조(퇴직급여 제도))'
)
REFER = (
    '【결론】\n퇴직급여 제도는 「근로자퇴직급여 보장법」이 정하는 대로 따릅니다.\n\n'
    '【근거 조문】\n근로기준법 제34조(퇴직급여 제도)'
)
FORCED = (
    '【결론】\n요양 중에는 평균임금의 100분의 60을 휴업보상으로 받습니다.\n\n【근거 조문】\n근로기준법 제79조(휴업보상)'
)


def test_paths_root_is_repo_top():
    """모노레포로 옮긴 뒤에도 ROOT는 레포 최상위 (data/·models/·results/가 그 아래)"""
    from lawbot.paths import ROOT

    assert (ROOT / 'pyproject.toml').is_file() and (ROOT / 'services' / 'chatbot').is_dir()
    assert DATA == ROOT / 'data' and RESULTS == ROOT / 'results'


@pytest.fixture(scope='module')
def oos():
    return pd.read_csv(DATA / 'oos_eval.csv', **E)


@pytest.fixture(scope='module')
def live():
    law = pd.read_csv(ARTICLES, **E)
    return set(law.loc[law['deleted'] == 0, 'article_id'])


def ids(col):
    return {a for v in col.dropna() for a in v.split(';')}


# 판정 규칙
@pytest.mark.parametrize(
    'answer,refer,verdict',
    [
        (REFUSAL, '제34조', '거절'),
        (REFER, '제34조', '안내'),
        (REFER, None, '억지적용'),  # 맡기는 조문이 지정되지 않은 질문
        (REFER + ', 제36조(금품 청산)', '제34조', '억지적용'),  # 안내 조문 밖 조문을 함께 인용
        (FORCED, '제34조', '억지적용'),
        ('제공된 조문은 질문에 대한 근거가 될 수 없습니다.', None, '미인용'),
    ],
)
def test_oos_verdict(answer, refer, verdict):
    assert oos_verdict(answer, refer) == verdict


def test_oos_verdict_on_recorded_results(oos):
    # 82문항 기준선: 다른 노동법 32문항 중 억지적용 13, 안내 5 (퇴직금 4건·산업안전 1건)
    o = pd.read_csv(RESULTS / 'oos_v1_4bit.csv', **E).fillna({'answer': ''})
    o = o.merge(oos[['id', 'refer_lsa']], on='id')
    o['verdict'] = [oos_verdict(a, r) for a, r in zip(o['answer'], o['refer_lsa'])]
    assert (o['verdict'] == '거절').equals(o['refusal'])
    labor = o[o['category'] == 'other_labor']['verdict'].value_counts().to_dict()
    assert labor == {'거절': 14, '억지적용': 13, '안내': 5}
    assert sorted(o.loc[o['verdict'] == '안내', 'id']) == ['OOS026', 'OOS027', 'OOS029', 'OOS030', 'OOS041']
    assert o.loc[o['verdict'] == '미인용', 'id'].tolist() == ['OOS003']


# 데이터 무결성
def test_oos_eval_schema(oos, live):
    assert oos['id'].is_unique and len(oos) == 102
    assert oos['category'].isin(['unrelated', 'other_labor', 'other_law']).all()
    assert ids(oos['near_lsa']) | ids(oos['near_lsa_v2']) | ids(oos['refer_lsa']) <= live
    # 기존 82문항의 near_lsa는 README 수치의 기준이므로 그대로, 추가 문항은 near_lsa_v2와 근거(note)를 가짐
    assert oos['near_lsa'].notna().sum() == 18
    added = oos[oos['id'] >= 'OOS083']
    assert len(added) == 20 and added['near_lsa_v2'].notna().all() and added['note'].notna().all()


def test_scope_controls(oos):
    c = pd.read_csv(DATA / 'scope_controls.csv', **E)
    ev = pd.read_csv(EVAL, **E).set_index('id')
    assert c['id'].is_unique and c['id'].isin(ev.index).all()
    assert (c['article_id'] == ev.loc[c['id'], 'article_id'].values).all()
    assert ids(c['twin_oos'].str.replace('|', ';')) <= set(oos['id'])


def test_refusal_hard_cases():
    h = pd.read_csv(DATA / 'refusal_hard_cases.csv', **E)
    o = pd.read_csv(RESULTS / 'refusal_v1_4bit.csv', **E).fillna({'answer': ''})
    # 기준선에서 거절하지 않은 답변 전부를 같은 참고 조문으로 기록
    nr = o[~o['refusal']].set_index(['test', 'id'])
    assert set(h.set_index(['test', 'id']).index) == set(nr.index)
    assert (
        h.set_index(['test', 'id'])['context_ids'] == nr.loc[h.set_index(['test', 'id']).index, 'context_ids']
    ).all()
    assert not nr['answer'].map(is_refusal).any()
    assert h['label'].value_counts().to_dict() == {'억지적용': 77, '지어냄': 4, '결론타당': 3, '근거밖_정답': 2}
    # 정답 조문은 참고 조문에 없음 (근거가 없는 조건)
    assert not any(g in c.split('|') for g, c in zip(h['article_id'], h['context_ids']))
