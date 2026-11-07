"""Persist full feedback records and their indexed fields in SQLite."""

import json
import sqlite3
from pathlib import Path

from lawbot.db.connection import connect, transaction
from lawbot_common.logs import logger

log = logger('lawbot', 'lawbot')
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
