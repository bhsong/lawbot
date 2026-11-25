"""Exercise the export CLI with disposable public data and a prebuilt site fixture."""

import json
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from law_calendar import erp_rules, server
from lawbot_common.erp_daily import replace_file, seal
from lawbot_common.erp_news import AGENCIES
from lawbot_common.tax_schedule import NTS_HOST, year_path
from test_calendar_api import dataset as dataset
from test_calendar_erp import FEED, ITEM


@pytest.fixture
def inputs(tmp_path, dataset):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<title>Calendar</title>', encoding='utf-8')
    (dist / 'licenses.txt').write_text('Synthetic license fixture', encoding='utf-8')
    (dist / 'assets').mkdir()
    (dist / 'assets/app-fixture.js').write_text('export {}', encoding='utf-8')
    feed = tmp_path / 'daily.json'
    snapshot = {
        'schemaVersion': 1,
        'dayKst': '2026-11-14',
        'cutoffAt': '2026-11-14T06:30:00+09:00',
        'runId': 'export-fixture',
        'configVersion': 0,
        'configHash': '0' * 64,
        'status': 'ok',
        'sources': [],
        'changes': [{'private': 'unpublished snapshot metadata'}],
        'feeds': {
            'erp': {'state': 'ok', **{key: value for key, value in FEED.items() if key != 'schemaVersion'}},
            'taxSchedule': {
                'confirmedAt': '2026-11-14T06:30:00+09:00',
                'items': [
                    {
                        'upstream_id': 'a' * 32,
                        'title': '가상 세무일정',
                        'deadline_on': '2031-02-03',
                        'canonical_url': NTS_HOST + year_path(2031),
                    }
                ],
            },
        },
    }
    replace_file(feed, seal(snapshot))
    return dataset, dist, feed, snapshot, tmp_path / 'site'


def export_args(inputs):
    db, dist, feed, _, out = inputs
    return [
        'export',
        '--database',
        str(db),
        '--erp-daily-feed',
        str(feed),
        '--frontend-dist',
        str(dist),
        '--out',
        str(out),
    ]


def run_export(inputs):
    return subprocess.run(
        [sys.executable, '-m', 'law_calendar.server', *export_args(inputs)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_export_matches_public_gets_and_replaces_the_previous_site(inputs):
    db, dist, feed, _, out = inputs
    first = run_export(inputs)
    assert first.returncode == 0, first.stderr
    with TestClient(server.create_app(db, daily_feed=feed)) as client:

        def api(route, **params):
            response = client.get('/api/' + route, params=params)
            assert response.status_code == 200
            return response.json()

        index = api('law-calendar/months')
        months = index['months']
        assert {'2026-11', '2031-02', erp_rules.today_kst().isoformat()[:7]} <= set(months)
        dates = index['erpRuleDates']
        result = {
            'law-calendar/status.json': api('law-calendar/status'),
            'law-calendar/months.json': index,
            'law-holds.json': api('law-holds', type='effective'),
            'erp-updates.json': api('erp-updates'),
            **{f'law-calendar/{month}.json': api('law-calendar', month=month) for month in months},
            **{f'law-events/{month}.json': api('law-events', month=month) for month in months},
            **{f'tax-schedule/{month}.json': api('tax-schedule', month=month) for month in months},
            **{f'erp-rules/{day}.json': api('erp-updates/v2', asOf=day) for day in dates},
            **{f'erp-news/{agency}.json': api('erp-news/latest', agency=agency) for agency in AGENCIES},
            **{f'law-changes/effective-{i}.json': api(f'law-changes/effective-{i}') for i in range(2)},
        }
    data = out / 'data'
    assert {str(p.relative_to(data)): read(p) for p in data.rglob('*.json')} == result
    assert len(dates) == 4 and dates[1] == erp_rules.today_kst().isoformat()
    assert {p.name for p in out.iterdir()} == {'index.html', 'assets', 'licenses.txt', '_headers', 'data'}
    assert (out / 'licenses.txt').read_bytes() == (dist / 'licenses.txt').read_bytes()

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE change_events SET review_state='withdrawn' WHERE id='effective'")
    second = run_export(inputs)
    assert second.returncode == 0, second.stderr
    assert not (data / 'law-changes').exists()
    assert all('effective-0' not in path.read_text(encoding='utf-8') for path in data.rglob('*.json'))


def test_export_merges_every_page_and_never_publishes_pending_article_body(inputs):
    db, _, _, _, out = inputs
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE article_changes SET review_state='pending', after_text='private draft' WHERE id='effective-1'"
        )
        conn.executemany(
            'INSERT INTO article_changes SELECT ?,event_id,?,effective_on,summary,before_text,after_text,comparable,'
            "review_state,verified_at FROM article_changes WHERE id='effective-0'",
            [(f'approved-{index}', f'제{index + 3}조') for index in range(101)],
        )
    result = run_export(inputs)
    assert result.returncode == 0, result.stderr
    payload = read(out / 'data/law-events/2026-11.json')
    assert len(payload['items']) == payload['total'] == 102
    assert {item['id'] for item in payload['items']} == {'effective-0', *(f'approved-{i}' for i in range(101))}
    assert not (out / 'data/law-changes/effective-1.json').exists()
    assert all('private draft' not in p.read_text(encoding='utf-8') for p in (out / 'data').rglob('*.json'))


@pytest.mark.parametrize(
    'value',
    [
        'http://www.law.go.kr/법령/예시',
        'https://outside.invalid/notice',
        'javascript:void(0)',
        '글에 포함된 https://outside.invalid/notice',
        '연락처 fixture' + '\u0040' + 'example.invalid',
        '주소 local' + 'host',
        '주소 ' + '.'.join(('127', '0', '0', '1')) + '.',
        '주소 ' + '.'.join(('192', '168', '2', '3')),
        '주소 fd00::1',
        '로컬 ' + '/Users/' + 'fixture/calendar.db',
        r'로컬 C:\fixture\calendar.db',
    ],
)
def test_unsafe_public_strings_fail_closed_without_echoing_the_value(inputs, value):
    _, _, feed, snapshot, out = inputs
    snapshot['feeds']['erp']['items'] = [{**ITEM, 'excerpt': value}]
    replace_file(feed, seal(snapshot))
    result = run_export(inputs)
    assert result.returncode == 1 and 'export' in result.stderr.lower()
    assert not out.exists()
    assert value not in result.stderr


@pytest.mark.parametrize('invalid', ['_worker.js', '_routes.json', 'functions/file.js', 'oversized', 'count'])
def test_invalid_build_preserves_the_previous_output(inputs, invalid, monkeypatch):
    _, dist, _, _, out = inputs
    out.mkdir()
    previous = out / 'index.html'
    previous.write_text('previous version', encoding='utf-8')
    if invalid == 'count':
        from law_calendar import export

        monkeypatch.setattr(export, 'MAX_FILES', 12)
    elif invalid == 'oversized':
        with (dist / 'oversized').open('wb') as stream:
            stream.truncate(25 * 1024 * 1024)
    else:
        forbidden = dist / invalid
        forbidden.parent.mkdir(exist_ok=True)
        forbidden.touch()
    monkeypatch.setattr(sys, 'argv', ['law-calendar', *export_args(inputs)])
    with pytest.raises(SystemExit) as failure:
        server.main()
    assert failure.value.code == 1
    assert list(out.iterdir()) == [previous] and previous.read_text(encoding='utf-8') == 'previous version'


def test_invalid_response_model_fails_before_output(inputs, monkeypatch):
    from fastapi import FastAPI

    out = inputs[-1]
    broken = FastAPI()

    @broken.get('/api/law-calendar/status')
    def bad_status():
        return {'timezone': 'Asia/Seoul', 'delayed': 'false', 'collectedAt': None, 'verifiedAt': None}

    monkeypatch.setattr(server, 'create_app', lambda *_args, **_kwargs: broken)
    monkeypatch.setattr(sys, 'argv', ['law-calendar', *export_args(inputs)])
    with pytest.raises(SystemExit) as failure:
        server.main()
    assert failure.value.code == 1
    assert not out.exists()


def test_unsafe_link_after_staging_started_preserves_the_previous_site(inputs):
    _, _, feed, snapshot, out = inputs
    out.mkdir()
    previous = out / 'index.html'
    previous.write_text('previous version', encoding='utf-8')
    snapshot['feeds']['erp']['items'] = [{**ITEM, 'sourceUrl': 'https://github.com/another-org/fixture'}]
    replace_file(feed, seal(snapshot))
    result = run_export(inputs)
    assert result.returncode == 1 and 'Unapproved public URL' in result.stderr
    assert list(out.iterdir()) == [previous] and previous.read_text(encoding='utf-8') == 'previous version'
