"""Scheduled import reads committed monitor state without using model summary state."""

import json
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from law_calendar.assembly_ingest import import_assembly
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


def test_sync_skips_registered_events_outside_current_scope(scheduled, monkeypatch):
    from test_calendar_assembly import register

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
    for source in ('assembly_bill', 'assembly_notice'):
        event_id = register(home, state, source=source)
        path = home / f'{event_id}.json'
        assembly = json.loads(path.read_text(encoding='utf-8'))
        assembly['target']['law_name'] = '미추적법 일부개정법률안'
        path.write_text(json.dumps(assembly), encoding='utf-8')
    monkeypatch.setattr('law_calendar.notice_content.fetch_official', lambda _: pytest.fail('Unexpected notice fetch'))

    result = synchronize(calendar_db, home)

    assert result['failed'] == 0
    assert result['imported'] == result['skipped'] == 1
    assert result['assembly_bill_imported'] == result['assembly_notice_imported'] == 0


def test_sync_imports_new_notice_after_redetecting_backfilled_notice(scheduled, monkeypatch, calendar_network):
    from test_calendar_assembly import register
    from test_calendar_notice_content import detail_html

    (calendar_db, home, _, _), state = scheduled
    backfilled_id = register(home, state)
    event = json.loads((home / f'{backfilled_id}.json').read_text(encoding='utf-8'))
    backfilled_at = '2026-11-04T06:30:00+09:00'
    backfilled = {**event, 'detected_at': backfilled_at}

    changed_id = register(home, state, notice_id='PRC_CHANGED')
    changed_path = home / f'{changed_id}.json'
    changed_event = json.loads(changed_path.read_text(encoding='utf-8'))
    # The monitor hashes committee_id even though the calendar does not store it.
    changed_event['target']['committee_id'] = '1001'
    changed_path.write_text(json.dumps(changed_event), encoding='utf-8')
    backfilled_with_old_id = {
        **changed_event,
        'event_id': 'backfilled-with-old-committee-id',
        'detected_at': backfilled_at,
        'target': {**changed_event['target'], 'committee_id': '1000'},
    }
    backfills = [backfilled, backfilled_with_old_id]
    assert import_assembly(calendar_db, 'assembly_notice', backfilled_at, backfills) == (2, 0)

    new_id = register(home, state, notice_id='PRC_NEW')
    html = detail_html('2200012', '2026-11-04~2026-11-05', '주요내용<br/>안 제10조를 신설함.', pdf_url='')
    monkeypatch.setattr('law_calendar.notice_content.fetch_official', lambda _url: html.encode())

    result = synchronize(calendar_db, home)

    assert result['failed'] == result['assembly_notice_failed'] == 0
    assert result['assembly_notice_imported'] == 1
    assert result['assembly_notice_unchanged'] == 2
    with sqlite3.connect(calendar_db) as conn:
        stored = conn.execute('SELECT summary FROM assembly_notice_content WHERE snapshot_id=?', (new_id,)).fetchone()
        assert stored == ('안 제10조를 신설함.',)


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
    event['new']['file'] = 'not-yet-available.md'
    path.write_text(json.dumps(event), encoding='utf-8')
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
