import io
import json
import sys
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

from lawbot import service
from lawbot_tools import eval_reasoning as ev
from lawbot_tools.eval_v4 import load_test


def test_reasoning_is_a_separate_fixed_validation_set():
    rows = ev.load_questions()
    assert [row['id'] for row in rows] == [*(f'ERR{i:03}' for i in range(1, 31)), 'CARD01', 'CARD02']
    assert sorted(Counter(row['error_type'] for row in rows).values()) == [2, 7, 7, 8, 8]
    assert all(all(row.values()) for row in rows)
    assert {row['id'] for row in rows}.isdisjoint(load_test('val', 'v5')['id'])
    assert {row['id'] for row in rows}.isdisjoint(load_test('test', 'v5')['id'])


def test_reasoning_uses_web_answer_and_counts_all_reference_articles(monkeypatch):
    a, b = '근로기준법 제26조', '근로기준법 제27조'
    rows = [
        {'id': 'case1', 'error_type': '예외', 'question': '질문 1', 'article_ids': f'{a}|{b}'},
        {'id': 'case2', 'error_type': '예외', 'question': '질문 2', 'article_ids': a},
    ]
    raw = [f'【결론】 답변\n【근거 조문】 {a}', '【결론】 확인할 수 없습니다.\n【근거 조문】 해당 없음']
    calls = []

    def ask(app, question, model):
        calls.append((question, model))
        hits = [{'law': '근로기준법', 'article_id': no} for no in ('제26조', '제27조')]
        yield 'generate', hits, '', {}
        yield 'done', hits, raw[len(calls) - 1], {'finish': 'stop', 'total': 1}

    monkeypatch.setattr(service, 'ask', ask)
    output = io.StringIO()
    summary = ev.run_questions(SimpleNamespace(), 'pro', rows, output)
    first, second = map(json.loads, output.getvalue().splitlines())
    assert calls == [('질문 1', 'pro'), ('질문 2', 'pro')]
    assert (first['answer'], first['cited'], first['context']) == (raw[0], [a], [a, b])
    assert first['all_reference_retrieved'] and not first['all_reference_cited']
    assert second['refused'] and not second['all_reference_cited']
    assert (
        summary['overall']
        == summary['by_error_type']['예외']
        == {'questions': 2, 'all_reference_retrieved': 2, 'all_reference_cited': 0, 'refused': 1}
    )


def test_reasoning_stops_if_generation_finishes_without_an_answer(monkeypatch):
    def ask(*args, **kwargs):
        yield 'done', [], '', {}

    monkeypatch.setattr(service, 'ask', ask)
    rows = [{'id': 'case', 'error_type': '예외', 'question': '질문', 'article_ids': '근로기준법 제26조'}]
    output = io.StringIO()
    assert ev.run_questions(SimpleNamespace(), 'pro', rows, output) is None
    assert json.loads(output.getvalue())['error'] == 'empty_answer'


def test_question_failure_records_stage_and_preserves_exception(monkeypatch):
    cause = TimeoutError('timeout')

    def ask(*args, **kwargs):
        raise service.AskError('model_server_timeout', 'rerank', cause) from cause

    monkeypatch.setattr(service, 'ask', ask)
    rows = [{'id': 'case', 'error_type': '예외', 'question': '질문', 'article_ids': '근로기준법 제26조'}]
    output = io.StringIO()
    with pytest.raises(service.AskError) as failed:
        ev.run_questions(SimpleNamespace(), 'pro', rows, output)
    assert failed.value.__cause__ is cause
    assert json.loads(output.getvalue())['stage'] == 'rerank'


@pytest.mark.parametrize('stage', ['실행 전 구성 확인', '웹앱 초기화'])
def test_failure_retains_safe_diagnostics(monkeypatch, tmp_path, capsys, stage):
    output = tmp_path / 'run'
    url, token = 'http://127.0.0.1:12345', 'test-model-secret'
    monkeypatch.setattr(sys, 'argv', ['eval_reasoning', '--output', str(output)])
    monkeypatch.setattr(ev.envfile, 'load', lambda: None)
    monkeypatch.setenv('LAWBOT_MODEL_SERVER', url)
    monkeypatch.setenv('LAWBOT_MODEL_TOKEN', token)
    monkeypatch.setenv('LAWBOT_LLM_BASE_URL', 'http://127.0.0.1:12346/v1')
    monkeypatch.setenv('LAWBOT_LLM_API_KEY', 'test-generation-secret')

    def snapshot(cfg, client, *, preflight=False):
        if stage == '실행 전 구성 확인':
            request = httpx.Request('GET', url + '/v1/models')
            httpx.Response(503, request=request).raise_for_status()
        return {}

    def load_app(cfg):
        try:
            raise OSError(f'조문 CSV 해시 불일치 ({cfg.model_server}, {cfg.model_token})')
        except OSError as exc:
            raise RuntimeError('웹앱 구성 검증 실패') from exc

    monkeypatch.setattr(ev, 'snapshot', snapshot)
    monkeypatch.setattr(service, 'load_app', load_app)
    assert ev.main() == 1
    diagnostics = (output / 'failure.txt').read_text(encoding='utf-8')
    assert diagnostics == capsys.readouterr().err
    assert stage in diagnostics and 'Traceback (most recent call last)' in diagnostics
    assert url not in diagnostics and '12345' not in diagnostics and token not in diagnostics
    if stage == '실행 전 구성 확인':
        assert '503' in diagnostics and '/v1/models' in diagnostics
        assert not (output / 'before.json').exists()
    else:
        assert 'OSError: 조문 CSV 해시 불일치' in diagnostics
        assert 'RuntimeError: 웹앱 구성 검증 실패' in diagnostics
        assert (output / 'after.json').is_file()


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('concurrent', [1, 2, None, True, '1', 0])
@pytest.mark.parametrize('mlx_lm_version', ['0.31.2', None])
def test_snapshot_separates_generation_from_embedding_metadata(monkeypatch, external, concurrent, mlx_lm_version):
    cfg = ev.settings.load(
        {
            'LAWBOT_PROFILE': 'v5',
            'LAWBOT_RERANKER_ENABLED': '1',
            'LAWBOT_DECREE_ENABLED': '1',
            'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:19001',
            'LAWBOT_MODEL_TOKEN': 'embedding-only-secret',
            'LAWBOT_LLM_BASE_URL': 'https://generation.example/v1' if external else 'http://127.0.0.1:19/v1',
            'LAWBOT_LLM_API_KEY': 'generation-only-secret',
            'LAWBOT_LLM_ALLOW_EXTERNAL': '1' if external else '',
        }
    )
    requests = []
    original_client = httpx.Client
    spec = cfg.profile.models[0]

    def reply(request):
        requests.append(request)
        if request.url.port == 19001:
            assert request.headers['authorization'] == 'Bearer embedding-only-secret'
            if request.url.path == '/health':
                return httpx.Response(200, json={'ready': True, 'commit': 'embedding-commit'})
            assert request.url.path == '/v1/models'
            return httpx.Response(
                200,
                json={
                    'data': [
                        {'id': cfg.profile.emb_model, 'type': 'embedding', 'loaded': True, 'revision': 'embedding-rev'},
                        {'id': cfg.reranker.model, 'type': 'rerank', 'loaded': True, 'revision': 'reranker-rev'},
                    ]
                },
            )
        assert not external
        assert request.url.port == 19 and request.url.path == '/v1/models'
        assert request.headers['authorization'] == 'Bearer generation-only-secret'
        return httpx.Response(
            200,
            json={
                'data': [
                    {
                        'id': spec.model,
                        'revision': 'generation-rev',
                        'max_concurrent': concurrent,
                        'mlx_lm_version': mlx_lm_version,
                    }
                ]
            },
        )

    class MockClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(reply))

    monkeypatch.setattr(ev.httpx, 'Client', MockClient)
    monkeypatch.setattr(ev.subprocess, 'check_output', lambda *args, **kwargs: 'webapp-commit')
    with MockClient(base_url=cfg.model_server, headers={'Authorization': f'Bearer {cfg.model_token}'}) as client:
        if not external and (type(concurrent) is not int or concurrent < 1):
            with pytest.raises(ValueError, match='max_concurrent'):
                ev.snapshot(cfg, client)
            return
        if not external and not mlx_lm_version:
            with pytest.raises(RuntimeError, match='mlx_lm_version'):
                ev.snapshot(cfg, client)
            return
        configuration = ev.snapshot(cfg, client)
    assert configuration['models'][-1] == {
        'id': spec.model,
        'revision': None if external else 'generation-rev',
    }
    assert configuration['generation_server_mlx_lm_version'] == (None if external else '0.31.2')
    assert configuration['server_max_concurrent'] == (None if external else concurrent)
    changed = configuration | {'server_max_concurrent': 3}
    assert ev.comparable(configuration) != ev.comparable(changed)
    assert ev.comparable(configuration) != ev.comparable(configuration | {'generation_server_mlx_lm_version': '0.32.0'})
    assert len(requests) == (2 if external else 3)
