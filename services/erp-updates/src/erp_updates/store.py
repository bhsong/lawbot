"""ERP-only SQLite store: items, content versions, attached files, reviews and collection history."""

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from erp_updates.sources import Found

KST = timezone(timedelta(hours=9))
PDF_MAGIC = b'%PDF'
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, source_key TEXT NOT NULL, ref TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), content_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL, collected_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'approved', 'rejected')),
    reviewer TEXT, reason TEXT, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id), url TEXT NOT NULL,
    sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, attached_at TEXT NOT NULL, UNIQUE (version_id, url)
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), at TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('collected', 'unchanged', 'attached', 'approved', 'rejected')),
    origin TEXT
);
"""


class StoreError(RuntimeError):
    """A requested review or attachment does not match the stored items."""


def now() -> str:
    return datetime.now(KST).isoformat(timespec='seconds')


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / 'erp.db')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(SCHEMA)
    return conn


def content_sha256(doc: Found) -> str:
    """Hash the full text and record, without attachment links: the official site reissues every link on each amendment."""
    stable = {
        **doc.data,
        'attachments': [entry['title'] for entry in doc.data['attachments']],
        'fullText': doc.full_text,
    }
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def latest(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute('SELECT * FROM versions WHERE item_id=? ORDER BY id DESC LIMIT 1', (item_id,)).fetchone()


def save(conn: sqlite3.Connection, found: list[Found]) -> int:
    """Record each document; a new content hash becomes a pending version. Returns new version count."""
    created = 0
    at = now()
    with conn:
        for doc in found:
            item_id = hashlib.sha1(f'{doc.source.key}:{doc.ref}'.encode()).hexdigest()[:16]
            # Existing IDs keep their original references. New rows qualify the reference so
            # two business items can use the same upstream document independently.
            conn.execute(
                'INSERT INTO items VALUES (?,?,?) ON CONFLICT(id) DO NOTHING',
                (item_id, doc.source.key, f'{doc.source.key}:{doc.ref}'),
            )
            digest = content_sha256(doc)
            current = latest(conn, item_id)
            if current is not None and current['content_sha256'] == digest:
                action = 'unchanged'
                # Keep reissued links current; files stay tied to the old link because each edition's PDF differs.
                conn.execute(
                    'UPDATE versions SET data_json=? WHERE id=?',
                    (json.dumps(doc.data, ensure_ascii=False), current['id']),
                )
            else:
                action = 'collected'
                conn.execute(
                    'INSERT INTO versions(item_id,content_sha256,data_json,collected_at) VALUES (?,?,?,?)',
                    (item_id, digest, json.dumps(doc.data, ensure_ascii=False), at),
                )
                created += 1
            origin = f'{doc.repo}@{doc.commit[:12]}'
            conn.execute(
                'INSERT INTO history(item_id,at,action,origin) VALUES (?,?,?,?)', (item_id, at, action, origin)
            )
    return created


def review(conn: sqlite3.Connection, item_id: str, decision: str, reviewer: str, reason: str | None) -> None:
    current = latest(conn, item_id)
    if current is None:
        raise StoreError(f'unknown item {item_id}')
    if current['state'] != 'pending':
        raise StoreError(f'latest version of {item_id} is already {current["state"]}')
    at = now()
    with conn:
        conn.execute(
            'UPDATE versions SET state=?,reviewer=?,reason=?,reviewed_at=? WHERE id=?',
            (decision, reviewer, reason, at, current['id']),
        )
        conn.execute('INSERT INTO history(item_id,at,action) VALUES (?,?,?)', (item_id, at, decision))


def attach(conn: sqlite3.Connection, data_dir: Path, path: Path, url: str, *, item_id: str | None = None) -> str:
    """Attach an official PDF to an exact item, or infer the item from a unique listed URL."""
    content = path.read_bytes()
    if not content.startswith(PDF_MAGIC):
        raise StoreError(f'{path.name} is not a PDF')
    matches = [
        version
        for (candidate,) in conn.execute('SELECT id FROM items WHERE ? IS NULL OR id=? ORDER BY id', (item_id, item_id))
        if (version := latest(conn, candidate)) is not None
        and url in {entry['pdfUrl'] for entry in json.loads(version['data_json'])['attachments']}
    ]
    if not matches:
        raise StoreError(f'{url} is not listed by any matching current item')
    if len(matches) > 1:
        raise StoreError(f'{url} is listed by {len(matches)} current items; select one with --item')
    version = matches[0]
    digest = hashlib.sha256(content).hexdigest()
    files = data_dir / 'files'
    files.mkdir(exist_ok=True)
    shutil.copyfile(path, files / f'{digest}.pdf')
    at = now()
    with conn:
        conn.execute(
            'INSERT OR REPLACE INTO files(version_id,url,sha256,bytes,attached_at) VALUES (?,?,?,?,?)',
            (version['id'], url, digest, len(content), at),
        )
        conn.execute(
            'INSERT INTO history(item_id,at,action,origin) VALUES (?,?,?,?)',
            (version['item_id'], at, 'attached', f'file sha256:{digest[:12]}'),
        )
    return version['item_id']


def rows(conn: sqlite3.Connection) -> list[dict]:
    """Latest version per item, for listing and export."""
    result = []
    for item in conn.execute('SELECT * FROM items ORDER BY source_key, ref'):
        version = latest(conn, item['id'])
        if version is None:
            continue
        files = {
            row['url']: dict(row) for row in conn.execute('SELECT * FROM files WHERE version_id=?', (version['id'],))
        }
        count = conn.execute('SELECT COUNT(*) FROM versions WHERE item_id=?', (item['id'],)).fetchone()[0]
        result.append(
            {
                'id': item['id'],
                'version': dict(version),
                'data': json.loads(version['data_json']),
                'files': files,
                'versions': count,
            }
        )
    return result


def history(conn: sqlite3.Connection, item_ids: list[str]) -> list[sqlite3.Row]:
    marks = ','.join('?' * len(item_ids))
    return conn.execute(
        f'SELECT h.*, i.source_key FROM history h JOIN items i ON i.id=h.item_id '
        f'WHERE h.item_id IN ({marks}) ORDER BY h.id',
        item_ids,
    ).fetchall()
