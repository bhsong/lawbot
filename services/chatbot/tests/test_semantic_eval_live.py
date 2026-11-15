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


def test_pointwise_union_replays_full_ranking_and_scores_only_new_pairs():
    from lawbot.semantic_search_eval import PairContract, ResearchSearch, UnionReplay

    def hit(number):
        return {'law': 'law', 'article_id': str(number), 'doc': str(number)}

    scores = {'1': 0.5, '2': 0.8, '3': 0.1, '4': 0.9}
    requests = []

    def score(question, docs):
        requests.append(docs)
        return [scores[doc] for doc in docs]

    reranker = SimpleNamespace(candidates=2, score=score)
    base = [hit(1), hit(2)]
    atomic = [hit(2), hit(3), hit(4)]
    contract = PairContract('r', 'rev', 'instruction', '2560', 'softmax', 'snapshot', True)
    replay = UnionReplay(base, atomic, {'1': scores['1'], '2': scores['2'], '3': scores['3']})
    assert replay.missing(3) == []
    assert replay.missing(4) == ['4']
    approximate = replay.rank('q', 4, reranker, contract)
    assert requests == [['4']]
    assert [hit['article_id'] for hit in approximate.hits] == ['4', '2', '1', '3']
    search = ResearchSearch(
        SimpleNamespace(search=lambda q, k: base),
        reranker,
        AtomicIndex((), ('a', 'b', 'c'), np.array([[1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32)),
        dict(zip(('a', 'b', 'c'), atomic, strict=True)),
        lambda q: np.array([[1, 0]], dtype=np.float32),
        contract,
    )
    actual = search.run('union', 'q', 3, 4)
    assert [h['article_id'] for h in actual.hits] == [h['article_id'] for h in approximate.hits]


def test_union_replay_uses_journal_pair_order_and_original_query_embedding(tmp_path):
    from io import BytesIO

    from lawbot.reranker import TASK
    from lawbot.semantic_eval_replay import item_key, restore
    from lawbot.semantic_eval_store import StageJournal, call_signature

    vector = np.array([[1.0, 0.0]], dtype=np.float32)
    buffer = BytesIO()
    np.save(buffer, vector, allow_pickle=False)
    document = {'law': 'L', 'article_id': '1', 'doc': 'law text'}
    extra = {'law': 'L', 'article_id': '2', 'doc': 'atomic text'}
    search = SimpleNamespace(
        retriever=SimpleNamespace(max_seq_length=4352, search_vector=lambda q, k: [document]),
        reranker=SimpleNamespace(candidates=1, model='rr'),
        atomic_candidates=lambda q, k, vector: ([extra], 'ok', 0, 1),
    )
    calibration = SimpleNamespace(embedding_model='emb', query_max_seq=1024)
    db = tmp_path / 'compare.sqlite'
    with StageJournal(db) as journal:
        for route, limit, docs, score in (
            ('base', 4352, [document['doc']], 0.25),
            ('atomic', 1024, [extra['doc']], 0.95),
        ):
            key = item_key(route, 20, 1, 'v4:1')
            journal.begin_call(key, 0, 'embedding', call_signature(['emb', ['q'], 'query', limit]))
            journal.save_call(key, 0, buffer.getvalue(), 10)
            journal.begin_call(key, 1, 'rerank', call_signature(['rr', 'q', docs, TASK, False]))
            journal.save_call(key, 1, [score], 20)
    with StageJournal(db, read_only=True) as saved:
        result, reason = restore(
            saved, search, calibration, 'v4:1', 'q', 20, base_k=20, base_limit=1, atomic_limit=1, atomic_reason='ok'
        )
    assert reason is None
    assert result.scores == {'law text': 0.25, 'atomic text': 0.95}
    assert result.missing(2) == []


def test_union_validation_sample_reports_order_score_and_latency_differences():
    from lawbot.semantic_eval_replay import sampled_rows, verify_replay

    cases = [{'id': f'case-{i}'} for i in range(120)]
    assert len(sampled_rows(cases)) == 100
    assert sampled_rows(cases) == sampled_rows(list(reversed(cases)))
    offline = {
        'id': 'case',
        'cohorts': ['labor'],
        'results': {
            'base': {'total_ms': 10.0},
            'union': {'top3': ['A', 'B'], 'scores': {'A': 0.6, 'B': 0.5}, 'total_ms': 0.1},
        },
    }
    online = {
        'id': 'case',
        'results': {'union': {'top3': ['B', 'A'], 'scores': {'B': 0.7, 'A': 0.6}, 'total_ms': 15.0}},
    }
    records, metrics = verify_replay([offline], [online])
    assert records[0]['different_top3']
    assert abs(records[0]['score_deltas']['B'] - 0.2) < 1e-10
    assert metrics['top3_compared'] == metrics['top3_different'] == 1
    assert metrics['miss_added_ms'] == {'p50': 5.0, 'p95': 5.0}
