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

from lawbot_common.erp_news import AGENCIES, SNAPSHOT_MAX_BYTES, Agency, NewsItem, NewsSnapshot, latest, official_url
from source_hub import materials
from source_hub.collector import collect_lock
from source_hub.store import KST, Conflict, Registry

REVIEW_SCHEMA = """CREATE TABLE IF NOT EXISTS news_reviews (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, upstream_id TEXT NOT NULL, revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve', 'withdraw')), actor TEXT NOT NULL, reason TEXT NOT NULL,
    reviewed_at TEXT NOT NULL, FOREIGN KEY(source_id,upstream_id,revision)
    REFERENCES revisions(source_id,upstream_id,revision)
)"""
REVIEW_INDEX = 'CREATE INDEX IF NOT EXISTS news_review_revision ON news_reviews(source_id,upstream_id,revision,id)'
REVIEW_AGENCIES = ('moel', 'mofe', 'nts')


def review(
    database: Path,
    source_id: str,
    upstream_id: str,
    revision: int,
    decision: str,
    actor: str,
    reason: str = '',
    *,
    require_current: bool = False,
    expected_review_id: int | None = None,
    agency: Agency | None = None,
) -> None:
    reason = reason.strip()
    if decision not in {'approve', 'withdraw'} or not actor.strip():
        raise ValueError('검토 결정과 담당자를 확인하세요')
    if decision == 'withdraw' and not reason:
        raise ValueError('철회 사유를 입력하세요')
    if len(reason) > 500:
        raise ValueError('검토 사유는 500자까지 입력할 수 있습니다')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        materials.check_database(conn)
        record = conn.execute(
            'SELECT metadata FROM revisions WHERE source_id=? AND upstream_id=? AND revision=?',
            (source_id, upstream_id, revision),
        ).fetchone()
        if record is None:
            raise ValueError('존재하는 자료 리비전을 지정하세요')
        if (
            agency is not None
            and decision == 'approve'
            and not official_url(json.loads(record[0])['canonical_url'], agency)
        ):
            raise ValueError('공식 원문 링크를 확인하세요')
        conn.execute(REVIEW_SCHEMA)
        conn.execute(REVIEW_INDEX)
        if require_current:
            latest_revision = conn.execute(
                'SELECT MAX(revision) FROM revisions WHERE source_id=? AND upstream_id=?',
                (source_id, upstream_id),
            ).fetchone()[0]
            prior = conn.execute(
                'SELECT id,decision FROM news_reviews WHERE source_id=? AND upstream_id=? AND revision=? '
                'ORDER BY id DESC LIMIT 1',
                (source_id, upstream_id, revision),
            ).fetchone()
            if revision != latest_revision or (prior[0] if prior else None) != expected_review_id:
                raise Conflict('자료가 변경되었습니다. 목록을 다시 불러오세요')
            is_approved = bool(prior and prior[1] == 'approve')
            if is_approved == (decision == 'approve'):
                raise Conflict('이미 같은 공개 결정이 기록되어 있습니다')
        conn.execute(
            'INSERT INTO news_reviews(source_id,upstream_id,revision,decision,actor,reason,reviewed_at) VALUES (?,?,?,?,?,?,?)',
            (source_id, upstream_id, revision, decision, actor, reason, datetime.now(KST).isoformat()),
        )


def review_sources(registry: Registry) -> dict[str, dict]:
    return review_sources_from_plan(registry.snapshot())


def review_sources_from_plan(plan: dict) -> dict[str, dict]:
    return {
        row['source_id']: row for row in plan['sources'] if row['agency'] in REVIEW_AGENCIES and row['group'] == 'erp'
    }


def eligible(source: dict) -> bool:
    return source['runnable'] and source['license_state'] != 'unverified'


def version(source: dict, revision: int, metadata: str) -> dict:
    row = json.loads(metadata)
    url = row['canonical_url']
    return {
        'revision': revision,
        'title': row['title'],
        'published_on': row['published_on'],
        'official_url': url if official_url(url, source['agency']) else None,
    }


def review_queue(
    database: Path,
    sources: dict[str, dict],
    source_id: str | None,
    status: str,
    page: int,
    size: int = 20,
) -> dict:
    selected = [source_id] if source_id is not None else list(sources)
    result: dict = {
        'items': [],
        'total': 0,
        'page': page,
        'page_size': size,
        'sources': [{'id': key, 'name': row['board_name'], 'agency': row['agency']} for key, row in sources.items()],
    }
    if not database.exists() or not selected:
        return result
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        materials.check_database(conn)
        has_reviews = conn.execute("SELECT 1 FROM sqlite_schema WHERE name='news_reviews'").fetchone()
        join = (
            """
            LEFT JOIN news_reviews n ON n.id = (
                SELECT MAX(id) FROM news_reviews
                WHERE source_id=r.source_id AND upstream_id=r.upstream_id AND revision=r.revision)
        """
            if has_reviews
            else ''
        )
        decision = (
            "CASE n.decision WHEN 'approve' THEN 'approved' WHEN 'withdraw' THEN 'withdrawn' ELSE 'pending' END"
            if has_reviews
            else "'pending'"
        )
        audit = (
            'n.id AS review_id, n.actor, n.reason, n.reviewed_at'
            if has_reviews
            else ('NULL AS review_id, NULL AS actor, NULL AS reason, NULL AS reviewed_at')
        )
        query = f"""
            SELECT r.source_id, r.upstream_id, r.revision, r.metadata, r.observed_at, {audit}, {decision} AS status
            FROM revisions r {join}
            WHERE r.source_id IN (SELECT value FROM json_each(?))
              AND r.revision=(SELECT MAX(revision) FROM revisions
                  WHERE source_id=r.source_id AND upstream_id=r.upstream_id)
        """
        params = (json.dumps(selected), status)
        result['total'] = conn.execute(f'SELECT COUNT(*) FROM ({query}) WHERE status=?', params).fetchone()[0]
        rows = conn.execute(
            f'SELECT * FROM ({query}) WHERE status=? ORDER BY observed_at DESC,source_id,upstream_id LIMIT ? OFFSET ?',
            (*params, size, (page - 1) * size),
        ).fetchall()
        for row in rows:
            source = sources[row['source_id']]
            prior = (
                conn.execute(
                    """SELECT r.revision,r.metadata FROM revisions r
                   WHERE r.source_id=? AND r.upstream_id=? AND r.revision<?
                     AND (SELECT decision FROM news_reviews WHERE source_id=r.source_id AND upstream_id=r.upstream_id
                         AND revision=r.revision ORDER BY id DESC LIMIT 1)='approve'
                   ORDER BY r.revision DESC LIMIT 1""",
                    (row['source_id'], row['upstream_id'], row['revision']),
                ).fetchone()
                if has_reviews
                else None
            )
            current = version(source, row['revision'], row['metadata'])
            result['items'].append(
                {
                    'source_id': row['source_id'],
                    'upstream_id': row['upstream_id'],
                    'board': source['board_name'],
                    'status': row['status'],
                    'review_id': row['review_id'],
                    'actor': row['actor'],
                    'reason': row['reason'],
                    'reviewed_at': row['reviewed_at'],
                    'observed_at': row['observed_at'],
                    'can_approve': eligible(source) and current['official_url'] is not None,
                    'current': current,
                    'previous': version(source, prior[0], prior[1]) if prior else None,
                }
            )
    return result


def read_snapshot(out: Path) -> dict:
    with out.open('rb') as stream:
        data = stream.read(SNAPSHOT_MAX_BYTES + 1)
    if len(data) > SNAPSHOT_MAX_BYTES:
        raise ValueError('공개 스냅샷이 허용 크기를 넘었습니다')
    return NewsSnapshot.model_validate_json(data).model_dump(mode='json')


def publication(registry: Registry, database: Path, out: Path) -> dict:
    try:
        public = read_snapshot(out)
    except FileNotFoundError:
        return {'state': 'missing', 'generated_at': None, 'published': 0}
    except OSError, ValueError:
        return {'state': 'invalid', 'generated_at': None, 'published': 0}
    generated_at = public.pop('generatedAt')
    desired = build(registry, database)
    desired.pop('generatedAt')
    return {
        'state': 'current' if public == desired else 'pending',
        'generated_at': generated_at,
        'published': sum(len(value['items']) for value in public['agencies'].values()),
    }


def approved(database: Path) -> dict[str, list[tuple[str, dict]]]:
    if not database.exists():
        return {}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('BEGIN')
        materials.check_database(conn)
        if not conn.execute("SELECT 1 FROM sqlite_schema WHERE name='news_reviews'").fetchone():
            return {}
        rows = conn.execute(
            """SELECT r.source_id,r.upstream_id,r.metadata,r.revision,r.event_type FROM revisions r
               WHERE r.revision = (SELECT MAX(revision) FROM revisions
                  WHERE source_id=r.source_id AND upstream_id=r.upstream_id)
               AND (SELECT decision FROM news_reviews
                  WHERE source_id=r.source_id AND upstream_id=r.upstream_id AND revision=r.revision
                  ORDER BY id DESC LIMIT 1)='approve'"""
        ).fetchall()
    result: dict[str, list[tuple[str, dict]]] = {}
    for source, upstream, metadata, revision, event in rows:
        result.setdefault(source, []).append((upstream, json.loads(metadata) | {'revision': revision, 'event': event}))
    return result


def build(
    registry: Registry,
    database: Path,
    *,
    plan: dict | None = None,
    records: dict[str, list[tuple[str, dict]]] | None = None,
    now: datetime | None = None,
) -> dict:
    sources = (registry.snapshot() if plan is None else plan)['sources']
    records = approved(database) if records is None else records
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
        if agency not in REVIEW_AGENCIES or not eligible(source):
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
        {'schemaVersion': 1, 'generatedAt': now or datetime.now(KST), 'agencies': agencies, 'lawNames': laws}
    ).model_dump(mode='json')


def export(registry: Registry, database: Path, out: Path) -> dict:
    for private in (registry.database, database):
        if out.resolve() == private.resolve() or (out.exists() and private.exists() and out.samefile(private)):
            raise ValueError('공개 소식 파일은 비공개 DB와 다른 경로를 지정하세요')
    with collect_lock(registry) as locked:
        if not locked:
            raise Conflict('수집 또는 공개 스냅샷 갱신이 진행 중입니다')
        try:
            read_snapshot(out)
        except FileNotFoundError:
            pass
        snapshot = build(registry, database)
        encoded = (json.dumps(snapshot, ensure_ascii=False) + '\n').encode('utf-8')
        if len(encoded) > SNAPSHOT_MAX_BYTES:
            raise ValueError('공개 스냅샷이 허용 크기를 넘었습니다')
        out.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=out.parent, delete=False) as tmp:
            try:
                tmp.write(encoded)
                tmp.close()
                os.replace(tmp.name, out)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
    return snapshot
