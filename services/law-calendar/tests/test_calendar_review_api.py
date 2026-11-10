"""Service authorization, snapshot review and publication through the calendar HTTP API."""

import json
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from law_calendar.assembly_ingest import import_assembly
from law_calendar.db import read_database
from law_calendar.server import create_app
from test_calendar_assembly import NOW, register
from test_calendar_sync import scheduled as scheduled
from test_calendar_workflow import SOURCE, approval, import_one
from test_calendar_workflow import workflow as workflow

TOKEN_VAR = 'LAWBOT_CALENDAR_ADMIN_TOKEN'
REVIEW_PREFIX = '/api/admin/law-holds'
FAILURES = '/api/admin/import-failures'


class ServiceHeaders(dict):
    def __repr__(self):
        return '<service authentication headers>'


@pytest.fixture
def headers(monkeypatch):
    token = secrets.token_hex(32)
    monkeypatch.setenv(TOKEN_VAR, token)
    return ServiceHeaders(Authorization=f'Bearer {token}')


def effective_path(client):
    first = client.get('/api/law-holds').json()['items'][0]
    return f'{REVIEW_PREFIX}/effective/{first["id"]}'


def test_unconfigured_token_disables_reviews_without_changing_public_reads(workflow, monkeypatch):
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    import_one(workflow)
    with TestClient(create_app(workflow[0])) as client:
        path = effective_path(client)
        assert client.get(path).status_code == 503
        assert client.post(path, json={}).status_code == 503
        assert client.get(FAILURES).status_code == 503
        assert client.get('/api/law-holds').json()['total'] == 3
        assert client.post('/api/law-holds', json={}).status_code == 405


def test_missing_and_wrong_service_tokens_are_rejected(workflow, headers):
    import_one(workflow)
    with TestClient(create_app(workflow[0])) as client:
        path = effective_path(client)
        for request_headers in ({}, {'Authorization': f'Bearer {secrets.token_hex(32)}'}):
            assert client.get(path, headers=request_headers).status_code == 403
            assert client.post(path, headers=request_headers, json={}).status_code == 403
            assert client.get(FAILURES, headers=request_headers).status_code == 403
        assert client.get(FAILURES, headers=headers).json()['items'] == []
        assert client.post(FAILURES, headers=headers, json={}).status_code == 405


def test_effective_review_template_validation_publication_and_replay(workflow, headers):
    database = workflow[0]
    event_id = import_one(workflow)
    with TestClient(create_app(database)) as client:
        path = effective_path(client)
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        data = response.json()
        [change] = data['changes']
        assert change['id'] == path.rsplit('/', 1)[1]
        assert change['article'] == '제1조'
        assert '이전 원문' in change['before'] and '이후 원문' in change['after']
        assert data['evidence_url'] == data['template']['source_url'] == SOURCE
        assert data['template']['promulgated_on'] == '2026-10-01'
        assert not data['template']['dates_verified']
        assert not data['template']['changes'][0]['comparison_verified']
        assert client.post(path, headers=headers, json={'reviewer': 'admin', **data['template']}).status_code == 422

        valid = approval(database, event_id)
        valid['changes'] = [c for c in valid['changes'] if c['id'] == change['id']]
        valid['reviewer'] = 'admin'
        invalid = {**valid, 'changes': [{**valid['changes'][0], 'effective_on': '2026-02-30'}]}
        error = client.post(path, headers=headers, json=invalid)
        assert error.status_code == 422
        assert 'changes[1].effective_on' in error.json()['detail']
        assert 'YYYY-MM-DD' in error.json()['detail']

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post(path, headers=headers, json=valid), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert [response.json() for response in responses] == [{'ok': True}, {'ok': True}]
        assert client.get(path, headers=headers).status_code == 404
        assert client.post(path, headers=headers, json=valid).json() == {'ok': True}
        assert client.get('/api/law-holds').json()['total'] == 2
        public = client.get('/api/law-changes/' + change['id']).json()
        assert public['effectiveOn'] == '2026-11-05'
        assert public['comparable'] is True
    with read_database(database) as conn:
        [record] = conn.execute("SELECT reviewer FROM review_audit WHERE decision='approve'").fetchall()
        assert record['reviewer'] == 'admin'


def test_effective_review_rejects_superseded_snapshot(workflow, headers):
    database, _, file, event = workflow
    event_id = import_one(workflow)
    old = {**approval(database, event_id), 'reviewer': 'admin'}
    with TestClient(create_app(database)) as client:
        path = effective_path(client)
        event['context']['amendment_reason'] = '수정된 원천'
        file.write_text(json.dumps(event), encoding='utf-8')
        import_one(workflow)
        response = client.post(path, headers=headers, json=old)
        assert response.status_code == 409
        assert 'snapshot' in response.json()['detail']
        assert client.get('/api/law-events?month=2026-11&type=effective').json()['total'] == 0


def test_effective_sibling_approval_preserves_corrected_promulgation(workflow, headers):
    database = workflow[0]
    event_id = import_one(workflow)
    corrected = '2026-10-02'
    with TestClient(create_app(database)) as client:
        first, second, *_ = client.get('/api/law-holds').json()['items']
        payload = {**approval(database, event_id), 'reviewer': 'admin'}
        first_decision = {
            **payload,
            'promulgated_on': corrected,
            'changes': [change for change in payload['changes'] if change['id'] == first['id']],
        }
        first_path = f'{REVIEW_PREFIX}/effective/{first["id"]}'
        assert client.post(first_path, headers=headers, json=first_decision).status_code == 200

        second_path = f'{REVIEW_PREFIX}/effective/{second["id"]}'
        response = client.get(second_path, headers=headers)
        assert response.status_code == 200
        second_decision = {
            **payload,
            'promulgated_on': response.json()['template']['promulgated_on'],
            'changes': [change for change in payload['changes'] if change['id'] == second['id']],
        }
        assert client.post(second_path, headers=headers, json=second_decision).status_code == 200
        for item in (first, second):
            assert client.get(f'/api/law-changes/{item["id"]}').json()['promulgatedOn'] == corrected


@pytest.mark.parametrize('decision', ['reject', 'withdraw'])
def test_effective_web_rejects_event_wide_decisions(workflow, headers, decision):
    database = workflow[0]
    event_id = import_one(workflow)
    with TestClient(create_app(database)) as client:
        first, second, *_ = client.get('/api/law-holds').json()['items']
        payload = {**approval(database, event_id), 'reviewer': 'admin'}
        payload['changes'] = [change for change in payload['changes'] if change['id'] == first['id']]
        assert client.post(f'{REVIEW_PREFIX}/effective/{first["id"]}', headers=headers, json=payload).status_code == 200

        response = client.post(
            f'{REVIEW_PREFIX}/effective/{second["id"]}',
            headers=headers,
            json={**payload, 'decision': decision, 'changes': []},
        )
        assert response.status_code == 422
        assert 'decision' in response.json()['detail']
        assert client.get(f'/api/law-changes/{first["id"]}').status_code == 200
        assert client.get('/api/law-holds').json()['total'] == 2


def test_effective_retry_after_sibling_approval_does_not_reapply(workflow, headers):
    database = workflow[0]
    event_id = import_one(workflow)
    with TestClient(create_app(database)) as client:
        first, second, *_ = client.get('/api/law-holds').json()['items']
        payload = {**approval(database, event_id), 'reviewer': 'admin'}
        decisions = {
            item['id']: {
                **payload,
                'changes': [change for change in payload['changes'] if change['id'] == item['id']],
            }
            for item in (first, second)
        }
        decisions[second['id']]['promulgated_on'] = '2026-10-02'
        for item in (first, second, first):
            response = client.post(
                f'{REVIEW_PREFIX}/effective/{item["id"]}', json=decisions[item['id']], headers=headers
            )
            assert response.json() == {'ok': True}
    with read_database(database) as conn:
        assert conn.execute("SELECT count(*) FROM review_audit WHERE decision='approve'").fetchone()[0] == 2
        assert (
            conn.execute('SELECT promulgated_on FROM change_events WHERE id=?', (event_id,)).fetchone()[0]
            == '2026-10-02'
        )


def test_effective_retry_after_overlapping_approval_preserves_new_decision(workflow, headers):
    database = workflow[0]
    event_id = import_one(workflow)
    with TestClient(create_app(database)) as client:
        path = effective_path(client)
        change_id = path.rsplit('/', 1)[1]
        first = {**approval(database, event_id), 'reviewer': 'admin'}
        first['changes'] = [change for change in first['changes'] if change['id'] == change_id]
        second = {**first, 'changes': [{**first['changes'][0], 'summary': '정정한 요약'}]}
        for decision in (first, second, first):
            assert client.post(path, json=decision, headers=headers).json() == {'ok': True}
        assert client.get(f'/api/law-changes/{change_id}').json()['summary'] == '정정한 요약'
    with read_database(database) as conn:
        assert conn.execute("SELECT count(*) FROM review_audit WHERE decision='approve'").fetchone()[0] == 2


def test_assembly_retry_after_overlapping_withdrawal_preserves_new_decision(scheduled, headers):
    (database, home, _, _), state = scheduled
    snapshot = register(home, state)
    event = json.loads((home / f'{snapshot}.json').read_text(encoding='utf-8'))
    import_assembly(database, event['source'], NOW, [event])
    first = {'snapshot_id': snapshot, 'decision': 'approve', 'reviewer': 'admin', 'note': '공식 일정 확인'}
    second = {**first, 'decision': 'withdraw', 'note': '정정된 일정 확인 필요'}
    path = f'{REVIEW_PREFIX}/notice/{snapshot}'
    with TestClient(create_app(database)) as client:
        for decision in (first, second, first):
            assert client.post(path, json=decision, headers=headers).json() == {'ok': True}
        assert client.get('/api/law-events?month=2026-11&type=notice').json()['total'] == 0
    with read_database(database) as conn:
        assert conn.execute('SELECT count(*) FROM assembly_review_audit').fetchone()[0] == 2


@pytest.mark.parametrize('decision,total', [('approve', 2), ('reject', 0), ('withdraw', 0)])
def test_assembly_review_records_each_decision_once(scheduled, headers, decision, total):
    (database, home, _, _), state = scheduled
    snapshot = register(home, state)
    event = json.loads((home / f'{snapshot}.json').read_text(encoding='utf-8'))
    import_assembly(database, event['source'], NOW, [event])
    path = f'{REVIEW_PREFIX}/notice/{snapshot}'
    with TestClient(create_app(database)) as client:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data['source_url'] == event['context']['source_url']
        assert data['dates'] == [
            {'role': 'notice_start', 'date': '2026-11-04'},
            {'role': 'notice_end', 'date': '2026-11-05'},
        ]
        payload = {**data['template'], 'reviewer': 'admin', 'decision': decision, 'note': '공식 원문 검토'}
        assert client.post(path, headers=headers, json=payload).json() == {'ok': True}
        assert client.post(path, headers=headers, json=payload).json() == {'ok': True}
        assert client.get('/api/law-holds?type=notice').json()['total'] == 0
        assert client.get('/api/law-events?month=2026-11&type=notice').json()['total'] == total

        corrected = register(home, state, revision=2, end='2026-11-07')
        event = json.loads((home / f'{corrected}.json').read_text(encoding='utf-8'))
        import_assembly(database, event['source'], NOW, [event])
        assert client.post(path, headers=headers, json=payload).status_code == 409
    with read_database(database) as conn:
        [record] = conn.execute("SELECT decision,reviewer FROM assembly_review_audit WHERE reviewer='admin'").fetchall()
        assert tuple(record) == (decision, 'admin')
