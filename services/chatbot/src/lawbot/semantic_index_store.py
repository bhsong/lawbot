"""Read eligible index inputs and register immutable vector manifests."""

from lawbot.cache_store import now, read_control, valid
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import canonical, digest


def index_questions(conn, snapshot, event):
    control = read_control(conn)
    if (control.namespace_id, control.activation_event) != (snapshot.namespace.id, event):
        raise ValueError('cache approval does not belong to the current corpus activation')
    rows = conn.execute(
        'SELECT q.query_revision_id,q.query_id,q.question,q.question_sha256,b.valid_from,b.valid_until,'
        'a.valid_from,a.valid_until,a.expires_at,x.valid_from,x.valid_until,x.expires_at '
        'FROM atomic_questions q JOIN atoms t USING (atom_revision_id) '
        'JOIN atom_answer_bindings b USING (query_revision_id,atom_revision_id) '
        'JOIN cache_answers a ON a.answer_id=b.answer_id '
        'JOIN cache_queries x ON x.query_id=b.candidate_query_id '
        'WHERE b.namespace_id=? AND t.corpus_snapshot_id=? AND b.activation_event=? '
        "AND t.status='approved' AND q.status='approved' AND a.status='approved' "
        "AND x.status='approved' AND x.exact_enabled=1 AND b.carry_status='keep' "
        'AND b.reviewed_at IS NOT NULL ORDER BY q.query_revision_id',
        (snapshot.namespace.id, snapshot.release_id, event),
    )
    at = now()
    questions = {}
    for (
        revision,
        source,
        question,
        checksum,
        binding_from,
        binding_until,
        answer_from,
        answer_until,
        answer_expires,
        query_from,
        query_until,
        query_expires,
    ) in rows:
        if not (
            valid(at, binding_from, binding_until, None)
            and valid(at, answer_from, answer_until, answer_expires)
            and valid(at, query_from, query_until, query_expires)
        ):
            continue
        if digest(question) != checksum:
            raise ValueError('atomic question hash mismatch')
        questions[revision] = (source, question)
    return control, questions


def read_questions(db, snapshot, event):
    with connect(db) as conn:
        return index_questions(conn, snapshot, event)


def register(db, snapshot, event, expected, revision, fingerprint, query_ids, dimension, checksum, check_snapshot):
    values = (
        revision,
        snapshot.namespace.id,
        snapshot.release_id,
        fingerprint,
        canonical(query_ids),
        len(query_ids),
        dimension,
        checksum,
    )
    with connect(db, write=True) as conn, transaction(conn):
        check_snapshot()
        if index_questions(conn, snapshot, event) != expected:
            raise ValueError('atomic questions or approvals changed during index build')
        conn.execute(
            'INSERT INTO semantic_index_manifests VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING', (*values, now())
        )
        manifest = conn.execute(
            'SELECT * FROM semantic_index_manifests WHERE index_revision=?',
            (revision,),
        ).fetchone()
        if manifest is None or manifest[:-1] != values:
            raise ValueError('immutable index manifest differs from generated data')
