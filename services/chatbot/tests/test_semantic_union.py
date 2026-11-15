"""Verify search-only supplementation, coverage, and evaluation with stubs."""

import json

import pytest

from lawbot.cache_seed import Protection
from lawbot.cache_store import init
from lawbot.db.connection import connect
from lawbot.semantic_import_store import import_seed
from test_semantic_seed import SECONDARY
from test_semantic_seed import handoff as handoff


def test_supplement_is_approved_search_only_idempotently(handoff, tmp_path):
    from lawbot.semantic_index_store import read_questions
    from lawbot.semantic_supplement import ACTION, REASON, Supplement, import_supplement

    seed, snapshot = handoff
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    protection = Protection(frozenset(), {}, 'protected', ('사용하지 않는 보호 대상 질문입니다',))
    import_seed(db, seed, snapshot, 1, protection)
    article = snapshot.articles[SECONDARY]
    rows = [
        Supplement('one', SECONDARY, 'version-hash', '새로운 합성 질문'),
        Supplement('two', SECONDARY, 'changed', '근거가 바뀐 질의'),
        Supplement('three', SECONDARY, 'version-hash', '월급 300만원으로 3년 일하면 퇴직금 얼마인가요?'),
        Supplement('four', SECONDARY, 'version-hash', '사용하지 않는 보호 대상 질문입니다'),
        Supplement('five', SECONDARY, 'version-hash', '사용하지 않는 보호 대상 질의입니다'),
        Supplement('six', 'missing', 'changed', '근거가 누락된 질의'),
    ]
    result = import_supplement(db, rows, 'sha', snapshot, 1, protection, version_hashes={SECONDARY: 'version-hash'})
    assert result == import_supplement(
        db, rows, 'sha', snapshot, 1, protection, version_hashes={SECONDARY: 'version-hash'}
    )
    assert result == {
        'input_rows': 6,
        'approved_questions': 1,
        'approved_articles': 1,
        'hash_mismatch': 1,
        'hash_missing': 1,
        'excluded_calculation': 1,
        'protected_exact': 1,
        'protected_near': 1,
        'duplicate': 0,
    }
    _, indexed = read_questions(db, snapshot, 1)
    assert {text for _, text in indexed.values()} >= {'새로운 합성 질문'}
    with connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM atom_answer_bindings').fetchone() == (2,)
        assert conn.execute('SELECT count(*) FROM cache_queries').fetchone() == (2,)
        assert conn.execute(
            'SELECT source_start,source_end,proposition FROM atoms WHERE parent_chunk_id=?', (SECONDARY,)
        ).fetchone() == (0, len(article.text), article.text)
        audit = conn.execute('SELECT actor,reason,detail FROM cache_audit_events WHERE action=?', (ACTION,)).fetchall()
    assert len(audit) == 1
    assert audit[0][:2] == ('import', REASON)
    assert json.loads(audit[0][2])['source_sha256'] == 'sha'


def test_coverage_stops_before_scoring_if_either_split_has_a_gap():
    from lawbot.semantic_search_eval import coverage_counts, require_coverage

    splits = {'val': ['A', 'A'], 'test': ['A', 'B']}
    counts = coverage_counts(splits, {'A'})
    assert counts['val'] == {'questions': 2, 'gold_articles': 1, 'covered_articles': 1, 'covered_questions': 2}
    assert counts['test'] == {'questions': 2, 'gold_articles': 2, 'covered_articles': 1, 'covered_questions': 1}
    with pytest.raises(ValueError, match='test'):
        require_coverage(counts)
    require_coverage(coverage_counts(splits, {'A', 'B'}))


def test_v2_13_14_16_union_keeps_base_order_and_budget_with_conflicts():
    from lawbot.semantic_search_eval import union_candidates

    def hit(article, doc):
        return {'law': 'law', 'article_id': article, 'doc': doc}

    base = [hit('1', 'first'), hit('2', 'second')]
    atomic = [hit('1', 'first'), hit('2', 'different'), hit('3', 'third'), hit('4', 'fourth')]
    assert union_candidates(base, atomic, 3) == (base + [atomic[2]], 1)
    assert union_candidates(base, atomic, 1) == (base, 1)


def test_v2_17_18_score_reuse_requires_matching_pointwise_contract():
    from dataclasses import replace

    from lawbot.semantic_search_eval import PairContract, SearchScores, rank_candidates

    class Fake:
        def __init__(self):
            self.calls = []

        def score(self, question, docs):
            self.calls.append((question, docs))
            return [0.6] * len(docs)

    base = {'law': 'law', 'article_id': '1', 'doc': 'first'}
    extra = {'law': 'law', 'article_id': '2', 'doc': 'second'}
    contract = PairContract('rr', 'revision', 'instruction', '2560/right', 'softmax', 'snapshot', True)
    previous = SearchScores('q', contract, {'first': 0.4})
    reranker = Fake()
    hits, _, reused = rank_candidates('q', [base, extra], reranker, contract, previous)
    assert [hit['article_id'] for hit in hits] == ['2', '1']
    assert reranker.calls == [('q', ['second'])]
    assert reused == 1
    for mismatch in (
        replace(contract, pointwise=False),
        replace(contract, snapshot='other'),
        replace(contract, truncation='512'),
        replace(contract, instruction='B'),
        replace(contract, score='logit'),
        replace(contract, revision='other'),
    ):
        reranker.calls.clear()
        _, _, reused = rank_candidates('q', [base, extra], reranker, mismatch, previous)
        assert reused == 0
        assert reranker.calls == [('q', ['first', 'second'])]


def test_v2_15_38_search_index_miss_falls_back_but_shared_model_error_propagates():
    from lawbot.api_client import ModelServerError
    from lawbot.semantic_search_eval import PairContract, ResearchSearch

    hit = {'law': 'law', 'article_id': '1', 'doc': 'first'}

    class Base:
        def search(self, question, k):
            return [hit]

    class Fake:
        candidates = 1

        def score(self, question, docs):
            if question == 'fails':
                raise ModelServerError('stub reranker unavailable')
            return [0.6]

    contract = PairContract('rr', 'revision', 'instruction', '2560/right', 'softmax', 'snapshot', True)
    search = ResearchSearch(Base(), Fake(), None, {}, None, contract)
    base = search.run('base', 'q', 3, 4)
    union = search.run('union', 'q', 3, 4)
    assert union.hits == base.hits and union.metrics['atomic_reason'] == 'unavailable_index'
    assert search.run('atomic', 'q', 3, 4).hits == []
    with pytest.raises(ModelServerError):
        search.run('union', 'fails', 3, 4)


def test_semantic_calibration_counts_wrong_answers_and_prefers_zero_false_hits():
    from lawbot.semantic_search_eval import ShadowRow, select_thresholds

    rows = [
        ShadowRow('reusable', True, 0.8, 0.9, 0.2, True),
        ShadowRow('reusable', False, 0.9, 0.75, 0.2, True),
        ShadowRow('not_reusable', False, 0.9, 0.75, 0.2, True),
        ShadowRow('uncertain', False, 0.8, 0.8, 0.1, True),
    ]
    chosen, counts = select_thresholds(rows, [0.5, 0.8], [0.0, 0.15])
    assert counts == {
        'reusable_hits': 1,
        'reusable_misses': 1,
        'wrong_answer': 0,
        'not_reusable_false_hits': 0,
        'uncertain_hits': 0,
    }
    assert chosen[1] >= 0.75 or chosen[2] >= 0.2


def test_comparison_reports_paired_wins_and_signed_added_latency():
    from lawbot.semantic_search_eval import summarize_comparison

    rows = [
        {
            'id': '1',
            'cohorts': ['labor', 'covered', 'supplement'],
            'results': {
                'base': {'rank': 1, 'total_ms': 2.0},
                'atomic': {'rank': None, 'total_ms': 4.0},
                'union': {'rank': 1, 'total_ms': 3.0},
            },
        },
        {
            'id': '2',
            'cohorts': ['labor', 'covered'],
            'results': {
                'base': {'rank': None, 'total_ms': 5.0},
                'atomic': {'rank': 3, 'total_ms': 3.0},
                'union': {'rank': 3, 'total_ms': 4.0},
            },
        },
    ]
    totals = summarize_comparison(rows)
    assert totals['labor']['base']['r1'] == 0.5
    assert totals['labor']['atomic']['r3'] == 0.5
    assert totals['labor']['atomic']['mrr'] == 1 / 6
    assert totals['labor']['base_vs_atomic'] == {'base_only': 1, 'atomic_only': 1, 'both': 0, 'neither': 0}
    assert totals['labor']['union_added_ms'] == {'p50': 0.0, 'p95': pytest.approx(0.9)}


def test_cli_coverage_gate_is_checked_before_any_model_client(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from lawbot import semantic_eval

    monkeypatch.setattr(
        semantic_eval,
        'coverage',
        lambda *args: {
            'val': {'questions': 1, 'gold_articles': 1, 'covered_articles': 1},
            'test': {'questions': 1, 'gold_articles': 1, 'covered_articles': 0},
        },
    )
    monkeypatch.setattr(semantic_eval, 'corpus_context', lambda db: SimpleNamespace(db=db, snapshot=None, event=1))
    monkeypatch.setattr(semantic_eval, 'check_db_path', lambda path: path)
    monkeypatch.setattr(semantic_eval, 'ModelServerClient', lambda *args: pytest.fail('model client initialized'))
    with pytest.raises(ValueError, match='test'):
        semantic_eval.run(
            SimpleNamespace(cache_db=str(tmp_path / 'a'), corpus_db=str(tmp_path / 'b'), phase='once-test')
        )


def test_atomic_and_union_reach_unique_parents_in_index_order():
    import numpy as np

    from lawbot.semantic_runtime import AtomicIndex
    from lawbot.semantic_search_eval import PairContract, ResearchSearch

    def hit(article):
        return {'law': 'law', 'article_id': article, 'doc': article}

    class Base:
        def search(self, question, k):
            return [hit('base'), hit('shared')][:k]

    class Fake:
        candidates = 2

        def score(self, question, docs):
            self.last_docs = docs
            return [0.6] * len(docs)

    vectors = np.array([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    index = AtomicIndex((), ('q1', 'q2', 'q3'), vectors)
    reranker = Fake()
    contract = PairContract('rr', 'rev', 'instruction', 'length', 'probability', 'snapshot', True)
    search = ResearchSearch(
        Base(),
        reranker,
        index,
        {'q1': hit('other'), 'q2': hit('other'), 'q3': hit('shared')},
        lambda q: vectors[:1],
        contract,
    )
    atomic = search.run('atomic', 'q', 3, 3)
    assert reranker.last_docs == ['other', 'shared']
    assert atomic.metrics['atomic_query_count'] == 3
    assert atomic.metrics['atomic_parent_count'] == 2
    for _ in range(2):
        union = search.run('union', 'q', 3, 3)
        assert reranker.last_docs == ['base', 'shared', 'other']
        assert [hit['article_id'] for hit in union.hits] == reranker.last_docs


def test_test_stage_requires_all_val_results_with_matching_inputs(tmp_path):
    from lawbot.semantic_eval import PHASES, prior_stages, write_private

    with pytest.raises(ValueError, match='compare-val'):
        prior_stages(tmp_path, 'once-test', {'index': 'first'})
    for stage in PHASES[:-1]:
        write_private(tmp_path / f'{stage}.json', {'input': {'index': 'first'}})
    assert set(prior_stages(tmp_path, 'once-test', {'index': 'first'})) == set(PHASES[:-1])
    with pytest.raises(ValueError, match='바뀌었습니다'):
        prior_stages(tmp_path, 'once-test', {'index': 'second'})
    assert (tmp_path / 'shadow-val.json').stat().st_mode & 0o777 == 0o600


def test_evaluation_sweeps_val_before_using_frozen_routes_on_test(monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from lawbot import semantic_eval
    from lawbot.semantic_search_eval import SearchResult

    calls = []

    class Search:
        reranker = SimpleNamespace(candidates=10)

        def run(self, route, question, k, limit):
            calls.append((route, question, k, limit))
            doc = 'gold' if (route == 'base' or k == 50) else 'miss'
            return SearchResult([{'law': 'law', 'article_id': doc}], {'atomic_reason': 'ok', 'total_ms': 1.0})

    def data(db, dataset, split, *, questions=False):
        assert questions
        return [(f'{dataset}:1', 'law gold', f'{dataset}:{split}')]

    monkeypatch.setattr(semantic_eval, 'evaluation_rows', data)
    monkeypatch.setattr(
        semantic_eval, 'tax_legal_rows', lambda ctx, split, **kwargs: data(ctx.db, 'v5', split, **kwargs)
    )
    context = SimpleNamespace(db=Path('synthetic.db'))
    search = Search()
    arguments = (context, tmp_path, search, {'law gold'}, {'law gold'})
    first = semantic_eval.compare_phase(*arguments)
    assert first['atomic_query_k'] == 50
    assert all(question == 'v4:val' for _, question, _, _ in calls)
    assert [route for route, *_ in calls] == ['base', 'atomic', 'atomic']
    assert first['comparisons'][50]['supplement']['base_vs_atomic']['both'] == 1


def test_shadow_calibration_reads_only_bindings_from_full_index_with_supplement(handoff, tmp_path):
    import numpy as np

    from lawbot.semantic_index_tools import IndexContract, build_index
    from lawbot.semantic_store import SemanticStore
    from lawbot.semantic_supplement import Supplement, import_supplement

    class Encoder:
        def revision(self):
            return 'fake'

        def fits(self, text):
            return True

        def encode(self, questions):
            return np.tile(np.array([1, 0], dtype=np.float32), (len(questions), 1))

    seed, snapshot = handoff
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    protection = Protection(frozenset(), {}, 'protected')
    import_seed(db, seed, snapshot, 1, protection)
    import_supplement(
        db,
        [Supplement('supplemented', SECONDARY, 'valid', '보충 합성 질문')],
        'sha',
        snapshot,
        1,
        protection,
        version_hashes={SECONDARY: 'valid'},
    )
    result = build_index(db, tmp_path / 'index', snapshot, 1, IndexContract('stub', 'fake', 1024, 2), Encoder())
    store = SemanticStore(db, snapshot, result['index_revision'])
    eligible, groups = store.calibration_candidates(store.manifest(), 1, set())
    assert result['rows'] == 3
    assert len(eligible) == len(groups) == 2
    assert {item.question for items in eligible.values() for item in items} == {q.text for q in seed.questions[:2]}
