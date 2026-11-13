import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from source_hub import cli, moel
from source_hub.config import load
from source_hub.store import Registry

SOURCE = 'moel-rss-notice'
HOST = 'https://www.moel.go.kr'
FEED = HOST + '/rss/notice.do'
ROBOTS = HOST + '/robots.txt'
NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def feed(*items):
    entries = ''.join(
        f'<item><title>{title}</title><link>{url}</link><dc:date>2026-11-13T01:02:03Z</dc:date>'
        '<description>Source text</description></item>'
        for title, url in items
    )
    return f'<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>{entries}</channel></rss>'.encode()


def item(title, upstream='20261100123'):
    return title, HOST + f'/news/notice/noticeView.do?bbs_seq={upstream}'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    registry = Registry(load(), tmp_path / 'registry.db')
    database = tmp_path / 'materials.db'
    calls = []
    bodies = {ROBOTS: b'User-agent: *\nDisallow: /portal/', FEED: feed(item('기준 공지'))}

    def get(url, media, limit):
        calls.append(url)
        result = bodies[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(moel, 'read_response', get)
    return registry, database, calls, bodies


def setting(registry, kind, target, enabled):
    return registry.patch(
        kind,
        target,
        {'enabled': enabled},
        actor='operator',
        reason='test',
        version=registry.snapshot()['config_version'],
    )


def test_collect_and_disable_at_each_level(setup):
    registry, database, calls, bodies = setup
    assert moel.collect(registry, database, SOURCE, now=NOW)['outcome'] == 'disabled'
    assert calls == [] and not database.exists()
    setting(registry, 'source', SOURCE, True)
    baseline = moel.collect(registry, database, SOURCE, now=NOW)
    assert baseline | {'run_id': None} == {
        'outcome': 'ok',
        'run_id': None,
        'baseline_count': 1,
        'new_count': 0,
        'modified_count': 0,
    }
    assert calls == [ROBOTS, FEED]
    assert moel.collect(registry, database, SOURCE, now=NOW + timedelta(minutes=1))['outcome'] == 'not_due'
    assert calls == [ROBOTS, FEED]
    for kind, target in [('group', 'erp'), ('agency', 'moel'), ('source', SOURCE)]:
        setting(registry, kind, target, False)
        assert moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))['outcome'] == 'disabled'
        setting(registry, kind, target, True)
    assert calls == [ROBOTS, FEED]
    bodies[FEED] = feed(item('정정 공지'), item('신규 공지', '20261100124'), item('정정 공지'))
    changed = moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
    assert (changed['new_count'], changed['modified_count']) == (1, 1)
    unchanged = moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=2))
    assert (unchanged['new_count'], unchanged['modified_count']) == (0, 0)
    documents = {record['upstream_id']: record for record in moel.documents(database, SOURCE)}
    assert documents['20261100123']['title'] == '정정 공지'
    assert documents['20261100123']['published_on'] == '2026-11-13'
    assert documents['20261100123']['revision_count'] == 2
    assert documents['20261100124']['event_type'] == 'new_post'
    assert 'Source text' not in json.dumps(documents)
    assert [row['config_version'] for row in registry.runs(SOURCE)] == [7, 7, 1]


@pytest.mark.parametrize('robots', [b'User-agent: *\nDisallow: /rss/*', b'<html>error</html>', b''])
def test_robots_denial_or_unverified_document_stops_before_feed(setup, robots):
    registry, database, calls, bodies = setup
    setting(registry, 'source', SOURCE, True)
    bodies[ROBOTS] = robots
    assert moel.collect(registry, database, SOURCE, now=NOW)['outcome'] == 'blocked'
    assert calls == [ROBOTS]
    assert registry.runs(SOURCE)[0]['outcome'] == 'blocked'
    assert moel.documents(database, SOURCE) == []


@pytest.mark.parametrize(
    'invalid_feed',
    [
        b'<!DOCTYPE rss [<!ENTITY title "notice">]><rss><channel /></rss>',
        b'<html>error</html>',
        b'<rss><channel><item><title>No link</title></item></channel></rss>',
        feed(('Outside', 'https://external.example/article?bbs_seq=123')),
    ],
)
def test_bad_feed_is_an_error_and_keeps_last_success(setup, invalid_feed):
    registry, database, _, bodies = setup
    setting(registry, 'source', SOURCE, True)
    moel.collect(registry, database, SOURCE, now=NOW)
    before = moel.documents(database, SOURCE)
    bodies[FEED] = invalid_feed
    assert moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))['outcome'] == 'error'
    assert moel.documents(database, SOURCE) == before
    assert [row['outcome'] for row in registry.runs(SOURCE)] == ['error', 'ok']


def test_unapproved_official_host_path_is_never_requested(setup):
    registry, database, calls, _ = setup
    registry.patch(
        'source',
        SOURCE,
        {'source_url': HOST + '/unreviewed', 'enabled': True},
        actor='operator',
        reason='test',
        version=0,
    )
    assert moel.collect(registry, database, SOURCE, now=NOW)['outcome'] == 'unverified'
    assert calls == [] and not database.exists()


def test_cli_collect_and_list_documents(setup, capsys):
    registry, database, _, _ = setup
    setting(registry, 'source', SOURCE, True)
    args = ['--database', str(registry.database)]
    material = ['--materials', str(database)]
    assert cli.main([*args, 'collect', SOURCE, *material]) == 0
    assert json.loads(capsys.readouterr().out)['outcome'] == 'ok'
    assert cli.main([*args, 'documents', SOURCE, *material]) == 0
    assert json.loads(capsys.readouterr().out)[0]['title'] == '기준 공지'


def test_materials_cannot_overwrite_registry(setup):
    registry, _, calls, _ = setup
    setting(registry, 'source', SOURCE, True)
    with pytest.raises(ValueError, match='자료 DB'):
        moel.collect(registry, Path(registry.database), SOURCE, now=NOW)
    assert calls == []


def test_success_log_is_recovered_without_refetch_after_a_write_failure(setup, monkeypatch):
    registry, database, calls, _ = setup
    setting(registry, 'source', SOURCE, True)
    record_run = registry.record_run

    def unavailable(run):
        raise sqlite3.OperationalError('fixture write failure')

    monkeypatch.setattr(registry, 'record_run', unavailable)
    with pytest.raises(sqlite3.OperationalError):
        moel.collect(registry, database, SOURCE, now=NOW)
    assert registry.runs(SOURCE) == []
    assert moel.documents(database, SOURCE)[0]['revision_count'] == 1
    monkeypatch.setattr(registry, 'record_run', record_run)
    assert moel.collect(registry, database, SOURCE, now=NOW + timedelta(minutes=1))['outcome'] == 'not_due'
    assert [row['outcome'] for row in registry.runs(SOURCE)] == ['ok']
    assert calls == [ROBOTS, FEED]


def test_redirect_handler_declines_official_and_unrelated_targets():
    redirect = moel.NoRedirect()
    for target in (HOST + '/different', 'https://external.example/resource'):
        assert redirect.redirect_request(moel.Request(FEED), None, 302, 'Found', {'location': target}, target) is None
