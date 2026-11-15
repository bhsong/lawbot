"""Build an immutable semantic index generation and publish it atomically."""

import fcntl
import json
import os
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import numpy as np

from lawbot.exact_cache import canonical, digest
from lawbot.semantic_gate import valid_vectors
from lawbot.semantic_index_store import read_questions, register
from lawbot.semantic_settings import QUERY_PROMPT_RESERVE, embedding_fingerprint
from lawbot.semantic_val import source_query_id
from lawbot_common.api import MAX_SEQ_LIMIT

ACTIVE_FILE = 'active.json'


def active_revision(directory: Path) -> str | None:
    try:
        pointer = json.loads((directory / ACTIVE_FILE).read_bytes())
    except OSError, ValueError:
        return None
    revision = pointer.get('index_revision') if isinstance(pointer, dict) else None
    return revision if isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{64}', revision) else None


@dataclass(frozen=True)
class IndexContract:
    model: str
    revision: str
    max_seq: int
    dimension: int


class EmbeddingClient(Protocol):
    def revision(self) -> str: ...
    def fits(self, question: str) -> bool: ...
    def encode(self, questions: Sequence[str]) -> np.ndarray: ...


def select_questions(all_questions, held_out: Collection[str]):
    keys = {revision: source_query_id('pass', source) for revision, (source, _) in all_questions.items()}
    if set(held_out) - set(keys.values()):
        raise ValueError('val holdout question is missing from the approved seed')
    return {revision: q for revision, (_, q) in all_questions.items() if keys[revision] not in held_out}


def build_index(
    db,
    directory: Path,
    snapshot,
    event,
    contract: IndexContract,
    encoder: EmbeddingClient,
    *,
    batch_size: int = 16,
    held_out: Collection[str] = (),
    check_snapshot=lambda: None,
    approved_requests: int | None = None,
):
    """Publish only after every vector and the snapshot contract have passed validation."""
    if not (
        1 <= batch_size <= 16
        and QUERY_PROMPT_RESERVE < contract.max_seq <= MAX_SEQ_LIMIT
        and contract.model
        and contract.revision
        and contract.dimension > 0
    ):
        raise ValueError('invalid embedding contract or batch size')
    expected = read_questions(db, snapshot, event)
    fingerprint = embedding_fingerprint(contract.model, contract.revision, contract.max_seq)
    questions = select_questions(expected[1], held_out)
    if not questions or not all(encoder.fits(q) for q in questions.values()):
        raise ValueError('empty index or embedding input would be truncated')
    if approved_requests is not None and (len(questions) + batch_size - 1) // batch_size != approved_requests:
        raise ValueError('approved embedding request count changed')
    check_snapshot()
    if encoder.revision() != contract.revision:
        raise ValueError('embedding revision differs from the requested revision')
    texts = list(questions.values())
    batches = []
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset : offset + batch_size]
        vectors = encoder.encode(batch)
        if not isinstance(vectors, np.ndarray) or not valid_vectors(vectors, len(batch), contract.dimension):
            raise ValueError('invalid embedding vectors')
        batches.append(vectors)
    if encoder.revision() != contract.revision:
        raise ValueError('embedding revision changed during index build')

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory) as tmp:
        staged = Path(tmp) / 'vectors.npy'
        staged.touch(mode=0o600)
        with staged.open('wb') as stream:
            np.save(stream, np.concatenate(batches), allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        checksum = file_hash(staged)
        query_ids = list(questions)
        revision = digest(
            canonical([snapshot.namespace.id, snapshot.release_id, event, fingerprint, query_ids, checksum])
        )
        pointer = Path(tmp) / ACTIVE_FILE
        pointer.touch(mode=0o600)
        with pointer.open('w', encoding='utf-8') as stream:
            stream.write(canonical({'index_revision': revision}))
            stream.flush()
            os.fsync(stream.fileno())
        final = directory / f'{revision}.npy'
        with (directory / '.publish.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                os.link(staged, final)
            except FileExistsError:
                if final.is_symlink() or file_hash(final) != checksum:
                    raise ValueError('existing vector generation is corrupted') from None
            register(
                db,
                snapshot,
                event,
                expected,
                revision,
                fingerprint,
                query_ids,
                contract.dimension,
                checksum,
                check_snapshot,
            )
            check_snapshot()
            os.replace(pointer, directory / ACTIVE_FILE)
    return {'index_revision': revision, 'rows': len(questions), 'embedding_requests': len(batches)}


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return sha256(stream.read()).hexdigest()
