"""Rank reviewed questions with the vector already used by legal retrieval."""

from dataclasses import dataclass
from threading import Lock

import numpy as np

from lawbot import cache_runtime
from lawbot.cache_question_store import question_catalog, unit_vector
from lawbot.cache_runtime import ExactCache
from lawbot.cache_store import READ_TIMEOUT, Control, translate_sqlite_errors
from lawbot.exact_cache import FORMAT, MODEL, PROFILE, normalize


@dataclass(frozen=True)
class Candidate:
    id: str
    answer_id: str
    question: str
    vector: np.ndarray
    source_id: str = ''


def candidates_with_vectors(questions, vectors):
    return [Candidate(q.id, q.answer_id, q.original, vectors[q.id], q.source_id) for q in questions if q.id in vectors]


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


class Suggestions:
    def __init__(self, cache: ExactCache, model: str, revision: str, tau: float, current_revision):
        self.cache, self.model, self.revision, self.tau = cache, model, revision, tau
        self.current_revision = current_revision
        self.generation: Control | None = None
        self.candidates: list[Candidate] = []
        self.missing = 0
        self.lock = Lock()

    def refresh(self):
        with self.lock, translate_sqlite_errors():
            control = self.cache.store.control()
            if control != self.generation:
                control, questions, vectors = question_catalog(
                    self.cache.store.path, self.cache.snapshot, self.model, self.revision
                )
                self.candidates = candidates_with_vectors(questions, vectors)
                self.missing = len(questions) - len(self.candidates)
                self.generation = control
            return control, self.candidates, self.missing

    def recommend(self, question, vector):
        try:
            control, candidates, _ = self.refresh()
            if control.mode != 'on':
                return []

            def eligible(row):
                decision = self.cache.lookup(row.question, MODEL, FORMAT)
                return (
                    decision.mode == 'on' and decision.match is not None and decision.match.answer_id == row.answer_id
                )

            matches = rank(candidates, vector, question, self.tau, eligible=eligible)
            if matches and self.current_revision() == self.revision:
                with translate_sqlite_errors():
                    expected = (self.cache.snapshot.release_id, control.activation_event)
                    if (
                        self.cache.store.control() == control
                        and cache_runtime.active_token(self.cache.corpus_db, PROFILE, timeout=READ_TIMEOUT) == expected
                    ):
                        return matches
        except OSError, RuntimeError, ValueError:
            pass  # Suggestions are optional; the completed answer can still be sent.
        return []
