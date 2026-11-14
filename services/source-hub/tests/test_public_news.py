import json
from datetime import timedelta

import pytest

from source_hub import cli, daily, materials, moel, news
from test_moel_rss import FEED, NOW, SOURCE, feed, item, setting
from test_moel_rss import setup as setup


def test_only_approved_latest_versions_are_exported(setup, tmp_path):
    registry, database, _, bodies = setup
    registry.patch(
        'source', SOURCE, {'enabled': True, 'review_policy': 'manual'}, actor='test', reason='test', version=0
    )
    moel.collect(registry, database, SOURCE, now=NOW)
    assert news.build(registry, database)['agencies']['moel']['items'] == []
    with pytest.raises(ValueError, match='리비전'):
        news.review(database, SOURCE, '20261100123', 2, 'approve', 'reviewer', '확인')
    news.review(database, SOURCE, '20261100123', 1, 'approve', 'reviewer', '공개 권리·개인정보 확인')
    out = tmp_path / 'public.json'
    assert (
        cli.main(['--database', str(registry.database), 'export-news', '--materials', str(database), '--out', str(out)])
        == 0
    )
    snapshot = json.loads(out.read_text())
    [record] = snapshot['agencies']['moel']['items']
    assert record['title'] == '기준 공지'
    assert set(record) == {'id', 'title', 'board', 'publishedOn', 'officialUrl', 'effectiveOn'}
    assert 'reviewer' not in out.read_text()
    assert out.stat().st_mode & 0o777 == 0o600

    bodies[FEED] = feed(item('제목 정정'))
    moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
    assert news.build(registry, database)['agencies']['moel']['items'] == []
    news.review(database, SOURCE, '20261100123', 2, 'approve', 'reviewer', '정정 검토')
    setting(registry, 'agency', 'moel', False)
    disabled = news.build(registry, database)['agencies']['moel']
    assert not disabled['items']
    assert any(board['status'] == 'disabled' and board['lastSuccessAt'] for board in disabled['boards'])
    setting(registry, 'agency', 'moel', True)
    news.review(database, SOURCE, '20261100123', 2, 'withdraw', 'reviewer', '철회')
    assert news.build(registry, database)['agencies']['moel']['items'] == []
    assert materials.documents(database, SOURCE)[0]['revision_count'] == 2


def test_scope_and_law_part_settings_control_public_law_selection(setup):
    registry, database, _, _ = setup
    scope = registry.snapshot()
    legalize = next(source for source in scope['sources'] if source['source_id'] == 'legalize_law')
    law = next(entry for entry in legalize['laws'] if len(entry['parts']) > 1)
    part = law['parts'][1]
    label = law['name'] + ' ' + part['name']
    assert label in news.build(registry, database)['lawNames']
    setting(registry, 'law-part', part['id'], False)
    assert label not in news.build(registry, database)['lawNames']
    setting(registry, 'group', 'law', False)
    assert not news.build(registry, database)['lawNames']


def test_review_cli_requires_a_reason_only_for_withdrawal(setup):
    registry, database, _, _ = setup
    setting(registry, 'source', SOURCE, True)
    moel.collect(registry, database, SOURCE, now=NOW)
    command = ['--database', str(registry.database), 'review-news', SOURCE, '20261100123', '1']
    materials_arg = ['--materials', str(database)]
    assert cli.main([*command, 'approve', *materials_arg]) == 0
    assert cli.main([*command, 'withdraw', *materials_arg]) == 2
    assert len(news.build(registry, database)['agencies']['moel']['items']) == 1
    assert cli.main([*command, 'withdraw', *materials_arg, '--reason', '철회 확인']) == 0
    assert not news.build(registry, database)['agencies']['moel']['items']


def test_publication_cannot_replace_either_private_db(setup):
    registry, database, _, _ = setup
    for out in (registry.database, database):
        assert (
            cli.main(
                ['--database', str(registry.database), 'export-news', '--materials', str(database), '--out', str(out)]
            )
            == 2
        )
        assert not out.exists()


def test_official_metadata_appears_automatically_without_baseline_alerts(setup):
    registry, database, calls, bodies = setup
    setting(registry, 'source', SOURCE, True)
    bodies[FEED] = feed(item('기준 공지'), item('문의 someone@example.test', '20261100124'))
    moel.collect(registry, database, SOURCE, now=NOW)
    plan = registry.snapshot()
    [public] = news.build(registry, database)['agencies']['moel']['items']
    assert public['title'] == '기준 공지' and public['publishedOn'] == '2026-11-13'
    assert 'Source text' not in json.dumps(public)
    records = news.approved(database, news.review_sources_from_plan(plan))
    feeds = {'erp': {'items': []}, 'rules': None}
    changes, ledger = daily.change_records(plan, records, feeds, '2026-11-13', {})
    assert changes == [] and len(ledger) == 1
    assert len(calls) == 2

    bodies[FEED] = feed(item('정정 공지'), item('새 공지', '20261100125'))
    moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
    records = news.approved(database, news.review_sources_from_plan(plan))
    changes, _ = daily.change_records(plan, records, feeds, '2026-11-14', ledger)
    assert {(entry['title'], entry['change']) for entry in changes} == {('정정 공지', 'modified'), ('새 공지', 'new')}

    setting(registry, 'agency', 'moel', False)
    assert news.build(registry, database)['agencies']['moel']['items'] == []
    assert len(calls) == 4


def test_only_verified_news_sources_opt_in_by_default(setup):
    registry, _, _, _ = setup
    sources = news.review_sources(registry)
    for source in ['moel-rss-notice', 'moel-press', 'mofe-rss-press', 'mofe-laws', 'nts-notice']:
        assert sources[source]['review_policy'] == 'auto_metadata'
    for source in ['moel-policy', 'mofe-rss', 'nts-calendar', 'nts-forms']:
        assert sources[source]['review_policy'] == 'manual'
