import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from subprocess import run

from fastapi.testclient import TestClient

from law_calendar.server import create_app
from lawbot_common.erp_daily import replace_file, seal
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


def test_daily_news_ages_without_changing_saved_hash(dataset, tmp_path):
    path = tmp_path / 'daily.json'
    old = NOW - timedelta(days=3)
    boards = [
        {
            'name': '예전 정상',
            'status': 'ok',
            'lastSuccessAt': old.isoformat(),
            'staleAfter': (old + timedelta(days=2)).isoformat(),
        },
        {
            'name': '수집 중지',
            'status': 'disabled',
            'lastSuccessAt': old.isoformat(),
            'staleAfter': (old + timedelta(days=2)).isoformat(),
        },
        {
            'name': '최근 정상',
            'status': 'ok',
            'lastSuccessAt': old.isoformat(),
            'staleAfter': (NOW + timedelta(days=1)).isoformat(),
        },
    ]
    entries = {
        agency: {
            'agency': agency,
            'state': 'ok',
            'officialHome': 'https://www.law.go.kr/',
            'boards': boards,
            'items': [],
        }
        for agency in ('moel', 'lawgo')
    }
    saved = seal(
        {
            'schemaVersion': 1,
            'dayKst': old.date().isoformat(),
            'cutoffAt': old.isoformat(),
            'runId': 'fixture',
            'configVersion': 0,
            'configHash': 'a' * 64,
            'status': 'ok',
            'sources': [],
            'feeds': {'news': entries},
            'changes': [],
        }
    )
    replace_file(path, saved)
    before = path.read_bytes()
    with TestClient(create_app(dataset, daily_feed=path)) as client:
        for agency in entries:
            response = client.get('/api/erp-news/latest', params={'agency': agency})
            assert response.headers['x-erp-snapshot'] == saved['snapshotHash']
            assert [board['status'] for board in response.json()['boards']] == ['delayed', 'disabled', 'ok']
            assert all('staleAfter' not in board for board in response.json()['boards'])
    assert path.read_bytes() == before


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


def test_offline_erp_capture_serializes_approved_law_and_agency_news(dataset, tmp_path):
    file = tmp_path / 'news.json'
    payload = snapshot()
    payload['agencies']['moel']['items'] = [
        {
            'id': 'notice',
            'title': '가상 기관 공지',
            'board': '공지',
            'publishedOn': '2026-11-10',
            'officialUrl': 'https://www.moel.go.kr/news/notice',
        }
    ]
    replace_file(file, payload)
    erp = tmp_path / 'erp.json'
    replace_file(
        erp,
        {
            'schemaVersion': 1,
            'generatedAt': NOW.isoformat(),
            'items': [],
            'relatedLaws': [],
            'history': [],
        },
    )
    rules = tmp_path / 'rules.json'
    replace_file(rules, {'schemaVersion': 2, 'generatedAt': NOW.isoformat(), 'rules': []})
    output = tmp_path / 'capture.json'
    result = run(
        [
            sys.executable,
            '-m',
            'law_calendar.erp_capture',
            '--database',
            str(dataset),
            '--erp',
            str(erp),
            '--rules',
            str(rules),
            '--news',
            str(file),
            '--out',
            str(output),
            '--cutoff',
            NOW.isoformat(),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    news = json.loads(output.read_bytes())['news']
    assert [(i['id'], i['publishedOn']) for i in news['lawgo']['items']] == [('effective', '2026-10-01')]
    assert news['moel']['items'] == [payload['agencies']['moel']['items'][0] | {'effectiveOn': None}]


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
