"""Read, publish, and restore corpus releases."""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lawbot.db.connection import connect, transaction
from lawbot.db.corpus_import import (
    CorpusImport,
    _activate,
    _record_event,
    _record_release,
    prepare_corpus,
    release_bytes,
    write_corpus,
)
from lawbot_common.logs import logger

if TYPE_CHECKING:
    from lawbot.embed_cache import EmbeddingMeta

log = logger('lawbot', 'lawbot')
UPDATE_ACTOR = 'articles-update'


@dataclass(frozen=True)
class CorpusPart:
    name: str
    data: bytes
    manifest: dict
    manifest_bytes: bytes


@dataclass(frozen=True)
class CorpusRelease:
    release_id: str
    law: CorpusPart
    combined: CorpusPart


def active_release(db: Path, profile: str) -> CorpusRelease:
    """Resolve the active ID and restore both verified CSVs from one SQLite snapshot."""
    try:
        with connect(db) as conn:
            conn.execute('BEGIN')
            row = conn.execute(
                'SELECT release_id,law_csv_name,combined_csv_name,law_manifest_name,combined_manifest_name '
                'FROM active_corpus a JOIN corpus_releases r USING (release_id) WHERE a.profile=? AND a.profile=r.profile',
                (profile,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f'프로필 {profile}의 활성 코퍼스 릴리스가 없습니다.')
            release_id, law, combined, law_manifest, combined_manifest = row
            files = release_bytes(conn, release_id)
    except sqlite3.Error as exc:
        raise RuntimeError(f'프로필 {profile}의 활성 코퍼스 릴리스를 읽지 못했습니다.') from exc
    return CorpusRelease(
        release_id,
        CorpusPart(law, files[law], json.loads(files[law_manifest]), files[law_manifest]),
        CorpusPart(combined, files[combined], json.loads(files[combined_manifest]), files[combined_manifest]),
    )


def active_token(db: Path, profile: str, *, timeout: float = 5) -> tuple[str, int] | None:
    """Read the active release and its latest activation/rollback event in one snapshot."""
    with connect(db, timeout=timeout) as conn:
        return conn.execute(
            'SELECT a.release_id,e.event_id FROM active_corpus a JOIN corpus_events e ON e.profile=a.profile '
            "WHERE a.profile=? AND e.kind IN ('activated','rolled_back') "
            'AND e.event_id=(SELECT max(event_id) FROM corpus_events WHERE profile=a.profile '
            "AND kind IN ('activated','rolled_back')) AND e.release_id=a.release_id",
            (profile,),
        ).fetchone()


def _active_id(conn: sqlite3.Connection, profile: str) -> str | None:
    row = conn.execute('SELECT release_id FROM active_corpus WHERE profile=?', (profile,)).fetchone()
    return row[0] if row else None


def _require_active(conn: sqlite3.Connection, profile: str, expected: str) -> None:
    if _active_id(conn, profile) != expected:
        raise RuntimeError(f'처리 중 프로필 {profile}의 활성 코퍼스 릴리스가 바뀌었습니다.')


def _next_release_id(conn: sqlite3.Connection, corpus: CorpusImport) -> str:
    base = f'{corpus.profile}-live-{corpus.combined.manifest_data["reference_date"]}'
    release_id, suffix = base, 1
    while conn.execute('SELECT 1 FROM corpus_releases WHERE release_id=?', (release_id,)).fetchone():
        suffix += 1
        release_id = f'{base}_{suffix}'
    return release_id


def initialize_release(db: Path, directory: Path, profile: str) -> str:
    """Import the profile's baseline and require no active release at commit."""
    with connect(db) as conn:
        if _active_id(conn, profile) is not None:
            raise ValueError(f'프로필 {profile}에 이미 활성 코퍼스 릴리스가 있습니다.')
    corpus = prepare_corpus(directory, profile, profile)
    with connect(db, write=True) as conn:
        release_id = _next_release_id(conn, corpus)
        write_corpus(conn, replace(corpus, release_id=release_id), activate=True, require_inactive=True)
    return release_id


def stage_release(db: Path, corpus: CorpusImport, previous: str) -> str:
    """Reserve a release ID and store the candidate before any model work."""
    at = datetime.now(UTC).isoformat()
    with connect(db, write=True) as conn, transaction(conn):
        _require_active(conn, corpus.profile, previous)
        release_id = _next_release_id(conn, corpus)
        _record_release(
            conn, replace(corpus, release_id=release_id), at, status='staged', origin=UPDATE_ACTOR, actor=UPDATE_ACTOR
        )
    return release_id


def record_failed(db: Path, profile: str, release_id: str | None, previous: str, error: BaseException) -> None:
    """Record a failed build or gate, keeping any staged candidate for audit."""
    at = datetime.now(UTC).isoformat()
    with connect(db, write=True) as conn, transaction(conn):
        if release_id is not None:
            changed = conn.execute(
                "UPDATE corpus_releases SET status='failed' WHERE release_id=? AND profile=? AND status='staged'",
                (release_id, profile),
            )
            if changed.rowcount != 1:
                raise RuntimeError(f'대기 중인 코퍼스 릴리스가 아닙니다: {release_id}')
        _record_event(
            conn,
            profile,
            'failed',
            release_id,
            at,
            UPDATE_ACTOR,
            previous=previous,
            detail={'error_type': type(error).__name__, 'error': str(error)},
        )


def validate_and_activate(db: Path, profile: str, release_id: str, previous: str) -> None:
    """Commit validation and activation together if the gate's baseline is still active."""
    at = datetime.now(UTC).isoformat()
    with connect(db, write=True) as conn, transaction(conn):
        _require_active(conn, profile, previous)
        changed = conn.execute(
            "UPDATE corpus_releases SET status='validated',validated_at=? "
            "WHERE release_id=? AND profile=? AND status='staged'",
            (at, release_id, profile),
        )
        if changed.rowcount != 1:
            raise RuntimeError(f'대기 중인 코퍼스 릴리스가 아닙니다: {release_id}')
        _record_event(conn, profile, 'validated', release_id, at, UPDATE_ACTOR)
        _activate(conn, profile, release_id, at, actor=UPDATE_ACTOR)


def previous_release(db: Path, profile: str, current: str) -> str:
    """Return the rollback target of the most recent activation of the current release."""
    with connect(db) as conn:
        conn.execute('BEGIN')
        _require_active(conn, profile, current)
        row = conn.execute(
            "SELECT previous_release_id FROM corpus_events WHERE profile=? AND release_id=? AND kind='activated' "
            'ORDER BY event_id DESC LIMIT 1',
            (profile, current),
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError(f'되돌릴 코퍼스 릴리스가 없습니다: {current}')
        target = row[0]
        release_bytes(conn, target)
        return target


def rollback_release(db: Path, profile: str, current: str, target: str, *, restart_error: str | None = None) -> None:
    """Switch to a previously verified release and record the reason in the same transaction."""
    at = datetime.now(UTC).isoformat()
    with connect(db, write=True) as conn, transaction(conn):
        _require_active(conn, profile, current)
        if restart_error is not None:
            _record_event(
                conn,
                profile,
                'restart_failed',
                current,
                at,
                UPDATE_ACTOR,
                previous=target,
                detail={'error': restart_error},
            )
        _activate(conn, profile, target, at, actor=UPDATE_ACTOR, kind='rolled_back')


def record_embeddings(db: Path, release_id: str, entries: list[tuple[str, str, EmbeddingMeta]]) -> None:
    """Record metadata for the loaded indexes without making startup depend on the write."""
    at = datetime.now(UTC).isoformat()
    try:
        with connect(db, write=True) as conn, transaction(conn):
            for part, key, meta in entries:
                conn.execute(
                    'INSERT INTO embedding_sets '
                    '(cache_key,model_id,model_revision,max_seq,dim,n_docs,docs_sha256,created_at) '
                    'VALUES (:cache_key,:model_id,:model_revision,:max_seq,:dim,:n_docs,:docs_sha256,:created_at) '
                    'ON CONFLICT (cache_key) DO UPDATE SET model_revision=excluded.model_revision,dim=excluded.dim',
                    {**meta, 'cache_key': key, 'created_at': at},
                )
                conn.execute(
                    'INSERT INTO release_embedding_sets (release_id,part,cache_key) VALUES (?,?,?) '
                    'ON CONFLICT (release_id,part,cache_key) DO NOTHING',
                    (release_id, part, key),
                )
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        log.warning('경고: 릴리스 %s 임베딩 메타데이터를 기록하지 못했습니다 (%s)', release_id, type(exc).__name__)
