"""Coalesce committed reviews across the calendar server and local review CLI."""

import fcntl
import logging
import os
import subprocess
import sys
from pathlib import Path
from threading import Thread
from time import sleep

from law_calendar import settings
from law_calendar.publish_state import LockTimeout, checked_file, locked_file, mark_withdrawal, private_directory
from lawbot_common.calendar_publish import PUBLISH_CALLER_TIMEOUT, read_report
from lawbot_common.process import child_environment, run_group

LOG = logging.getLogger('law_calendar.auto_publish')
DEBOUNCE = 0.5


QUEUE_TIMEOUT = 0.25


def _start_worker(runner: int, log: int) -> None:
    process = subprocess.Popen(
        [sys.executable, '-I', '-m', 'law_calendar.auto_publish', str(runner)],
        cwd=settings.SERVICE,
        env=child_environment(),
        pass_fds=(runner,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log,
        start_new_session=True,
    )
    Thread(target=process.wait, daemon=True).start()


def schedule(database: Path, *, purge: bool = False) -> None:
    """Request a publish after a commit to the configured database only."""
    if os.environ.get('LAWBOT_ENV_FILE') == '':
        return
    try:
        config = settings.publish_config()
        configured = config.get(settings.DB_KEY)
        if not config.get(settings.PROJECT_KEY, '').strip():
            return
        if not configured:
            raise ValueError
        target = settings.SERVICE / Path(configured).expanduser()
        if database.resolve(strict=True) != target.resolve(strict=True):
            return
    except OSError, RuntimeError, ValueError:
        LOG.warning('calendar_publish_config_failed')
        return
    try:
        state = private_directory()
        if purge:
            mark_withdrawal(state)
        with locked_file(state / 'pending', QUEUE_TIMEOUT) as pending:
            os.pwrite(pending, b'1', 0)
            with checked_file(state / 'worker.lock', os.O_CREAT | os.O_RDWR) as runner:
                try:
                    fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return
                with checked_file(state / 'worker.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND) as log:
                    _start_worker(runner, log)
    except LockTimeout:
        LOG.warning('calendar_publish_queue_timeout')
    except OSError:
        LOG.warning('calendar_publish_start_failed')


def _publish() -> None:
    try:
        result = run_group(
            [sys.executable, '-I', '-m', 'law_calendar.server', 'publish'],
            cwd=settings.SERVICE,
            env=child_environment(),
            timeout=PUBLISH_CALLER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        LOG.warning('calendar_publish_timeout')
    except OSError, UnicodeError:
        LOG.warning('calendar_publish_start_failed')
    else:
        report = read_report(result)
        if report['status'] == 'failed':
            LOG.warning('calendar_publish_failed: %s', report['error'])


def _drain(state: Path, runner: int) -> None:
    try:
        while True:
            sleep(DEBOUNCE)
            with locked_file(state / 'pending', QUEUE_TIMEOUT) as fd:
                if not os.pread(fd, 1, 0):
                    # Release under the queue lock so the next submitter can start a worker.
                    fcntl.flock(runner, fcntl.LOCK_UN)
                    return
                os.ftruncate(fd, 0)
            _publish()
    except LockTimeout:
        LOG.warning('calendar_publish_queue_timeout')
    except OSError:
        LOG.warning('calendar_publish_queue_failed')
    finally:
        os.close(runner)


if __name__ == '__main__':
    _drain(private_directory(), int(sys.argv[1]))
