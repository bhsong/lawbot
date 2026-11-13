"""Exercise the offline review CLI, the immutable v1 contract and the public v2 reader."""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from erp_updates import cli, store
from erp_updates.sources import Found, Source, record
from law_calendar.server import create_app

BASE = '/api/erp-updates/v2'
SOURCE = Source('INS-01', '가상 연금 기준', 'insurance')
NOW = datetime(2026, 11, 13, 12, tzinfo=store.KST)


@pytest.fixture
def erp(tmp_path, monkeypatch):
    data = tmp_path / 'erp'
    out = tmp_path / 'rules.json'
    v1 = tmp_path / 'v1.json'
    monkeypatch.setattr(store, 'now', lambda: NOW.isoformat())
    with store.connect(data) as conn:
        for title in ('기존 공식 고시', '새 공식 고시', '미승인 고시'):
            data_record = record(
                SOURCE, kind='admrule', title=title, sourceUrl='https://www.law.go.kr/행정규칙/가상고시'
            )
            store.save(conn, [Found(SOURCE, title, 'admrule-kr', 'a' * 40, data_record)])
        for row in store.rows(conn):
            if row['data']['title'] != '미승인 고시':
                store.review(conn, row['id'], 'approved', 'fixture', None)

    def run(*args):
        return cli.main(['--data', str(data), *args])

    def review(**fields):
        path = tmp_path / 'review.json'
        path.write_text(json.dumps(fields))
        return run('review-rule', '--file', str(path), '--reviewer', 'private-fixture-operator')

    return run, review, v1, out


def edition(identifier, revision, start, value, **overrides):
    return {
        'ruleVersionId': identifier,
        'sourceKey': 'INS-01',
        'scopeId': 'standard',
        'scope': '일반 가입자',
        'applicability': 'insurance_month',
        'sourceRevisionId': revision,
        'decision': 'confirmed',
        'effectiveFrom': start,
        'appliesFrom': start,
        'evidenceKind': 'final_notice',
        'evidenceUrl': f'https://www.law.go.kr/행정규칙/{identifier}',
        'valueText': value,
        **overrides,
    }


def test_exact_review_keeps_v1_and_separates_future_partial_and_proposed(erp, tmp_path):
    run, review, v1, out = erp
    assert run('export', '--out', str(v1)) == 0
    before = v1.read_bytes()
    assert review(**edition('old', 1, '2026-01-01', '가상 현행')) == 0
    assert review(**edition('new', 2, '2027-01-01', '가상 예정', predecessorId='old')) == 0
    assert review(**edition('later', 2, '2027-07-01', '가상 추가 변경', predecessorId='new')) == 0
    assert review(**edition('part', 2, '2027-02-01', '가상 부분 시행', scopeId='special', scope='일부 가입자')) == 0
    assert review(**edition('draft', None, None, None, decision='proposed', evidenceKind='proposal')) == 0
    assert review(**edition('unapproved', 3, '2028-01-01', '비공개', predecessorId='later')) == 1
    assert review(**edition('forged', 2, '2028-01-01', '가짜 근거', evidenceUrl='https://law.go.kr.example/')) == 1
    assert run('export-rules', '--out', str(out)) == 0
    assert run('export', '--out', str(v1)) == 0 and v1.read_bytes() == before
    assert 'private-fixture-operator' not in out.read_text()
    with TestClient(create_app(tmp_path / 'unused.db', erp_feed=v1, rules_feed=out)) as client:
        assert client.get('/api/erp-updates').json()['items'] == json.loads(before)['items']
        response = client.get(BASE, params={'asOf': '2026-11-13', 'targetOn': '2026-11-01'})
        assert response.headers['cache-control'] == 'no-store'
        rules = {rule['ruleId']: rule for rule in response.json()['rules']}
        assert len(rules) == 6 and rules['TAX-01']['scopes'] == []
        rule = rules['INS-01']
        assert rule['ruleId'] == 'INS-01'
        scopes = {scope['scopeId']: scope for scope in rule['scopes']}
        assert scopes['standard']['currentVersion']['ruleVersionId'] == 'old'
        assert [v['ruleVersionId'] for v in scopes['standard']['scheduledChanges']] == ['new', 'later']
        assert scopes['special']['currentVersion'] is None
        assert scopes['special']['scheduledChanges'][0]['ruleVersionId'] == 'part'
        assert [link['ruleVersionId'] for link in rule['proposalLinks']] == ['draft']
        assert client.get(BASE, params={'asOf': '20261113'}).status_code == 422


def test_rollover_needs_recheck_and_preserves_prior_target_and_withdrawal(erp, tmp_path, monkeypatch):
    run, review, _, out = erp
    old = edition('old', 1, '2026-01-01', '가상 현행')
    new = edition('new', 2, '2027-01-01', '가상 예정', predecessorId='old')
    assert review(**old) == 0 and review(**new) == 0
    assert run('export-rules', '--out', str(out)) == 0
    with TestClient(create_app(tmp_path / 'unused.db', rules_feed=out)) as client:

        def scope(as_of: str, target: str):
            return client.get(BASE, params={'asOf': as_of, 'targetOn': target}).json()['rules'][0]['scopes'][0]

        assert scope('2026-12-31', '2026-12-31')['scheduledChanges'][0]['ruleVersionId'] == 'new'
        due = scope('2027-01-01', '2027-01-01')
        assert due['currentVersion']['ruleVersionId'] == 'old'
        assert due['recheckRequired'] is True and due['scheduledChanges'] == []
        monkeypatch.setattr(store, 'now', lambda: '2027-01-01T00:00:00+09:00')
        assert review(**new) == 0
        assert run('export-rules', '--out', str(out)) == 0
        assert scope('2027-01-01', '2027-01-01')['currentVersion']['ruleVersionId'] == 'new'
        assert scope('2027-01-01', '2026-12-01')['currentVersion']['ruleVersionId'] == 'old'
        assert scope('2026-12-31', '2026-12-31')['currentVersion']['ruleVersionId'] == 'old'
        monkeypatch.setattr(store, 'now', lambda: '2027-01-02T00:00:00+09:00')
        assert review(**(new | {'decision': 'withdrawn', 'reason': '가상 철회'})) == 0
        assert run('export-rules', '--out', str(out)) == 0
        assert scope('2027-01-02', '2027-01-02')['currentVersion']['ruleVersionId'] == 'old'
        assert '가상 예정' not in out.read_text()


def test_expired_successor_does_not_restore_an_open_ended_predecessor(erp, tmp_path, monkeypatch):
    run, review, _, out = erp
    assert review(**edition('old', 1, '2026-01-01', '가상 현행')) == 0
    new = edition('new', 2, '2027-01-01', '가상 한시 기준', predecessorId='old', appliesUntil='2027-02-01')
    assert review(**new) == 0
    monkeypatch.setattr(store, 'now', lambda: '2027-01-01T00:00:00+09:00')
    assert review(**new) == 0
    assert run('export-rules', '--out', str(out)) == 0
    with TestClient(create_app(tmp_path / 'unused.db', rules_feed=out)) as client:
        response = client.get(BASE, params={'asOf': '2027-03-05', 'targetOn': '2027-03-05'})
        scope = response.json()['rules'][0]['scopes'][0]
        assert scope['currentVersion'] is None and scope['scheduledChanges'] == []


def test_recheck_and_postponement_keep_previous_and_do_not_publish_the_superseded_value(erp, tmp_path):
    run, review, _, out = erp
    old = edition('old', 1, '2026-01-01', '가상 현행')
    new = edition('new', 2, '2027-01-01', '가상 변경', predecessorId='old')
    assert review(**old) == 0 and review(**new) == 0
    assert review(**(new | {'decision': 'recheck_required', 'reason': '근거 재확인 실패'})) == 0
    assert run('export-rules', '--out', str(out)) == 0
    with TestClient(create_app(tmp_path / 'unused.db', rules_feed=out)) as client:

        def scope():
            return client.get(BASE, params={'asOf': '2026-11-13'}).json()['rules'][0]['scopes'][0]

        assert scope()['recheckRequired'] and scope()['scheduledChanges'] == []
        assert review(**(new | {'decision': 'withdrawn', 'reason': '시행 연기'})) == 0
        assert review(**edition('moved', 2, '2027-04-01', '가상 연기판', predecessorId='old')) == 0
        assert run('export-rules', '--out', str(out)) == 0
        assert scope()['currentVersion']['ruleVersionId'] == 'old'
        assert [v['ruleVersionId'] for v in scope()['scheduledChanges']] == ['moved']
        assert '가상 변경' not in out.read_text()


def test_changes_to_a_predecessor_require_rechecking_its_successor(erp, tmp_path):
    run, review, _, out = erp
    old = edition('old', 1, '2026-01-01', '가상 현행')
    new = edition('new', 2, '2027-01-01', '가상 예정', predecessorId='old')
    assert review(**old) == 0 and review(**new) == 0
    assert review(**(old | {'decision': 'recheck_required'})) == 0
    assert review(**new) == 1
    assert run('export-rules', '--out', str(out)) == 0
    with TestClient(create_app(tmp_path / 'unused.db', rules_feed=out)) as client:
        scope = client.get(BASE, params={'asOf': '2026-11-13'}).json()['rules'][0]['scopes'][0]
        assert scope['currentVersion'] is None and scope['recheckRequired'] and not scope['scheduledChanges']
    assert review(**old) == 0 and review(**new) == 0


def test_missing_invalid_and_utc_to_kst_rollover(erp, tmp_path, monkeypatch):
    from law_calendar import erp_rules

    _, _, _, out = erp
    monkeypatch.setattr(erp_rules, 'today_kst', lambda: date(2027, 1, 1))
    with TestClient(create_app(tmp_path / 'unused.db')) as client:
        assert client.get(BASE).json()['state'] == 'unconfigured'
    with TestClient(create_app(tmp_path / 'unused.db', rules_feed=out)) as client:
        assert client.get(BASE).json()['state'] == 'missing'
        out.write_text('{}')
        assert client.get(BASE).json()['state'] == 'invalid'
        assert client.get(BASE).json()['asOfDate'] == '2027-01-01'
