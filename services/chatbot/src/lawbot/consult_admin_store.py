"""List, inspect and delete completed consultations without duplicating their payloads."""

import base64
import hmac
import json
import sqlite3
from datetime import UTC, datetime

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
        row = conn.execute('SELECT * FROM consults WHERE id = ?', (consult_id,)).fetchone()
        if row is None:
            return None
        steps = [
            dict(row)
            for row in conn.execute(
                'SELECT * FROM consult_steps WHERE consult_id = ? ORDER BY seq',
                (consult_id,),
            )
        ]
        calls = [
            dict(row)
            for row in conn.execute(
                'SELECT * FROM model_calls WHERE consult_id = ? ORDER BY seq',
                (consult_id,),
            )
        ]
    consult = dict(row)
    for record in (consult, *steps, *calls):
        record['redaction_reasons'] = json.loads(record['redaction_reasons'])
    for step in steps:
        step['result'] = json.loads(step['result'])
    for call in calls:
        call['request_body'] = _body(call['request_body'])
        call['response_body'] = _body(call['response_body'])
        if call['usage'] is not None:
            call['usage'] = json.loads(call['usage'])
        if call['response_chunks'] is not None:
            call['response_chunks'] = [
                {'offset_ns': offset, 'body': _body(base64.b64decode(value))}
                for offset, value in json.loads(call['response_chunks'])
            ]
    return {'consult': consult, 'steps': steps, 'calls': calls}


def delete_consults(db, mutation: Mutation, filters, secret: bytes, session_id: str, confirmation: str | None):
    """Bind confirmation to the exact matching row set and recheck under the delete lock."""
    if not filters:
        raise StoreError('invalid_filter', '사용자 또는 기간을 지정하세요.')
    where, args = _where(filters)
    with connect(db, write=True) as conn, transaction(conn):
        if receipt := mutation.receipt(conn):
            return receipt
        conditions = json.dumps(filters, sort_keys=True)
        digest = hmac.new(secret, json.dumps([session_id, conditions]).encode(), 'sha256')
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
        expected = digest.hexdigest()
        at, actor = stamp(datetime.now(UTC)), account_hash(mutation.actor)
        if confirmation is None:
            result = {'counts': counts, 'confirmation': expected}
            audit_store.record_action(
                conn, at, actor, 'consult_trace_delete_preview', 'consults', 'scope', summary=counts
            )
        else:
            if not hmac.compare_digest(confirmation, expected):
                raise StoreError('stale_preview', '삭제 대상이 달라졌습니다. 건수를 다시 확인하세요.', 409)
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
            result = counts
        mutation.save(conn, 200, result, at)
        return 200, result
