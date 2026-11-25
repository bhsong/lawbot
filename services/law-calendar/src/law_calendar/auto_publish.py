"""Coalesce committed reviews across the calendar server and local review CLI."""

import fcntl
import logging
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from time import sleep

from law_calendar.settings import PUBLISH_TIMEOUT, PagesSettings, site_path

LOG = logging.getLogger('law_calendar.auto_publish')
DEBOUNCE = 0.5


@contextmanager
def _file(path: Path, flags: int = 0) -> Iterator[int]:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('Invalid calendar publish queue file')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _pending(path: Path) -> Iterator[int]:
    with _file(path) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd


def _start_worker(queue: Path, runner: int, log: int, args: list[str]) -> None:
    process = subprocess.Popen(
        [sys.executable, '-m', 'law_calendar.auto_publish', str(queue), str(runner), *args],
        pass_fds=(runner,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log,
        start_new_session=True,
    )
    Thread(target=process.wait, daemon=True).start()


def schedule(database: Path, *, daily_feed: Path | None = None, purge: bool = False) -> None:
    """Queue a publish after commit without waiting for an active publisher."""
    if PagesSettings.from_environment() is None:
        return
    try:
        out = site_path().expanduser().absolute()
        out = out.parent.resolve() / out.name
        out.parent.mkdir(parents=True, exist_ok=True)
        queue = out.with_name(f'.{out.name}.publish.pending')
        with _pending(queue) as pending, _file(queue.with_suffix('.worker')) as runner:
            if purge or os.pread(pending, 1, 0) != b'1':
                os.pwrite(pending, b'1' if purge else b'0', 0)
            try:
                fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            args = [
                sys.executable,
                '-m',
                'law_calendar.server',
                'publish',
                '--database',
                str(database),
                '--out',
                str(out),
            ]
            if daily_feed is not None:
                args.extend(('--erp-daily-feed', str(daily_feed)))
            with _file(queue.with_suffix('.log'), os.O_APPEND) as log:
                _start_worker(queue, runner, log, args)
    except OSError, RuntimeError, ValueError:
        LOG.warning('calendar_publish_start_failed')


def _publish(args: list[str], *, purge: bool) -> None:
    try:
        result = subprocess.run(
            [*args, '--purge-history'] if purge else args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PUBLISH_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        LOG.warning('calendar_publish_timeout')
    except OSError:
        LOG.warning('calendar_publish_start_failed')
    else:
        if result.returncode != 0:
            LOG.warning('calendar_publish_failed')


def _drain(queue: Path, runner: int, args: list[str]) -> None:
    try:
        while True:
            sleep(DEBOUNCE)
            with _pending(queue) as fd:
                signal = os.pread(fd, 1, 0)
                if not signal:
                    # Release under the queue lock so the next submitter can start a worker.
                    fcntl.flock(runner, fcntl.LOCK_UN)
                    return
                os.ftruncate(fd, 0)
            _publish(args, purge=signal == b'1')
    except OSError:
        LOG.warning('calendar_publish_queue_failed')
    finally:
        os.close(runner)


if __name__ == '__main__':
    _drain(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3:])
