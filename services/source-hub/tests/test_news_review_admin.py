import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from lawbot_common.erp_news import SNAPSHOT_MAX_BYTES
from source_hub import moel, news
from source_hub.server import create_app
from test_moel_rss import FEED, NOW, SOURCE, feed, item, setting
from test_moel_rss import setup as setup
from test_source_hub import AUTH, ORIGIN, admin_browser

BASE = '/api/collector/news'
POST = {
    'source_id': SOURCE,
    'upstream_id': '20261100123',
    'revision': 1,
    'review_id': None,
    'decision': 'approve',
    'reason': '공개 조건 확인',
}


def test_admin_review_comparison_conflict_and_publication(setup, tmp_path):
    registry, database, _, bodies = setup
    setting(registry, 'source', SOURCE, True)
    moel.collect(registry, database, SOURCE, now=NOW)
    out = tmp_path / 'public.json'
    with TestClient(create_app(registry, *AUTH, host='testserver', materials_db=database, news_feed=out)) as client:
        assert client.get(BASE + '/reviews').status_code == 401
        assert client.post(BASE + '/reviews', auth=AUTH, json=POST).status_code == 403
        headers = {'Origin': ORIGIN}
        queue = client.get(BASE + '/reviews', auth=AUTH).json()
        [row] = queue['items']
        assert row['current']['title'] == '기준 공지' and row['previous'] is None
        assert client.post(BASE + '/reviews', auth=AUTH, headers=headers, json=POST).status_code == 200
        assert client.post(BASE + '/reviews', auth=AUTH, headers=headers, json=POST).status_code == 409
        assert client.get(BASE + '/publication', auth=AUTH).json()['state'] == 'missing'
        published = client.post(BASE + '/publication', auth=AUTH, headers=headers, json={}).json()
        assert published['state'] == 'current' and published['published'] == 1
        assert out.exists()

        bodies[FEED] = feed(item('정정 공지'))
        moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
        [row] = client.get(BASE + '/reviews', auth=AUTH).json()['items']
        assert row['current']['revision'] == 2 and row['current']['title'] == '정정 공지'
        assert row['previous']['revision'] == 1 and row['previous']['title'] == '기준 공지'
        assert client.post(BASE + '/reviews', auth=AUTH, headers=headers, json=POST).status_code == 409
        assert client.get(BASE + '/publication', auth=AUTH).json()['state'] == 'pending'
        assert (
            client.post(BASE + '/reviews', auth=AUTH, headers=headers, json=POST | {'revision': 2}).status_code == 200
        )
        [approved] = client.get(BASE + '/reviews?status=approved', auth=AUTH).json()['items']
        assert (
            client.post(
                BASE + '/reviews',
                auth=AUTH,
                headers=headers,
                json=POST
                | {
                    'revision': 2,
                    'review_id': approved['review_id'],
                    'decision': 'withdraw',
                },
            ).status_code
            == 200
        )
        assert news.build(registry, database)['agencies']['moel']['items'] == []
        assert client.get(BASE + '/reviews?status=withdrawn', auth=AUTH).json()['total'] == 1


def test_admin_cannot_publish_to_an_invalid_existing_file(setup, tmp_path):
    registry, database, _, _ = setup
    out = tmp_path / 'public.json'
    old = json.dumps(news.build(registry, database)).ljust(SNAPSHOT_MAX_BYTES + 1)
    out.write_text(old)
    with TestClient(create_app(registry, *AUTH, host='testserver', materials_db=database, news_feed=out)) as client:
        assert client.get(BASE + '/publication', auth=AUTH).json()['state'] == 'invalid'
        assert client.post(BASE + '/publication', auth=AUTH, headers={'Origin': ORIGIN}, json={}).status_code == 422
    assert out.read_text() == old


@pytest.mark.e2e_stub
def test_admin_browser_reviews_correction_and_refreshes_public_snapshot(setup, tmp_path):
    registry, database, _, bodies = setup
    setting(registry, 'source', SOURCE, True)
    moel.collect(registry, database, SOURCE, now=NOW)
    out = tmp_path / 'public.json'
    with admin_browser(registry, '--materials', str(database), '--news-feed', str(out)) as page:
        page.get_by_role('button', name='공개 검토', exact=True).click()
        panel = page.get_by_role('region', name='기관 소식 공개 검토')
        dialog = page.get_by_role('dialog')
        refresh = panel.get_by_role('button', name='검토 목록 새로고침')
        publish = panel.get_by_role('button', name='공개 스냅샷 갱신')
        expect(panel.get_by_text('이전 승인판 없음')).to_be_visible()
        panel.get_by_role('button', name='공개 승인', exact=True).click()
        dialog.get_by_label('검토 사유').fill('기준 공지 검토')
        dialog.get_by_role('button', name='결정 저장').click()
        expect(panel.get_by_text('해당 검토 자료가 없습니다.')).to_be_visible()
        assert not out.exists()
        publish.click()
        expect(panel.get_by_text('공개 스냅샷이 현재 공개 대상과 일치합니다.', exact=False)).to_be_visible()
        assert json.loads(out.read_text())['agencies']['moel']['items'][0]['title'] == '기준 공지'

        bodies[FEED] = feed(item('정정 공지'))
        moel.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
        refresh.click()
        expect(panel.get_by_text('반영되지 않았습니다.', exact=False)).to_be_visible()
        expect(panel.get_by_text('기준 공지', exact=True)).to_be_visible()
        expect(panel.get_by_role('heading', name='정정 공지')).to_be_visible()
        panel.get_by_role('button', name='새 버전 승인').click()
        dialog.get_by_label('검토 사유').fill('정정 공지 검토')
        dialog.get_by_role('button', name='결정 저장').click()
        expect(panel.get_by_text('해당 검토 자료가 없습니다.')).to_be_visible()
        publish.click()
        expect(panel.get_by_text('공개 스냅샷이 현재 공개 대상과 일치합니다.', exact=False)).to_be_visible()
        assert json.loads(out.read_text())['agencies']['moel']['items'][0]['title'] == '정정 공지'

        panel.get_by_label('검토 상태').select_option('approved')
        panel.get_by_role('button', name='공개 철회').click()
        dialog.get_by_label('검토 사유').fill('공개 철회 확인')
        dialog.get_by_role('button', name='결정 저장').click()
        expect(panel.get_by_text('반영되지 않았습니다.', exact=False)).to_be_visible()
        publish.click()
        expect(panel.get_by_text('기관 게시물 0건', exact=False)).to_be_visible()
        assert json.loads(out.read_text())['agencies']['moel']['items'] == []
