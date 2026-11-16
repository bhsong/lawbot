"""Rank reviewed questions with the vector already used by legal retrieval."""

from dataclasses import dataclass
from threading import Lock

import numpy as np

from lawbot import cache_runtime
from lawbot.cache_question_store import question_catalog, unit_vector
from lawbot.cache_runtime import ExactCache
from lawbot.cache_store import READ_TIMEOUT, CacheDatabaseError, Control, now, translate_sqlite_errors, valid
from lawbot.exact_cache import PROFILE, normalize
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')


@dataclass(frozen=True)
class Candidate:
    id: str
    answer_id: str
    question: str
    vector: np.ndarray
    source_id: str = ''
    bounds: tuple[tuple[str | None, str | None, str | None], ...] = ()


def candidates_with_vectors(questions, vectors):
    return [
        Candidate(q.id, q.answer_id, q.original, vectors[q.id], q.source_id, (q.query_bounds, q.answer_bounds))
        for q in questions
        if q.id in vectors
    ]


def rank(candidates, query, question, tau, *, eligible=lambda _: True):
    """Return up to two distinct answers at the inclusive cosine threshold."""
    direction = unit_vector(query)
    if direction is None:
        return []
    normalized = normalize(question)
    pool = [row for row in candidates if row.vector.shape == direction.shape and normalize(row.question) != normalized]
    if not pool:
        return []
    scores = np.clip(np.stack([row.vector for row in pool]) @ direction, -1.0, 1.0)
    picked = []
    seen = set()
    for index in np.argsort(-scores, kind='stable'):
        score = float(scores[index])
        if score < tau:
            break
        row = pool[index]
        if row.answer_id not in seen and eligible(row):
            seen.add(row.answer_id)
            picked.append((row, score))
            if len(picked) == 2:
                break
    return picked


@dataclass(frozen=True)
class Recommendation:
    matches: list[tuple[Candidate, float]]
    error: str | None = None


def failure(stage, kind):
    log.warning('검토 질문 추천 실패 · %s · %s', stage, kind)
    return Recommendation([], kind)


def error_kind(exc):
    return exc.reason if isinstance(exc, CacheDatabaseError) else type(exc).__name__


class Suggestions:
    def __init__(self, cache: ExactCache, model: str, revision: str, tau: float, max_seq_length: int):
        self.cache, self.model, self.revision, self.tau = cache, model, revision, tau
        self.max_seq_length = max_seq_length
        self.revision_mismatch = False
        self.status_error: str | None = None
        self.generation: Control | None = None
        self.candidates: list[Candidate] = []
        self.missing = 0
        self.lock = Lock()

    def observe_revision(self, revision):
        self.status_error = None
        if revision != self.revision and not self.revision_mismatch:
            self.revision_mismatch = True
            failure('revision', 'revision_mismatch')

    def model_status_error(self, exc):
        self.status_error = type(exc).__name__
        failure('model_status', self.status_error)

    def refresh(self):
        with self.lock, translate_sqlite_errors():
            control = self.cache.store.control()
            if control != self.generation:
                control, questions, vectors = question_catalog(
                    self.cache.store.path,
                    self.cache.snapshot,
                    self.model,
                    self.revision,
                    self.max_seq_length,
                    include_scheduled=True,
                )
                self.candidates = candidates_with_vectors(questions, vectors)
                self.missing = len(questions) - len(self.candidates)
                self.generation = control
            return control, self.candidates, self.missing

    def recommend(self, question, vector):
        if self.revision_mismatch:
            return Recommendation([], 'revision_mismatch')
        if self.status_error:
            return Recommendation([], self.status_error)
        try:
            control, candidates, _ = self.refresh()
        except (CacheDatabaseError, OSError) as exc:
            return failure('catalog', error_kind(exc))
        if control.mode != 'on':
            return Recommendation([])
        at = now()
        try:
            matches = rank(
                candidates, vector, question, self.tau, eligible=lambda row: all(valid(at, *b) for b in row.bounds)
            )
        except (TypeError, ValueError) as exc:
            return failure('ranking', error_kind(exc))
        if not matches:
            return Recommendation([])
        try:
            with translate_sqlite_errors():
                active = cache_runtime.active_token(self.cache.corpus_db, PROFILE, timeout=READ_TIMEOUT)
        except (CacheDatabaseError, OSError) as exc:
            return failure('corpus', error_kind(exc))
        try:
            with translate_sqlite_errors():
                latest = self.cache.store.control()
        except (CacheDatabaseError, OSError) as exc:
            return failure('control', error_kind(exc))
        if self.revision_mismatch:
            return Recommendation([], 'revision_mismatch')
        if self.status_error:
            return Recommendation([], self.status_error)
        if latest != control or active != (self.cache.snapshot.release_id, control.activation_event):
            return failure('control', 'scope_changed')
        return Recommendation(matches)
