"""Persist full feedback records and their indexed fields in SQLite."""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lawbot import audit_store, dataset_store
from lawbot.db.connection import connect, transaction
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')
DatabaseError = sqlite3.Error
FIELDS = (
    'feedback_id',
    'turn_id',
    'ts',
    'label',
    'reason',
    'question',
    'answer_raw',
    'answer_shown',
    'model',
    'profile',
    'dataset_version',
    'corpus_release_id',
    'articles_sha256',
    'commit_sha',
    'request_id',
    'instance',
)
_PLACEHOLDERS = ', '.join(['?'] * (len(FIELDS) + 2))
_INSERT = f'INSERT INTO feedbacks ({", ".join(FIELDS)}, record, origin) VALUES ({_PLACEHOLDERS})'


def record_values(row: dict) -> tuple[str | None, ...]:
    values = row | {'reason': row.get('reason') or None, 'commit_sha': row.get('commit_sha', row.get('commit'))}
    return tuple(values.get(field) for field in FIELDS)


def insert(conn: sqlite3.Connection, values: tuple[str | None, ...], record: str, origin: str) -> None:
    """Insert a prepared record inside the caller's write transaction."""
    conn.execute(_INSERT, (*values, record, origin))


def append(conn: sqlite3.Connection, row: dict) -> None:
    """Append one web feedback row and commit it atomically."""
    values = record_values(row)
    record = json.dumps(row, ensure_ascii=False, default=str)
    with transaction(conn):
        insert(conn, values, record, 'web')


def write(path: Path, row: dict) -> None:
    """Write to the validated DB, warning without including record contents on failure."""
    try:
        with connect(path, write=True) as conn:
            append(conn, row)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        log.warning('경고: 피드백을 쓰지 못했습니다 (%s: %s)', path, type(exc).__name__)


def read_records(path: Path) -> list[dict]:
    """Read full records in insertion order to preserve equal-timestamp tie breaks."""
    with connect(path) as conn:
        return [json.loads(record) for (record,) in conn.execute('SELECT record FROM feedbacks ORDER BY rowid')]


def _rows(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor]


def status_of(*, superseded=False, promoted=False, overlap=None, review=None):
    """Derive a feedback item's status in the documented precedence order."""
    if superseded:
        return 'superseded'
    if promoted:
        return 'promoted'
    if overlap is not None and overlap['split'] != 'train':
        return 'quarantined'
    if review is None:
        return 'pending'
    return 'reviewed' if review['decision'] == 'approved' else review['decision']


def _decode_review(row):
    return row | {'gold_articles': json.loads(row['gold_articles'])}


def review_of(conn, feedback_id):
    rows = _rows(
        conn, 'SELECT * FROM feedback_reviews WHERE feedback_id=? ORDER BY review_id DESC LIMIT 1', (feedback_id,)
    )
    return _decode_review(rows[0]) if rows else None


def _latest_reviews(conn):
    return {
        row['feedback_id']: _decode_review(row)
        for row in _rows(
            conn,
            'SELECT * FROM feedback_reviews WHERE review_id IN '
            '(SELECT MAX(review_id) FROM feedback_reviews GROUP BY feedback_id)',
        )
    }


def overlap_of(index, feedback, review):
    return index.match(feedback['question'], review.get('corrected_question') if review else None)


def list_feedback(path, index, *, current_sha, current_version, status, label, reason, q, limit, cursor):
    from lawbot.qa_rules import latest_rows

    with connect(path) as conn:
        conn.execute('BEGIN')
        rows = _rows(
            conn,
            'SELECT feedback_id,turn_id,ts,label,reason,question,model,profile,dataset_version,corpus_release_id,'
            'articles_sha256 '
            'FROM feedbacks ORDER BY rowid',
        )
        reviews = _latest_reviews(conn)
        promoted = {row[0] for row in conn.execute("SELECT feedback_id FROM feedback_promotions WHERE state='active'")}
    labels = defaultdict(set)
    for row in rows:
        if row['label'] != 'cleared':
            labels[row['turn_id']].add(row['label'])
    latest, _ = latest_rows(rows)
    latest.sort(key=lambda row: (row['ts'], row['feedback_id']), reverse=True)
    items = []
    for row in latest:
        if cursor is not None and (row['ts'], row['feedback_id']) >= cursor:
            continue
        if (
            (label and row['label'] != label)
            or (reason and row['reason'] != reason)
            or q not in row['question'].casefold()
        ):
            continue
        review = reviews.get(row['feedback_id'])
        overlap = overlap_of(index, row, review)
        current = status_of(promoted=row['feedback_id'] in promoted, overlap=overlap, review=review)
        if status != 'all' and current != status:
            continue
        warnings = (
            [] if overlap is None else ['overlap_train' if overlap['split'] == 'train' else 'leak_' + overlap['split']]
        )
        if len(labels[row['turn_id']]) > 1:
            warnings.append('label_changed')
        recorded, active = (
            (row['articles_sha256'], current_sha)
            if row['articles_sha256']
            else (row['dataset_version'], current_version)
        )
        if recorded and recorded != active:
            warnings.append('corpus_changed')
        items.append(
            {key: value for key, value in row.items() if key not in ('question', 'articles_sha256')}
            | dict(
                status=current,
                question_preview=row['question'][:120],
                warnings=warnings,
                latest_review={key: review[key] for key in ('verdict', 'decision', 'reviewer', 'created_at')}
                if review
                else None,
            )
        )
        if len(items) > limit:
            break
    next_cursor = (items[limit - 1]['ts'], items[limit - 1]['feedback_id']) if len(items) > limit else None
    return items[:limit], next_cursor


def feedback_detail(path, feedback_id):
    with connect(path) as conn:
        conn.execute('BEGIN')
        rows = _rows(conn, 'SELECT * FROM feedbacks WHERE feedback_id=?', (feedback_id,))
        if not rows:
            return None
        feedback = rows[0]
        turn = (feedback['turn_id'],)
        reviews = [
            _decode_review(row)
            for row in _rows(
                conn,
                'SELECT r.* FROM feedback_reviews r JOIN feedbacks f USING (feedback_id) '
                'WHERE f.turn_id=? ORDER BY r.review_id DESC',
                turn,
            )
        ]
        promotions = _rows(
            conn,
            'SELECT p.* FROM feedback_promotions p JOIN feedbacks f USING (feedback_id) '
            'WHERE f.turn_id=? ORDER BY p.promotion_id DESC',
            turn,
        )
        record = json.loads(feedback.pop('record'))
        assigned = dataset_store.feedback_assignments(conn, feedback_id)
    return record | feedback, reviews, promotions, assigned


class StoreError(Exception):
    def __init__(self, code, detail, status=422):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


@dataclass(frozen=True)
class Mutation:
    actor: str
    endpoint: str
    key: str
    digest: str

    def receipt(self, conn):
        row = conn.execute(
            'SELECT request_sha256,status_code,response FROM idempotency_keys WHERE actor=? AND endpoint=? AND key=?',
            (self.actor, self.endpoint, self.key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != self.digest:
            raise StoreError('idempotency_conflict', '같은 멱등 키에 다른 요청이 저장되어 있습니다.', 409)
        return row[1], json.loads(row[2])

    def save(self, conn, status, result, at):
        conn.execute(
            'INSERT INTO idempotency_keys VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(actor,endpoint,key) DO UPDATE SET status_code=excluded.status_code,response=excluded.response',
            (self.actor, self.endpoint, self.key, self.digest, status, json.dumps(result, ensure_ascii=False), at),
        )

    def reject(self, conn, error, at):
        result = dict(code=error.code, detail=error.detail)
        self.save(conn, error.status, result, at)
        return error.status, result


def _now():
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class FeedbackState:
    row: dict
    superseded: bool
    promoted: bool
    review: dict | None


def _state(conn, feedback_id):
    rows = _rows(conn, 'SELECT * FROM feedbacks WHERE feedback_id=?', (feedback_id,))
    if not rows:
        raise StoreError('not_found', '피드백을 찾을 수 없습니다.', 404)
    row = rows[0]
    latest = conn.execute(
        'SELECT feedback_id FROM feedbacks WHERE turn_id=? ORDER BY ts DESC,rowid DESC LIMIT 1', (row['turn_id'],)
    ).fetchone()
    active = conn.execute(
        "SELECT 1 FROM feedback_promotions WHERE feedback_id=? AND state='active'", (feedback_id,)
    ).fetchone()
    return FeedbackState(row, latest != (feedback_id,), active is not None, review_of(conn, feedback_id))


def _check_gold(gold, articles):
    if len({(item['law'], item['article_no']) for item in gold}) != len(gold):
        raise StoreError('invalid_body', '근거 조문은 중복될 수 없습니다.')
    if any((item['law'], item['article_no']) not in articles for item in gold):
        raise StoreError('unknown_article', '현재 조문에서 근거 조문을 찾을 수 없습니다.')


def _insert_review(conn, at, actor, feedback_id, review):
    cursor = conn.execute(
        'INSERT INTO feedback_reviews (feedback_id,reviewer,verdict,decision,scope,gold_articles,'
        'corrected_question,corrected_answer,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (
            feedback_id,
            actor,
            review['verdict'],
            review['decision'],
            review.get('scope'),
            json.dumps(review['gold_articles'], ensure_ascii=False),
            review.get('corrected_question'),
            review.get('corrected_answer'),
            review.get('note'),
            at,
        ),
    )
    audit_store.record_action(conn, at, actor, 'review', 'feedback', feedback_id)
    return cursor.lastrowid


def _review_overlap(conn, index, articles, feedback_id, review):
    state = _state(conn, feedback_id)
    if review['decision'] == 'approved':
        if review['verdict'] == 'uncertain':
            raise StoreError('invalid_verdict', '판정이 불확실하면 승인할 수 없습니다.')
        if review['verdict'] == 'incorrect' and not review['corrected_answer']:
            raise StoreError('missing_corrected_answer', '오답을 승인하려면 교정 답변이 필요합니다.')
    _check_gold(review['gold_articles'], articles)
    return overlap_of(index, state.row, review)


def save_review(path, index, articles, mutation, feedback_id, review):
    rejected = None
    with connect(path) as conn:
        if (saved := mutation.receipt(conn)) is not None:
            return saved
        try:
            overlap = _review_overlap(conn, index, articles, feedback_id, review)
        except StoreError as exc:
            rejected, overlap = exc, None
    at = _now()
    with connect(path, write=True) as conn, transaction(conn):
        if (saved := mutation.receipt(conn)) is not None:
            return saved
        if rejected is not None:
            return mutation.reject(conn, rejected, at)
        state = _state(conn, feedback_id)
        review_id = _insert_review(conn, at, mutation.actor, feedback_id, review)
        status = status_of(superseded=state.superseded, promoted=state.promoted, overlap=overlap, review=review)
        result = dict(review_id=review_id, status=status)
        mutation.save(conn, 201, result, at)
    return 201, result


@dataclass(frozen=True)
class Proposal:
    feedback_id: str
    kind: str
    review: dict
    question: str
    original_question: str
    answer: str
    rejected: str | None


def _proposal(conn, articles, feedback_id, down_review):
    state = _state(conn, feedback_id)
    if state.superseded:
        raise StoreError('superseded', '더 최근 피드백이 있습니다.', 409)
    if state.promoted:
        raise StoreError('already_promoted', '이미 활성 승격이 있습니다.', 409)
    up = down_review is None
    if state.row['label'] != ('like' if up else 'flag'):
        raise StoreError('not_like' if up else 'not_flag', '승격할 수 없는 피드백 종류입니다.')
    review = state.review if up else down_review
    if review is None:
        raise StoreError('not_reviewed', '먼저 검수해야 합니다.')
    if review['decision'] != 'approved':
        raise StoreError('not_approved', '승인된 검수가 필요합니다.')
    if up and review['verdict'] != 'correct':
        raise StoreError('verdict_not_correct', '맞음으로 판정된 검수가 필요합니다.')
    gold = review['gold_articles']
    if not gold:
        raise StoreError('missing_gold', '근거 조문이 필요합니다.')
    _check_gold(gold, articles)
    original = state.row['question']
    answer = review.get('corrected_answer') or state.row['answer_raw']
    if not answer:
        raise StoreError('missing_answer', '승격할 답변이 필요합니다.')
    rejected = state.row['answer_raw'] if not up and review['verdict'] == 'incorrect' else None
    if not up and review['verdict'] == 'incorrect' and not rejected:
        raise StoreError('missing_answer', '선호 쌍에 사용할 원 답변이 필요합니다.')
    return Proposal(
        feedback_id,
        'up' if up else 'down',
        review,
        review.get('corrected_question') or original,
        original,
        answer,
        rejected,
    )


def _prepare(conn, index, articles, feedback_id, down_review):
    conn.execute('BEGIN')
    try:
        proposal = _proposal(conn, articles, feedback_id, down_review)
    except StoreError:
        proposal = None
    finally:
        conn.execute('ROLLBACK')
    overlap = index.match(proposal.original_question, proposal.question) if proposal else None
    return proposal, overlap


class _RetryPromotion(Exception):
    """The reviewed state changed while validating outside the write transaction."""


def _apply_promotion(conn, articles, mutation, feedback_id, down_review, prepared, at):
    proposal = _proposal(conn, articles, feedback_id, down_review)
    checked, overlap = prepared
    if proposal != checked:
        raise _RetryPromotion
    if overlap and overlap['split'] != 'train':
        raise StoreError('leak_' + overlap['split'], '보호 대상 문항과 겹쳐 승격할 수 없습니다.', 409)
    review_id = (
        _insert_review(conn, at, mutation.actor, feedback_id, proposal.review)
        if proposal.kind == 'down'
        else proposal.review['review_id']
    )
    (count,) = conn.execute('SELECT COUNT(*) FROM feedback_promotions WHERE feedback_id=?', (feedback_id,)).fetchone()
    key = f'FB-{feedback_id}-r{count + 1}'
    uid, preference = dataset_store.insert_feedback_qa(
        conn,
        key=key,
        feedback_id=feedback_id,
        question=proposal.question,
        answer=proposal.answer,
        gold=proposal.review['gold_articles'],
        rejected=proposal.rejected,
        at=at,
    )
    promotion = conn.execute(
        'INSERT INTO feedback_promotions (feedback_id,review_id,kind,qa_uid,preference_id,approver,created_at,state) '
        "VALUES (?,?,?,?,?,?,?,'active')",
        (feedback_id, review_id, proposal.kind, uid, preference, mutation.actor, at),
    ).lastrowid
    audit_store.record_action(
        conn,
        at,
        mutation.actor,
        'promote_' + proposal.kind,
        'promotion',
        promotion,
        summary={'overlap': overlap} if overlap else None,
    )
    return dict(review_id=review_id, promotion_id=promotion, qa_uid=uid, preference_id=preference)


def promote_up(path, index, articles, mutation, ids):
    """Commit each item together with its durable position in the batch response."""
    retries = 0
    while True:
        with connect(path) as conn:
            saved = mutation.receipt(conn)
            if saved is not None and saved[0] == 200:
                return saved
            results = [] if saved is None else saved[1]['results']
            position = len(results)
            fid = ids[position]
            prepared = _prepare(conn, index, articles, fid, None)
        at = _now()
        try:
            with connect(path, write=True) as conn, transaction(conn):
                if mutation.receipt(conn) != saved:
                    continue
                try:
                    result = _apply_promotion(conn, articles, mutation, fid, None, prepared, at)
                except StoreError as exc:
                    item = dict(feedback_id=fid, ok=False, code=exc.code, detail=exc.detail)
                else:
                    item = dict(feedback_id=fid, ok=True, promotion_id=result['promotion_id'], qa_uid=result['qa_uid'])
                payload = dict(results=[*results, item])
                status = 200 if position + 1 == len(ids) else 0
                mutation.save(conn, status, payload, at)
            if status == 200:
                return status, payload
            retries = 0
        except _RetryPromotion:
            retries += 1
            if retries == 5:
                raise StoreError('feedback_changed', '피드백 상태가 바뀌었습니다. 다시 시도하세요.', 409) from None


def promote_down(path, index, articles, mutation, feedback_id, review):
    for _ in range(5):
        with connect(path) as conn:
            if (saved := mutation.receipt(conn)) is not None:
                return saved
            prepared = _prepare(conn, index, articles, feedback_id, review)
        at = _now()
        try:
            with connect(path, write=True) as conn, transaction(conn):
                if (saved := mutation.receipt(conn)) is not None:
                    return saved
                try:
                    result = _apply_promotion(conn, articles, mutation, feedback_id, review, prepared, at)
                except StoreError as exc:
                    error = StoreError(exc.code, exc.detail) if exc.status == 404 else exc
                    return mutation.reject(conn, error, at)
                mutation.save(conn, 201, result, at)
            return 201, result
        except _RetryPromotion:
            continue
    raise StoreError('feedback_changed', '피드백 상태가 바뀌었습니다. 다시 시도하세요.', 409)


def revoke(path, mutation, promotion_id, note):
    at = _now()
    with connect(path, write=True) as conn, transaction(conn):
        if (saved := mutation.receipt(conn)) is not None:
            return saved
        row = conn.execute(
            'SELECT qa_uid,state FROM feedback_promotions WHERE promotion_id=?', (promotion_id,)
        ).fetchone()
        if row is None:
            return mutation.reject(conn, StoreError('not_found', '승격을 찾을 수 없습니다.', 404), at)
        if row[1] != 'active':
            return mutation.reject(conn, StoreError('already_revoked', '이미 취소된 승격입니다.', 409), at)
        conn.execute(
            "UPDATE feedback_promotions SET state='revoked',revoked_at=?,revoked_by=?,revoke_note=? WHERE promotion_id=?",
            (at, mutation.actor, note, promotion_id),
        )
        dataset_store.retire_feedback_qa(conn, row[0])
        audit_store.record_action(conn, at, mutation.actor, 'revoke', 'promotion', promotion_id)
        result = dict(promotion_id=promotion_id, state='revoked')
        mutation.save(conn, 200, result, at)
    return 200, result
