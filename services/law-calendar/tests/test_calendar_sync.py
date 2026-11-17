"""Scheduled import reads committed monitor state without using model summary state."""

import json
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from law_calendar import ingest
from law_calendar.import_failures import list_import_failures
from law_calendar.server import create_app
from law_calendar.sync import synchronize
from test_calendar_workflow import NOW
from test_calendar_workflow import workflow as workflow

pytest_plugins = ['calendar_test_fixtures']
FINISHED = '2026-11-05T06:31:00+09:00'


@pytest.fixture
def scheduled(workflow):
    calendar_db, home, event_file, event = workflow
    event['detected_at'] = '2026-11-05T06:30:30+09:00'
    event['target']['item_id'] = 'kr/근로기준법/법률.md'
    event_file.write_text(json.dumps(event), encoding='utf-8')
    state = home / 'data' / 'state.db'
    summary = {
        'collectors': {'legalize_law': {'status': 'ok', 'events': 1}, 'legalize_admrule': {'status': 'ok', 'events': 0}}
    }
    with sqlite3.connect(state) as conn:
        conn.executescript(
            'CREATE TABLE events(event_id TEXT,source TEXT,change_type TEXT,json_path TEXT,created_at TEXT,processed_at TEXT);'
            'CREATE TABLE runs(run_id TEXT,mode TEXT,started_at TEXT,finished_at TEXT,status TEXT,summary_json TEXT);'
        )
        conn.execute(
            'INSERT INTO runs VALUES (?,?,?,?,?,?)',
            ('collection', 'normal', '2026-11-05T06:30:00+09:00', FINISHED, 'ok', json.dumps(summary)),
        )
        conn.execute(
            'INSERT INTO events VALUES (?,?,?,?,?,?)',
            (event['event_id'], event['source'], event['change_type'], 'event.json', event['detected_at'], NOW),
        )
        conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('summary', 'summarize', NOW, NOW, 'failed', '{}'))
    return workflow, state


def test_sync_imports_registered_law_events_even_if_model_summary_already_processed_them(scheduled):
    (calendar_db, home, path, event), state = scheduled
    before = state.read_bytes()
    result = synchronize(calendar_db, home)
    assert result == {'imported': 1, 'unchanged': 0, 'failed': 0, 'skipped': 0}
    assert synchronize(calendar_db, home)['unchanged'] == 1
    assert state.read_bytes() == before
    with TestClient(create_app(calendar_db)) as client:
        assert client.get('/api/law-holds').json()['total'] == 3
        assert client.get('/api/law-calendar/status').json()['collectedAt'] == FINISHED
        assert client.get('/api/law-events?month=2026-11&type=notice').json()['total'] == 0


def test_managed_run_skips_intentionally_disabled_law_import(scheduled):
    (database, home, _, _), state = scheduled
    assert synchronize(database, home)['imported'] == 1
    with sqlite3.connect(state) as conn:
        conn.execute(
            'INSERT INTO runs VALUES (?,?,?,?,?,?)',
            (
                'managed',
                'normal',
                '2026-11-06T06:30:00+09:00',
                '2026-11-06T06:31:00+09:00',
                'ok',
                json.dumps({'managed': True, 'collectors': {}, 'events': 0}),
            ),
        )
    assert synchronize(database, home) == {'imported': 0, 'unchanged': 0, 'failed': 0, 'skipped': 0}
    with TestClient(create_app(database)) as client:
        assert client.get('/api/law-calendar/status').json()['collectedAt'] == FINISHED


def test_sync_counts_missing_json_of_an_imported_event_as_unchanged(scheduled):
    (calendar_db, home, path, _), _ = scheduled
    assert synchronize(calendar_db, home)['imported'] == 1
    path.unlink()
    result = synchronize(calendar_db, home)
    assert result['failed'] == result['imported'] == 0
    assert result['unchanged'] == 1


def test_sync_does_not_revalidate_imported_events_after_model_change(scheduled, monkeypatch):
    (calendar_db, home, _, _), state = scheduled
    assert synchronize(calendar_db, home)['imported'] == 1
    monkeypatch.setattr(ingest, 'PARSER_VERSION', 'new-model-version')
    result = synchronize(calendar_db, home)
    assert result['failed'] == result['imported'] == 0
    assert result['unchanged'] == 1
    with sqlite3.connect(calendar_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM source_snapshots').fetchone() == (1,)


def test_sync_records_failed_registered_event_and_retries_it(scheduled):
    (calendar_db, home, path, law), state = scheduled
    event_id = law['event_id']
    secret = 'synthetic-password-marker'
    with sqlite3.connect(state) as conn:
        conn.execute('UPDATE events SET json_path=? WHERE event_id=?', (f'{secret}.json', event_id))
    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            synchronize(calendar_db, home)

    with sqlite3.connect(calendar_db) as conn:
        errors = [
            error
            for (raw,) in conn.execute('SELECT errors_json FROM import_runs WHERE failed > 0')
            for error in json.loads(raw)
        ]
    assert len(errors) == 2
    for error in errors:
        assert error['source'] == 'legalize_law'
        assert error['event_id'] == event_id
        assert error['error_type'] == 'FileNotFoundError'
        assert 'No such file' in error['message']
        assert secret not in json.dumps(error)

    with sqlite3.connect(state) as conn:
        conn.execute('UPDATE events SET json_path=? WHERE event_id=?', (path.name, event_id))
    result = synchronize(calendar_db, home)
    assert result['failed'] == 0
    assert result['imported'] == 1


def test_import_failure_page_excludes_legacy_diagnostics(scheduled):
    (database, home, path, event), state = scheduled
    original = path.read_text(encoding='utf-8')
    path.unlink()
    with pytest.raises(FileNotFoundError):
        synchronize(database, home)
    path.write_text('{broken JSON', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        synchronize(database, home)
    path.write_text(original, encoding='utf-8')

    with sqlite3.connect(database) as conn:
        conn.executemany(
            'INSERT INTO import_runs VALUES (?,?,?,?,?,?,?)',
            [
                ('legacy', NOW, NOW, 0, 0, 1, '[{"file":"/private/old.json","error":"private diagnostic"}]'),
                ('broken', NOW, NOW, 0, 0, 1, 'invalid json'),
            ],
        )
    first = list_import_failures(database, 1, 1)
    second = list_import_failures(database, 2, 1)
    assert (first['total'], first['page'], first['page_size']) == (2, 1, 1)
    assert (second['total'], second['page'], second['page_size']) == (2, 2, 1)
    assert first['items'][0]['error_type'] == 'JSONDecodeError'
    assert second['items'][0]['error_type'] == 'FileNotFoundError'
    assert list_import_failures(database, 3, 1)['items'] == []


def test_sync_skips_registered_events_outside_current_scope(scheduled):
    (calendar_db, home, _, event), state = scheduled
    outside = {
        **event,
        'event_id': 'outside',
        'target': {'law_id': 'outside', 'law_name': '미추적법', 'item_id': 'kr/미추적법/법률.md'},
    }
    (home / 'outside.json').write_text(json.dumps(outside), encoding='utf-8')
    with sqlite3.connect(state) as conn:
        conn.execute(
            'INSERT INTO events VALUES (?,?,?,?,?,?)',
            ('outside', outside['source'], outside['change_type'], 'outside.json', outside['detected_at'], NOW),
        )

    result = synchronize(calendar_db, home)

    assert result['failed'] == 0
    assert result['imported'] == result['skipped'] == 1


def test_successful_collection_without_events_updates_freshness(scheduled):
    (calendar_db, home, _, _), state = scheduled
    with sqlite3.connect(state) as conn:
        conn.execute('DELETE FROM events WHERE event_id=?', ('upstream-id',))
    assert synchronize(calendar_db, home) == {'imported': 0, 'unchanged': 0, 'failed': 0, 'skipped': 0}


def test_metadata_only_events_do_not_permanently_fail_the_daily_import(scheduled):
    (calendar_db, home, path, event), state = scheduled
    event['diff']['chunks'] = [{'article': '머리', 'kind': 'changed', 'diff': 'metadata only'}]
    path.write_text(json.dumps(event), encoding='utf-8')
    result = synchronize(calendar_db, home)
    assert result['failed'] == 0 and result['skipped'] == 1 and result['imported'] == 0
    with TestClient(create_app(calendar_db)) as client:
        assert client.get('/api/law-calendar/status').json()['collectedAt'] == FINISHED
    with TestClient(create_app(calendar_db)) as client:
        assert client.get('/api/law-calendar/status').json()['collectedAt'] == FINISHED


@pytest.mark.parametrize('failure', ['failed', 'missing', 'running'])
def test_failed_or_missing_law_collection_does_not_claim_success(scheduled, failure):
    (calendar_db, home, _, _), state = scheduled
    with sqlite3.connect(state) as conn:
        summary = {'collectors': {'legalize_law': {'status': 'failed'}}} if failure == 'failed' else {}
        conn.execute(
            'UPDATE runs SET summary_json=?,finished_at=? WHERE run_id=?',
            (json.dumps(summary), None if failure == 'running' else FINISHED, 'collection'),
        )
    with pytest.raises(ValueError):
        synchronize(calendar_db, home)
    with sqlite3.connect(calendar_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_status').fetchone()[0] == 0


def test_other_sources_and_orphan_files_are_excluded(scheduled):
    (calendar_db, home, event_file, event), state = scheduled
    (home / 'unregistered.json').write_text('{bad', encoding='utf-8')
    with sqlite3.connect(state) as conn:
        conn.execute('UPDATE events SET source=? WHERE event_id=?', ('legalize_admrule', event['event_id']))
    assert synchronize(calendar_db, home)['imported'] == 0


@pytest.mark.parametrize('problem', ['path', 'identity', 'missing_file', 'replay'])
def test_invalid_registered_paths_and_identities_fail_without_freshness(scheduled, problem):
    (calendar_db, home, event_file, event), state = scheduled
    with sqlite3.connect(state) as conn:
        if problem == 'replay':
            event['replay'] = True
            event_file.write_text(json.dumps(event), encoding='utf-8')
        elif problem == 'identity':
            conn.execute('UPDATE events SET event_id=? WHERE event_id=?', ('different', event['event_id']))
        else:
            conn.execute(
                'UPDATE events SET json_path=? WHERE event_id=?',
                ('../outside.json' if problem == 'path' else 'missing.json', event['event_id']),
            )
    with pytest.raises((ValueError, OSError)):
        synchronize(calendar_db, home)
    with sqlite3.connect(calendar_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_status').fetchone()[0] == 0


def test_failure_of_an_unrelated_collector_does_not_block_law_import(scheduled):
    (calendar_db, home, _, _), state = scheduled
    with sqlite3.connect(state) as conn:
        summary = {'collectors': {'legalize_law': {'status': 'ok'}, 'legalize_admrule': {'status': 'failed'}}}
        conn.execute(
            'UPDATE runs SET status=?,summary_json=? WHERE run_id=?', ('partial', json.dumps(summary), 'collection')
        )
    assert synchronize(calendar_db, home)['imported'] == 1


def test_import_failure_is_retryable_and_keeps_previous_freshness(scheduled):
    (calendar_db, home, path, event), state = scheduled
    synchronize(calendar_db, home)
    with sqlite3.connect(state) as conn:
        conn.execute('UPDATE runs SET finished_at=? WHERE run_id=?', ('2026-11-06T06:31:00+09:00', 'collection'))
    event['event_id'] = 'not-yet-imported'
    event['new']['file'] = 'not-yet-available.md'
    path = home / 'next-event.json'
    path.write_text(json.dumps(event), encoding='utf-8')
    with sqlite3.connect(state) as conn:
        conn.execute(
            'INSERT INTO events VALUES (?,?,?,?,?,?)',
            (event['event_id'], event['source'], event['change_type'], path.name, FINISHED, None),
        )
    assert synchronize(calendar_db, home)['failed'] == 1
    with TestClient(create_app(calendar_db)) as client:
        assert client.get('/api/law-calendar/status').json()['collectedAt'] == FINISHED
    event['new']['file'] = 'data/files/new.md'
    path.write_text(json.dumps(event), encoding='utf-8')
    assert synchronize(calendar_db, home)['failed'] == 0
    with TestClient(create_app(calendar_db)) as client:
        assert client.get('/api/law-calendar/status').json()['collectedAt'] == '2026-11-06T06:31:00+09:00'


def test_missing_databases_are_never_created(tmp_path):
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.Error):
        synchronize(missing, tmp_path / 'monitor')
    assert not missing.exists()


def test_cli_failure_is_a_failed_check_not_a_warning(scheduled):
    (calendar_db, home, _, _), state = scheduled
    base = [sys.executable, '-m', 'law_calendar.sync', '--database', str(calendar_db), '--monitor-home', str(home)]
    assert subprocess.run(base, capture_output=True, text=True, check=False).returncode == 0
    with sqlite3.connect(state) as conn:
        conn.execute('UPDATE runs SET status=? WHERE run_id=?', ('failed', 'collection'))
        conn.execute('UPDATE runs SET summary_json=? WHERE run_id=?', ('{}', 'collection'))
    result = subprocess.run(base, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and 'calendar sync failed' in result.stderr


def test_v9_sync_ignores_retired_collectors_and_has_only_law_counters(scheduled):
    (database, home, _, _), state = scheduled
    with sqlite3.connect(state) as conn:
        conn.execute(
            "UPDATE runs SET summary_json=? WHERE mode='normal'",
            (
                json.dumps(
                    {
                        'collectors': {
                            'legalize_law': {'status': 'ok'},
                            'assembly_bill': {'status': 'failed'},
                            'assembly_notice': {'status': 'failed'},
                        }
                    }
                ),
            ),
        )
        conn.execute(
            'INSERT INTO events VALUES (?,?,?,?,?,?)',
            ('retired', 'assembly_notice', 'assembly_notice_observed', 'missing.json', NOW, None),
        )
    assert synchronize(database, home) == {'imported': 1, 'unchanged': 0, 'failed': 0, 'skipped': 0}


@pytest.mark.parametrize('failed,expected', [(0, 0), (1, 2)])
def test_v9_sync_exit_code_depends_only_on_law_failures(scheduled, monkeypatch, capsys, failed, expected):
    from law_calendar import sync

    (database, home, _, _), _ = scheduled
    monkeypatch.setattr(sys, 'argv', ['sync', '--database', str(database), '--monitor-home', str(home)])
    monkeypatch.setattr(
        sync,
        'synchronize',
        lambda *_: {
            'imported': 0,
            'unchanged': 0,
            'failed': failed,
            'skipped': 0,
            'notice_pending': 1,
        },
    )
    assert sync.main() == expected
    assert 'calendar sync imported=0 unchanged=0' in capsys.readouterr().out
