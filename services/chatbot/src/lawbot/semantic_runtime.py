"""Evaluate reviewed bindings without changing the user's RAG response."""

import json
import time
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
from httpx import Timeout

from lawbot.api_client import ModelServerClient, ModelServerError, ModelServerTimeout
from lawbot.cache_store import READ_TIMEOUT, CacheDatabaseError, translate_sqlite_errors
from lawbot.corpus_store import active_token
from lawbot.exact_cache import FORMAT, MODEL, PROFILE
from lawbot.semantic_gate import TruncatedInput, assess, score_pairs, valid_vectors
from lawbot.semantic_settings import QUERY_PROMPT_RESERVE, ShadowSettings
from lawbot.semantic_store import SemanticStore, ShadowMiss


@dataclass(frozen=True)
class AtomicIndex:
    manifest: tuple
    query_ids: tuple[str, ...]
    vectors: np.ndarray

    @classmethod
    def load(cls, settings: ShadowSettings, store: SemanticStore):
        """Read one immutable generation. A missing or incompatible artifact permits only RAG."""
        if settings.index_path is None:
            return None
        calibration = settings.calibration
        try:
            row = store.manifest()
            if row is None or row[:3] != (
                store.snapshot.namespace.id,
                store.snapshot.release_id,
                calibration.embedding_fingerprint,
            ):
                return None
            raw = settings.index_path.read_bytes()
            if not raw.startswith(b'\x93NUMPY') or sha256(raw).hexdigest() != row[6]:
                return None
            query_ids = json.loads(row[3])
            if (
                not isinstance(query_ids, list)
                or len(query_ids) != row[4]
                or any(not isinstance(key, str) or not key for key in query_ids)
                or len(set(query_ids)) != len(query_ids)
            ):
                return None
            vectors = np.load(BytesIO(raw), allow_pickle=False)
            if not isinstance(vectors, np.ndarray) or not valid_vectors(vectors, row[4], row[5]):
                return None
        except CacheDatabaseError, OSError, ValueError, EOFError:
            return None
        vectors.setflags(write=False)
        return cls(row, tuple(query_ids), vectors)


@dataclass(frozen=True)
class SemanticShadow:
    settings: ShadowSettings
    store: SemanticStore
    corpus_db: Path
    client: ModelServerClient
    timeout: Timeout | float | None
    index: AtomicIndex | None

    @classmethod
    def load(cls, settings, cache_db, corpus_db, snapshot, client, *, timeout):
        store = SemanticStore(cache_db, snapshot, settings.calibration.index_revision)
        return cls(settings, store, corpus_db, client, timeout, AtomicIndex.load(settings, store))

    def lookup(self, question: str, model: str, fmt: str) -> dict:
        calibration = self.settings.calibration
        metrics = {
            'semantic_mode': 'shadow',
            'semantic_calibration_id': calibration.calibration_id,
            'semantic_index_revision': calibration.index_revision,
            'semantic_corpus_snapshot_id': self.store.snapshot.release_id,
            'semantic_embedding_fingerprint': calibration.embedding_fingerprint,
            'semantic_reranker_fingerprint': calibration.reranker_fingerprint,
        }
        started = time.perf_counter()
        try:
            reason = self.evaluate(question, model, fmt, metrics)
        except ShadowMiss as exc:
            reason = str(exc)
        except TruncatedInput:
            reason = 'truncated_input'
        except UnicodeError:
            reason = 'unsupported_query'
        except CacheDatabaseError as exc:
            reason = exc.reason
        except ModelServerTimeout:
            reason = 'deadline_exceeded'
        except ModelServerError:
            reason = 'score_contract_error'
        except OSError:
            reason = 'unavailable'
        return metrics | {
            'semantic_reason': reason,
            'semantic_would_hit': reason == 'hit',
            'semantic_lookup_ms': round((time.perf_counter() - started) * 1000, 3),
        }

    def evaluate(self, question: str, model: str, fmt: str, metrics: dict) -> str:
        if (model, fmt) != (MODEL, FORMAT) or self.store.snapshot.namespace.visibility_scope != 'public':
            return 'unsupported_scope'
        if self.index is None:
            return 'stale_index'
        calibration = self.settings.calibration
        if len(question.encode()) > calibration.query_max_seq - QUERY_PROMPT_RESERVE:
            return 'truncated_input'
        token = self.active_snapshot()
        control, candidates = self.store.candidates(self.index.manifest, self.index.query_ids, token[1])

        try:
            metrics['semantic_embedding_calls'] = 1
            vector = self.client.embed(
                calibration.embedding_model, [question], 'query', calibration.query_max_seq, timeout=self.timeout
            )
        except ValueError:
            return 'invalid_vector'
        if not valid_vectors(vector, 1, self.index.vectors.shape[1]):
            return 'invalid_vector'
        similarities = self.index.vectors @ vector[0]
        eligible_ids = [
            self.index.query_ids[i]
            for i in np.argsort(-similarities, kind='stable')
            if self.index.query_ids[i] in candidates
        ]
        ranked = [item for key in eligible_ids[: calibration.top_k] for item in candidates[key]]
        scores = score_pairs(question, ranked, self.client, calibration, timeout=self.timeout)
        metrics.update(
            semantic_rerank_http_calls=2,
            semantic_rerank_pair_count=len(scores.parents) + len(scores.questions),
            semantic_candidate_query_count=len(scores.questions),
            semantic_unique_atom_count=len({item.atom_revision for item in ranked}),
            semantic_unique_parent_count=len(scores.parents),
        )
        decision = assess(question, ranked, scores, calibration, complete=len(eligible_ids) <= calibration.top_k)
        selected = decision.candidate
        if selected is not None:
            metrics.update(
                semantic_atom_revision=selected.atom_revision,
                semantic_query_revision=selected.question_revision,
                semantic_answer_revision=selected.answer_id,
                semantic_policy_version=selected.policy_version,
                linked_parent_score=decision.linked_parent_score,
                question_match_score=decision.question_match_score,
                semantic_margin=decision.margin,
                semantic_condition_status=decision.condition.status if decision.condition else None,
            )
        if not decision.would_hit or selected is None:
            return decision.reason
        # Fence the hypothetical hit after inference, when the binding or corpus may have changed.
        if self.active_snapshot() != token:
            return 'corpus_mismatch'
        reason = self.store.recheck(self.index.manifest, token[1], control, selected)
        return reason if self.active_snapshot() == token else 'corpus_mismatch'

    def active_snapshot(self):
        with translate_sqlite_errors():
            token = active_token(self.corpus_db, PROFILE, timeout=READ_TIMEOUT)
        if token is None or token[0] != self.store.snapshot.release_id:
            raise ShadowMiss('corpus_mismatch')
        return token
