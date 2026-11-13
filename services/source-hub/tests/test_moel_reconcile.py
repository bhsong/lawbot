import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from html import escape

import pytest

from source_hub import cli, materials, moel
from test_moel_rss import FEED, HOST, NOW, ROBOTS, SOURCE, feed, item, setting
from test_moel_rss import setup as setup

BOARD = HOST + '/news/notice/noticeList.do'
PAGE_TWO = BOARD + '?pageIndex=2'


def board(*items, next_page=False):
    rows = ''.join(
        '<tr><td aria-label="제목"><a href="' + escape(url) + '">' + escape(title) + '</a></td>'
        '<td aria-label="등록일">2026.11.13</td></tr>'
        for title, url in items
    )
    link = '<a href="?pageIndex=2">2</a>' if next_page else ''
    return f'<table class="tstyle_list"><tbody>{rows}</tbody></table>{link}'.encode()


def test_board_baseline_and_later_changes_do_not_duplicate_rss(setup):
    registry, database, calls, bodies = setup
    setting(registry, 'source', SOURCE, True)
    moel.collect(registry, database, SOURCE, now=NOW)
    bodies[BOARD] = board(item('목록에 정정된 공지'), next_page=True)
    bodies[PAGE_TWO] = board(item('기존 목록 전용', '20261100122'))
    result = moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1), reconcile=True)
    assert (result['baseline_count'], result['new_count'], result['modified_count']) == (1, 0, 1)
    assert result['reconciliation'] == {'outcome': 'ok', 'pages': 2, 'listed_count': 2, 'board_only_count': 1}
    assert calls == [ROBOTS, FEED, ROBOTS, FEED, BOARD, PAGE_TWO]

    bodies[BOARD] = board(item('목록에 정정된 공지'), item('RSS 누락 신규', '20261100124'), next_page=True)
    rss_only = moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=2), reconcile=True)
    assert rss_only['modified_count'] == 0 and rss_only['reconciliation'] == {'outcome': 'not_due'}
    assert calls[-2:] == [ROBOTS, FEED]
    result = moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=8), reconcile=True)
    assert (result['new_count'], result['modified_count']) == (1, 0)
    bodies[FEED] = feed(item('오래된 RSS 제목'), item('RSS 누락 신규', '20261100124'))
    assert moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=9))['modified_count'] == 0
    by_id = {row['upstream_id']: row for row in moel.documents(database, SOURCE)}
    assert by_id['20261100123']['title'] == '목록에 정정된 공지'
    assert by_id['20261100123']['revision_count'] == 2
    assert by_id['20261100122']['event_type'] == 'baseline'
    assert by_id['20261100124']['event_type'] == 'new_post'


@pytest.mark.parametrize('kind', ['policy', 'invalid', 'repeated'])
def test_incomplete_board_is_partial_and_rss_is_saved(setup, kind):
    registry, database, calls, bodies = setup
    setting(registry, 'source', SOURCE, True)
    bodies[BOARD] = board(item('목록용 제목'), next_page=True)
    bodies[PAGE_TWO] = b'<html>layout changed</html>'
    if kind == 'policy':
        bodies[ROBOTS] = b'User-agent: *\nDisallow: /*?pageIndex=2$'
    if kind == 'repeated':
        bodies[PAGE_TWO] = bodies[BOARD]
    result = moel.collect(registry, database, SOURCE, now=NOW, reconcile=True)
    assert result['outcome'] == 'partial'
    assert result['reconciliation']['outcome'] == ('blocked' if kind == 'policy' else 'error')
    assert registry.runs(SOURCE)[0]['outcome'] == 'partial'
    assert moel.documents(database, SOURCE)[0]['title'] == '기준 공지'
    assert (PAGE_TWO in calls) == (kind != 'policy')


def test_reconciliation_off_does_not_contact_board(setup):
    registry, database, calls, _ = setup
    assert moel.collect(registry, database, SOURCE, now=NOW, reconcile=True) == {'outcome': 'disabled'}
    assert calls == [] and not database.exists()


def test_existing_materials_schema_and_cli(setup, capsys):
    registry, database, _, bodies = setup
    setting(registry, 'source', SOURCE, True)
    post_id = '20261100123'
    baseline = {
        'upstream_id': post_id,
        'title': '이전 제목',
        'canonical_url': item('', post_id)[1],
        'published_on': '2026-11-13',
        'guid': 'old-rss-id',
    }
    timestamp = NOW.isoformat()
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(f'PRAGMA application_id={materials.APPLICATION_ID}')
        for statement in materials.SCHEMA:
            conn.execute(statement)
        conn.execute('INSERT INTO documents VALUES (?,?,?,?)', (SOURCE, post_id, timestamp, timestamp))
        conn.execute(
            'INSERT INTO revisions VALUES (?,?,?,?,?,?)',
            (SOURCE, post_id, 1, json.dumps(baseline, ensure_ascii=False), 'baseline', timestamp),
        )
    bodies[BOARD] = board(item('공식 정정', post_id))
    assert (
        cli.main(['--database', str(registry.database), 'collect', SOURCE, '--materials', str(database), '--reconcile'])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result['reconciliation']['outcome'] == 'ok'
    assert result['modified_count'] == 1
    [document] = moel.documents(database, SOURCE)
    assert (document['title'], document['guid'], document['revision_count']) == ('공식 정정', 'old-rss-id', 2)
