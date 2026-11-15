"""Durable question results and request receipts for one private evaluation phase."""

import json
import os
import sqlite3
from hashlib import sha256

from lawbot.exact_cache import canonical


def call_signature(value):
    return sha256(canonical(value).encode()).hexdigest()


class StageJournal:
    def __init__(self, path, *, read_only=False):
        self.path = path
        self.read_only = read_only

    def __enter__(self):
        if self.read_only:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
            return self
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS items (key TEXT PRIMARY KEY, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls (
                item TEXT NOT NULL, position INTEGER NOT NULL, kind TEXT NOT NULL, signature TEXT NOT NULL,
                status TEXT NOT NULL, response BLOB, milliseconds REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (item, position)
            );
        """)
        return self

    def __exit__(self, *exception):
        self.db.close()

    def get_item(self, key):
        row = self.db.execute('SELECT result FROM items WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_item(self, key, value):
        self.db.execute('INSERT INTO items VALUES (?,?)', (key, canonical(value)))

    def completed(self):
        return self.db.execute('SELECT count(*) FROM items').fetchone()[0]

    def begin_call(self, item, position, kind, signature, *, reserve=True):
        row = self.db.execute(
            'SELECT kind,signature,status,response,milliseconds FROM calls WHERE item=? AND position=?',
            (item, position),
        ).fetchone()
        if row is None:
            if reserve:
                self.db.execute(
                    "INSERT INTO calls(item,position,kind,signature,status) VALUES (?,?,?,?,'started')",
                    (item, position, kind, signature),
                )
            return None
        previous_kind, previous_signature, status, response, duration = row
        if (previous_kind, previous_signature) != (kind, signature):
            raise ValueError('저장한 모델 요청과 재개할 요청이 다릅니다.')
        if status == 'started':
            return 'interrupted', None, duration
        value = response if kind == 'embedding' or response is None else json.loads(response)
        return status, value, duration

    def save_call(self, item, position, result, duration, *, status='done'):
        response = result if isinstance(result, bytes) or result is None else canonical(result)
        self.db.execute(
            'UPDATE calls SET status=?, response=?, milliseconds=? WHERE item=? AND position=?',
            (status, response, duration, item, position),
        )

    def request_counts(self, *, prefix=''):
        return dict(self.db.execute('SELECT kind,count(*) FROM calls WHERE item LIKE ? GROUP BY kind', (prefix + '%',)))
