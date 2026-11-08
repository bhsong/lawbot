"""Read a pinned corpus release and record its embedding index metadata."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lawbot.db.connection import connect, transaction
from lawbot.db.corpus_import import release_bytes
from lawbot_common.logs import logger

if TYPE_CHECKING:
    from lawbot.embed_cache import EmbeddingMeta

log = logger('lawbot', 'lawbot')


@dataclass(frozen=True)
class CorpusPart:
    name: str
    data: bytes
    manifest: dict


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
        CorpusPart(law, files[law], json.loads(files[law_manifest])),
        CorpusPart(combined, files[combined], json.loads(files[combined_manifest])),
    )


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
