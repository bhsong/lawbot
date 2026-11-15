"""Read the current generation pointer without pulling in offline index tools."""

import json
import re
from functools import lru_cache
from pathlib import Path

ACTIVE_FILE = 'active.json'


def pointer_identity(path: Path) -> tuple:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@lru_cache(maxsize=32)
def read_revision(path: Path, identity: tuple) -> str | None:
    try:
        pointer = json.loads(path.read_bytes())
        if pointer_identity(path) != identity:
            return None
    except OSError, ValueError:
        return None
    revision = pointer.get('index_revision') if isinstance(pointer, dict) else None
    return revision if isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{64}', revision) else None


def active_revision(directory: Path) -> str | None:
    path = directory / ACTIVE_FILE
    try:
        return read_revision(path, pointer_identity(path))
    except OSError:
        return None
