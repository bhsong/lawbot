"""Official notice content and links stay within the approved snapshot."""

import json
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from law_calendar.assembly_ingest import import_assembly, list_assembly, review_assembly
from law_calendar.db import (
    CURRENT_VERSION,
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_V5,
    SCHEMA_V6,
    SCHEMA_V7,
    migrate,
)
from law_calendar.manage import main as manage
from law_calendar.notice_content import NoticeContent, refresh_notice_content
from law_calendar.notice_source import NoticeSourceError, parse_notice_detail
from law_calendar.server import create_app
from law_calendar.sync import synchronize
from test_calendar_assembly import register
from test_calendar_sync import scheduled as scheduled
from test_calendar_workflow import workflow as workflow

PDF_URL = 'https://likms.assembly.go.kr/filegate/servlet/FileGate?bookId=123-abc&type=1'


def detail_html(bill_no, period, content, pdf_url=PDF_URL):
    return f'''
    <table><tr><th>의안번호</th><th>입법예고기간</th><th>법률안원문</th></tr>
    <tr><td>{bill_no}</td><td>{period}</td><td><a href="{pdf_url.replace('&', '&amp;')}"
    title="법률안.pdf"><span>PDF</span></a></td></tr></table>
    <!-- 제안이유 및 주요내용 -->
    <div class="item"><h4>제안이유 및 주요내용</h4><div class="desc">{content}</div></div>
    <!-- //제안이유 및 주요내용 -->
    <div>독자 의견</div>
    '''


@pytest.mark.parametrize(
    'body,summary',
    [
        ('제안이유 및 주요내용<br/><br/>종전 범위를 확대함.<br/>안 제10조', '종전 범위를 확대함.\n안 제10조'),
        ('제안이유<br/>제안 배경<br/><br/>주요내용<br/>안 제10조를 신설함.', '안 제10조를 신설함.'),
    ],
)
def test_notice_detail_uses_the_official_body_and_same_bill_attachment(body, summary):
    parsed = parse_notice_detail(
        detail_html('2220001', '2026-11-04~2026-11-05', body), '2220001', '2026-11-04', '2026-11-05'
    )
    assert parsed == (summary, PDF_URL)
    invalid = detail_html('2220001', '2026-11-04~2026-11-05', body, 'https://example.com/malicious.pdf')
    assert parse_notice_detail(invalid, '2220001', '2026-11-04', '2026-11-05') == (summary, None)
    with pytest.raises(NoticeSourceError):
        parse_notice_detail(invalid, '2220002', '2026-11-04', '2026-11-05')


def test_legacy_approved_notice_is_enriched_without_changing_its_calendar_rows(tmp_path, scheduled):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA + SCHEMA_V2 + SCHEMA_V3 + SCHEMA_V4 + 'PRAGMA user_version=4;')
    (_, home, _, _), state = scheduled
    snapshot = register(home, state)
    notice = json.loads((home / f'{snapshot}.json').read_text())
    stamp = notice['detected_at']
    import_assembly(path, 'assembly_notice', stamp, [notice])
    review_assembly(path, notice['event_id'], 'approve', reviewer='reviewer', note='날짜 확인')
    with TestClient(create_app(path)) as client:
        before = client.get('/api/law-events?month=2026-11&type=notice').json()
    migrate(path)
    assert path.with_suffix('.db.pre-v5.backup').exists()
    calls = []

    def read(_url, bill_no, start, end):
        calls.append((bill_no, start, end))
        return NoticeContent('제10조의 적용 대상을 확대하는 안입니다.', PDF_URL)

    assert refresh_notice_content(path, limit=10, read=read)['stored'] == 1
    assert refresh_notice_content(path, limit=10, read=read)['stored'] == 0
    assert len(calls) == 1
    with TestClient(create_app(path)) as client:
        listing = client.get('/api/law-events?month=2026-11&type=notice').json()
        assert listing == before
        for row in listing['items']:
            url = f'/api/law-changes/{row["id"]}'
            assert client.get(url).json()['noticeContent'] == {
                'summary': '제10조의 적용 대상을 확대하는 안입니다.',
                'pdfUrl': PDF_URL,
            }
            assert client.get(f'{url}/notice-comparison/5').status_code == 404
        review_assembly(path, notice['event_id'], 'withdraw', reviewer='reviewer', note='공개 철회')
        assert client.get(url).status_code == 404


def test_failed_refresh_preserves_saved_notice_body_and_link(scheduled):
    (path, home, _, _), state = scheduled
    snapshot = register(home, state)
    notice = json.loads((home / f'{snapshot}.json').read_text())
    import_assembly(path, 'assembly_notice', notice['detected_at'], [notice])
    review_assembly(path, snapshot, 'approve', reviewer='reviewer', note='날짜 확인')
    refresh_notice_content(path, read=lambda *_: NoticeContent('보관된 주요 내용', PDF_URL))

    def unavailable(*_):
        raise NoticeSourceError('Official source read failed')

    result = refresh_notice_content(path, refresh=True, read=unavailable)
    assert result['stored'] == 0 and len(result['failed']) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT summary,pdf_url FROM assembly_notice_content').fetchone() == (
            '보관된 주요 내용',
            PDF_URL,
        )


def test_refresh_can_continue_after_the_first_150_approved_notices(scheduled, monkeypatch, capsys):
    (path, home, _, _), state = scheduled
    snapshots = [register(home, state, notice_id=f'PRC_{index:03d}') for index in range(151)]
    notices = [json.loads((home / f'{snapshot}.json').read_text()) for snapshot in snapshots]
    import_assembly(path, 'assembly_notice', notices[0]['detected_at'], notices)
    for snapshot in snapshots:
        review_assembly(path, snapshot, 'approve', reviewer='reviewer', note='날짜 확인')

    def old(*_):
        return NoticeContent('기존 내용', None)

    def new(*_):
        return NoticeContent('새 내용', None)

    assert refresh_notice_content(path, read=old)['stored'] == 150
    assert refresh_notice_content(path, read=old)['stored'] == 1
    first = refresh_notice_content(path, refresh=True, read=new)
    assert first['stored'] == 150 and first['nextAfterSnapshot'] == snapshots[149]
    review_assembly(path, snapshots[149], 'withdraw', reviewer='reviewer', note='배치 처리 뒤 철회')
    html = detail_html('2200012', '2026-11-04~2026-11-05', '새 내용', pdf_url='')
    monkeypatch.setattr('law_calendar.notice_content.fetch_official', lambda _url: html.encode())
    monkeypatch.setattr(
        sys,
        'argv',
        ['manage', '--database', str(path), 'notice-content', '--refresh', '--after-snapshot', snapshots[149]],
    )
    manage()
    second = json.loads(capsys.readouterr().out)
    assert second['stored'] == 1 and second['nextAfterSnapshot'] is None
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT summary, COUNT(*) FROM assembly_notice_content GROUP BY summary').fetchall() == [
            ('새 내용', 151)
        ]


def test_daily_sync_and_manual_refresh_keep_pdf_link_without_fetching_attachment(scheduled, monkeypatch, capsys):
    (path, home, _, _), state = scheduled
    snapshot = register(home, state)
    register(home, state, source='assembly_bill', date='2026-11-05')
    calls = []
    html = detail_html('2200012', '2026-11-04~2026-11-05', '주요내용<br/>안 제10조를 신설함.')

    def fetch(url):
        assert url.startswith('https://pal.assembly.go.kr/')
        calls.append(url)
        return html.encode()

    monkeypatch.setattr('law_calendar.notice_content.fetch_official', fetch)
    first = synchronize(path, home)
    assert first['notice_auto_published'] == 1
    with TestClient(create_app(path)) as client:
        items = client.get('/api/law-events?month=2026-11&type=notice').json()['items']
        assert len(items) == 2
        url = f'/api/law-changes/{items[0]["id"]}'
        assert client.get(url).json()['noticeContent'] == {
            'summary': '안 제10조를 신설함.',
            'pdfUrl': PDF_URL,
        }
        assert client.get('/api/law-events?month=2026-11&type=bill').json()['total'] == 1
        assert client.get(f'{url}/notice-comparison/5').status_code == 404
    assert synchronize(path, home)['notice_auto_published'] == 0 and len(calls) == 1
    monkeypatch.setattr(sys, 'argv', ['manage', '--database', str(path), 'notice-content', '--refresh'])
    manage()
    assert json.loads(capsys.readouterr().out)['stored'] == 1
    assert len(calls) == 2
    rows = {item['snapshot_id']: item for item in list_assembly(path)}
    assert rows[snapshot]['reviewer'] == 'notice-auto'
    assert rows[snapshot]['review_state'] == 'approved'
    assert list_assembly(path, notice_sample=5) == [rows[snapshot]]


@pytest.mark.parametrize('decision', ['reject', 'withdraw'])
def test_daily_sync_never_reopens_explicitly_hidden_notice_on_correction(scheduled, monkeypatch, decision):
    (path, home, _, _), state = scheduled
    snapshot = register(home, state)
    html = detail_html('2200012', '2026-11-04~2026-11-05', '주요내용<br/>신설함.', pdf_url='')
    monkeypatch.setattr('law_calendar.notice_content.fetch_official', lambda *_: html.encode())
    assert synchronize(path, home)['notice_auto_published'] == 1
    review_assembly(path, snapshot, decision, reviewer='tester', note='수동 조치')
    correction = register(home, state, revision=2, end='2026-11-07')
    monkeypatch.setattr('law_calendar.notice_content.fetch_official', lambda *_: pytest.fail('Hidden notice was read'))
    assert synchronize(path, home)['notice_auto_published'] == 0
    [notice] = list_assembly(path)
    assert (
        notice['snapshot_id'] == correction
        and notice['review_state'] == {'reject': 'rejected', 'withdraw': 'withdrawn'}[decision]
    )


def test_daily_sync_leaves_mismatched_notice_pending_and_rechecks_after_source_recovers(scheduled, monkeypatch):
    (path, home, _, _), state = scheduled
    register(home, state)
    html = detail_html('2200012', '2026-11-04~2026-11-07', '주요내용<br/>신설함.', pdf_url='')
    monkeypatch.setattr('law_calendar.notice_content.fetch_official', lambda *_: html.encode())
    result = synchronize(path, home)
    assert result['failed'] == 0 and result['notice_pending'] == 1 and result['notice_auto_published'] == 0
    with TestClient(create_app(path)) as client:
        assert client.get('/api/law-events?month=2026-11&type=notice').json()['total'] == 0
    html = html.replace('2026-11-07', '2026-11-05')
    assert synchronize(path, home)['notice_auto_published'] == 1


def test_daily_batch_checks_unattempted_notices_before_retrying_a_persistent_mismatch(scheduled, monkeypatch):
    (path, home, _, _), state = scheduled
    register(home, state, notice_id='PRC_A')
    register(home, state, notice_id='PRC_Z')
    monkeypatch.setattr('law_calendar.notice_content.DAILY_NOTICE_LIMIT', 1)

    def fetch(url):
        end = '2026-11-07' if url.endswith('PRC_A') else '2026-11-05'
        return detail_html('2200012', f'2026-11-04~{end}', '주요내용<br/>신설함.', pdf_url='').encode()

    monkeypatch.setattr('law_calendar.notice_content.fetch_official', fetch)
    assert synchronize(path, home)['notice_pending'] == 2
    assert synchronize(path, home)['notice_pending'] == 1


@pytest.mark.parametrize('version', [5, 6, 7])
def test_migration_removes_notice_images_and_keeps_public_content(tmp_path, scheduled, version):
    path = tmp_path / 'legacy.db'
    automation_schema = ''.join(schema for upgrade, schema in ((6, SCHEMA_V6), (7, SCHEMA_V7)) if version >= upgrade)
    with sqlite3.connect(path) as db:
        db.executescript(
            SCHEMA
            + SCHEMA_V2
            + SCHEMA_V3
            + SCHEMA_V4
            + SCHEMA_V5
            + automation_schema
            + f'PRAGMA user_version={version};'
        )
    (_, home, _, _), state = scheduled
    snapshot = register(home, state)
    notice = json.loads((home / f'{snapshot}.json').read_text())
    import_assembly(path, 'assembly_notice', notice['detected_at'], [notice])
    review_assembly(path, snapshot, 'approve', reviewer='reviewer', note='날짜 확인')
    with sqlite3.connect(path) as db:
        db.execute(
            'INSERT INTO assembly_notice_content(snapshot_id,summary,pdf_url) VALUES (?,?,?)',
            (snapshot, '주요 변경 내용', PDF_URL),
        )
        image = b'calendar-migration-test-image' * 256
        db.execute('INSERT INTO assembly_notice_pages VALUES (?,?,?)', (snapshot, 2, image))
    with TestClient(create_app(path)) as client:
        items = client.get('/api/law-events?month=2026-11&type=notice').json()['items']
        url = f'/api/law-changes/{items[0]["id"]}'
        before = client.get(url).json()
        assert before['noticeContent'] == {'summary': '주요 변경 내용', 'pdfUrl': PDF_URL}
        assert client.get(f'{url}/notice-comparison/2').status_code == 404
        migrate(path)
        assert path.with_suffix(f'.db.pre-v{version + 1}.backup').exists()
        assert client.get(url).json() == before
        assert client.get(f'{url}/notice-comparison/2').status_code == 404
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (CURRENT_VERSION,)
        assert db.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute("SELECT name FROM sqlite_schema WHERE name='assembly_notice_pages'").fetchone() is None
        assert [row[1] for row in db.execute('PRAGMA table_info(assembly_notice_content)')] == [
            'snapshot_id',
            'summary',
            'pdf_url',
        ]
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    assert image[:256] not in path.read_bytes()
    assert synchronize(path, home)['notice_content_stored'] == 0


def test_failed_migration_preserves_legacy_image_table(tmp_path, monkeypatch):
    from law_calendar import db as calendar_db

    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA + SCHEMA_V2 + SCHEMA_V3 + SCHEMA_V4 + SCHEMA_V5 + SCHEMA_V6 + SCHEMA_V7)
        db.execute('PRAGMA user_version=7')
    monkeypatch.setattr(calendar_db, 'SCHEMA_V8', calendar_db.SCHEMA_V8 + 'DROP TABLE missing_table;')
    with pytest.raises(sqlite3.OperationalError, match='missing_table'):
        migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (7,)
        assert db.execute('SELECT COUNT(*) FROM assembly_notice_pages').fetchone() == (0,)
        assert db.execute('PRAGMA quick_check').fetchone() == ('ok',)
