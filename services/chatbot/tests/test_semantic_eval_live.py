"""Check live evaluation safeguards with synthetic logs, vectors, and responses."""

from types import SimpleNamespace

import numpy as np

from lawbot.semantic_runtime import AtomicIndex
from lawbot.semantic_search_eval import PairContract, ResearchSearch


def test_union_shares_one_query_vector_and_preserves_exact_candidate_ranking():
    hit = {'law': 'law', 'article_id': '1', 'doc': 'first'}
    other = {'law': 'law', 'article_id': '2', 'doc': 'second'}
    vector = np.array([1, 0], dtype=np.float32)
    calls = []

    def embed(question):
        calls.append(question)
        return vector

    def search_vector(values, k):
        assert values is vector
        return [hit]

    base = SimpleNamespace(
        query_vector=embed, search_vector=search_vector, search=lambda q, k: search_vector(embed(q), k)
    )
    reranker = SimpleNamespace(
        candidates=1, score=lambda query, docs: [0.7 if doc == 'second' else 0.4 for doc in docs]
    )
    contract = PairContract('rr', 'rev', 'task', 'length', 'prob', 'snapshot', True)
    index = AtomicIndex((), ('second',), vector[None, :])
    shared = ResearchSearch(base, reranker, index, {'second': other}, None, contract, shared_query=lambda q: True)
    assert [item['article_id'] for item in shared.run('union', 'q', 2, 2).hits] == ['2', '1']
    assert calls == ['q']
    baseline = shared.run('base', 'q', 2, 2)
    assert baseline.hits[0]['article_id'] == '1'
    assert calls == ['q', 'q']


def test_journal_remembers_answered_calls_and_skips_unconfirmed_calls(tmp_path):
    from lawbot.semantic_eval_store import StageJournal

    path = tmp_path / 'private.sqlite'
    with StageJournal(path) as journal:
        assert journal.begin_call('q', 0, 'rerank', 'signature') is None
        journal.save_call('q', 0, [0.4], 20.0)
        assert journal.begin_call('q', 1, 'rerank', 'second') is None
        journal.save_item('somekey', {'id': 'a'})
    with StageJournal(path) as journal:
        assert journal.get_item('somekey') == {'id': 'a'}
        assert journal.begin_call('q', 0, 'rerank', 'signature') == ('done', [0.4], 20.0)
        assert journal.begin_call('q', 1, 'rerank', 'second') == ('interrupted', None, 0.0)
        assert journal.request_counts() == {'rerank': 2}


def test_logging_filters_top_level_fields_and_memory_uses_relative_swap():
    from lawbot.semantic_eval_watch import log_fields, memory_state

    assert log_fields(
        b'{"ts":"2026-01-01","metadata":{"total_ms":5000},"question":"private-data","status":"ok","total_ms":150}'
    ) == {
        'ts': '2026-01-01',
        'status': 'ok',
        'total_ms': 150,
    }
    starting = {'pressure': 2, 'free_percent': 37, 'swap_mib': 4200}
    assert memory_state(starting | {'swap_mib': 6200}, starting) == 'ready'
    assert memory_state(starting | {'swap_mib': 7300}, starting) == 'pause'
    assert memory_state(starting | {'free_percent': 12}, starting) == 'watch'
