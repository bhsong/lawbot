"""Verify effective dates for changed article fragments and dated borrowed evidence."""

import json

import pytest
from fastapi.testclient import TestClient

from law_calendar.automation import article_kind, read_schedule, resolve_changes
from law_calendar.ingest import import_files
from law_calendar.server import create_app
from test_calendar_workflow import workflow as workflow


def test_numbered_effective_dates_keep_exact_paragraphs_and_ignore_application_dates(workflow):
    db, home, path, event = workflow
    before = (
        '##### 제17조\n\n**①** 같은 본문\n\n  5의2\\. 같은 호\n\n'
        '**②** 종전 일반 규정\n\n**③** 종전 예외 규정\n\n'
        '##### 제18조\n\n종전 본문\n'
    )
    after = before.replace('종전', '개정')
    (home / 'data/files/old.md').write_text(before, encoding='utf-8')
    (home / 'data/files/new.md').write_text(after, encoding='utf-8')
    event['diff']['chunks'] = [{'article': a} for a in ('제17조', '제18조')]
    event['context'].pop('lawgo')
    event['dates']['promulgation'] = '2026-04-07'
    event['context']['addenda_added'] = [
        '제1조(시행일) 이 법은 공포한 날부터 시행한다. 다만, 다음 각 호의 개정규정은 '
        '해당 호에서 정하는 날부터 시행한다. <개정 2026.10.1>\n\n'
        '1. 제17조제3항 및 별표 1의 개정규정: 2027년 1월 1일\n\n'
        '2. 제18조: 2027년 2월 1일\n\n'
        '제2조(적용례) 제17조제3항은 2028년 1월 1일 이후 신청하는 경우부터 적용한다.'
    ]
    path.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
    assert import_files(db, home, [path]) == {'imported': 1, 'unchanged': 0, 'failed': 0}
    with TestClient(create_app(db)) as client:
        assert client.get('/api/law-holds').json()['total'] == 0
        april = client.get('/api/law-events?date=2026-04-07').json()['items']
        jan = client.get('/api/law-events?date=2027-01-01').json()['items']
        feb = client.get('/api/law-events?date=2027-02-01').json()['items']
        assert [row['article'] for row in april] == ['제17조 제2항 본문']
        assert [row['article'] for row in jan] == ['제17조 제3항']
        assert jan[0]['before'] == '**③** 종전 예외 규정\n\n'
        assert jan[0]['after'] == '**③** 개정 예외 규정\n\n'
        assert [row['article'] for row in feb] == ['제18조']


def test_relative_item_references_and_conditional_portions_do_not_hold_other_changes():
    raw = {
        'dates': {'promulgation': '2026-04-07'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': [
                '이 영은 공포한 날부터 시행한다. 다만, 다음 각 호의 개정규정은 '
                '각 호의 구분에 따른 날부터 시행한다.\n'
                '1. 제10조제1항 각 호 외의 부분 및 같은 항 제3호의2: 2027년 1월 1일\n'
                '2. 제10조제2항(상시 5명 이상 50명 미만인 사업장으로 한정한다): 2027년 7월 1일'
            ],
        },
    }
    before = (
        '##### 제10조\n\n**①** 종전 본문\n\n  3\\. 종전 제3호\n\n  3의2\\. 종전 제3호의2\n\n**②** 종전 규모별 규정\n'
    )
    after = before.replace('종전', '개정')
    order = ('제10조',)
    changes = resolve_changes(raw, '제10조', before, after, order, read_schedule(raw, order), '')
    by_date = {change.day: change for change in changes}
    assert set(by_date) == {'2026-04-07', '2027-01-01', None}
    assert by_date['2026-04-07'].after == '  3\\. 개정 제3호\n\n'
    assert by_date['2027-01-01'].after == '**①** 개정 본문\n\n  3의2\\. 개정 제3호의2\n\n'
    assert by_date[None].after == '**②** 개정 규모별 규정\n'


def test_editorial_history_is_not_part_of_the_commencement_sentence():
    raw = {
        'dates': {'promulgation': '2022-03-08'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': ['이 영은 2027년 1월 1일부터 시행한다. <개정 2022.12.31, 2024.12.31>'],
        },
    }
    schedule, reason = read_schedule(raw, ('제179조',))
    assert reason == 'simple_addendum'
    assert schedule is not None and schedule.default_on == '2027-01-01'


def test_labor_commencement_paragraphs_keep_employer_conditions_separate():
    raw = {
        'dates': {'promulgation': '2026-04-07'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': [
                '제1조(시행일) ① 이 법은 공포한 날부터 시행한다.\n'
                '② 제3조제1항의 개정규정은 다음 각 호의 구분에 따른 날부터 시행한다.\n'
                '1. 상시 50명 이상의 근로자를 사용하는 사업장: 2027년 1월 1일\n'
                '2. 상시 50명 미만의 근로자를 사용하는 사업장: 2028년 1월 1일\n'
                '③ 제4조제2항의 개정규정은 2027년 1월 1일부터 시행한다.\n'
                '제2조(준비행위) 관련 실태를 2027년 2월 1일까지 조사한다.'
            ],
        },
    }
    order = ('제3조', '제4조', '제5조')
    schedule, _ = read_schedule(raw, order)
    assert schedule is not None
    assert schedule.scopes_for('제3조') == {'제3조제1항': None}
    assert schedule.scopes_for('제4조') == {'제4조제2항': '2027-01-01'}
    assert schedule.date_for('제5조') == ('2026-04-07', 'default_addendum')


def test_sibling_range_includes_inserted_items_and_preserves_subitems():
    raw = {
        'dates': {'promulgation': '2026-04-07'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': [
                '이 영은 공포한 날부터 시행한다. 다만, 제10조제1항제1호부터 제3호까지의 개정규정은 '
                '2027년 1월 1일부터 시행한다.'
            ],
        },
    }
    before = (
        '##### 제10조\n\n**①** 같은 본문\n\n  1\\. 같은 제1호\n\n'
        '  1의2\\. 같은 제1호의2 본문\n\n    나\\. 종전 나목\n  1) 종전 세목\n\n'
        '  3\\. 같은 제3호\n\n  3의2\\. 종전 별도 시행 호\n'
    )
    after = before.replace('종전', '개정')
    order = ('제10조',)
    changes = resolve_changes(raw, '제10조', before, after, order, read_schedule(raw, order), '')
    by_date = {change.day: change for change in changes}
    assert by_date['2027-01-01'].after == '    나\\. 개정 나목\n  1) 개정 세목\n\n'
    assert by_date['2026-04-07'].after == '  3의2\\. 개정 별도 시행 호\n'


@pytest.mark.parametrize(
    'mark,item_scope,sibling_changed,expected',
    [
        ('2026.4.7', False, False, '2027-01-01'),
        ('2026.4.8', False, False, None),
        ('2026.4.7', True, False, '2027-01-01'),
        ('2026.4.7', True, True, None),
    ],
)
def test_same_law_prior_amendment_requires_matching_version_annotations(mark, item_scope, sibling_changed, expected):
    scope = '제10조제1항제1호' if item_scope else '제10조제1항'
    raw = {
        'target': {'law_name': '가상법'},
        'dates': {'promulgation': '2026-04-07'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': [
                '이 법은 공포한 날부터 시행한다. 다만, 다음 각 호의 개정규정은 해당 호에서 정한 날부터 시행한다.\n'
                f'1. 법률 제123호 가상법 일부개정법률 {scope}의 개정규정: 2027년 1월 1일'
            ],
        },
    }
    document = "---\n공포번호: '124'\n---\n\n부칙 <제123호,2025.4.7>\n\n이 법은 2027년 1월 1일부터 시행한다."
    before = '##### 제10조\n\n**①** 종전 본문 <신설 2025.4.7>\n\n  1\\. 종전 호\n\n  2\\. 같은 호\n'
    if item_scope:
        after = before.replace('신설 2025.4.7', f'신설 2025.4.7, {mark}').replace('종전 호', '개정 호')
        if sibling_changed:
            after = after.replace('같은 호', '바뀐 호')
    else:
        after = f'##### 제10조\n\n**①** 개정 본문 <개정 {mark}>\n'
    order = ('제10조',)
    evidence = read_schedule(raw, order, new_document=document)
    changes = resolve_changes(raw, '제10조', before, after, order, evidence, document)
    change = next(change for change in changes if change.article == f'제10조 {scope.removeprefix("제10조")}')
    assert change.day == expected


def test_pending_backfill_keeps_future_fragments_and_unresolved_candidates(workflow):
    db, home, path, event = workflow
    before = '##### 제1조\n\n**①** 종전 본문\n\n**②** 종전 예외\n\n##### 제2조\n\n종전 조문\n'
    after = before.replace('종전', '개정')
    (home / 'data/files/old.md').write_text(before, encoding='utf-8')
    (home / 'data/files/new.md').write_text(after, encoding='utf-8')
    event['diff']['chunks'] = [{'article': '제1조'}, {'article': '제2조'}]
    event['context'].pop('lawgo')
    event['context']['offline_backfill'] = {
        'v5_scope_as_of': '2026-11-06',
        'v5_pending_candidates': {'제1조': '2027-02-01', '제2조': '2027-02-01'},
    }
    event['context']['addenda_added'] = [
        '이 법은 공포한 날부터 시행한다. 다만, 제1조제2항의 개정규정은 2027년 1월 1일부터 시행하고, '
        '제2조(상시 50명 이상인 사업장으로 한정한다)의 개정규정은 2027년 2월 1일부터 시행한다.'
    ]
    path.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
    assert import_files(db, home, [path])['imported'] == 1
    with TestClient(create_app(db)) as client:
        assert client.get('/api/law-events?month=2026-10').json()['total'] == 0
        items = client.get('/api/law-events?date=2027-01-01').json()['items']
        assert [item['article'] for item in items] == ['제1조 제2항']
        assert [item['article'] for item in client.get('/api/law-holds').json()['items']] == ['제2조']


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


def test_borrowed_addendum_accepts_direct_subarticle_and_skipped_section_range():
    from law_calendar.source_addenda import borrowed_clause

    document = (
        "---\n공포번호: '999'\n---\n\n부칙(상위법) <제999호,2026.4.7>\n\n"
        '제1조(시행일) 이 법은 2027년 1월 1일부터 시행한다.\n\n'
        '제2조 부터 제8조까지 생략\n\n'
        '제9조(다른 법률의 개정) ① 가상법 일부를 다음과 같이 개정한다.\n\n'
        '제43조의6제1항제4호 및 같은 조 제2항 중 "이전"을 "이후"로 한다.\n\n'
        '② 다른 법 일부를 다음과 같이 개정한다.\n\n제6조를 삭제한다.\n'
    )
    assert borrowed_clause(document, '가상법', '제43조의6', '2026-04-07').endswith('2027년 1월 1일부터 시행한다.')
    assert not borrowed_clause(document, '가상법', '제43조', '2026-04-07')
    assert not borrowed_clause(document, '가상법', '제6조', '2026-04-07')


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


@pytest.mark.parametrize(
    'after,kind',
    [
        ('  1\\. 삭제 <2026.4.7>\n\n', '삭제'),
        ('**①** 삭제 <2026.4.7>\n\n', '삭제'),
        ('##### 제2조\n\n삭제 <2026.4.7>\n\n', '삭제'),
        ('  1\\. 삭제 <2026.4.7>\n\n  2\\. 다른 변경\n', '변경'),
        ('  1\\. 자료를 삭제하여야 한다.\n', '변경'),
    ],
)
def test_article_kind_recognizes_deletion_markers_in_whole_articles_and_fragments(after, kind):
    assert article_kind('이전 원문', after) == kind


@pytest.mark.parametrize('number,stamp', [('123', '2000.1.1'), ('124', '2026.4.7')])
def test_unrelated_numbered_addendum_cannot_supply_the_effective_date(number, stamp):
    raw = {
        'dates': {'promulgation': '2026-04-07'},
        'context': {
            'source_url': 'https://www.law.go.kr/법령/예시',
            'addenda_added': [f'부칙 <제{number}호,{stamp}>\n\n이 법은 공포한 날부터 시행한다.'],
        },
    }
    document = "---\n공포번호: '123'\n---\n"
    assert read_schedule(raw, ('제1조',), new_document=document) == (None, 'conflict')
