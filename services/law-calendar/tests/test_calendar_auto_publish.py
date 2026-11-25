"""Exercise the shared administrator queue with a non-network publisher."""

import fcntl
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from calendar_publish_fixtures import offline_calendar as offline_calendar
from law_calendar import auto_publish
from test_calendar_api import dataset as dataset
from test_calendar_export import inputs as inputs
from test_calendar_publish import PUBLIC_STATUS, fake_wrangler


def test_writes_coalesce_before_and_during_publish_and_purge_is_sticky(tmp_path, monkeypatch):
    for key, value in {
        'LAWBOT_CALENDAR_PAGES_PROJECT': 'calendar-fixture',
        'CLOUDFLARE_ACCOUNT_ID': 'fixture-account',
        'CLOUDFLARE_API_TOKEN': 'fixture-token',
        'LAWBOT_CALENDAR_SITE': str(tmp_path / 'site'),
    }.items():
        monkeypatch.setenv(key, value)
    database = tmp_path / 'calendar.db'
    daily = tmp_path / 'daily.json'
    debouncing, allow_first, publishing, finish_first = (Event() for _ in range(4))
    calls = []
    workers = []

    def debounce(_seconds):
        if not debouncing.is_set():
            debouncing.set()
            assert allow_first.wait(5)

    def fake_run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            publishing.set()
            assert finish_first.wait(5)
        return subprocess.CompletedProcess(args, 0)

    with ThreadPoolExecutor(max_workers=2) as pool:

        def start_worker(queue, runner, log, args):
            workers.append(pool.submit(auto_publish._drain, queue, os.dup(runner), args))

        monkeypatch.setattr(auto_publish, '_start_worker', start_worker)
        monkeypatch.setattr(auto_publish, 'sleep', debounce)
        monkeypatch.setattr(auto_publish.subprocess, 'run', fake_run)
        auto_publish.schedule(database, daily_feed=daily)
        assert debouncing.wait(5)
        auto_publish.schedule(database, daily_feed=daily)
        allow_first.set()
        assert publishing.wait(5)
        auto_publish.schedule(database, daily_feed=daily)
        auto_publish.schedule(database, daily_feed=daily, purge=True)
        auto_publish.schedule(database, daily_feed=daily)
        finish_first.set()
        [worker] = workers
        worker.result(timeout=5)
        [first, second] = calls
        assert first[-7:] == [
            'publish',
            '--database',
            str(database),
            '--out',
            str(tmp_path / 'site'),
            '--erp-daily-feed',
            str(daily),
        ]
        assert second == [*first, '--purge-history']
        auto_publish.schedule(database, daily_feed=daily)
        workers[-1].result(timeout=5)
        assert calls == [first, second, first]


def test_missing_pages_setting_does_not_start_automatic_publish(tmp_path, monkeypatch):
    monkeypatch.setenv('LAWBOT_CALENDAR_PAGES_PROJECT', 'calendar-fixture')
    monkeypatch.setenv('CLOUDFLARE_ACCOUNT_ID', 'fixture-account')
    monkeypatch.delenv('CLOUDFLARE_API_TOKEN', raising=False)
    monkeypatch.setenv('LAWBOT_CALENDAR_SITE', str(tmp_path / 'uncreated/site'))
    auto_publish.schedule(tmp_path / 'calendar.db', purge=True)
    assert list(tmp_path.iterdir()) == []


def test_detached_worker_publishes_and_logs_only_a_fixed_failure(inputs, monkeypatch):
    fake_wrangler(inputs, monkeypatch, fail=True)
    db, dist, feed, _, out = inputs
    monkeypatch.setenv('LAWBOT_CALENDAR_SITE', str(out))
    monkeypatch.setenv('LAWBOT_CALENDAR_FRONTEND_DIST', str(dist))
    auto_publish.schedule(db, daily_feed=feed)
    worker = out.with_name('.site.publish.worker')
    deadline = time.monotonic() + 10
    with worker.open() as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                assert time.monotonic() < deadline, 'background publisher did not exit'
                time.sleep(0.025)
            else:
                break
    assert (out / PUBLIC_STATUS).is_file()
    assert out.with_name('.site.publish.log').read_text().splitlines() == ['calendar_publish_failed']
