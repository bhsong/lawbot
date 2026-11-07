from collections.abc import Callable
from pathlib import Path
from sqlite3 import Connection
from sqlite3 import Error as DatabaseError

from lawbot.db.connection import connect
from lawbot.db.corpus_import import release_bytes
from lawbot.db.csv_import import sha256
from lawbot.db.dataset_import import DATASETS, dataset_bytes, dataset_sources
from lawbot.db.migrate import check_schema
from lawbot.paths import ROOT


def apply_import(path: Path, write: Callable[[Connection], int]) -> int:
    """Open a current-schema database and run one prepared import."""
    check_schema(path)
    with connect(path, write=True) as conn:
        return write(conn)


def _compare(restored: dict[str, bytes], sources: dict[str, Path]) -> tuple[int, list[str]]:
    if restored.keys() != sources.keys():
        raise ValueError('원본과 복원 파일 이름이 다릅니다.')
    status, lines = 0, []
    for name, raw in restored.items():
        lines.append(f'  {name} sha256 {sha256(raw)}')
        try:
            source = (ROOT / sources[name]).read_bytes()
        except FileNotFoundError:
            source_status = 'MISSING'
        except OSError:
            source_status = 'UNREADABLE'
        else:
            if source == raw:
                continue
            source_status = 'CHANGED'
        status = 1
        lines.append(f'  SOURCE {source_status} {name}')
    return status, lines


def verify_imports(path: Path) -> tuple[int, list[str]]:
    """Verify restored data, reporting source drift separately from DB failure."""
    check_schema(path)
    lines = []
    status = 0
    with connect(path) as conn:
        conn.execute('BEGIN')
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            return 2, ['FAIL 외래 키 검사']
        for name, directory in conn.execute('SELECT release_id, origin FROM corpus_releases ORDER BY release_id'):
            try:
                restored = release_bytes(conn, name)
                source_status, details = _compare(restored, {file: Path(directory) / file for file in restored})
            except (ValueError, TypeError, KeyError, DatabaseError) as exc:
                status = 2
                lines.append(f'FAIL corpus {name} · {exc}')
            else:
                status = max(status, source_status)
                lines.extend((f'OK corpus {name}', *details))
        for (name,) in conn.execute('SELECT split_set FROM split_sets ORDER BY split_set'):
            if name not in DATASETS:
                continue
            try:
                source_status, details = _compare(dataset_bytes(conn, name), dataset_sources(conn, name))
            except (ValueError, TypeError, KeyError, DatabaseError) as exc:
                status = 2
                lines.append(f'FAIL qa {name} · {exc}')
            else:
                status = max(status, source_status)
                lines.extend((f'OK qa {name}', *details))
    return (status, lines) if lines else (2, ['FAIL 대조할 조문 릴리스 또는 QA 세트가 없습니다.'])
