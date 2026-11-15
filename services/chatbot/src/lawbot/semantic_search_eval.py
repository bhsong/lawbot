"""Compare statutory search routes without changing the web app's dispatcher."""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import product
from time import perf_counter
from typing import cast

import numpy as np

from lawbot.db.connection import connect
from lawbot.decree import rank_order
from lawbot.hits import Hit
from lawbot.reranker import Reranker
from lawbot.retriever import Retriever
from lawbot.semantic_gate import valid_vectors
from lawbot.semantic_index_store import index_questions
from lawbot.semantic_runtime import AtomicIndex

SPLITS = ('val', 'test')


def evaluation_rows(db, dataset, split, *, questions=False):
    columns = ',q.question' if questions else ''
    with connect(db) as conn:
        return conn.execute(
            f'SELECT q.qa_uid,q.article_id{columns} FROM qa_items q JOIN qa_splits s USING (qa_uid) '
            "WHERE q.status='active' AND q.dataset=? AND s.split_set=q.dataset AND s.purpose='emb' "
            "AND s.split=? AND (?<>'v4' OR q.eval_cat IN ('A_lsa','B_newlaw')) ORDER BY q.qa_uid",
            (dataset, split, dataset),
        ).fetchall()


def coverage_counts(splits, approved):
    return {
        split: {
            'questions': len(gold),
            'gold_articles': len(set(gold)),
            'covered_articles': len(set(gold) & approved),
            'covered_questions': sum(article in approved for article in gold),
        }
        for split, gold in splits.items()
    }


def require_coverage(counts):
    missing = [
        split
        for split in SPLITS
        if not counts[split]['questions'] or counts[split]['gold_articles'] != counts[split]['covered_articles']
    ]
    if missing:
        raise ValueError('승인 원자 커버리지 미충족: ' + ', '.join(missing))


def approved_parents(db, snapshot, event):
    parents: dict[str, str] = {}
    with connect(db) as conn:
        index_questions(conn, snapshot, event, parents=parents)
    return parents


def coverage(db, cache, snapshot, event):
    approved = set(approved_parents(cache, snapshot, event).values())
    splits = {split: [gold for _, gold in evaluation_rows(db, 'v4', split)] for split in SPLITS}
    return coverage_counts(splits, approved)


@dataclass(frozen=True)
class PairContract:
    model: str
    revision: str
    instruction: str
    truncation: str
    score: str
    snapshot: str
    pointwise: bool


@dataclass(frozen=True)
class SearchScores:
    question: str
    contract: PairContract
    by_document: dict[str, float]


def hit_key(hit: Hit) -> str:
    return f'{hit["law"]} {hit["article_id"]}'


def union_candidates(base: list[Hit], atomic: list[Hit], limit: int) -> tuple[list[Hit], int]:
    """Preserve the base list and drop atomic versions that conflict with it or each other."""
    documents = {hit_key(hit): hit['doc'] for hit in base}
    additions: dict[str, Hit] = {}
    conflicts = set()
    for hit in atomic:
        key = hit_key(hit)
        if key in documents:
            if documents[key] != hit['doc']:
                conflicts.add(key)
        elif key in additions and additions[key]['doc'] != hit['doc']:
            conflicts.add(key)
        else:
            additions[key] = hit
    extra = [hit for key, hit in additions.items() if key not in conflicts]
    return base + extra[: max(0, limit - len(base))], len(conflicts)


def rank_candidates(
    question: str,
    hits: list[Hit],
    reranker,
    contract: PairContract,
    previous: SearchScores | None = None,
):
    documents = [hit['doc'] for hit in hits]
    matching = (
        previous.by_document
        if contract.pointwise and previous and previous.question == question and previous.contract == contract
        else {}
    )
    missing = [doc for doc in documents if doc not in matching]
    new = reranker.score(question, missing) if missing else []
    scored = matching | dict(zip(missing, new, strict=True))
    scores = [scored[doc] for doc in documents]
    ranked = [cast(Hit, hits[i] | {'rerank_score': float(scores[i]), 'emb_rank': i + 1}) for i in rank_order(scores)]
    return ranked, SearchScores(question, contract, {doc: scored[doc] for doc in documents}), len(hits) - len(missing)


@dataclass(frozen=True)
class SearchResult:
    hits: list[Hit]
    metrics: dict[str, str | int | float]


@dataclass(frozen=True)
class ResearchSearch:
    retriever: Retriever
    reranker: Reranker
    index: AtomicIndex | None
    parents: dict[str, Hit]
    encode: Callable[[str], np.ndarray | None] | None
    contract: PairContract
    index_revision: str = ''

    def atomic_candidates(self, question: str, query_k: int):
        if self.index is None or self.encode is None or not self.parents:
            return [], 'unavailable_index', 0, 0
        if not self.index.active(self.index_revision):
            return [], 'stale_index', 0, 0
        vectors = self.encode(question)
        if vectors is None:
            return [], 'truncated_input', 0, 0
        if not valid_vectors(vectors, 1, self.index.vectors.shape[1]):
            return [], 'invalid_vector', 1, 0
        revisions = self.index.top_queries(vectors[0], set(self.parents).intersection(self.index.query_ids), query_k)
        unique: dict[str, Hit] = {}
        for revision in revisions:
            hit = self.parents[revision]
            unique.setdefault(hit_key(hit), hit)
        return list(unique.values()), 'ok', 1, len(revisions)

    def run(self, route: str, question: str, query_k: int, union_limit: int, *, previous=None) -> SearchResult:
        if route not in ('base', 'atomic', 'union') or query_k < 1 or union_limit < 1:
            raise ValueError('invalid retrieval route or candidate budget')
        started = perf_counter()
        base = self.retriever.search(question, k=self.reranker.candidates) if route != 'atomic' else []
        atomic, reason, atomic_calls, atomic_queries = (
            self.atomic_candidates(question, query_k) if route != 'base' else ([], 'disabled', 0, 0)
        )
        collisions = 0
        if route == 'union':
            candidates, collisions = union_candidates(base, atomic, union_limit)
        elif route == 'atomic':
            candidates = atomic[: self.reranker.candidates]
        else:
            candidates = base
        search_ms = (perf_counter() - started) * 1000
        rerank_start = perf_counter()
        hits, _, reused = rank_candidates(question, candidates, self.reranker, self.contract, previous)
        return SearchResult(
            hits,
            {
                'atomic_reason': reason,
                'embedding_calls': int(route != 'atomic') + atomic_calls,
                'atomic_query_count': atomic_queries,
                'atomic_parent_count': len(atomic),
                'base_candidates': len(base),
                'rerank_candidates': len(candidates),
                'conflicts': collisions,
                'rerank_pairs': len(candidates) - reused,
                'reused_pairs': reused,
                'rerank_calls': int(len(candidates) > reused),
                'search_ms': search_ms,
                'rerank_ms': (perf_counter() - rerank_start) * 1000,
                'total_ms': (perf_counter() - started) * 1000,
            },
        )


@dataclass(frozen=True)
class ShadowRow:
    label: str
    correct_answer: bool
    parent: float | None
    question: float | None
    margin: float | None
    eligible: bool


def select_thresholds(rows: list[ShadowRow], thresholds, margins):
    """Prefer zero unsafe hits, then maximize capture of labeled reusable answers."""
    labels = {'reusable', 'not_reusable', 'uncertain'}
    if {row.label for row in rows} != labels:
        raise ValueError('semantic val must include all three labels')
    eligible = [row for row in rows if row.eligible and row.parent is not None and row.question is not None]
    parent_values = np.array([row.parent for row in eligible], dtype=float)
    question_values = np.array([row.question for row in eligible], dtype=float)
    margin_values = np.array([row.margin if row.margin is not None else np.inf for row in eligible])
    reusable = np.array([row.label == 'reusable' and row.correct_answer for row in eligible], dtype=bool)
    wrong = np.array([row.label == 'reusable' and not row.correct_answer for row in eligible], dtype=bool)
    negative = np.array([row.label == 'not_reusable' for row in eligible], dtype=bool)
    uncertain = np.array([row.label == 'uncertain' for row in eligible], dtype=bool)
    reusable_count = sum(row.label == 'reusable' for row in rows)
    best_order = None
    selection = None
    for parent, question, margin in product(
        sorted({*thresholds, 1.0}), sorted({*thresholds, 1.0}), sorted(set(margins))
    ):
        hit = (parent_values > parent) & (question_values > question) & (margin_values > margin)
        correct, incorrect, false, unsure = (
            int(np.count_nonzero(hit & label)) for label in (reusable, wrong, negative, uncertain)
        )
        order = (false, incorrect, unsure, -correct, -parent, -question, -margin)
        if best_order is None or order < best_order:
            best_order = order
            selection = (
                (parent, question, margin),
                {
                    'reusable_hits': correct,
                    'reusable_misses': reusable_count - correct,
                    'wrong_answer': incorrect,
                    'not_reusable_false_hits': false,
                    'uncertain_hits': unsure,
                },
            )
    if selection is None:
        raise ValueError('a semantic margin grid is required')
    return selection


def latency(values):
    p50, p95 = np.percentile(values, [50, 95])
    return {'p50': float(p50), 'p95': float(p95)}


def paired_outcome(base_rank, atomic_rank):
    return {
        (True, True): 'both',
        (True, False): 'base_only',
        (False, True): 'atomic_only',
        (False, False): 'neither',
    }[(base_rank is not None and base_rank <= 3, atomic_rank is not None and atomic_rank <= 3)]


def summarize_comparison(rows):
    cohorts = defaultdict(list)
    for row in rows:
        for group in row['cohorts']:
            cohorts[group].append(row['results'])
    summary = {}
    for group, items in cohorts.items():
        routes = items[0].keys()
        stats: dict[str, int | dict] = {'questions': len(items)}
        for route in routes:
            results = [item[route] for item in items]
            ranks = [result['rank'] or float('inf') for result in results]
            stats[route] = {
                'r1': sum(rank == 1 for rank in ranks) / len(ranks),
                'r3': sum(rank <= 3 for rank in ranks) / len(ranks),
                'mrr': sum(1 / rank for rank in ranks) / len(ranks),
                'search_ms': latency([result['total_ms'] for result in results]),
            }
        if 'atomic' in routes and 'base' in routes:
            pairs = Counter(paired_outcome(item['base']['rank'], item['atomic']['rank']) for item in items)
            stats['base_vs_atomic'] = {name: pairs[name] for name in ('base_only', 'atomic_only', 'both', 'neither')}
        if 'union' in routes:
            stats['union_added_ms'] = latency([item['union']['total_ms'] - item['base']['total_ms'] for item in items])
        summary[group] = stats
    return summary
