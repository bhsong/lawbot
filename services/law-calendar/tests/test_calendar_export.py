"""Exercise the export CLI with disposable public data and a prebuilt site fixture."""

import fcntl
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
from test_calendar_workflow import import_one
from test_calendar_workflow import workflow as workflow


@pytest.fixture
def inputs(tmp_path, dataset):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<title>Calendar</title>', encoding='utf-8')
    (dist / 'licenses.txt').write_text('Synthetic license fixture', encoding='utf-8')
    (dist / '404.html').write_text('<title>Not Found</title>', encoding='utf-8')
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
            'news': {
                'lawgo': {
                    'agency': 'lawgo',
                    'state': 'ok',
                    'officialHome': 'https://www.law.go.kr/',
                    'boards': [],
                    'items': [
                        {
                            'id': 'effective',
                            'title': '가상법 개정',
                            'board': '추적 법령 공포',
                            'publishedOn': '2026-10-01',
                            'officialUrl': 'https://www.law.go.kr/법령/예시',
                        }
                    ],
                },
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
    assert {p.name for p in out.iterdir()} == {
        'index.html',
        '404.html',
        'assets',
        'licenses.txt',
        '_headers',
        'data',
        '.law-calendar-site',
    }
    assert [item['id'] for item in read(data / 'erp-news/lawgo.json')['items']] == ['effective']
    assert (out / 'licenses.txt').read_bytes() == (dist / 'licenses.txt').read_bytes()

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE change_events SET review_state='withdrawn' WHERE id='effective'")
    second = run_export(inputs)
    assert second.returncode == 0, second.stderr
    assert not (data / 'law-changes').exists()
    assert all('effective-0' not in path.read_text(encoding='utf-8') for path in data.rglob('*.json'))
    assert read(data / 'erp-news/lawgo.json')['items'] == []
    with TestClient(server.create_app(db, daily_feed=feed)) as client:
        assert client.get('/api/erp-news/latest?agency=lawgo').json()['items'] == []


def test_export_merges_every_page_and_never_publishes_pending_article_body(inputs):
    db, _, _, _, out = inputs
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE article_changes SET review_state='pending', effective_on='2190-04-03', "
            "after_text='private draft' WHERE id='effective-1'"
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
    assert '2190-04' not in read(out / 'data/law-calendar/months.json')['months']
    for folder in ('law-calendar', 'law-events', 'tax-schedule'):
        assert not (out / 'data' / folder / '2190-04.json').exists()
    assert all('private draft' not in p.read_text(encoding='utf-8') for p in (out / 'data').rglob('*.json'))


def test_export_includes_only_metadata_for_an_actual_held_change(inputs, workflow):
    _, _, path, event = workflow
    event['context']['lawgo']['article_effective'] = '20261105:제1조20261201:제3조'
    path.write_text(json.dumps(event), encoding='utf-8')
    import_one(workflow)
    out = inputs[-1]
    result = run_export(inputs)
    assert result.returncode == 0, result.stderr
    holds = read(out / 'data/law-holds.json')
    assert holds['total'] == 1
    (held,) = holds['items']
    assert held['article'] == '제2조'
    assert set(held) == {'id', 'lawName', 'article', 'reason', 'sourceUrl', 'collectedAt'}
    assert not (out / 'data/law-changes' / (held['id'] + '.json')).exists()
    assert all('삭제 원문' not in path.read_text(encoding='utf-8') for path in (out / 'data').rglob('*.json'))


def test_export_preserves_verbatim_article_text(inputs):
    db, _, _, _, out = inputs
    before = '첨부 http://www.law.go.kr/flDownload.do?flSeq=113561639\n<img>표</img>'
    after = '/LSW/flDownload.do\n원 /㎡\n│천원 /월│\n다른 출처 http://example.com/notice'
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE article_changes SET before_text=?,after_text=? WHERE id='effective-0'", (before, after))
    result = run_export(inputs)
    assert result.returncode == 0, result.stderr
    detail = read(out / 'data/law-changes/effective-0.json')
    assert detail['before'] == before and detail['after'] == after
    assert '- <img>표</img>' in detail['diff'] and '+ │천원 /월│' in detail['diff']


@pytest.mark.parametrize(
    'field,value,code',
    [
        ('sourceUrl', 'http://www.law.go.kr/법령/예시', 'url_scheme'),
        ('sourceUrl', 'javascript:void(0)', 'url_scheme'),
        ('sourceUrl', 'https://outside.invalid/notice', 'url_host'),
        ('sourceUrl', 'https://github.com/legalize-kr-extra/repo', 'url_host'),
        ('sourceUrl', 'https://github.com/legalize-kr/../other/repo', 'url_path'),
        ('sourceUrl', 'https://github.com/legalize-kr/%2e%2e/other/repo', 'url_path'),
        ('sourceUrl', 'https://github.com/legalize-kr/repo%2fother', 'url_path'),
        ('sourceUrl', 'https://github.com/legalize-kr/repo%5cother', 'url_path'),
        ('sourceUrl', 'https://www.law.go.kr/?download=%2Ftmp%2Fcalendar.db', 'local_path'),
        ('excerpt', '연락처 fixture' + '\u0040' + 'example.invalid', 'email'),
        ('excerpt', 'localhost에서', 'url_host'),
        ('excerpt', '10.0.0.5에서', 'private_ip'),
        ('excerpt', '주소 127.0.0.1.', 'private_ip'),
        ('excerpt', '주소 fd00::1', 'private_ip'),
        ('excerpt', '첨부 http://intranet/notice', 'url_host'),
        ('excerpt', '첨부 http://example.internal/notice', 'url_host'),
        ('excerpt', '첨부 http://[::ffff:10.0.0.5]/notice', 'private_ip'),
        ('excerpt', '로컬 /Users/fixture/calendar.db', 'local_path'),
        ('excerpt', r'로컬 C:\fixture\calendar.db', 'local_path'),
        ('excerpt', '첨부 file:///fixture/calendar.db', 'local_path'),
    ],
)
def test_unsafe_public_strings_fail_closed_without_echoing_the_value(inputs, field, value, code):
    _, _, feed, snapshot, out = inputs
    snapshot['feeds']['erp']['items'] = [{**ITEM, field: value}]
    replace_file(feed, seal(snapshot))
    result = run_export(inputs)
    assert result.returncode == 1
    assert result.stderr == f'Calendar export failed: erp-updates.json $.items[0].{field} {code}\n'
    assert not out.exists()


def test_law_link_validation_reports_the_logical_file_and_field(inputs):
    db, _, _, _, out = inputs
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE change_events SET source_url='https://www.law.go.kr/a b' WHERE id='effective'")
    result = run_export(inputs)
    assert result.returncode == 1
    assert result.stderr == 'Calendar export failed: law-events/2026-11.json $.items[0].sourceUrl url_path\n'
    assert not out.exists()


def test_official_url_exception_is_reported_without_its_original_message(inputs, monkeypatch, capsys):
    from law_calendar import validation

    def invalid(_value):
        raise ValueError('private fixture')

    monkeypatch.setattr(validation, 'official_url', invalid)
    monkeypatch.setattr(sys, 'argv', ['law-calendar', *export_args(inputs)])
    with pytest.raises(SystemExit) as failure:
        server.main()
    assert failure.value.code == 1
    assert capsys.readouterr().err == 'Calendar export failed: law-events/2026-11.json $.items[0].sourceUrl url_path\n'


def test_export_allows_the_normalized_github_organization(inputs):
    _, _, feed, snapshot, out = inputs
    link = 'https://github.com/./%6cegalize-kr/fixture'
    snapshot['feeds']['erp']['items'] = [{**ITEM, 'sourceUrl': link}]
    replace_file(feed, seal(snapshot))
    result = run_export(inputs)
    assert result.returncode == 0, result.stderr
    assert read(out / 'data/erp-updates.json')['items'][0]['sourceUrl'] == link


@pytest.mark.parametrize(
    'invalid',
    ['_worker.js', '_routes.json', 'functions/file.js', 'extra.txt', 'assets/a.db', 'oversized', 'count'],
)
def test_invalid_build_preserves_the_previous_output(inputs, invalid, monkeypatch, capsys):
    _, dist, _, _, out = inputs
    first = run_export(inputs)
    assert first.returncode == 0, first.stderr
    previous = (out / 'index.html').read_bytes()
    code = 'build'
    if invalid == 'count':
        from law_calendar import export

        monkeypatch.setattr(export, 'MAX_FILES', 9)
        code = 'limit'
    elif invalid == 'oversized':
        with (dist / 'assets/large.js').open('wb') as stream:
            stream.truncate(25 * 1024 * 1024)
        code = 'limit'
    else:
        forbidden = dist / invalid
        forbidden.parent.mkdir(exist_ok=True)
        forbidden.touch()
    monkeypatch.setattr(sys, 'argv', ['law-calendar', *export_args(inputs)])
    with pytest.raises(SystemExit) as failure:
        server.main()
    assert failure.value.code == 1 and capsys.readouterr().err.rstrip().endswith(' ' + code)
    assert (out / 'index.html').read_bytes() == previous
    assert (out / 'data/law-calendar/months.json').is_file()


@pytest.mark.parametrize('marker', [None, b'not an export'])
def test_unmarked_directory_is_never_replaced(inputs, marker):
    out = inputs[-1]
    out.mkdir()
    previous = out / 'keep.txt'
    previous.write_text('keep', encoding='utf-8')
    if marker is not None:
        (out / '.law-calendar-site').write_bytes(marker)
    result = run_export(inputs)
    assert result.returncode == 1 and result.stderr == 'Calendar export failed: <output> $ destination\n'
    assert previous.read_text(encoding='utf-8') == 'keep'
    assert not (out / 'data').exists()


def test_export_waits_for_the_output_lock_before_taking_a_new_database_snapshot(inputs, monkeypatch):
    from law_calendar import export

    db, _, _, _, out = inputs
    lock = out.with_name(f'.{out.name}.lock')
    install_site = export.install_site
    with lock.open('wb') as held:
        fcntl.flock(held, fcntl.LOCK_EX)

        def finish_previous_export(_delay):
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE change_events SET review_state='withdrawn' WHERE id='effective'")
            fcntl.flock(held, fcntl.LOCK_UN)

        def install_while_locked(site, destination):
            with lock.open('rb') as competitor, pytest.raises(BlockingIOError):
                fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            install_site(site, destination)

        monkeypatch.setattr(export.time, 'sleep', finish_previous_export)
        monkeypatch.setattr(export, 'install_site', install_while_locked)
        export.main(export_args(inputs)[1:])
    assert not (out / 'data/law-changes').exists()
    assert read(out / 'data/erp-news/lawgo.json')['items'] == []


def test_busy_output_times_out_without_opening_inputs(inputs, monkeypatch, capsys):
    from law_calendar import export

    out = inputs[-1]
    lock = out.with_name(f'.{out.name}.lock')
    monkeypatch.setattr(export, 'LOCK_TIMEOUT', 0)
    args = export_args(inputs)[1:]
    args[1] = str(out.parent / 'nonexistent.db')
    with lock.open('wb') as held, pytest.raises(SystemExit) as failure:
        fcntl.flock(held, fcntl.LOCK_EX)
        export.main(args)
    assert failure.value.code == 1
    assert capsys.readouterr().err == 'Calendar export failed: <output> $ lock_timeout\n'
    assert not out.exists()


def test_invalid_response_model_fails_before_output(inputs, monkeypatch, capsys):
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
    assert capsys.readouterr().err == 'Calendar export failed: law-calendar/status.json $.delayed schema\n'
    assert not out.exists()


def test_unreadable_snapshot_uses_an_input_label_and_io_code(inputs):
    db, dist, feed, snapshot, out = inputs
    result = run_export((db, dist, feed.with_name('missing.json'), snapshot, out))
    assert result.returncode == 1
    assert result.stderr == 'Calendar export failed: <erp-daily-feed> $ io\n'
    assert not out.exists()


def test_unsafe_link_after_staging_started_preserves_the_previous_site(inputs):
    _, _, feed, snapshot, out = inputs
    first = run_export(inputs)
    assert first.returncode == 0, first.stderr
    previous = (out / 'data/erp-updates.json').read_bytes()
    snapshot['feeds']['erp']['items'] = [{**ITEM, 'sourceUrl': 'https://github.com/another-org/fixture'}]
    replace_file(feed, seal(snapshot))
    result = run_export(inputs)
    assert result.returncode == 1
    assert result.stderr == 'Calendar export failed: erp-updates.json $.items[0].sourceUrl url_host\n'
    assert (out / 'data/erp-updates.json').read_bytes() == previous
