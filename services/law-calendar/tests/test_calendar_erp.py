"""ERP tab API: reads only the approved public feed file and never affects the law calendar endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from law_calendar.server import create_app
from test_calendar_api import dataset as dataset

ITEM = {
    'id': 'item-1',
    'sourceKey': 'INS-01',
    'sourceTitle': '[예시] 가상 연금 상·하한',
    'category': 'insurance',
    'kind': 'admrule',
    'title': '[예시] 가상 연금 상한액',
    'agency': '가상부',
    'lawName': None,
    'article': None,
    'issuedNo': '2026-1',
    'issuedOn': '2026-02-02',
    'effectiveFrom': '2026-07-01',
    'asOf': None,
    'excerpt': '1. 하한액 : 100천원',
    'sourceUrl': 'https://www.law.go.kr/행정규칙/예시',
    'attachments': [
        {'title': '별지', 'pdfUrl': 'https://www.law.go.kr/LSW/flDownload.do?flSeq=1', 'sha256': None, 'bytes': None}
    ],
    'contentSha256': 'a' * 64,
    'collectedAt': '2026-11-12T20:00:00+09:00',
    'reviewedAt': '2026-11-12T20:05:00+09:00',
}
FEED = {
    'schemaVersion': 1,
    'generatedAt': '2026-11-12T20:06:00+09:00',
    'items': [ITEM],
    'relatedLaws': ['[예시] 가상법'],
    'history': [
        {
            'itemId': 'item-1',
            'sourceKey': 'INS-01',
            'title': ITEM['title'],
            'at': '2026-11-12T20:00:00+09:00',
            'action': 'collected',
            'origin': 'admrule-kr@0123456789ab',
        }
    ],
}


@pytest.fixture
def feed(tmp_path):
    path = tmp_path / 'erp_calendar_public.json'
    path.write_text(json.dumps(FEED, ensure_ascii=False), encoding='utf-8')
    return path


def test_serves_the_feed_as_published(dataset, feed):
    with TestClient(create_app(dataset, erp_feed=feed)) as client:
        body = client.get('/api/erp-updates').json()
    assert body == {'state': 'ok', **{key: value for key, value in FEED.items() if key != 'schemaVersion'}}


@pytest.mark.parametrize(
    ('content', 'state'),
    [
        (None, 'missing'),
        ('{not json', 'invalid'),
        (json.dumps({**FEED, 'schemaVersion': 2}), 'invalid'),
        (json.dumps({**FEED, 'items': [{'id': 'partial'}]}), 'invalid'),
    ],
    ids=['absent', 'not-json', 'unknown-version', 'partial-item'],
)
def test_missing_or_broken_feed_is_reported_without_items(dataset, tmp_path, content, state):
    path = tmp_path / 'feed.json'
    if content is not None:
        path.write_text(content, encoding='utf-8')
    with TestClient(create_app(dataset, erp_feed=path)) as client:
        body = client.get('/api/erp-updates').json()
        assert client.get('/api/law-calendar?month=2026-11').status_code == 200
    assert body == {'state': state, 'generatedAt': None, 'items': [], 'relatedLaws': [], 'history': []}


def test_unconfigured_feed_is_missing_and_law_endpoints_are_unchanged(dataset, feed):
    with TestClient(create_app(dataset)) as plain, TestClient(create_app(dataset, erp_feed=feed)) as with_erp:
        assert plain.get('/api/erp-updates').json()['state'] == 'missing'
        for path in ('/api/law-calendar?month=2026-11', '/api/law-events?month=2026-11', '/api/law-calendar/status'):
            assert plain.get(path).json() == with_erp.get(path).json()
