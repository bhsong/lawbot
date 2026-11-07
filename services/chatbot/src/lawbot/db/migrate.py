import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from lawbot.db.connection import check_db_path, connect, new_database, transaction

MIGRATIONS = files('lawbot.db') / 'migrations'


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def _migrations() -> list[Migration]:
    migrations = []
    for file in sorted(MIGRATIONS.iterdir(), key=lambda file: file.name):
        match = re.fullmatch(r'(\d{4})_.+\.sql', file.name)
        if match:
            sql = file.read_bytes()
            migrations.append(Migration(int(match[1]), file.name, hashlib.sha256(sql).hexdigest(), sql.decode('utf-8')))
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)) or not migrations:
        raise RuntimeError('마이그레이션 파일 번호가 0001부터 연속이어야 합니다.')
    return migrations


def _current_version(conn: sqlite3.Connection, migrations: list[Migration]) -> int:
    table = conn.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'schema_migrations'").fetchone()
    if table is None:
        return 0
    applied = conn.execute('SELECT version, name, checksum FROM schema_migrations ORDER BY version').fetchall()
    version = applied[-1][0] if applied else 0
    latest = migrations[-1].version
    if version > latest:
        raise RuntimeError(f'DB 스키마 버전 {version}이 코드의 버전 {latest}보다 높습니다.')
    if [row[0] for row in applied] != list(range(1, version + 1)):
        raise RuntimeError('DB 마이그레이션 이력이 연속되지 않습니다.')
    for (_, name, checksum), migration in zip(applied, migrations):
        if (name, checksum) != (migration.name, migration.checksum):
            raise RuntimeError(f'마이그레이션 파일 이름 또는 checksum 불일치: {migration.name}')
    return version


def check_schema(path: str | Path) -> int:
    """Require the complete schema version and migration history known to this code."""
    migrations = _migrations()
    latest = migrations[-1].version
    with connect(check_db_path(path)) as conn:
        version = _current_version(conn, migrations)
    if version < latest:
        raise RuntimeError(f'DB 스키마 버전 {version}이 코드의 버전 {latest}보다 낮습니다. uv run lawbot-db migrate')
    return version


def migrate(path: str | Path) -> int:
    """Apply each pending migration with its history row in one transaction."""
    migrations = _migrations()
    with connect(check_db_path(path), write=True) as conn:
        for migration in migrations:
            with transaction(conn):
                if migration.version <= _current_version(conn, migrations):
                    continue
                # With autocommit=True, executescript() keeps the explicit transaction open.
                conn.executescript(migration.sql)
                conn.execute(
                    'INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)',
                    (migration.version, migration.name, migration.checksum, datetime.now(UTC).isoformat()),
                )
    return migrations[-1].version


def init(path: str | Path) -> int:
    """Create a fully migrated database at a new path."""
    with new_database(path) as staged:
        return migrate(staged)
