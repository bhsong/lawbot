import json
from datetime import timedelta

import pytest

from source_hub import cli, materials, moel, news
from test_moel_rss import FEED, NOW, SOURCE, feed, item, setting
from test_moel_rss import setup as setup


def test_only_approved_latest_versions_are_exported(setup, tmp_path):
    registry, database, _, bodies = setup
    setting(registry, 'source', SOURCE, True)
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
