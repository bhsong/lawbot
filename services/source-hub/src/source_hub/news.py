"""Review exact revisions and export only the fields the public news drawer needs."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from lawbot_common.erp_news import AGENCIES, Agency, NewsItem, NewsSnapshot, latest, official_url
from source_hub import materials
from source_hub.store import KST, Registry

REVIEW_SCHEMA = """CREATE TABLE IF NOT EXISTS news_reviews (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, upstream_id TEXT NOT NULL, revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve', 'withdraw')), actor TEXT NOT NULL, reason TEXT NOT NULL,
    reviewed_at TEXT NOT NULL, FOREIGN KEY(source_id,upstream_id,revision)
    REFERENCES revisions(source_id,upstream_id,revision)
)"""
REVIEW_INDEX = 'CREATE INDEX IF NOT EXISTS news_review_revision ON news_reviews(source_id,upstream_id,revision,id)'


def review(
    database: Path, source_id: str, upstream_id: str, revision: int, decision: str, actor: str, reason: str
) -> None:
    if decision not in {'approve', 'withdraw'} or not actor.strip() or not 1 <= len(reason.strip()) <= 500:
        raise ValueError('검토 결정과 사유를 입력하세요')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        materials.check_database(conn)
        if not conn.execute(
            'SELECT 1 FROM revisions WHERE source_id=? AND upstream_id=? AND revision=?',
            (source_id, upstream_id, revision),
        ).fetchone():
            raise ValueError('존재하는 자료 리비전을 지정하세요')
        conn.execute(REVIEW_SCHEMA)
        conn.execute(REVIEW_INDEX)
        conn.execute(
            'INSERT INTO news_reviews(source_id,upstream_id,revision,decision,actor,reason,reviewed_at) VALUES (?,?,?,?,?,?,?)',
            (source_id, upstream_id, revision, decision, actor, reason.strip(), datetime.now(KST).isoformat()),
        )


def approved(database: Path) -> dict[str, list[tuple[str, dict]]]:
    if not database.exists():
        return {}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        materials.check_database(conn)
        if not conn.execute("SELECT 1 FROM sqlite_schema WHERE name='news_reviews'").fetchone():
            return {}
        rows = conn.execute(
            """SELECT r.source_id,r.upstream_id,r.metadata FROM revisions r
               WHERE r.revision = (SELECT MAX(revision) FROM revisions
                  WHERE source_id=r.source_id AND upstream_id=r.upstream_id)
               AND (SELECT decision FROM news_reviews
                  WHERE source_id=r.source_id AND upstream_id=r.upstream_id AND revision=r.revision
                  ORDER BY id DESC LIMIT 1)='approve'"""
        ).fetchall()
    result: dict[str, list[tuple[str, dict]]] = {}
    for source, upstream, metadata in rows:
        result.setdefault(source, []).append((upstream, json.loads(metadata)))
    return result


def build(registry: Registry, database: Path) -> dict:
    sources = registry.snapshot()['sources']
    records = approved(database)
    agencies = {agency: {'boards': [], 'items': []} for agency in AGENCIES}
    laws: list[str] = []
    for source in sources:
        agency = cast(Agency, source['agency'])
        if agency not in agencies or (agency == 'lawgo' and source['source_id'] != 'legalize_law'):
            continue
        name, status = source['board_name'], source['status']
        stamp = source['last_success_at']
        stale_after = (
            datetime.fromisoformat(stamp) + timedelta(minutes=2 * source['interval_minutes']) if stamp else None
        )
        if status == 'ready':
            status = 'pending'
        if status in {'not_tested', 'manual_review'}:
            status = 'unverified'
        agencies[agency]['boards'].append(
            {'name': name, 'status': status, 'lastSuccessAt': stamp, 'staleAfter': stale_after}
        )
        if agency == 'lawgo':
            for law in source['laws']:
                for part in law['parts']:
                    if part['effective_enabled']:
                        laws.append(law['name'] if part['name'] == '법률' else f'{law["name"]} {part["name"]}')
        if agency in {'lawgo', 'hometax'} or not source['runnable'] or source['license_state'] == 'unverified':
            continue
        for upstream, row in records.get(source['source_id'], []):
            if not official_url(row['canonical_url'], agency):
                raise ValueError('승인 자료의 공식 URL을 확인하세요')
            identifier = hashlib.sha256(f'{source["source_id"]}:{upstream}'.encode()).hexdigest()
            agencies[agency]['items'].append(
                NewsItem(
                    id=identifier,
                    title=row['title'],
                    board=name,
                    publishedOn=row['published_on'],
                    officialUrl=row['canonical_url'],
                )
            )
    for entry in agencies.values():
        entry['items'] = latest(entry['items'])
    return NewsSnapshot.model_validate(
        {'schemaVersion': 1, 'generatedAt': datetime.now(KST), 'agencies': agencies, 'lawNames': laws}
    ).model_dump(mode='json')


def export(registry: Registry, database: Path, out: Path) -> dict:
    for private in (registry.database, database):
        if out.resolve() == private.resolve() or (out.exists() and private.exists() and out.samefile(private)):
            raise ValueError('공개 소식 파일은 비공개 DB와 다른 경로를 지정하세요')
    if out.exists():
        NewsSnapshot.model_validate_json(out.read_bytes())
    snapshot = build(registry, database)
    out.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', encoding='utf-8', dir=out.parent, delete=False) as tmp:
        try:
            json.dump(snapshot, tmp, ensure_ascii=False)
            tmp.write('\n')
            tmp.close()
            os.replace(tmp.name, out)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return snapshot
