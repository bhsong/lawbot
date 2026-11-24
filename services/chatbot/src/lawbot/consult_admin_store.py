"""List, inspect and delete completed consultations without duplicating their payloads."""

import base64
import hmac
import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from lawbot import audit_store
from lawbot.consult_trace import account_hash, stamp
from lawbot.db.connection import connect, transaction
from lawbot.feedback_store import Mutation, StoreError


def _where(filters):
    terms = {'user_hash': 'user_hash = ?', 'start': 'created_at >= ?', 'end': 'created_at < ?', 'status': 'status = ?'}
    return ' AND '.join(terms[key] for key in filters) or '1', tuple(filters.values())


def list_consults(db, filters, limit, cursor):
    where, args = _where(filters)
    if cursor is not None:
        where += ' AND (created_at, id) < (?, ?)'
        args += cursor
    with connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT id, created_at, ended_at, user_hash, role, status, answer_verdict, '
            f'substr(question, 1, 120) AS question_preview FROM consults WHERE {where} '
            'ORDER BY created_at DESC, id DESC LIMIT ?',
            (*args, limit + 1),
        ).fetchall()
    items = [dict(row) for row in rows[:limit]]
    tail = items[-1] if len(rows) > limit else None
    return items, (tail['created_at'], tail['id']) if tail else None


def _body(value: bytes | None):
    if value is None:
        return None
    try:
        return {'encoding': 'utf-8', 'data': value.decode()}
    except UnicodeDecodeError:
        return {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')}


def detail(db, consult_id):
    with connect(db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        row = conn.execute(
            'SELECT id, request_id, created_at, ended_at, user_hash, role, session_id, answer_verdict, status, '
            'delivery_status, error_type, retention_policy, app_commit, corpus_release_id, writer_system_sha256, '
            'feedback_turn_id, redaction_count, redaction_reasons FROM consults WHERE id = ?',
            (consult_id,),
        ).fetchone()
        if row is None:
            return None
        steps = [
            dict(row)
            for row in conn.execute(
                'SELECT seq, stage, started_at, ended_at, redaction_count, redaction_reasons '
                'FROM consult_steps WHERE consult_id = ? ORDER BY seq',
                (consult_id,),
            )
        ]
        calls = [
            dict(row)
            for row in conn.execute(
                'SELECT seq, purpose, method, path, model_id, model_revision, send_status, status_code, '
                'response_streaming, response_body IS NOT NULL AS has_response, response_decoded, '
                'started_at, first_byte_at, ended_at, usage, error_type, redaction_count, redaction_reasons '
                'FROM model_calls WHERE consult_id = ? ORDER BY seq',
                (consult_id,),
            )
        ]
    consult = dict(row)
    for record in (consult, *steps, *calls):
        record['redaction_reasons'] = json.loads(record['redaction_reasons'])
    for call in calls:
        if call['usage'] is not None:
            call['usage'] = json.loads(call['usage'])
    return {'consult': consult, 'steps': steps, 'calls': calls}


def consult_text(db, consult_id):
    with connect(db) as conn:
        row = conn.execute('SELECT question, answer_shown FROM consults WHERE id = ?', (consult_id,)).fetchone()
    return dict(zip(('question', 'answer_shown'), row, strict=True)) if row is not None else None


def step_result(db, consult_id, seq):
    with connect(db) as conn:
        row = conn.execute(
            'SELECT result FROM consult_steps WHERE consult_id = ? AND seq = ?', (consult_id, seq)
        ).fetchone()
    return {'result': json.loads(row[0])} if row is not None else None


BODY_PAGE_BYTES = 32 * 1024
CHUNK_PAGE_COUNT = 64
BodyPart = Literal['request', 'response', 'chunks']


def _utf8_boundary(value: bytes, more: bool) -> bytes:
    if more:
        try:
            value.decode()
        except UnicodeDecodeError as exc:
            if exc.reason == 'unexpected end of data' and exc.start:
                return value[: exc.start]
    return value


def call_body(db, consult_id, seq, part: BodyPart, cursor: tuple[int, int]):
    """Read only a bounded byte page; oversized receive chunks keep their sequence and timestamp."""
    number, offset = cursor
    with connect(db) as conn:
        if part != 'chunks':
            column = 'request_body' if part == 'request' else 'response_body'
            row = conn.execute(
                f'SELECT length({column}), substr({column}, ?, ?) FROM model_calls WHERE consult_id = ? AND seq = ?',
                (offset + 1, BODY_PAGE_BYTES, consult_id, seq),
            ).fetchone()
            if row is None:
                return None
            size, body = row
            if body is None:
                return {'body': None, 'next_cursor': None}
            body = _utf8_boundary(body, offset + len(body) < size)
            after = offset + len(body)
            return {'body': _body(body), 'next_cursor': f'1:{after}' if after < size else None}

        conn.execute('BEGIN')
        if (
            conn.execute('SELECT 1 FROM model_calls WHERE consult_id = ? AND seq = ?', (consult_id, seq)).fetchone()
            is None
        ):
            return None
        rows = conn.execute(
            'SELECT seq, offset_ns, length(body), '
            'substr(body, CASE WHEN seq = ? THEN ? ELSE 1 END, ?) '
            'FROM model_call_chunks WHERE consult_id = ? AND call_seq = ? AND seq >= ? '
            'ORDER BY seq LIMIT ?',
            (number, offset + 1, BODY_PAGE_BYTES, consult_id, seq, number, CHUNK_PAGE_COUNT + 1),
        )
        items = []
        remaining = BODY_PAGE_BYTES
        for chunk, offset_ns, size, body in rows:
            start = offset if chunk == number else 0
            if len(items) == CHUNK_PAGE_COUNT or remaining == 0:
                return {'items': items, 'next_cursor': f'{chunk}:{start}'}
            piece = _utf8_boundary(body[:remaining], start + min(len(body), remaining) < size)
            items.append({'seq': chunk, 'offset_ns': offset_ns, 'byte_offset': start, 'body': _body(piece)})
            remaining -= len(piece)
            if start + len(piece) < size:
                return {'items': items, 'next_cursor': f'{chunk}:{start + len(piece)}'}
        return {'items': items, 'next_cursor': None}


def _selection(conn, where, args, secret, session_tag, conditions):
    digest = hmac.new(secret, json.dumps([session_tag, conditions]).encode(), 'sha256')
    counts = dict(consults=0, steps=0, calls=0)
    for consult_id, steps, calls in conn.execute(
        'SELECT id, (SELECT count(*) FROM consult_steps WHERE consult_id = id), '
        f'(SELECT count(*) FROM model_calls WHERE consult_id = id) FROM consults WHERE {where} ORDER BY id',
        args,
    ):
        digest.update(consult_id.encode())  # Fixed-length UUIDs.
        counts['consults'] += 1
        counts['steps'] += steps
        counts['calls'] += calls
    return counts, session_tag + digest.hexdigest()


def _check_session(confirmation: str, session_tag: str) -> None:
    if not hmac.compare_digest(confirmation[: len(session_tag)], session_tag):
        raise StoreError('preview_session_changed', '관리자 세션이 바뀌었습니다. 삭제 건수를 다시 확인하세요.', 409)


def _preview_receipt(conn, mutation, session_tag):
    receipt = mutation.receipt(conn)
    if receipt is not None:
        _check_session(receipt[1]['confirmation'], session_tag)
    return receipt


def delete_consults(db, mutation: Mutation, filters, secret: bytes, session_id: str, confirmation: str | None):
    """Preview in a read snapshot and recheck the exact row set under the delete lock."""
    if not filters:
        raise StoreError('invalid_filter', '사용자 또는 기간을 지정하세요.')
    where, args = _where(filters)
    conditions = json.dumps(filters, sort_keys=True)
    session_tag = hmac.new(secret, session_id.encode(), 'sha256').hexdigest()[:32]
    actor = account_hash(mutation.actor)
    if confirmation is None:
        with connect(db) as conn:
            conn.execute('BEGIN')
            if receipt := _preview_receipt(conn, mutation, session_tag):
                return receipt
            counts, expected = _selection(conn, where, args, secret, session_tag, conditions)
        result = {'counts': counts, 'confirmation': expected}
        with connect(db, write=True) as conn, transaction(conn):
            if receipt := _preview_receipt(conn, mutation, session_tag):
                return receipt
            at = stamp(datetime.now(UTC))
            audit_store.record_action(
                conn, at, actor, 'consult_trace_delete_preview', 'consults', 'scope', summary=counts
            )
            mutation.save(conn, 200, result, at)
        return 200, result

    _check_session(confirmation, session_tag)
    with connect(db, write=True) as conn, transaction(conn):
        if receipt := mutation.receipt(conn):
            return receipt
        counts, expected = _selection(conn, where, args, secret, session_tag, conditions)
        if not hmac.compare_digest(confirmation, expected):
            raise StoreError('stale_preview', '삭제 대상이 달라졌습니다. 건수를 다시 확인하세요.', 409)
        at = stamp(datetime.now(UTC))
        conn.execute('PRAGMA secure_delete = ON')
        conn.execute(f'DELETE FROM consults WHERE {where}', args)
        deletion = conn.execute(
            'INSERT INTO consult_trace_deletions (deleted_at, admin_hash, conditions, consult_count, step_count, call_count) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (at, actor, conditions, *counts.values()),
        )
        audit_store.record_action(
            conn, at, actor, 'consult_trace_delete', 'consults', deletion.lastrowid, summary=counts
        )
        mutation.save(conn, 200, counts, at)
        return 200, counts
