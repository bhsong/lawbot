"""Exercise the shared administrator queue with a non-network publisher."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from calendar_publish_fixtures import offline_calendar as offline_calendar
from law_calendar import auto_publish, publish_state
from test_calendar_api import dataset as dataset
from test_calendar_export import inputs as inputs
from test_calendar_publish import config
from test_calendar_publish import service as service


def test_writes_coalesce_before_and_during_publish(inputs, service, monkeypatch):
    config(service, inputs)
    database = inputs[0]
    state = service / '.pages-publish'
    monkeypatch.delenv('LAWBOT_ENV_FILE')
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'untrusted-fixture')
    monkeypatch.setenv('LAWBOT_CALENDAR_ADMIN_TOKEN', 'untrusted-fixture')
    debouncing, allow_first, publishing, finish_first = (Event() for _ in range(4))
    calls = []
    workers = []

    def debounce(_seconds):
        if not debouncing.is_set():
            debouncing.set()
            assert allow_first.wait(5)

    def fake_run(args, *, env, **kwargs):
        assert set(env) == {'PATH', 'HOME'}
        assert kwargs['cwd'] == service
        calls.append(args)
        if len(calls) == 1:
            publishing.set()
            assert finish_first.wait(5)
        payload = {
            'status': 'skipped',
            'reason': 'pages_not_configured',
            'deploymentId': None,
            'deleted': 0,
            'files': 0,
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), '')

    with ThreadPoolExecutor(max_workers=2) as pool:

        def start_worker(runner, _log):
            workers.append(pool.submit(auto_publish._drain, state, os.dup(runner)))

        monkeypatch.setattr(auto_publish, '_start_worker', start_worker)
        monkeypatch.setattr(auto_publish, 'sleep', debounce)
        monkeypatch.setattr(auto_publish, 'run_group', fake_run)
        auto_publish.schedule(database)
        assert debouncing.wait(5)
        auto_publish.schedule(database)
        allow_first.set()
        assert publishing.wait(5)
        auto_publish.schedule(database)
        auto_publish.schedule(database, purge=True)
        auto_publish.schedule(database)
        finish_first.set()
        [worker] = workers
        worker.result(timeout=5)
        [first, second] = calls
        assert first[-2:] == ['law_calendar.server', 'publish']
        assert second == first
        assert len(publish_state.pending_withdrawals(state)) == 1
        auto_publish.schedule(database)
        workers[-1].result(timeout=5)
        assert calls == [first, second, first]


def test_only_the_configured_db_can_schedule_even_with_ambient_settings(inputs, service, monkeypatch):
    config(service, inputs)
    foreign = service / 'synthetic.db'
    foreign.touch()
    database = inputs[0]
    monkeypatch.setenv('LAW_CALENDAR_DB', str(foreign))
    monkeypatch.setenv('LAWBOT_CALENDAR_PAGES_PROJECT', 'fixture')
    monkeypatch.delenv('LAWBOT_ENV_FILE')
    worker = []
    monkeypatch.setattr(auto_publish, '_start_worker', lambda *_: worker.append(True))
    auto_publish.schedule(foreign, purge=True)
    assert not (service / '.pages-publish').exists()
    monkeypatch.setenv('LAWBOT_ENV_FILE', '')
    auto_publish.schedule(database, purge=True)
    assert not (service / '.pages-publish').exists()
    monkeypatch.delenv('LAWBOT_ENV_FILE')
    alias = service / 'database-alias'
    alias.symlink_to(database)
    auto_publish.schedule(alias, purge=True)
    assert worker == [True]
    assert len(publish_state.pending_withdrawals(service / '.pages-publish')) == 1


def test_disabled_project_does_not_create_queue_from_environment(inputs, service, monkeypatch):
    config(service, inputs, project='')
    monkeypatch.setenv('LAWBOT_CALENDAR_PAGES_PROJECT', 'fixture')
    monkeypatch.delenv('LAWBOT_ENV_FILE')
    auto_publish.schedule(inputs[0], purge=True)
    assert not (service / '.pages-publish').exists()


def test_busy_queue_times_out_but_a_withdrawal_remains_pending(inputs, service, monkeypatch, caplog):
    config(service, inputs)
    monkeypatch.delenv('LAWBOT_ENV_FILE')
    monkeypatch.setattr(auto_publish, 'QUEUE_TIMEOUT', 0.01)
    state = publish_state.private_directory()
    with publish_state.locked_file(state / 'pending', 0):
        auto_publish.schedule(inputs[0], purge=True)
    assert 'calendar_publish_queue_timeout' in caplog.messages
    assert not (state / 'worker.lock').exists()
    assert len(publish_state.pending_withdrawals(state)) == 1


def test_queue_rejects_a_hardlinked_file(inputs, service, monkeypatch, caplog):
    config(service, inputs)
    monkeypatch.delenv('LAWBOT_ENV_FILE')
    state = publish_state.private_directory()
    victim = service / 'unrelated'
    victim.touch(mode=0o600)
    os.link(victim, state / 'pending')
    auto_publish.schedule(inputs[0])
    assert 'calendar_publish_start_failed' in caplog.messages
    assert not (state / 'worker.lock').exists()
    assert victim.read_bytes() == b''


def test_worker_reports_only_fixed_error_codes(monkeypatch, caplog):
    def result(args, **kwargs):
        payload = {
            'status': 'failed',
            'error': next(errors),
            'deploymentId': None,
            'deleted': 0,
            'files': 0,
        }
        return subprocess.CompletedProcess(args, 1, json.dumps(payload), 'untrusted error')

    errors = iter(['invalid_pages_credentials', 'untrusted diagnostic'])
    monkeypatch.setattr(auto_publish, 'run_group', result)
    auto_publish._publish()
    auto_publish._publish()
    assert caplog.messages == [
        'calendar_publish_failed: invalid_pages_credentials',
        'calendar_publish_failed: publish_failed',
    ]
