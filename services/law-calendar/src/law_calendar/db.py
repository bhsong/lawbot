"""Explicit, versioned SQLite initialization; serving never creates or changes a DB."""

import argparse
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE laws(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE change_events(
 id TEXT PRIMARY KEY, law_id TEXT NOT NULL REFERENCES laws(id),
 kind TEXT NOT NULL CHECK(kind IN ('notice','effective')),
 status TEXT NOT NULL, review_state TEXT NOT NULL DEFAULT 'pending'
 CHECK(review_state IN ('pending','approved','rejected','withdrawn')),
 published_on TEXT, promulgated_on TEXT, comment_deadline TEXT,
 source_url TEXT, collected_at TEXT NOT NULL, verified_at TEXT
);
CREATE TABLE article_changes(
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES change_events(id),
 article TEXT NOT NULL, effective_on TEXT,
 summary TEXT NOT NULL, before_text TEXT, after_text TEXT,
 comparable INTEGER NOT NULL DEFAULT 0 CHECK(comparable IN (0,1)),
 review_state TEXT NOT NULL DEFAULT 'pending'
 CHECK(review_state IN ('pending','approved','rejected','withdrawn')),
 verified_at TEXT
);
CREATE TABLE review_audit(
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES change_events(id),
 change_id TEXT REFERENCES article_changes(id), decision TEXT NOT NULL,
 reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE collection_status(
 id INTEGER PRIMARY KEY CHECK(id=1), collected_at TEXT, verified_at TEXT
);
CREATE INDEX event_dates ON change_events(kind,published_on);
CREATE INDEX change_dates ON article_changes(effective_on,event_id);
CREATE VIEW public_changes AS
 SELECT c.*, e.kind,e.status,e.published_on,e.promulgated_on,e.comment_deadline,
 e.source_url,e.collected_at,e.verified_at AS event_verified_at,l.name AS law_name,
 CASE WHEN e.kind='notice' THEN e.published_on ELSE c.effective_on END AS event_on
 FROM article_changes c JOIN change_events e ON e.id=c.event_id JOIN laws l ON l.id=e.law_id
 WHERE e.review_state='approved' AND c.review_state='approved'
 AND e.verified_at IS NOT NULL AND c.verified_at IS NOT NULL
 AND julianday(e.verified_at) IS NOT NULL AND julianday(c.verified_at) IS NOT NULL
 AND julianday(e.collected_at) IS NOT NULL
 AND date(CASE WHEN e.kind='notice' THEN e.published_on ELSE c.effective_on END)
     = CASE WHEN e.kind='notice' THEN e.published_on ELSE c.effective_on END
 AND (e.source_url GLOB 'https://www.law.go.kr/*'
 OR e.source_url GLOB 'https://law.go.kr/*'
 OR e.source_url GLOB 'https://opinion.lawmaking.go.kr/*')
 AND (e.kind='notice' AND e.published_on IS NOT NULL
 OR e.kind='effective' AND c.effective_on IS NOT NULL);
"""

SCHEMA_V2 = """
CREATE TABLE source_snapshots(
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES change_events(id),
 raw_event TEXT NOT NULL, files_json TEXT NOT NULL, parser_version TEXT NOT NULL,
 collected_at TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE event_sources(
 event_id TEXT PRIMARY KEY REFERENCES change_events(id), source TEXT NOT NULL,
 upstream_id TEXT NOT NULL, snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
 UNIQUE(source,upstream_id)
);
CREATE TABLE law_versions(
 id TEXT PRIMARY KEY, law_id TEXT NOT NULL REFERENCES laws(id),
 snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id), side TEXT NOT NULL,
 version_key TEXT NOT NULL, raw_text TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE article_versions(
 id TEXT PRIMARY KEY, law_version_id TEXT NOT NULL REFERENCES law_versions(id),
 article TEXT NOT NULL, raw_text TEXT NOT NULL
);
CREATE TABLE change_versions(
 change_id TEXT PRIMARY KEY REFERENCES article_changes(id),
 snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
 before_version_id TEXT REFERENCES article_versions(id), after_version_id TEXT REFERENCES article_versions(id)
);
CREATE TABLE review_details(
 audit_id INTEGER PRIMARY KEY REFERENCES review_audit(id),
 snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id), payload_json TEXT NOT NULL
);
CREATE TABLE import_runs(
 id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
 imported INTEGER NOT NULL, unchanged INTEGER NOT NULL, failed INTEGER NOT NULL, errors_json TEXT NOT NULL
);
CREATE INDEX snapshots_event ON source_snapshots(event_id);
"""

SCHEMA_V3 = """
CREATE TABLE held_changes(
 change_id TEXT PRIMARY KEY REFERENCES article_changes(id),
 snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
 reason TEXT NOT NULL, rule_version TEXT NOT NULL
);
CREATE VIEW public_holds AS
 SELECT c.id,l.name AS law_name,c.article,h.reason,e.source_url,e.collected_at
 FROM held_changes h JOIN article_changes c ON c.id=h.change_id
 JOIN change_events e ON e.id=c.event_id JOIN laws l ON l.id=e.law_id
 JOIN event_sources s ON s.event_id=e.id AND s.snapshot_id=h.snapshot_id
 JOIN change_versions v ON v.change_id=c.id AND v.snapshot_id=h.snapshot_id
 WHERE c.review_state='pending' AND e.review_state IN ('pending','approved')
 AND (e.source_url GLOB 'https://www.law.go.kr/*'
 OR e.source_url GLOB 'https://law.go.kr/*'
 OR e.source_url GLOB 'https://opinion.lawmaking.go.kr/*');
"""


def migrate(path: Path) -> None:
    """Initialize or upgrade explicitly, backing up existing databases before DDL."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 3:
            return
        if version not in (0, 1, 2) or (
            version == 0 and conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone()
        ):
            raise ValueError('Unsupported database schema; migration refused')
        if existed:
            backup = path.with_suffix(path.suffix + ('.pre-v3.backup' if version else '.pre-v1.backup'))
            if backup.exists():
                raise FileExistsError('Migration backup already exists')
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        script = (SCHEMA if version == 0 else '') + (SCHEMA_V2 if version < 2 else '') + SCHEMA_V3
        conn.executescript('BEGIN IMMEDIATE;\n' + script + '\nPRAGMA user_version=3; COMMIT;')


@contextmanager
def read_database(path: Path):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise sqlite3.DatabaseError('Unsupported schema')
        yield conn
    finally:
        conn.close()


@contextmanager
def write_database(path: Path):
    """Serialize offline imports/reviews; never create or migrate an implicit DB."""
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        if conn.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise sqlite3.DatabaseError('Run the explicit database migration first')
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            yield conn


def main():
    parser = argparse.ArgumentParser(description='Initialize the independent calendar database')
    parser.add_argument('database', type=Path)
    migrate(parser.parse_args().database)


if __name__ == '__main__':
    main()
