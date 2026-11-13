import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from law_calendar.server import create_app
from test_calendar_api import dataset as dataset

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)


def snapshot():
    return {
        'schemaVersion': 1,
        'generatedAt': NOW.isoformat(),
        'lawNames': ['[예시] 가상법'],
        'agencies': {agency: {'boards': [], 'items': []} for agency in ('lawgo', 'moel', 'mofe', 'nts', 'hometax')},
    }


def test_latest_news_orders_metadata_and_limits_results(dataset, tmp_path):
    file = tmp_path / 'news.json'
    payload = snapshot()
    payload['agencies']['moel'] = {
        'boards': [
            {
                'name': '공지',
                'status': 'ok',
                'lastSuccessAt': NOW.isoformat(),
                'staleAfter': (NOW - timedelta(minutes=1)).isoformat(),
            }
        ],
        'items': [
            {
                'id': f'post-{index}',
                'title': f'게시물 {index}',
                'board': '공지',
                'publishedOn': f'2026-11-{index:02}' if index > 1 else None,
                'officialUrl': f'https://www.moel.go.kr/news/notice/noticeView.do?bbs_seq={index}',
            }
            for index in range(1, 11)
        ],
    }
    file.write_text(json.dumps(payload))
    with TestClient(create_app(dataset, news_feed=file)) as client:
        response = client.get('/api/erp-news/latest?agency=moel&limit=3')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert [item['id'] for item in response.json()['items']] == ['post-10', 'post-9', 'post-8']
        assert response.json()['boards'][0]['status'] == 'delayed'
        assert 'staleAfter' not in response.json()['boards'][0]
        assert client.get('/api/erp-news/latest?agency=moel&limit=11').status_code == 422
        assert client.get('/api/erp-news/latest?agency=other').status_code == 422


def test_law_news_only_uses_approved_promulgations_and_hometax_is_link_only(dataset, tmp_path):
    file = tmp_path / 'news.json'
    payload = snapshot()
    payload['agencies']['lawgo']['boards'] = [{'name': '추적 법령', 'status': 'pending', 'lastSuccessAt': None}]
    file.write_text(json.dumps(payload))
    with TestClient(create_app(dataset, news_feed=file)) as client:
        law = client.get('/api/erp-news/latest?agency=lawgo').json()
        assert [(row['id'], row['publishedOn'], row['effectiveOn']) for row in law['items']] == [
            ('effective', '2026-10-01', None),
        ]
        with closing(sqlite3.connect(dataset)) as conn, conn:
            conn.execute("UPDATE article_changes SET effective_on='2026-11-05' WHERE id='effective-1'")
        assert client.get('/api/erp-news/latest?agency=lawgo').json()['items'][0]['effectiveOn'] == '2026-11-05'
        home = client.get('/api/erp-news/latest?agency=hometax').json()
        assert home['state'] == 'link_only' and home['items'] == []
        assert home['officialHome'] == 'https://www.hometax.go.kr/'
        assert home['navigation'] == '전체메뉴 → 기타 → 고객센터 → 자료실'
        payload['lawNames'] = []
        file.write_text(json.dumps(payload))
        assert client.get('/api/erp-news/latest?agency=lawgo').json()['items'] == []
        file.write_text('broken')
        assert client.get('/api/erp-news/latest?agency=moel').json()['state'] == 'invalid'
        assert client.get('/api/erp-updates').json()['state'] == 'missing'
        assert client.get('/api/law-calendar?month=2026-11').status_code == 200


def test_nonofficial_or_nonpublic_fields_fail_closed(dataset, tmp_path):
    file = tmp_path / 'news.json'
    payload = snapshot()
    item = {
        'id': '1',
        'title': '공식 게시물',
        'board': '공지',
        'publishedOn': None,
        'officialUrl': 'https://www.moel.go.kr.example.invalid/',
    }
    payload['agencies']['moel']['items'] = [item]
    with TestClient(create_app(dataset, news_feed=file)) as client:
        for fields in ({}, {'officialUrl': 'https://www.moel.go.kr/news', 'reviewer': 'private'}):
            item.update(fields)
            file.write_text(json.dumps(payload))
            body = client.get('/api/erp-news/latest?agency=moel').json()
            assert body['state'] == 'invalid' and body['items'] == []
