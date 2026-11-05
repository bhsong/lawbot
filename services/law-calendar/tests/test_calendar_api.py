"""Publication and date contracts using synthetic records in disposable databases."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from law_calendar.db import migrate, read_database
from law_calendar.server import create_app


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / 'calendar.db'
    migrate(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO laws VALUES ('law','[예시] 가상법')")
        for kind in ('notice', 'effective'):
            conn.execute(
                'INSERT INTO change_events VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    kind,
                    'law',
                    kind,
                    '예시',
                    'approved',
                    '2026-11-05',
                    '2026-10-01',
                    None,
                    'https://www.law.go.kr/법령/예시',
                    '2026-11-05T10:00:00+09:00',
                    '2026-11-05T11:00:00+09:00',
                ),
            )
            for index in range(2):
                conn.execute(
                    'INSERT INTO article_changes VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (
                        f'{kind}-{index}',
                        kind,
                        f'제{index + 1}조',
                        f'2026-11-0{5 + index}',
                        '예시 요약',
                        '원문\n이전',
                        '원문\n이후',
                        1,
                        'approved',
                        '2026-11-05T11:00:00+09:00',
                    ),
                )
    return path


def test_counts_lists_and_detail_agree_with_article_effective_dates(dataset):
    with TestClient(create_app(dataset)) as client:
        month = client.get('/api/law-calendar', params={'month': '2026-11'}).json()
        assert month['days'] == [
            {'event_on': '2026-11-05', 'notice_count': 1, 'effective_count': 1},
            {'event_on': '2026-11-06', 'notice_count': 0, 'effective_count': 1},
        ]
        listing = client.get('/api/law-events?date=2026-11-05&page_size=1').json()
        assert listing['total'] == 3 and len(listing['items']) == 1
        assert client.get('/api/law-events?month=2026-11&type=effective').json()['total'] == 2
        detail = client.get('/api/law-changes/effective-0').json()
        assert detail['before'] == '원문\n이전' and detail['after'] == '원문\n이후'
        assert '- 이전' in detail['diff'] and detail['reviewState'] == 'approved'
        assert client.post('/api/law-events', json={}).status_code == 405


@pytest.mark.parametrize(
    'table,field,value',
    [
        ('change_events', 'review_state', 'pending'),
        ('change_events', 'review_state', 'withdrawn'),
        ('article_changes', 'review_state', 'rejected'),
        ('article_changes', 'verified_at', None),
        ('change_events', 'verified_at', None),
        ('change_events', 'source_url', 'https://example.com/unverified'),
        ('article_changes', 'effective_on', None),
        ('article_changes', 'effective_on', '2026-11-99'),
        ('article_changes', 'verified_at', 'unverified'),
    ],
)
def test_nonpublic_data_is_absent_from_every_endpoint(dataset, table, field, value):
    with sqlite3.connect(dataset) as conn:
        key = 'effective' if table == 'change_events' else 'effective-0'
        conn.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (value, key))
    with TestClient(create_app(dataset)) as client:
        assert client.get('/api/law-changes/effective-0').status_code == 404
        items = client.get('/api/law-events?month=2026-11').json()['items']
        assert 'effective-0' not in [item['id'] for item in items]
        assert client.get('/api/law-calendar?month=2026-11').json()['days'][0]['effective_count'] == 0


@pytest.mark.parametrize(
    'url',
    [
        '/api/law-calendar?month=2026-13',
        '/api/law-calendar?month=202611',
        '/api/law-events?date=2026-02-30',
        '/api/law-events',
        '/api/law-events?month=2026-11&date=2026-11-05',
        '/api/law-events?month=2026-11&type=other',
        '/api/law-events?month=2026-11&page_size=101',
    ],
)
def test_invalid_queries(dataset, url):
    with TestClient(create_app(dataset)) as client:
        assert client.get(url).status_code == 422


def test_unconfigured_database_is_unavailable_and_never_created(tmp_path):
    missing = tmp_path / 'private.db'
    with TestClient(create_app(missing)) as client:
        result = client.get('/api/law-calendar/status')
        assert result.status_code == 503 and str(missing) not in result.text
    assert not missing.exists()


def test_migration_is_idempotent_and_reads_cannot_write(dataset):
    migrate(dataset)
    with read_database(dataset) as conn:
        assert conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO laws VALUES ('bad','bad')")


def test_migration_preserves_foreign_database_and_backs_up_existing_empty_file(tmp_path):
    foreign = tmp_path / 'monitor.db'
    with sqlite3.connect(foreign) as conn:
        conn.execute('CREATE TABLE original(value TEXT)')
    with pytest.raises(ValueError):
        migrate(foreign)
    with sqlite3.connect(foreign) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('original',)]
    empty = tmp_path / 'empty.db'
    empty.touch()
    migrate(empty)
    assert empty.with_suffix('.db.pre-v1.backup').exists()
    with TestClient(create_app(empty)) as client:
        result = client.get('/api/law-events?month=2026-11')
        assert result.json()['total'] == 0 and result.headers['cache-control'] == 'no-store'
        assert client.get('/api/law-calendar/status').json()['delayed'] is True
