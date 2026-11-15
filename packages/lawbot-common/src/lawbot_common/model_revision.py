"""Fingerprint local model assets using file names, sizes, and boundary bytes."""

import hashlib
from pathlib import Path

EDGE = 1 << 20  # 파일마다 앞뒤로 읽는 바이트


def fingerprint(path):
    """'sha256:<16자>' 또는 폴더가 없으면(경로 없음 포함) None"""
    if path is None or not Path(path).is_dir():
        return None
    root = Path(path)
    h = hashlib.sha256()
    files = sorted(
        p
        for p in root.rglob('*')
        if p.is_file() and not any(part.startswith('.') for part in p.relative_to(root).parts)
    )
    for p in files:
        size = p.stat().st_size
        h.update(f'{p.relative_to(root).as_posix()}\0{size}\0'.encode())
        with p.open('rb') as f:
            h.update(f.read(EDGE))
            if size > EDGE:
                f.seek(max(EDGE, size - EDGE))
                h.update(f.read(EDGE))
    return 'sha256:' + h.hexdigest()[:16]
