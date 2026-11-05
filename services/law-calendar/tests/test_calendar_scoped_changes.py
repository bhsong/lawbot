"""Verify effective dates for changed article fragments and dated borrowed evidence."""

import json

import pytest
from fastapi.testclient import TestClient

from law_calendar.automation import read_schedule, resolve_changes
from law_calendar.ingest import import_files
from law_calendar.server import create_app
from test_calendar_workflow import workflow as workflow


def test_changed_fragments_publish_on_separate_effective_days(workflow):
    db, home, path, event = workflow
    before = (
        '##### 제110조\n\n본문 <개정 2020.1.1>\n\n  1\\. 종전 제1호\n\n  2\\. 같은 제2호\n\n'
        '##### 제116조\n\n**①** 종전 본문\n\n**②** 같은 본문\n\n'
        '  1\\. 종전 제1호\n\n  2\\. 종전 제2호\n\n  4\\. 종전 제4호\n\n'
    )
    after = (
        '##### 제110조\n\n본문 <개정 2020.1.1, 2026.4.7>\n\n  1\\. 개정 제1호\n\n  2\\. 같은 제2호\n\n'
        '##### 제116조\n\n**①** 개정 본문\n\n  1\\. 본문에서 옮긴 내용\n\n  2\\. 새 제2호\n\n'
        '**②** 같은 본문\n\n  1\\. 개정 제1호\n\n  2\\. 개정 제2호\n\n  4\\. 삭제 <2026.4.7>\n\n'
    )
    (home / 'data/files/old.md').write_text(before, encoding='utf-8')
    (home / 'data/files/new.md').write_text(after, encoding='utf-8')
    event['diff']['chunks'] = [{'article': a} for a in ('제110조', '제116조')]
    event['dates']['promulgation'] = '2026-04-07'
    event['context'].pop('lawgo')
    event['context']['addenda_added'] = [
        '제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다. 다만, '
        '제110조제1호 및 제116조제2항제1호ㆍ제4호의 개정규정은 공포 후 8개월이 경과한 날부터 시행하고, '
        '제116조제1항제2호 및 제116조제2항제2호의 개정규정은 2027년 1월 1일부터 시행한다.'
    ]
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_files(db, home, [path]) == {'imported': 1, 'unchanged': 0, 'failed': 0}
    assert import_files(db, home, [path]) == {'imported': 0, 'unchanged': 1, 'failed': 0}
    with TestClient(create_app(db)) as client:
        assert client.get('/api/law-holds').json()['total'] == 0
        oct = client.get('/api/law-events?date=2026-10-08').json()['items']
        dec = client.get('/api/law-events?date=2026-12-08').json()['items']
        jan = client.get('/api/law-events?date=2027-01-01').json()['items']
        assert len(oct) == len(jan) == 1 and len(dec) == 2
        assert '옮긴 내용' in oct[0]['after'] and '새 제2호' not in oct[0]['after']
        by_label = {r['article']: r for r in dec}
        assert by_label['제110조 제1호']['after'] == '  1\\. 개정 제1호\n\n'
        assert '삭제 <2026.4.7>' in by_label['제116조 제2항제1호·제2항제4호']['after']
        assert '개정 제2호' in jan[0]['after'] and '개정 제1호' not in jan[0]['after']
        assert all(not r['comparable'] for r in [*oct, *dec, *jan])


@pytest.mark.parametrize('stamp,matched', [('2026.4.7', True), ('2026.4.8', False)])
def test_numbered_borrowed_addendum_only_dates_its_matched_law_and_article(workflow, stamp, matched):
    db, home, path, event = workflow
    new = home / 'data/files/new.md'
    raw = new.read_text(encoding='utf-8').replace('제목: 예시\n', "제목: 예시\n공포번호: '999'\n")
    new.write_text(
        raw + f'\n부칙(상위법) <제999호,{stamp}>\n\n제1조(시행일) 이 법은 공포 후 8개월이 경과한 날부터 시행한다.\n\n'
        '제2조 생략\n\n제3조(다른 법률의 개정) ① 생략\n\n'
        '② [예시] 가상법 일부를 다음과 같이 개정한다.\n\n제1조 중 "이전"을 "이후"로 한다.\n\n'
        '③ 다른 법 일부를 다음과 같이 개정한다.\n\n제3조를 다음과 같이 한다.\n',
        encoding='utf-8',
    )
    event['dates']['promulgation'] = '2026-04-07'
    event['context'].pop('lawgo')
    event['context']['addenda_added'] = []
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_files(db, home, [path])['imported'] == 1
    with TestClient(create_app(db)) as client:
        dated = client.get('/api/law-events?date=2026-12-08').json()['items']
        assert [r['article'] for r in dated] == (['제1조'] if matched else [])
        holds = client.get('/api/law-holds').json()['items']
        assert {r['article'] for r in holds} == ({'제2조', '제3조'} if matched else {'제1조', '제2조', '제3조'})


@pytest.mark.parametrize('day,reason', [('20261208', 'scoped_fragment'), ('20261209', 'conflict')])
def test_scoped_schedule_checks_metadata_agreement(day, reason):
    raw = {
        'dates': {'promulgation': '2026-04-07'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': [
                '제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다. 다만, '
                '제110조제1호의 개정규정은 공포 후 8개월이 경과한 날부터 시행한다.'
            ],
            'lawgo': {'article_effective': f'{day}:제110조제1호'},
        },
    }
    article = '제110조'
    before = '##### 제110조\n\n  1\\. 이전\n'
    after = before.replace('이전', '이후')
    order = (article,)
    (change,) = resolve_changes(raw, article, before, after, order, read_schedule(raw, order), '')
    assert change.basis == reason
    assert change.day == ('2026-12-08' if reason == 'scoped_fragment' else None)
