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
from lawbot.semantic_gate import assess, score_pairs, valid_vectors
from lawbot.semantic_pointer import active_revision
from lawbot.semantic_settings import QUERY_PROMPT_RESERVE, ShadowSettings
from lawbot.semantic_store import SemanticStore, ShadowMiss
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')


def unavailable_index(reason: str) -> None:
    log.warning('시맨틱 shadow: %s', reason)


def remaining(deadline: float) -> float:
    budget = deadline - time.perf_counter()
    if budget <= 0:
        raise ModelServerTimeout('시맨틱 shadow 시간이 초과되었습니다.')
    return budget


@dataclass(frozen=True)
class AtomicIndex:
    manifest: tuple
    query_ids: tuple[str, ...]
    vectors: np.ndarray
    directory: Path | None = None

    def active(self, revision: str) -> bool:
        return self.directory is None or active_revision(self.directory) == revision

    def top_queries(self, query: np.ndarray, eligible: set[str], k: int) -> list[str]:
        positions = np.fromiter(
            (i for i, key in enumerate(self.query_ids) if key in eligible), dtype=np.intp, count=len(eligible)
        )
        scores = (self.vectors @ query)[positions]
        if len(scores) > k:
            partition = np.argpartition(scores, -k)[-k:]
            cutoff = scores[partition].min()
            higher = np.flatnonzero(scores > cutoff)
            tied = np.flatnonzero(scores == cutoff)[: k - len(higher)]
            selected = np.concatenate((higher, tied))
        else:
            selected = np.arange(len(scores))
        order = np.lexsort((selected, -scores[selected]))
        return [self.query_ids[i] for i in positions[selected[order]]]

    @classmethod
    def load(cls, settings: ShadowSettings, store: SemanticStore):
        """Read one immutable generation. A missing or incompatible artifact permits only RAG."""
        if settings.index_path is None:
            return unavailable_index('LAWBOT_SEMANTIC_INDEX가 비어 있습니다.')
        calibration = settings.calibration
        path = settings.index_path
        directory = path if path.is_dir() else None
        if directory is not None:
            if active_revision(directory) != calibration.index_revision:
                return unavailable_index('활성 인덱스가 calibration의 revision과 일치하지 않습니다.')
            path = directory / f'{calibration.index_revision}.npy'
        try:
            row = store.manifest()
        except CacheDatabaseError, OSError:
            return unavailable_index('캐시 DB에서 인덱스 manifest를 읽을 수 없습니다. 스키마 0003을 확인하세요.')
        if row is None or row[:3] != (
            store.snapshot.namespace.id,
            store.snapshot.release_id,
            calibration.embedding_fingerprint,
        ):
            return unavailable_index('인덱스 manifest의 revision, 코퍼스 또는 임베딩 계약이 일치하지 않습니다.')
        try:
            raw = path.read_bytes()
        except OSError:
            return unavailable_index('인덱스 파일을 읽을 수 없습니다.')
        if not raw.startswith(b'\x93NUMPY') or sha256(raw).hexdigest() != row[6]:
            return unavailable_index('인덱스 파일 형식 또는 SHA-256이 manifest와 일치하지 않습니다.')
        try:
            query_ids = json.loads(row[3])
        except json.JSONDecodeError:
            return unavailable_index('인덱스 질문 ID의 manifest JSON을 해석할 수 없습니다.')
        if (
            not isinstance(query_ids, list)
            or len(query_ids) != row[4]
            or any(not isinstance(key, str) or not key for key in query_ids)
            or len(set(query_ids)) != len(query_ids)
        ):
            return unavailable_index('인덱스 질문 ID가 중복되거나 manifest 행 수와 일치하지 않습니다.')
        try:
            vectors = np.load(BytesIO(raw), allow_pickle=False)
        except ValueError, EOFError:
            return unavailable_index('인덱스 벡터 파일을 해석할 수 없습니다.')
        if not isinstance(vectors, np.ndarray) or not valid_vectors(vectors, row[4], row[5]):
            return unavailable_index('인덱스 벡터의 형태 또는 값이 유효하지 않습니다.')
        vectors.setflags(write=False)
        return cls(row, tuple(query_ids), vectors, directory)


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
            'semantic_embedding_calls': 0,
            'semantic_rerank_http_calls': 0,
            'semantic_rerank_pair_count': 0,
            'semantic_model_list_calls': 0,
        }
        started = time.perf_counter()
        read_timeout = self.timeout.read if isinstance(self.timeout, Timeout) else self.timeout
        deadline = started + min(calibration.shadow_timeout_s, read_timeout or calibration.shadow_timeout_s)
        try:
            reason = self.evaluate(question, model, fmt, metrics, deadline)
        except ShadowMiss as exc:
            reason = str(exc)
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
        if time.perf_counter() >= deadline:
            reason = 'deadline_exceeded'
        return metrics | {
            'semantic_reason': reason,
            'semantic_would_hit': reason == 'hit',
            'semantic_lookup_ms': round((time.perf_counter() - started) * 1000, 3),
        }

    def evaluate(self, question: str, model: str, fmt: str, metrics: dict, deadline: float) -> str:
        if (model, fmt) != (MODEL, FORMAT) or self.store.snapshot.namespace.visibility_scope != 'public':
            return 'unsupported_scope'
        calibration = self.settings.calibration
        if self.index is None or not self.index.active(calibration.index_revision):
            return 'stale_index'
        if len(question.encode()) > calibration.query_max_seq - QUERY_PROMPT_RESERVE:
            return 'truncated_input'
        token = self.active_snapshot()
        control, eligible = self.store.eligible(self.index.manifest, self.index.query_ids, token[1])
        self.check_model_revisions(metrics, deadline)

        try:
            timeout = remaining(deadline)
            metrics['semantic_embedding_calls'] = 1
            vector = self.client.embed(
                calibration.embedding_model, [question], 'query', calibration.query_max_seq, timeout=timeout
            )
        except ValueError:
            return 'invalid_vector'
        if not valid_vectors(vector, 1, self.index.vectors.shape[1]):
            return 'invalid_vector'
        selected_ids = self.index.top_queries(vector[0], eligible, calibration.top_k)
        ranked = self.store.candidates(self.index.manifest, selected_ids, token[1], control)
        metrics.update(
            semantic_candidate_query_count=len(selected_ids),
            semantic_unique_atom_count=len({item.atom_revision for item in ranked}),
            semantic_unique_parent_count=len({item.parent.chunk_id for item in ranked}),
        )

        def before_rerank(pair_count):
            timeout = remaining(deadline)
            metrics['semantic_rerank_http_calls'] += 1
            metrics['semantic_rerank_pair_count'] += pair_count
            return timeout

        scores = score_pairs(
            question, ranked, self.client, calibration, self.settings.rerank_tokens, before_request=before_rerank
        )
        self.check_model_revisions(metrics, deadline)
        decision = assess(question, ranked, scores, calibration, complete=len(eligible) <= calibration.top_k)
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
        if not self.index.active(calibration.index_revision):
            return 'stale_index'
        return reason if self.active_snapshot() == token else 'corpus_mismatch'

    def check_model_revisions(self, metrics: dict, deadline: float) -> None:
        timeout = remaining(deadline)
        metrics['semantic_model_list_calls'] += 1
        try:
            available = self.client.models(timeout=timeout)
        except ValueError as exc:
            raise ModelServerError('시맨틱 모델 목록을 읽을 수 없습니다.') from exc
        if not self.settings.calibration.matches_revisions(available):
            raise ModelServerError('시맨틱 calibration의 모델 revision이 바뀌었습니다.')

    def active_snapshot(self):
        with translate_sqlite_errors():
            token = active_token(self.corpus_db, PROFILE, timeout=READ_TIMEOUT)
        if token is None or token[0] != self.store.snapshot.release_id:
            raise ShadowMiss('corpus_mismatch')
        return token
