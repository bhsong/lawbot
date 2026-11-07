from pathlib import Path

from lawbot.db.connection import connect, new_database
from lawbot.db.migrate import check_schema


def _copy(source: str | Path, staged: Path) -> None:
    with connect(source) as src, connect(staged, write=True) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('SQLite 무결성 검사에 실패했습니다.')


def backup(path: str | Path, out: str | Path) -> None:
    """Take an online, integrity-checked backup of committed data."""
    with new_database(out) as staged:
        _copy(path, staged)


def restore(source: str | Path, target: str | Path) -> None:
    """Copy a backup with the expected schema to a new path."""
    with new_database(target) as staged:
        _copy(source, staged)
        check_schema(staged)
