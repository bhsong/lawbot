"""Private runtime files shared by export, the request queue and the publisher."""

import fcntl
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from law_calendar import settings

WITHDRAWAL = re.compile(r'withdraw-[0-9a-f]{32}')


class LockTimeout(TimeoutError):
    """The local state lock stayed busy past its deadline."""


def trusted_directory(path: Path, *, require_owner: bool = True) -> None:
    """Reject symlinks, foreign owners and writable ancestors (including system directories)."""
    if not path.is_absolute() or '..' in path.parts:
        raise OSError('Untrusted calendar publish directory')
    for directory in (*reversed(path.parents), path):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            raise OSError('Untrusted calendar publish directory')
    if require_owner and info.st_uid != os.getuid():
        raise OSError('Untrusted calendar publish directory')


def private_directory() -> Path:
    trusted_directory(settings.SERVICE)
    directory = settings.SERVICE / '.pages-publish'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise OSError('Invalid calendar publish directory')
    return directory


@contextmanager
def checked_file(path: Path, flags: int, *, private: bool = True) -> Iterator[int]:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or (mode != 0o600 if private else bool(mode & 0o022))
        ):
            raise OSError('Invalid calendar publish file')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def locked_file(path: Path, timeout: float) -> Iterator[int]:
    with checked_file(path, os.O_CREAT | os.O_RDWR) as fd:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeout from None
                time.sleep(min(remaining, 0.025))
            else:
                break
        yield fd


def output_lock_path() -> Path:
    return private_directory() / 'export.lock'


def _sync_directory(state: Path) -> None:
    fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mark_withdrawal(state: Path) -> None:
    # An independent generation survives a busy queue and cannot be cleared by an older export.
    with checked_file(state / f'withdraw-{uuid4().hex}', os.O_CREAT | os.O_EXCL | os.O_WRONLY) as fd:
        os.fsync(fd)
    _sync_directory(state)


def pending_withdrawals(state: Path) -> tuple[Path, ...]:
    pending = tuple(path for path in state.iterdir() if WITHDRAWAL.fullmatch(path.name))
    for path in pending:
        with checked_file(path, os.O_RDONLY):
            pass
    return pending


def clear_withdrawals(state: Path, pending: tuple[Path, ...]) -> None:
    for path in pending:
        with checked_file(path, os.O_RDONLY):
            path.unlink()
    if pending:
        _sync_directory(state)
