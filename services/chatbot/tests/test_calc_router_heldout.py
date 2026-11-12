import json
from datetime import datetime
from hashlib import sha256

import httpx
import pytest

from lawbot_tools import eval_calc_router as evaluator

CASE = {
    'question': '퇴직금을 계산하려면 어떤 정보가 필요한가요?',
    'calculator': 'severance_pay',
    'kind': 'missing',
    'status': 'guidance',
    'expected_results': None,
    'basis': '합성 시험',
}
COMMIT = 'f' * 40


def fixture_file(tmp_path, count=60):
    path = tmp_path / 'synthetic.json'
    raw = json.dumps([CASE | {'id': f'T{i:03d}'} for i in range(count)], ensure_ascii=False).encode()
    path.write_bytes(raw)
    return path, sha256(raw).hexdigest()


def install_transport(monkeypatch, on_post=None):
    requests = []
    client = httpx.Client
    posts = 0

    def reply(request):
        nonlocal posts
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer fixture-token'
        if request.url.path == '/health':
            return httpx.Response(200, json={'ready': True, 'max_concurrent': 1, 'commit': COMMIT})
        if request.url.path == '/v1/models':
            return httpx.Response(
                200,
                json={'data': [{'id': 'pro', 'type': 'chat', 'loaded': True, 'revision': 'sha256:fixture'}]},
            )
        assert request.url.path == '/v1/chat/completions'
        posts += 1
        if on_post and (response := on_post(request, posts)) is not None:
            return response
        return httpx.Response(
            200,
            json={
                'id': 'route',
                'object': 'chat.completion',
                'created': 0,
                'model': 'pro',
                'choices': [
                    {
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': '{"calculator":"severance_pay","inputs":{}}',
                        },
                        'finish_reason': 'stop',
                    }
                ],
            },
        )

    class MockClient(client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(reply))

    monkeypatch.setattr(evaluator.httpx, 'Client', MockClient)
    monkeypatch.setattr(evaluator, '_heldout_settings', lambda: ('http://127.0.0.1:9', 'fixture-token', 'pro'))
    monkeypatch.setattr(evaluator, '_heldout_guard', lambda: 1)
    monkeypatch.setattr(evaluator, '_webapp_commit', lambda: COMMIT)
    monkeypatch.setattr(evaluator, 'calculator_compatibility', lambda path: {'severance_pay': ()})
    monkeypatch.setattr(evaluator.service, 'load_app', lambda *args: pytest.fail('must not initialize the app'))
    return requests


def test_heldout_router_only_complete_run(monkeypatch, tmp_path):
    fixture, digest = fixture_file(tmp_path)
    output = tmp_path / 'report.json'
    requests = install_transport(monkeypatch)

    assert evaluator._live_heldout(fixture, digest, output) == 0
    report = json.loads(output.read_text())
    assert report['status'] == 'completed'
    assert report['valid'] is True and report['before'] == report['after']
    assert report['before']['fixture_sha256'] == digest
    assert report['before']['webapp_commit'] == report['before']['model_server_commit'] == COMMIT
    assert report['inference_posts'] == len(report['routes']) == 60
    assert all(row['classification_match'] and row['reason'] == 'missing_required' for row in report['routes'])
    assert [req.method for req in requests] == ['GET', 'GET', *(['POST'] * 60), 'GET', 'GET']
    for req in requests[2:-2]:
        body = json.loads(req.content)
        assert body['model'] == 'pro'
        assert body['messages'][-1]['content'] == CASE['question']
        assert body['max_tokens'] == 128 and body['temperature'] == 0
        assert body['stream'] is False and body['chat_template_kwargs'] == {'enable_thinking': False}
        assert req.extensions['timeout']['read'] == 300


@pytest.mark.parametrize('failure', ['http_error', 'invalid_response', 'read_timeout'])
def test_heldout_stops_and_keeps_partial_json(monkeypatch, tmp_path, failure):
    fixture, digest = fixture_file(tmp_path)
    output = tmp_path / 'report.json'

    def fail_second(request, count):
        if count != 2:
            return None
        checkpoint = json.loads(output.read_text())
        assert checkpoint['inference_posts'] == 2
        assert [row['id'] for row in checkpoint['routes']] == ['T000']
        if failure == 'http_error':
            return httpx.Response(503, json={'error': {'message': 'private failure details'}})
        if failure == 'invalid_response':
            return httpx.Response(200, json={'choices': []})
        raise httpx.ReadTimeout('private failure details', request=request)

    requests = install_transport(monkeypatch, fail_second)
    assert evaluator._live_heldout(fixture, digest, output) == 2
    report = json.loads(output.read_text())
    assert report['status'] == 'stopped' and report['valid'] is False and report['after'] is None
    assert report['stop_reason'] == failure
    assert report['inference_posts'] == 2
    assert report['routes'][0]['classification_match']
    assert 'private failure details' not in output.read_text()
    assert [req.method for req in requests] == ['GET', 'GET', 'POST', 'POST']


def test_heldout_rejects_oversized_fixture_before_api(monkeypatch, tmp_path):
    fixture, digest = fixture_file(tmp_path, count=61)
    output = tmp_path / 'report.json'
    requests = install_transport(monkeypatch)
    assert evaluator._live_heldout(fixture, digest, output) == 2
    report = json.loads(output.read_text())
    assert report['stop_reason'] == 'fixture_invalid' and report['inference_posts'] == 0
    assert not requests


def test_heldout_invalidates_report_if_fixture_bytes_change(monkeypatch, tmp_path):
    fixture, digest = fixture_file(tmp_path)
    output = tmp_path / 'report.json'

    def mutate_at_end(request, count):
        if count == 60:
            fixture.write_bytes(fixture.read_bytes() + b'\n')

    install_transport(monkeypatch, mutate_at_end)
    assert evaluator._live_heldout(fixture, digest, output) == 2
    report = json.loads(output.read_text())
    assert report['status'] == 'completed' and report['valid'] is False
    assert report['before']['fixture_sha256'] != report['after']['fixture_sha256']
    assert report['stop_reason'] == 'configuration_changed'


def test_heldout_pressure_stop_does_not_send_next_post(monkeypatch, tmp_path):
    fixture, digest = fixture_file(tmp_path)
    output = tmp_path / 'report.json'
    requests = install_transport(monkeypatch)
    levels = iter([1, 1, 4])

    def guard():
        level = next(levels)
        if level == 4:
            raise evaluator._HeldoutStop('critical_memory_pressure')
        return level

    monkeypatch.setattr(evaluator, '_heldout_guard', guard)
    assert evaluator._live_heldout(fixture, digest, output) == 2
    report = json.loads(output.read_text())
    assert report['stop_reason'] == 'critical_memory_pressure'
    assert report['inference_posts'] == len(report['routes']) == 1
    assert [req.method for req in requests] == ['GET', 'GET', 'POST']


def test_heldout_requires_exact_guidance_reason_and_no_results():
    row = {'classification_match': True, 'status': 'guidance', 'reason': 'ambiguous_input', 'results': {}}
    assert not evaluator._heldout_score(CASE, row)['classification_match']
    assert evaluator._heldout_score(CASE | {'kind': 'ambiguous'}, row)['classification_match']
    row['results'] = {'퇴직금': 10}
    assert not evaluator._heldout_score(CASE | {'kind': 'ambiguous'}, row)['classification_match']


def test_heldout_rejects_critical_pressure_and_maintenance_window(monkeypatch):
    monkeypatch.setattr(evaluator.subprocess, 'check_output', lambda *args, **kwargs: '4\n')
    with pytest.raises(evaluator._HeldoutStop, match='critical_memory_pressure'):
        evaluator._heldout_guard(datetime(2026, 11, 12, 5, 30, tzinfo=evaluator.SEOUL))
    with pytest.raises(evaluator._HeldoutStop, match='maintenance_window'):
        evaluator._heldout_guard(datetime(2026, 11, 12, 6, 20, tzinfo=evaluator.SEOUL))
