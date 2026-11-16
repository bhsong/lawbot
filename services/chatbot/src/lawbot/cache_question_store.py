"""Read approved exact questions and their revision-bound query vectors."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lawbot.cache_store import READ_TIMEOUT, Control, now, read_control, valid, verify_answer
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import MAX_QUESTION, Snapshot, canonical, digest, normalize


@dataclass(frozen=True)
class ApprovedQuestion:
    id: str
    answer_id: str
    source_id: str
    original: str
    normalized: str
    checksum: str
    query_bounds: tuple[str | None, str | None, str | None]
    answer_bounds: tuple[str | None, str | None, str | None]


def approved_questions(conn, snapshot: Snapshot, *, include_scheduled=False) -> list[ApprovedQuestion]:
    """Mirror exact eligibility while allowing offline work with the cache off."""
    namespace = snapshot.namespace.id
    at = now()
    rows = conn.execute(
        'SELECT q.query_id,q.answer_id,q.source_id,q.original,q.normalized,q.normalized_sha256,q.raw_sha256,'
        'q.valid_from,q.valid_until,q.expires_at,a.valid_from,a.valid_until,a.expires_at,'
        'a.answer_key,a.raw_sha256,a.answer_text,a.text_sha256,a.citation_count '
        'FROM cache_queries q JOIN cache_answers a USING(namespace_id,answer_id) '
        "WHERE q.namespace_id=? AND q.status='approved' AND q.exact_enabled=1 AND a.status='approved' "
        'ORDER BY q.query_id',
        (namespace,),
    )
    approved = []
    answers = {}
    for (
        uid,
        aid,
        source_id,
        original,
        normalized,
        checksum,
        raw,
        start,
        end,
        expiry,
        a_start,
        a_end,
        a_expiry,
        *answer,
    ) in rows:
        if (
            not normalized
            or len(original) > MAX_QUESTION
            or (normalize(original), digest(normalized), digest(original), digest(canonical([namespace, checksum])))
            != (normalized, checksum, raw, uid)
            or is_calculation_question(original)
            or (not include_scheduled and not valid(at, start, end, expiry))
            or (not include_scheduled and not valid(at, a_start, a_end, a_expiry))
        ):
            continue
        if aid not in answers:
            citations, _ = verify_answer(conn, namespace, (aid, *answer))
            answers[aid] = citations is not None and snapshot.resolve(citations) is not None
        if answers[aid]:
            approved.append(
                ApprovedQuestion(
                    uid,
                    aid,
                    source_id,
                    original,
                    normalized,
                    checksum,
                    (start, end, expiry),
                    (a_start, a_end, a_expiry),
                )
            )
    return approved


def unit_vector(vector):
    vector = np.asarray(vector, dtype=np.float32)
    length = np.linalg.norm(vector)
    if vector.ndim != 1 or not np.isfinite(length) or length <= 0:
        return None
    return vector / length


def question_catalog(
    path: Path,
    snapshot: Snapshot,
    model: str,
    revision: str,
    max_seq_length: int,
    *,
    timeout=READ_TIMEOUT,
    include_scheduled=False,
):
    with connect(path, timeout=timeout) as conn:
        conn.execute('BEGIN')
        control = read_control(conn)
        if control.namespace_id != snapshot.namespace.id:
            return control, [], {}
        questions = approved_questions(conn, snapshot, include_scheduled=include_scheduled)
        rows = conn.execute(
            'SELECT v.query_id,v.normalized_sha256,v.dimension,v.vector FROM cache_question_vectors v '
            'JOIN cache_queries q USING(query_id) WHERE q.namespace_id=? AND v.embedding_model=? AND v.model_revision=? '
            'AND v.max_seq_length=?',
            (snapshot.namespace.id, model, revision, max_seq_length),
        ).fetchall()
    hashes = {q.id: q.checksum for q in questions}
    vectors = {}
    for uid, checksum, dimension, raw in rows:
        if hashes.get(uid) == checksum and dimension > 0 and len(raw) == dimension * 4:
            if (vector := unit_vector(np.frombuffer(raw, dtype='<f4'))) is not None:
                vectors[uid] = vector
    return control, questions, vectors


def save_vectors(path: Path, expected: Control, model: str, revision: str, max_seq_length: int, rows, check):
    with connect(path, write=True) as conn, transaction(conn):
        if read_control(conn) != expected:
            raise ValueError('벡터 생성 중 캐시 승인 또는 모드가 변경되었습니다.')
        check()
        conn.executemany(
            'INSERT INTO cache_question_vectors VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(query_id,embedding_model) DO UPDATE SET model_revision=excluded.model_revision,'
            'normalized_sha256=excluded.normalized_sha256, max_seq_length=excluded.max_seq_length, '
            'dimension=excluded.dimension, vector=excluded.vector',
            (
                (q.id, model, revision, q.checksum, max_seq_length, vector.size, vector.astype('<f4').tobytes())
                for q, vector in rows
            ),
        )
        if rows:
            conn.execute('UPDATE cache_control SET generation=generation+1 WHERE singleton=1')
