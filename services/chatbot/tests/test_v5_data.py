"""Check the generated v5 corpus and its frozen evaluation data."""

import csv
import hashlib
import json

import pandas as pd
import pytest

from lawbot import manifest
from lawbot.paths import DATA, V4_DECREE_ARTICLES
from lawbot_common.law_scope import load_law_scope
from lawbot_tools import build_articles

V5 = DATA / 'v5'
LAW_CSV, DECREE_CSV, SCOPE = V5 / 'articles_v5.csv', V5 / 'law_articles_v5_decree.csv', V5 / 'scope_tax.csv'
E = dict(encoding='utf-8-sig', dtype=str, keep_default_na=False)
ADDENDUM = '부칙<'
EXPANDED_LAWS = {
    '고용보험법',
    '국민연금법',
    '국민건강보험법',
    '고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률',
    '노인장기요양보험법',
    '국세기본법',
    '농어촌특별세법',
}


@pytest.fixture(scope='module')
def v5():
    return pd.read_csv(DECREE_CSV, **E)


@pytest.fixture(scope='module')
def scope():
    return pd.read_csv(SCOPE, **E)


def test_v5_scope_expands_registered_documents_and_keeps_two_partial_laws():
    scope = build_articles.load_scope(SCOPE)
    assert {law for law, _ in scope} == {'조세특례제한법', '지방세법'}

    documents = build_articles.docs(scope)
    expected = {
        (entry.name, kind)
        for entry in load_law_scope()
        if entry.name in EXPANDED_LAWS
        for kind in ('법률', *entry.decrees)
    }
    expanded = [(law, kind, selection) for law, kind, selection in documents if law in EXPANDED_LAWS]
    assert {(law, kind) for law, kind, _ in expanded} == expected
    assert len(expanded) == len(expected)
    assert all(selection is None for _, _, selection in expanded)


def test_expanded_documents_are_present_in_the_generated_corpus(v5):
    expanded = v5[v5['parent_law'].isin(EXPANDED_LAWS)]
    expected = {
        (entry.name, kind)
        for entry in load_law_scope()
        if entry.name in EXPANDED_LAWS
        for kind in ('법률', *entry.decrees)
    }
    assert set(zip(expanded['parent_law'], expanded['kind'])) == expected
    assert expanded['article_id'].is_unique
    assert (expanded['deleted'] == '1').any()


@pytest.mark.parametrize(
    ('path', 'law_column', 'baseline_sha256'),
    [
        (SCOPE, 'law', 'fb1f40f4659b907dd5919af57ad4b341b84ecb7fb49df0cf662a1c525eaff3ad'),
        (LAW_CSV, 'law', 'e397deca1c62a88b2b5ac7e1d7ec10b7fa18707d353cd6f04179b97d7322cd47'),
        (DECREE_CSV, 'parent_law', '8678c4080c2310b7aac0cc2545a78de50031491cbab7bb2aa469b8dc099b897b'),
    ],
)
def test_new_b_expansion_preserves_all_other_rows(path, law_column, baseline_sha256):
    """Hash the rows and relative order from main 0eac5af outside the seven expanded laws."""
    with path.open(encoding='utf-8-sig', newline='') as source:
        rows = [row for row in csv.DictReader(source) if row[law_column] not in EXPANDED_LAWS]
    assert hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest() == baseline_sha256


def test_doc_tokens_cover_every_live_article_in_corpus_order(v5):
    tokens = pd.read_csv(V5 / 'doc_tokens_v5.csv', **E)
    live = v5[v5['deleted'] == '0']
    assert tokens['article'].tolist() == live['article_id'].tolist()
    assert tokens['tokens'].astype(int).gt(0).all()
    expanded_ids = set(live.loc[live['parent_law'].isin(EXPANDED_LAWS), 'article_id'])
    rows = tokens[~tokens['article'].isin(expanded_ids)].to_dict('records')
    assert (
        hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
        == 'fb441dc773eee3a40ee2097a26748e446ec2b9ecb80a4d3ff334a7b4924bfe5a'
    )


def test_manifests_match_the_csvs():
    for path in (LAW_CSV, DECREE_CSV):
        m = manifest.check(path)
        assert (m['dataset_version'], m['reference_date']) == ('v5', '2026-11-03')
        assert m['source']['ref'] == 'bcb7aea7782e81c21fbeb08382c442f7c2041690'


def test_law_csv_is_the_law_rows_of_the_decree_csv(v5):
    law = pd.read_csv(LAW_CSV, **E)
    assert law.equals(v5[v5['kind'] == '법률'][list(law.columns)].reset_index(drop=True))


def test_amended_addendum_keeps_postponed_article_out(v5, scope):
    """소득세법 제164조의4(가상자산 거래내역 제출)는 제17757호 부칙이 나중에 2027-01-01 시행으로 고쳐져 아직 시행 전 신설 조문"""
    assert '소득세법 제164조의4' not in set(v5['article_id'])
    assert not ((scope['law'] == '소득세법') & (scope['article_no'] == '제164조의4')).any()


def test_deleted_article_recreated_by_a_pending_amendment_stays_deleted(v5):
    """소득세법 시행령 제216조의4는 2022.2.15 삭제 뒤 제32516호가 2027-01-01 시행으로 다시 만든다. 정부조직법 타법개정(제35947호)이
    그 시행 전 내용을 고친 것도 함께 시행 전이라 전체 조문 CSV에는 삭제 행으로 남는다."""
    row = v5.set_index('article_id').loc['소득세법 시행령 제216조의4']
    assert row['deleted'] == '1'
    assert row['effective_date'] == '2022-02-15'


def test_scoped_documents_have_exactly_the_scope(v5, scope):
    tax = v5[v5['parent_law'].isin(set(scope['law']))]
    main = tax[~tax['article_no'].str.startswith(ADDENDUM)]
    got = set(zip(main['parent_law'], main['kind'], main['article_no']))
    assert got == set(zip(scope['law'], scope['kind'], scope['article_no']))
    assert len(main) == len(scope)
    assert (tax['deleted'] == '0').all()
    for targets in tax.loc[tax['article_no'].str.startswith(ADDENDUM), 'related_ids']:
        assert set(targets.split('|')) <= set(main['article_id'])


def test_expansion_preserves_active_addendum_overrides(v5):
    existing = {
        '국민연금법 부칙<제8541호>제7조',
        '국민연금법 부칙<제11143호>제7조',
        '국민연금법 부칙<제20903호>제4조',
    }
    live = v5[v5['deleted'] == '0']
    assert existing <= set(live['article_id'])
    for targets in live.loc[live['article_id'].isin(existing), 'related_ids']:
        assert set(targets.split('|')) <= set(live['article_id'])


def test_labor_documents_equal_v4_except_the_known_rule_changes(v5):
    """The v4 documents differ in two generator rules recorded in the 2026-11-03 report."""
    v4 = pd.read_csv(V4_DECREE_ARTICLES, **E)
    labor = v5[v5['parent_law'].isin(v4['parent_law'])]
    added = labor[labor['article_no'].str.startswith(ADDENDUM)]
    assert list(added['article_id']) == ['근로자퇴직급여 보장법 부칙<제21475호>제2조']
    labor = labor.drop(added.index).reset_index(drop=True)
    assert list(labor.index[: len(v4)]) == list(v4.index) and len(labor) == len(v4)
    changed = labor != v4
    cells = [(labor.at[i, 'article_id'], labor.columns[j]) for i, j in zip(*changed.to_numpy().nonzero())]
    assert cells == [('근로자퇴직급여 보장법 제1조', 'effective_date')]


def test_existing_mixed_articles_are_marked(v5):
    """Keep the existing 2027-01-01 notices outside the expanded families."""
    unchanged = v5[~v5['parent_law'].isin(EXPANDED_LAWS)]
    marked = unchanged[unchanged['text'].str.contains('[주의:', regex=False)]
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
        '소득세법 시행령 제88조',
        '소득세법 시행령 제164조',
        '소득세법 시행령 제179조',
        '소득세법 시행령 제207조',
        '소득세법 시행령 제216조의2',
        '소득세법 시행규칙 제100조',
        '지방세법 제93조',
    ]


# ── v5 세금·보험 질문 평가셋 (qa_split로 만든 것, 계획 3-1) ─────────────


@pytest.fixture(scope='module')
def tax_qa():
    return pd.read_csv(V5 / 'qa_tax_v5.csv', **E)


def test_frozen_tax_questions_link_to_live_articles_and_answer_keys(v5, scope, tax_qa):
    keys = pd.read_csv(V5 / 'answer_key_tax_v5.csv', **E)
    live = v5[v5['deleted'] == '0']
    selected_tax = live[live['parent_law'].isin(set(scope['law']))]
    covered = set(tax_qa['article_id'])
    assert len(covered) == 684
    assert set(selected_tax['article_id']) <= covered <= set(live['article_id'])
    assert len(tax_qa) == 13_680
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
