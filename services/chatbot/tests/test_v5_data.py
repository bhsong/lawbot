# tests/test_v5_data.py  →  data/v5 (노동 7개 법률 전체 + 세금·보험 범위 목록, 계획 docs/plans/v5-tax-scope.md 2단계)
# 생성: lawbot-build-articles --scope data/v5/scope_tax.csv --version v5. 여기서는 만든 파일이 범위 목록·v4와 맞는지만 본다
import pandas as pd
import pytest

from lawbot import manifest
from lawbot.paths import DATA, V4_DECREE_ARTICLES

V5 = DATA / 'v5'
LAW_CSV, DECREE_CSV, SCOPE = V5 / 'articles_v5.csv', V5 / 'law_articles_v5_decree.csv', V5 / 'scope_tax.csv'
E = dict(encoding='utf-8-sig', dtype=str, keep_default_na=False)
ADDENDUM = '부칙<'


@pytest.fixture(scope='module')
def v5():
    return pd.read_csv(DECREE_CSV, **E)


@pytest.fixture(scope='module')
def scope():
    return pd.read_csv(SCOPE, **E)


def test_manifests_match_the_csvs():
    for csv in (LAW_CSV, DECREE_CSV):
        m = manifest.check(csv)
        assert (m['dataset_version'], m['reference_date']) == ('v5', '2026-11-03')
        assert m['source']['ref'] == 'bcb7aea7782e81c21fbeb08382c442f7c2041690'


def test_law_csv_is_the_law_rows_of_the_decree_csv(v5):
    law = pd.read_csv(LAW_CSV, **E)
    assert law.equals(v5[v5['kind'] == '법률'][list(law.columns)].reset_index(drop=True))


def test_amended_addendum_keeps_postponed_article_out(v5, scope):
    """소득세법 제164조의4(가상자산 거래내역 제출)는 제17757호 부칙이 나중에 2027-01-01 시행으로 고쳐져 아직 시행 전 신설 조문"""
    assert '소득세법 제164조의4' not in set(v5['article_id'])
    assert not ((scope['law'] == '소득세법') & (scope['article_no'] == '제164조의4')).any()


def test_deleted_article_recreated_by_a_pending_amendment_stays_deleted(v5, scope):
    """소득세법 시행령 제216조의4는 2022.2.15 삭제 뒤 제32516호가 2027-01-01 시행으로 다시 만든다. 정부조직법 타법개정(제35947호)이
    그 시행 전 내용을 고친 것도 함께 시행 전이라 지금은 삭제 조문이고 범위 목록에서 뺐다 (생성 규칙은 test_build_articles)"""
    assert '소득세법 시행령 제216조의4' not in set(v5['article_id'])
    assert not ((scope['kind'] == '시행령') & (scope['article_no'] == '제216조의4')).any()


def test_scoped_documents_have_exactly_the_scope_and_its_addendum_overrides(v5, scope):
    """세금·보험 문서: 범위 목록 조문과, 범위 안 본칙을 대신하는 부칙 특례만. 삭제 조문 없음.
    범위는 번호로만 정한다 (사용자 목록의 제목은 시행 전 개정의 제목인 것이 있음: 보험료징수법 제16조의10)"""
    tax = v5[v5['parent_law'].isin(set(scope['law']))]
    main = tax[~tax['article_no'].str.startswith(ADDENDUM)]
    got = set(zip(main['parent_law'], main['kind'], main['article_no']))
    assert got == set(zip(scope['law'], scope['kind'], scope['article_no']))
    assert len(main) == len(scope)
    assert (tax['deleted'] == '0').all()
    extra = tax[tax['article_no'].str.startswith(ADDENDUM)]
    assert list(extra['article_id']) == [
        '소득세법 부칙<제21548호>제2조',
        '국민연금법 부칙<제8541호>제7조',
        '국민연금법 부칙<제11143호>제7조',
        '국민연금법 부칙<제20903호>제4조',
    ]
    assert set(extra['related_ids']) <= set(main['article_id'])  # 대신하는 본칙은 범위 안


def test_labor_documents_equal_v4_except_the_known_rule_changes(v5, scope):
    """노동 문서는 v4와 칸 단위로 같고, 2026-11-03 규칙 변경 두 가지만 다르다 (docs/evals/2026-11-03-v5-generator.md)"""
    v4 = pd.read_csv(V4_DECREE_ARTICLES, **E)
    labor = v5[~v5['parent_law'].isin(set(scope['law']))]
    added = labor[labor['article_no'].str.startswith(ADDENDUM)]
    assert list(added['article_id']) == ['근로자퇴직급여 보장법 부칙<제21475호>제2조']
    labor = labor.drop(added.index).reset_index(drop=True)
    assert list(labor.index[: len(v4)]) == list(v4.index) and len(labor) == len(v4)
    changed = labor != v4
    cells = [(labor.at[i, 'article_id'], labor.columns[j]) for i, j in zip(*changed.to_numpy().nonzero())]
    assert cells == [('근로자퇴직급여 보장법 제1조', 'effective_date')]


def test_mixed_articles_are_marked(v5):
    """시행 전 개정이 남은 채 다시 바뀐 조문 (사용자 결정: 최신 본문 + 주의). 소득세법 8개·지방세법 1개는 나중 법률이 고친
    부칙(가상자산 과세 2027 유예 등)을 읽은 뒤 드러났다 (2026-11-03)"""
    marked = v5[v5['text'].str.contains('[주의:', regex=False)]
    assert list(marked['article_id']) == [
        '소득세법 제14조',
        '소득세법 제21조',
        '소득세법 제37조',
        '소득세법 제70조',
        '소득세법 제81조의11',
        '소득세법 제84조',
        '소득세법 제119조',
        '소득세법 제127조',
        '소득세법 제156조',
        '지방세법 제93조',
        '소득세법 시행령 제179조',
        '소득세법 시행령 제207조',
        '소득세법 시행령 제216조의2',
    ]


# ── v5 세금·보험 질문 평가셋 (qa_split로 만든 것, 계획 3-1) ─────────────


@pytest.fixture(scope='module')
def tax_qa():
    return pd.read_csv(V5 / 'qa_tax_v5.csv', **E)


def test_tax_questions_cover_every_scoped_article_and_link_to_answer_keys(v5, scope, tax_qa):
    keys = pd.read_csv(V5 / 'answer_key_tax_v5.csv', **E)
    tax = v5[v5['parent_law'].isin(set(scope['law'])) & (v5['deleted'] == '0')]
    assert set(tax_qa['article_id']) == set(tax['article_id'])  # 범위 조문 684개 모두 (부칙 특례 포함)
    assert list(tax_qa['id']) == [f'T{i:05d}' for i in range(1, len(tax_qa) + 1)]
    assert (tax_qa['eval_cat'] == 'D_tax').all() and tax_qa['rid'].is_unique and tax_qa['question'].is_unique
    assert set(tax_qa['answer_key']) == set(keys['answer_key']) and keys['answer_key'].is_unique
    owner = dict(zip(keys['answer_key'], keys['article_id']))
    assert (tax_qa['answer_key'].map(owner) == tax_qa['article_id']).all()


def test_tax_split_is_the_v4_rule_and_reproducible(tax_qa):
    from lawbot_tools import qa_split

    sp = pd.read_csv(V5 / 'split_tax_v5.csv', **E)
    pd.testing.assert_frame_equal(qa_split.split(tax_qa), sp)
    test = tax_qa[sp['split_emb'] == 'test']
    assert (test.groupby(['article_id', 'question_type']).size() == 1).all()  # 조문×유형마다 test 1개
    assert (sp['split_emb'] == 'test').equals(sp['split_sllm'] == 'test')
