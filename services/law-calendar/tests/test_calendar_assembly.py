"""Registered Assembly observations become public only after snapshot review."""

import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from law_calendar.assembly_ingest import import_assembly, review_assembly
from law_calendar.db import migrate
from law_calendar.notice_source import NoticeSourceError
from law_calendar.server import create_app
from law_calendar.sync import synchronize
from test_calendar_sync import scheduled as scheduled
from test_calendar_workflow import workflow as workflow

NOW = '2026-11-05T06:31:00+09:00'


def register(
    home,
    state,
    *,
    revision=1,
    start='2026-11-04',
    end='2026-11-05',
    source='assembly_notice',
    date=None,
    notice_id=None,
):
    notice = source == 'assembly_notice'
    item = (notice_id or 'PRC_NOTICE') if notice else 'PRC_BILL'
    event_id = f'{source}-{revision}'
    if notice_id:
        event_id += f'-{notice_id}'
    link = (
        f'https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId={item}'
        if notice
        else 'https://likms.assembly.go.kr/bill/billDetail.do?billId=PRC_BILL'
    )
    dates = {'notice_end': end} if notice else ({'bill_proposed': date} if date else {})
    if notice and start:
        dates['notice_start'] = start
    change_type = (
        ('assembly_notice_observed' if revision == 1 else 'assembly_notice_changed') if notice else 'assembly_bill_new'
    )
    code = 'nknalejkafmvgzmpt' if notice else 'TVBPMBILL11'
    event = {
        'event_id': event_id,
        'source': source,
        'change_type': change_type,
        'detected_at': NOW,
        'new': {'version_key': str(revision)},
        'dates': dates,
        'target': {
            'item_id': item,
            'bill_id': 'PRC_BILL',
            'bill_no': '2200012',
            'law_name': '근로기준법 개정안',
            'agency': '소관위원회',
            'age': 22,
        },
        'context': {
            'source_url': link,
            'status': '진행 중',
            'schema_version': 1,
            'endpoint': code,
            'date_sources': {key: 'pal.assembly.go.kr' if key == 'notice_start' else code for key in dates},
        },
    }
    path = home / f'{event_id}.json'
    path.write_text(json.dumps(event), encoding='utf-8')
    with sqlite3.connect(state) as conn:
        conn.execute('INSERT INTO events VALUES (?,?,?,?,?,?)', (event_id, source, change_type, path.name, NOW, NOW))
        raw = conn.execute("SELECT summary_json FROM runs WHERE mode='normal'").fetchone()[0]
        summary = json.loads(raw)
        summary['collectors'][source] = {'status': 'ok'}
        conn.execute("UPDATE runs SET summary_json=? WHERE mode='normal'", (json.dumps(summary),))
    return event_id


@pytest.fixture
def unavailable_notice_source(monkeypatch):
    def unavailable(_url):
        raise NoticeSourceError('Official detail unavailable')

    monkeypatch.setattr('law_calendar.notice_content.fetch_official', unavailable)


def test_approved_notice_dates_correction_and_failed_source(scheduled, unavailable_notice_source):
    (database, home, _, _), state = scheduled
    event_id = register(home, state)
    assert synchronize(database, home)['assembly_notice_imported'] == 1
    with TestClient(create_app(database)) as client:
        path = '/api/law-events?month=2026-11&type=notice'
        assert client.get(path).json()['total'] == 0
        review_assembly(database, event_id, 'approve', reviewer='tester', note='공식 원문 확인')
        items = client.get(path).json()['items']
        assert [(item['date'], item['assembly']['dateRole']) for item in items] == [
            ('2026-11-04', 'notice_start'),
            ('2026-11-05', 'notice_end'),
        ]
        assert client.get('/api/law-changes/' + items[0]['id']).json()['assembly']['agency'] == '소관위원회'
        assert synchronize(database, home)['assembly_notice_unchanged'] == 1
        with sqlite3.connect(state) as conn:
            raw = conn.execute("SELECT summary_json FROM runs WHERE mode='normal'").fetchone()[0]
            summary = json.loads(raw)
            summary['collectors']['assembly_notice']['status'] = 'failed'
            conn.execute("UPDATE runs SET summary_json=? WHERE mode='normal'", (json.dumps(summary),))
        result = synchronize(database, home)
        assert result['assembly_notice_failed'] == 1 and result['failed'] == 1
        assert client.get(path).json()['total'] == 2
        correction = register(home, state, revision=2, end='2026-11-07')
        assert synchronize(database, home)['assembly_notice_imported'] == 1
        assert client.get(path).json()['total'] == 0
        with pytest.raises(ValueError, match='Stale'):
            review_assembly(database, event_id, 'approve', reviewer='tester', note='오래된 검토')
        review_assembly(database, correction, 'approve', reviewer='tester', note='정정 확인')
        assert [item['date'] for item in client.get(path).json()['items']] == ['2026-11-04', '2026-11-07']
        review_assembly(database, correction, 'withdraw', reviewer='tester', note='철회')
        assert client.get(path).json()['total'] == 0


def test_bill_dates_are_separate_from_notices(scheduled):
    (database, home, _, _), state = scheduled
    event_id = register(home, state, source='assembly_bill', date='2026-11-05')
    synchronize(database, home)
    review_assembly(database, event_id, 'approve', reviewer='tester', note='제안일 검토')
    with TestClient(create_app(database)) as client:
        assert client.get('/api/law-events?date=2026-11-05&type=notice').json()['total'] == 0
        [item] = client.get('/api/law-events?date=2026-11-05&type=bill').json()['items']
        assert item['assembly']['dateRole'] == 'bill_proposed'
        assert client.get('/api/law-calendar?month=2026-11').json()['days'][0]['bill_count'] == 1


def test_same_day_notice_counts_once_and_undated_bill_is_not_publishable(scheduled, unavailable_notice_source):
    (database, home, _, _), state = scheduled
    notice = register(home, state, start='2026-11-05')
    bill = register(home, state, source='assembly_bill')
    assert synchronize(database, home)['failed'] == 0
    review_assembly(database, notice, 'approve', reviewer='tester', note='예고기간 확인')
    with pytest.raises(ValueError, match='official date'):
        review_assembly(database, bill, 'approve', reviewer='tester', note='일자 없음')
    with TestClient(create_app(database)) as client:
        [item] = client.get('/api/law-events?date=2026-11-05').json()['items']
        assert item['assembly']['dateRole'] == 'notice_period'
        assert client.get('/api/law-calendar?month=2026-11').json()['days'][0]['notice_count'] == 1


def test_bad_assembly_evidence_does_not_block_other_sources(scheduled):
    (database, home, _, _), state = scheduled
    notice = register(home, state)
    register(home, state, source='assembly_bill', date='2026-11-05')
    path = home / f'{notice}.json'
    event = json.loads(path.read_text())
    event['context']['source_url'] = 'https://pal.assembly.go.kr.example/notice'
    path.write_text(json.dumps(event))
    result = synchronize(database, home)
    assert result['assembly_notice_failed'] == 1
    assert result['assembly_bill_imported'] == result['imported'] == 1
    with TestClient(create_app(database)) as client:
        status = client.get('/api/law-calendar/status').json()
        assert status['billCollectedAt'] == NOW
        assert 'noticeCollectedAt' not in status


def test_notice_hold_list_shows_only_current_pending_notices_and_paginates(scheduled):
    (database, home, _, _), state = scheduled
    waiting = register(home, state, notice_id='PRC_A', start=None)
    checking = register(home, state, notice_id='PRC_B')
    reviewed = {
        decision: register(home, state, notice_id=f'PRC_{decision}') for decision in ('approve', 'reject', 'withdraw')
    }
    bill = register(home, state, source='assembly_bill', date='2026-11-05')

    def read(snapshot):
        return json.loads((home / f'{snapshot}.json').read_text())

    import_assembly(database, 'assembly_notice', NOW, [read(i) for i in (waiting, checking, *reviewed.values())])
    import_assembly(database, 'assembly_bill', NOW, [read(bill)])
    for decision, snapshot in reviewed.items():
        review_assembly(database, snapshot, decision, reviewer='tester', note='날짜 확인')
    latest = register(home, state, notice_id='PRC_B', revision=2, end='2026-11-07')
    import_assembly(database, 'assembly_notice', NOW, [read(latest)])

    with TestClient(create_app(database)) as client:
        route = '/api/law-holds?type=notice&page_size=1'
        first = client.get(route).json()
        second = client.get(route + '&page=2').json()
        assert first['total'] == second['total'] == 2
        rows = {row['id']: row for row in first['items'] + second['items']}
        assert set(rows) == {waiting, latest}
        assert rows[waiting]['billNo'] == '2200012'
        assert rows[waiting]['sourceUrl'].endswith('PRC_A')
        assert rows[waiting]['reason'] == '예고 시작일 또는 종료일 확인 필요'
        assert rows[latest]['reason'] == '국회 공식 상세의 의안번호와 예고기간 확인 필요'
        assert client.get('/api/law-holds').json()['total'] == 0
        assert client.get('/api/law-events?month=2026-11&type=notice').json()['total'] == 2
        assert client.get('/api/law-holds?type=bill').status_code == 422
        assert client.post('/api/law-holds?type=notice').status_code == 405
        review_assembly(database, latest, 'approve', reviewer='tester', note='예고기간 대조')
        assert [row['id'] for row in client.get(route).json()['items']] == [waiting]
        assert client.get('/api/law-events?month=2026-11&type=notice').json()['total'] == 4


def test_v3_upgrade_keeps_existing_records_and_backup(tmp_path):
    from law_calendar.db import SCHEMA, SCHEMA_V2, SCHEMA_V3

    path = tmp_path / 'calendar.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA + SCHEMA_V2 + SCHEMA_V3 + 'PRAGMA user_version=3;')
        conn.execute("INSERT INTO laws VALUES ('existing', '기존')")
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone() == (6,)
        assert conn.execute('SELECT name FROM laws').fetchone() == ('기존',)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    backup = path.with_suffix('.db.pre-v4.backup')
    recovered = tmp_path / 'restored.db'
    shutil.copyfile(backup, recovered)
    with sqlite3.connect(recovered) as conn:
        assert conn.execute('PRAGMA user_version').fetchone() == (3,)
        assert conn.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert conn.execute('SELECT name FROM laws').fetchone() == ('기존',)
