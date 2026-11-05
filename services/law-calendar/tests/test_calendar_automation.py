"""Deterministic effective dates and explicitly public, undated hold records."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from law_calendar.automation import resolve_date
from law_calendar.ingest import import_files
from law_calendar.server import create_app
from test_calendar_workflow import SOURCE, import_one
from test_calendar_workflow import workflow as workflow


def source(addendum='', metadata=None):
    return {
        'dates': {'promulgation': '2026-10-01', 'effective': '2026-10-01'},
        'context': {
            'source_url': SOURCE,
            'addenda_added': [addendum] if addendum else [],
            'lawgo': {'article_effective': metadata},
        },
    }


@pytest.mark.parametrize(
    'text,expected',
    [
        ('제1조(시행일) 이 법은 2026년 11월 5일부터 시행한다.', '2026-11-05'),
        ('제1조(시행일) 이 규칙은 공포한 날부터 시행한다.', '2026-10-01'),
        ('제1조(시행일) 이 법은 대통령령으로 정하는 날부터 시행한다.', None),
        ('제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다.', None),
        ('제1조(시행일) 이 법은 2026년 2월 30일부터 시행한다.', None),
        ('제1조(시행일) 이 법은 2026년 11월 5일부터 시행한다. 다만 제2조는 별도로 정한다.', None),
        ('제1조(시행일) 이 법은 공포한 날부터 시행한다. 제2조(경과조치) 종전 규정을 적용한다.', None),
        ('', None),
    ],
)
def test_only_supported_unambiguous_addenda_resolve(text, expected):
    assert resolve_date(source(text), '제1조')[0] == expected


def test_structured_article_dates_and_conflicts():
    raw = source(metadata='20261105:제1조20261201:제2조')
    assert resolve_date(raw, '제1조')[0] == '2026-11-05'
    assert resolve_date(raw, '제2조')[0] == '2026-12-01'
    assert resolve_date(raw, '제3조')[0] is None
    assert resolve_date(source(metadata='20261105:제1조제2항'), '제1조')[0] is None
    assert resolve_date(source(metadata='20261105:제1조20261201:제1조'), '제1조')[0] is None
    assert (
        resolve_date(source('제1조(시행일) 이 법은 대통령령으로 정하는 날부터 시행한다.', '20261105:제1조'), '제1조')[0]
        is None
    )


def test_multiple_addenda_and_unrecognized_metadata_are_held():
    raw = source(metadata='20261105:제1조')
    raw['context']['addenda_added'] = ['첫 번째 부칙', '두 번째 부칙']
    assert resolve_date(raw, '제1조')[0] is None
    assert resolve_date(source('시행일은 대통령령이 정한 날로 한다.', '20261105:제1조'), '제1조')[0] is None
    raw = source(metadata=['20261105:제1조'])
    assert resolve_date(raw, '제1조')[0] is None
    assert (
        resolve_date(source('제1조(시행일) 이 법은 2026년 11월 6일부터 시행한다.', '20261105:제1조'), '제1조')[0]
        is None
    )


def test_import_auto_publishes_dates_but_holds_unresolved_articles(workflow):
    db, home, path, event = workflow
    event['context']['lawgo']['article_effective'] = '20261105:제1조20261201:제3조'
    path.write_text(json.dumps(event), encoding='utf-8')
    import_one(workflow)
    with TestClient(create_app(db)) as client:
        assert client.get('/api/law-events?date=2026-11-05').json()['total'] == 1
        change = client.get('/api/law-events?date=2026-11-05').json()['items'][0]
        assert change['comparable'] is False
        assert change['status'] == '시행일 자동 확인 · 비교 미확인'
        held = client.get('/api/law-holds').json()
        assert held['total'] == 1 and held['items'][0]['article'] == '제2조'
        assert set(held['items'][0]) == {'id', 'lawName', 'article', 'reason', 'sourceUrl', 'collectedAt'}
        assert client.get('/api/law-calendar?month=2026-10').json()['days'] == []
        assert client.post('/api/law-holds', json={}).status_code == 405
        assert client.get('/api/law-holds?page_size=101').status_code == 422
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_audit WHERE decision='auto_publish'").fetchone()[0] == 1
    assert import_files(db, home, [path])['unchanged'] == 1


def test_holds_are_independent_of_calendar_dates_and_hide_private_or_withdrawn_records(workflow):
    db, home, path, event = workflow
    event['context']['amendment_text'] = (
        '部則ではなく\n부칙\n제1조(시행일) 이 법은 대통령령으로 정하는 날부터 시행한다.'
    )
    path.write_text(json.dumps(event), encoding='utf-8')
    event_id = import_one(workflow)
    with TestClient(create_app(db)) as client:
        result = client.get('/api/law-holds?page_size=1').json()
        assert result['total'] == 3 and len(result['items']) == 1
        assert '대통령령' in result['items'][0]['reason']
        assert 'snapshot' not in json.dumps(result) and str(home) not in json.dumps(result)
        assert client.get('/api/law-events?month=2026-11').json()['total'] == 0
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE change_events SET review_state='withdrawn' WHERE id=?", (event_id,))
        assert client.get('/api/law-holds').json()['total'] == 0
    assert import_files(db, home, [path])['unchanged'] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_changes').fetchone()[0] == 0


def test_generic_pending_rows_do_not_become_public_holds(dataset):
    with sqlite3.connect(dataset) as conn:
        conn.execute("UPDATE change_events SET review_state='pending'")
    with TestClient(create_app(dataset)) as client:
        assert client.get('/api/law-holds').json()['items'] == []


@pytest.mark.parametrize('state', ['rejected', 'withdrawn'])
def test_explicit_rejection_survives_source_corrections(workflow, state):
    db, home, path, event = workflow
    event_id = import_one(workflow)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE change_events SET review_state=? WHERE id=?', (state, event_id))
    event['context']['lawgo']['article_effective'] = '20261105:제1조'
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_files(db, home, [path])['imported'] == 1
    with TestClient(create_app(db)) as client:
        assert client.get('/api/law-events?month=2026-11').json()['total'] == 0
        assert client.get('/api/law-holds').json()['total'] == 0


def test_source_reclassification_moves_between_calendar_and_holds(workflow):
    db, home, path, event = workflow
    import_one(workflow)
    with TestClient(create_app(db)) as client:
        assert client.get('/api/law-holds').json()['total'] == 3
        event['context']['lawgo']['article_effective'] = ''
        event['context']['addenda_added'] = ['제1조(시행일) 이 법은 2026년 11월 5일부터 시행한다.']
        path.write_text(json.dumps(event), encoding='utf-8')
        import_files(db, home, [path])
        assert client.get('/api/law-holds').json()['total'] == 0
        assert client.get('/api/law-events?month=2026-11').json()['total'] == 3
        event['context']['addenda_added'] = ['제1조(시행일) 이 법은 대통령령으로 정하는 날부터 시행한다.']
        path.write_text(json.dumps(event), encoding='utf-8')
        import_files(db, home, [path])
        assert client.get('/api/law-holds').json()['total'] == 3
        assert client.get('/api/law-events?month=2026-11').json()['total'] == 0


def test_v2_upgrade_keeps_a_backup_and_existing_rows(tmp_path):
    from law_calendar.db import SCHEMA, SCHEMA_V2, migrate

    db = tmp_path / 'v2.db'
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA + SCHEMA_V2 + '\nPRAGMA user_version=2;')
        conn.execute("INSERT INTO laws VALUES ('existing','existing')")
    migrate(db)
    with sqlite3.connect(db.with_suffix('.db.pre-v3.backup')) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT name FROM laws').fetchone()[0] == 'existing'
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 3


from test_calendar_api import dataset as dataset
