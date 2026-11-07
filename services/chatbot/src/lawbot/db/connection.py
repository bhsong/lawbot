import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from lawbot.feedback import check_private
from lawbot.paths import ROOT
from lawbot_common.logs import logger

DEFAULT_DB = ROOT / 'data' / 'db' / 'lawbot.db'
log = logger('lawbot', 'lawbot')


def db_path(value: str) -> Path:
    """Resolve a configured database path relative to the repository root."""
    path = ROOT / Path(value).expanduser()
    check_private(path, 'LAWBOT_DB', folder=False)
    return path


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(path.with_name(path.name + suffix) for suffix in ('-wal', '-shm', '-journal'))


def _private_files(path: Path) -> tuple[Path, ...]:
    files = (path, *_sidecars(path))
    for file in files:
        check_private(file, 'DB', folder=False)
    return files


def _warn_permissions(path: Path, allowed: int) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return
    if mode & ~allowed:
        log.warning(f'DB 권한이 넓습니다. chmod {allowed:o} {path}')


@contextmanager
def connect(path: str | Path, write: bool = False) -> Iterator[sqlite3.Connection]:
    """Open an existing DB for one task. Use transaction() for writes."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'DB 파일이 없습니다. uv run lawbot-db init --db {path}')
    _warn_permissions(path.parent, 0o700)
    for file in _private_files(path):
        _warn_permissions(file, 0o600)

    mode = 'rw' if write else 'ro'
    uri = f'{path.as_uri()}?mode={mode}'
    with closing(sqlite3.connect(uri, uri=True, timeout=5, autocommit=True)) as conn:
        conn.execute('PRAGMA foreign_keys = ON')
        if conn.execute('PRAGMA foreign_keys').fetchone() != (1,):
            raise RuntimeError('SQLite foreign_keys를 켤 수 없습니다.')
        if write and conn.execute('PRAGMA journal_mode = WAL').fetchone() != ('wal',):
            raise RuntimeError('SQLite WAL 모드를 켤 수 없습니다.')
        conn.execute('PRAGMA synchronous = NORMAL')
        yield conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Start a write transaction and roll it back if the body or commit fails."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.execute('COMMIT')
    finally:
        if conn.in_transaction:
            conn.execute('ROLLBACK')


def _require_new(path: Path) -> None:
    for file in (path, *_sidecars(path)):
        if file.exists() or file.is_symlink():
            raise FileExistsError(f'이미 파일이 있습니다. 새 경로를 지정하세요. {file}')


@contextmanager
def new_database(path: str | Path) -> Iterator[Path]:
    """Publish a closed, validated SQLite file at a new path."""
    path = Path(path).expanduser()
    _private_files(path)
    _require_new(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _warn_permissions(path.parent, 0o700)
    with TemporaryDirectory(dir=path.parent, prefix='.lawbot-db-') as directory:
        staged = Path(directory) / path.name
        staged.touch(mode=0o600, exist_ok=False)
        yield staged
        _require_new(path)
        # Unlike replace(), link() fails if a concurrent command publishes first.
        os.link(staged, path)
