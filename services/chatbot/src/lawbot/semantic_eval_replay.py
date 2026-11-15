"""Reconstruct ranked candidates from durable, content-verified model receipts."""

from collections import Counter
from io import BytesIO

import numpy as np

from lawbot.exact_cache import digest
from lawbot.reranker import TASK
from lawbot.semantic_eval_store import call_signature
from lawbot.semantic_search_eval import UnionReplay, hit_key, latency


def item_key(route, question_k, limit, uid):
    return f'{route}:{question_k}:{limit}:{uid}'


def response(journal, item, position, kind, payload):
    row = journal.begin_call(item, position, kind, call_signature(payload), reserve=False)
    if row is None or row[0] != 'done':
        return None
    return np.load(BytesIO(row[1]), allow_pickle=False) if kind == 'embedding' else row[1]


class CachedDocumentEncoder:
    """Use the phase-one article vectors with the same corpus and embedding signature."""

    def __init__(self, source, model, client):
        self.source = source
        self.model = model
        self.client = client

    def encode(self, texts, input_type, max_seq_length):
        if input_type == 'query':
            if self.client is None:
                raise ValueError('union plan cannot send a query embedding request')
            return self.client.embed(self.model, texts, input_type, max_seq_length)
        cached = response(
            self.source,
            'corpus-documents',
            0,
            'embedding',
            [self.model, texts, input_type, max_seq_length],
        )
        if cached is None:
            raise ValueError('비교 단계의 조문 임베딩 영수증이 없습니다.')
        return cached


def restore(saved, search, calibration, uid, question, atomic_k, *, base_k, base_limit, atomic_limit, atomic_reason):
    """Replay the candidates in request order before associating each score with its document."""
    model = calibration.embedding_model
    base_key = item_key('base', base_k, base_limit, uid)
    atomic_key = item_key('atomic', atomic_k, atomic_limit, uid)
    query = response(
        saved,
        base_key,
        0,
        'embedding',
        [model, [question], 'query', search.retriever.max_seq_length],
    )
    if query is None:
        return None, 'base_embedding_unavailable'
    base = search.retriever.search_vector(query[0], search.reranker.candidates)
    if atomic_reason == 'truncated_input':
        atomic, previously_scored = [], []
    else:
        atomic_query = response(
            saved,
            atomic_key,
            0,
            'embedding',
            [model, [question], 'query', calibration.query_max_seq],
        )
        if atomic_query is None:
            return None, 'atomic_embedding_unavailable'
        previously_scored, reason, _, _ = search.atomic_candidates(question, atomic_k, vector=atomic_query[0])
        if reason != 'ok':
            return None, reason
        atomic, reason, _, _ = search.atomic_candidates(question, atomic_k, vector=query[0])
        if reason != 'ok':
            return None, reason
    scores = {}
    for key, hits in ((base_key, base), (atomic_key, previously_scored[: search.reranker.candidates])):
        if not hits:
            continue
        docs = [hit['doc'] for hit in hits]
        values = response(saved, key, 1, 'rerank', [search.reranker.model, question, docs, TASK, False])
        if values is None:
            return None, 'original_rerank_unavailable'
        for doc, value in zip(docs, values, strict=True):
            scores.setdefault(doc, value)
    return UnionReplay(base, atomic, scores), None


def verify_replay(offline_rows, online_rows):
    """Compare exact top-three order and retain deltas for all common candidate pairs."""
    reports = []
    added = []
    cohort_times = {}
    for offline, online in zip(offline_rows, online_rows, strict=True):
        if offline['id'] != online['id']:
            raise ValueError('union verification sample IDs are out of order')
        base, expected, actual = offline['results']['base'], offline['results']['union'], online['results']['union']
        left, right = expected.get('scores', {}), actual.get('scores', {})
        deltas = {doc: right[doc] - left[doc] for doc in left.keys() & right.keys()}
        valid = not (expected.get('error') or actual.get('error'))
        diff = valid and expected['top3'] != actual['top3']
        if not base.get('error') and not actual.get('error'):
            delta = actual['total_ms'] - base['total_ms']
            added.append(delta)
            for group in offline['cohorts']:
                cohort_times.setdefault(group, []).append((actual['total_ms'], delta))
        reports.append(
            {
                'id': offline['id'],
                'compared': valid,
                'different_top3': diff,
                'offline_top3': expected['top3'],
                'live_top3': actual['top3'],
                'score_deltas': deltas,
            }
        )
    return reports, {
        'sampled': len(reports),
        'top3_compared': sum(r['compared'] for r in reports),
        'top3_different': sum(r['different_top3'] for r in reports),
        'max_abs_score_delta': max((abs(v) for r in reports for v in r['score_deltas'].values()), default=None),
        'miss_added_ms': latency(added) if added else None,
        'cohort_latency': {
            group: {
                'questions': len(times),
                'union_ms': latency([total for total, _ in times]),
                'miss_added_ms': latency([delta for _, delta in times]),
            }
            for group, times in cohort_times.items()
        },
    }


def result_values(result, gold):
    keys = [hit_key(hit) for hit in result.hits]
    return result.metrics | {
        'rank': keys.index(gold) + 1 if gold in keys else None,
        'top3': keys[:3],
        'error': None,
        'scores': {hit_key(hit): hit.get('rerank_score', 0.0) for hit in result.hits},
    }


def proposed_queries(saved, search, calibration, examples, previous, chosen, limits, *, compare, compare_grid):
    if compare:
        base_k, base_limit, atomic_limit = compare_grid[0], search.reranker.candidates, search.reranker.candidates
    else:
        base_k, base_limit, atomic_limit = chosen, limits[-1], limits[-1]
    if len(examples) != len(previous):
        raise ValueError('union replay requires the complete baseline rows')
    for (uid, gold, question), baseline in zip(examples, previous, strict=True):
        if uid != baseline['id'] or gold != baseline['gold']:
            raise ValueError('union replay baseline does not match the question order')
        atomic = baseline['results']['atomic']
        restored, reason = restore(
            saved,
            search,
            calibration,
            uid,
            question,
            chosen,
            base_k=base_k,
            base_limit=base_limit,
            atomic_limit=atomic_limit,
            atomic_reason=atomic.get('atomic_reason'),
        )
        yield uid, gold, question, baseline, restored, reason


def plan_proposals(proposals, limits):
    pairs, requests = Counter(), Counter()
    unavailable = 0
    for _, _, _, _, restored, _ in proposals:
        if restored is None:
            unavailable += 1
            continue
        for limit in limits:
            n = len(restored.missing(limit))
            pairs[str(limit)] += n
            requests[str(limit)] += int(n > 0)
    return {'additional_pairs': dict(pairs), 'additional_rerank_requests': dict(requests), 'unavailable': unavailable}


def sampled_rows(rows):
    return sorted(rows, key=lambda row: digest(row['id']))[:100]
