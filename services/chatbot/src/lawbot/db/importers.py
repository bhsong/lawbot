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


def _compare(restored: dict[str, bytes], sources: dict[str, Path]) -> list[str]:
    if restored.keys() != sources.keys():
        raise ValueError('원본과 복원 파일 이름이 다릅니다.')
    lines = []
    for name, raw in restored.items():
        if (ROOT / sources[name]).read_bytes() != raw:
            raise ValueError(f'{name}의 원본과 복원 바이트가 다릅니다.')
        lines.append(f'  {name} sha256 {sha256(raw)}')
    return lines


def verify_imports(path: Path) -> tuple[bool, list[str]]:
    """Compare reconstructed corpora and QA datasets with their recorded sources."""
    check_schema(path)
    lines = []
    ok = True
    with connect(path) as conn:
        conn.execute('BEGIN')
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            return False, ['FAIL 외래 키 검사']
        for name, directory in conn.execute('SELECT release_id, origin FROM corpus_releases ORDER BY release_id'):
            try:
                restored = release_bytes(conn, name)
                details = _compare(restored, {file: Path(directory) / file for file in restored})
            except (OSError, ValueError, TypeError, KeyError, DatabaseError) as exc:
                ok = False
                lines.append(f'FAIL corpus {name} · {exc}')
            else:
                lines.extend((f'OK corpus {name}', *details))
        for (name,) in conn.execute('SELECT split_set FROM split_sets ORDER BY split_set'):
            if name not in DATASETS:
                continue
            try:
                details = _compare(dataset_bytes(conn, name), dataset_sources(conn, name))
            except (OSError, ValueError, TypeError, KeyError, DatabaseError) as exc:
                ok = False
                lines.append(f'FAIL qa {name} · {exc}')
            else:
                lines.extend((f'OK qa {name}', *details))
    return (ok, lines) if lines else (False, ['FAIL 대조할 조문 릴리스 또는 QA 세트가 없습니다.'])
