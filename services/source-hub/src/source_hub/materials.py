"""Store private source observations in an independent SQLite database."""

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from source_hub.store import Run

APPLICATION_ID = 0x4C455250
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS documents (
        source_id TEXT NOT NULL, upstream_id TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_confirmed_at TEXT NOT NULL, PRIMARY KEY (source_id, upstream_id)
    )""",
    """CREATE TABLE IF NOT EXISTS revisions (
        source_id TEXT NOT NULL, upstream_id TEXT NOT NULL, revision INTEGER NOT NULL,
        metadata TEXT NOT NULL, event_type TEXT NOT NULL, observed_at TEXT NOT NULL,
        PRIMARY KEY (source_id, upstream_id, revision),
        FOREIGN KEY (source_id, upstream_id) REFERENCES documents(source_id, upstream_id)
    )""",
    'CREATE TABLE IF NOT EXISTS latest_success (source_id TEXT PRIMARY KEY, result TEXT NOT NULL)',
)


@dataclass(frozen=True)
class Article:
    upstream_id: str
    title: str
    canonical_url: str
    published_on: str | None
    guid: str | None


def check_database(conn: sqlite3.Connection, *, initialize: bool = False) -> None:
    application = conn.execute('PRAGMA application_id').fetchone()[0]
    if application == APPLICATION_ID:
        return
    if not initialize or application or conn.execute('SELECT 1 FROM sqlite_schema LIMIT 1').fetchone():
        raise ValueError('지정한 파일은 수집 자료 DB가 아닙니다')
    conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
    for statement in SCHEMA:
        conn.execute(statement)


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        pass
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        check_database(conn, initialize=True)


def latest_success(path: Path, source_id: str) -> Run | None:
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute('SELECT result FROM latest_success WHERE source_id=?', (source_id,)).fetchone()
    return Run.model_validate_json(row[0]) if row else None


def save(path: Path, articles: list[Article], run: Run, baseline_policy: str) -> tuple[Run, dict[str, int]]:
    source_id = run.source_id
    now = run.model_dump()['finished_at']
    counts: dict[str, int] = dict.fromkeys(('baseline_count', 'new_count', 'modified_count'), 0)
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        check_database(conn)
        first = not conn.execute('SELECT 1 FROM documents WHERE source_id=? LIMIT 1', (source_id,)).fetchone()
        for article in articles:
            key = (source_id, article.upstream_id)
            metadata = json.dumps(asdict(article), ensure_ascii=False)
            previous = conn.execute(
                'SELECT revision, metadata FROM revisions WHERE source_id=? AND upstream_id=? '
                'ORDER BY revision DESC LIMIT 1',
                key,
            ).fetchone()
            conn.execute(
                'INSERT INTO documents VALUES (?,?,?,?) ON CONFLICT(source_id,upstream_id) '
                'DO UPDATE SET last_confirmed_at=excluded.last_confirmed_at',
                (*key, now, now),
            )
            if previous and previous[1] == metadata:
                continue
            if previous:
                count, event = 'modified_count', 'post_modified'
            elif first:
                count = 'baseline_count'
                event = 'baseline_review' if baseline_policy == 'review_backfill' else 'baseline'
            else:
                count, event = 'new_count', 'new_post'
            counts[count] += 1
            conn.execute(
                'INSERT INTO revisions VALUES (?,?,?,?,?,?)',
                (*key, previous[0] + 1 if previous else 1, metadata, event, now),
            )
        result = run.model_copy(update={key: counts[key] for key in ('new_count', 'modified_count')})
        conn.execute(
            'INSERT INTO latest_success VALUES (?,?) ON CONFLICT(source_id) DO UPDATE SET result=excluded.result',
            (source_id, result.model_dump_json()),
        )
    return result, counts


def documents(path: Path, source_id: str) -> list[dict]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        check_database(conn)
        rows = conn.execute(
            'SELECT r.metadata,r.revision,r.event_type,d.first_seen_at,d.last_confirmed_at '
            'FROM documents d JOIN revisions r USING (source_id,upstream_id) '
            'WHERE d.source_id=? AND r.revision=(SELECT MAX(revision) FROM revisions '
            'WHERE source_id=d.source_id AND upstream_id=d.upstream_id)',
            (source_id,),
        ).fetchall()
    result = [
        json.loads(metadata)
        | {
            'revision_count': revision,
            'event_type': event,
            'first_seen_at': first,
            'last_confirmed_at': last,
            'content_level': 'metadata_only',
        }
        for metadata, revision, event, first, last in rows
    ]
    return sorted(result, key=lambda row: (row['published_on'] or '', row['upstream_id']), reverse=True)
