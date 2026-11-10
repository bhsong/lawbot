# tests/test_qa_split.py  →  질문 분할 도구 (lawbot_tools.qa_split, 계획 docs/archives/v5-tax-scope.md 3-1)
# v4 노트북(lawbot_v4_emb_1.ipynb 셀 ①)의 신규 질문 분할 규칙을 옮긴 것이 맞는지 레포의 v4 데이터로 확인한다
import pandas as pd
import pytest

from lawbot.paths import V4_ARTICLES, V4_QA, V4_SPLIT
from lawbot_tools import qa_split

E = dict(encoding='utf-8-sig', dtype=str, keep_default_na=False)


@pytest.fixture(scope='module')
def v4_new():
    """v4 신규 질문 (B_newlaw 5,016문항, ID Q2603~): 노트북이 이 문항만 새로 나눴다"""
    qa = pd.read_csv(V4_QA, **E)
    return qa[qa['eval_cat'] == 'B_newlaw'].reset_index(drop=True)


def test_reproduces_the_v4_split_of_new_questions(v4_new):
    got = qa_split.split(v4_new)
    want = pd.read_csv(V4_SPLIT, **E).set_index('id').loc[v4_new['id']].reset_index()
    assert list(got.columns) == ['id', 'eval_cat', 'split_emb', 'split_sllm']
    pd.testing.assert_frame_equal(got, want[list(got.columns)])


def test_reproduces_the_v4_question_order(v4_new):
    """법령 → 조문(조문 CSV 행 순서) → 질문 유형 순으로 안정 정렬. 섞어도 원래 ID 순서가 나온다"""
    articles = pd.read_csv(V4_ARTICLES, **E)
    assert list(qa_split.order(v4_new, articles)['id']) == list(v4_new['id'])
    shuffled = qa_split.order(v4_new.sample(frac=1, random_state=7), articles)
    key = ['law', 'article_id', 'question_type']  # 같은 그룹 안의 순서는 입력 순서 (안정 정렬)
    assert shuffled[key].values.tolist() == v4_new[key].values.tolist()


def frame(rows):
    cols = ['id', 'question', 'law', 'article_id', 'question_type', 'eval_cat']
    return pd.DataFrame(rows, columns=cols)


def test_every_article_and_type_needs_two_questions():
    qa = frame(
        [
            ('T1', '질문 하나', '시험법', '시험법 제1조', 'situation', 'D_tax'),
            ('T2', '질문 둘', '시험법', '시험법 제1조', 'situation', 'D_tax'),
            ('T3', '질문 셋', '시험법', '시험법 제2조', 'keyword', 'D_tax'),
        ]
    )
    with pytest.raises(ValueError, match='2개 미만'):
        qa_split.split(qa)


def test_same_question_text_in_two_splits_fails():
    # 모두 같은 문장: test 1개는 반드시 다른 분할
    rows = [(f'T{i}', '같은 문장', '시험법', '시험법 제1조', 'situation', 'D_tax') for i in range(10)]
    with pytest.raises(ValueError, match='같은 질문'):
        qa_split.split(frame(rows))


def test_assign_ids_numbers_in_order():
    qa = frame([('B001-002', '가', '시험법', '시험법 제1조', 'keyword', 'D_tax')] * 2)
    got = qa_split.assign_ids(qa, 'T')
    assert list(got['id']) == ['T00001', 'T00002'] and list(got['rid']) == ['B001-002', 'B001-002']
