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

    def ask(app, question, model, fmt):
        calls.append((question, model, fmt))
        hits = [{'law': '근로기준법', 'article_id': no} for no in ('제26조', '제27조')]
        yield 'generate', hits, '', {}
        yield 'done', hits, raw[len(calls) - 1], {'finish': 'stop', 'total': 1}

    monkeypatch.setattr(service, 'ask', ask)
    output = io.StringIO()
    summary = ev.run_questions(SimpleNamespace(), 'pro', 'concise', rows, output)
    first, second = map(json.loads, output.getvalue().splitlines())
    assert calls == [('질문 1', 'pro', 'concise'), ('질문 2', 'pro', 'concise')]
    assert (first['answer'], first['cited'], first['context']) == (raw[0], [a], [a, b])
    assert first['all_reference_retrieved'] and not first['all_reference_cited']
    assert second['refused'] and not second['all_reference_cited']
    assert (
        summary['overall']
        == summary['by_error_type']['예외']
        == {'questions': 2, 'all_reference_retrieved': 2, 'all_reference_cited': 0, 'refused': 1}
    )


def test_reasoning_stops_if_a_chat_model_would_reload(monkeypatch):
    def ask(*args, **kwargs):
        yield 'load', [], '', {}
        raise AssertionError('Generation must not start')

    monkeypatch.setattr(service, 'ask', ask)
    rows = [{'id': 'case', 'error_type': '예외', 'question': '질문', 'article_ids': '근로기준법 제26조'}]
    output = io.StringIO()
    assert ev.run_questions(SimpleNamespace(), 'pro', 'concise', rows, output) is None
    assert json.loads(output.getvalue())['error'] == 'chat_model_not_loaded'


def test_question_failure_records_stage_and_preserves_exception(monkeypatch):
    cause = TimeoutError('timeout')

    def ask(*args, **kwargs):
        raise service.AskError('model_server_timeout', 'rerank', cause) from cause

    monkeypatch.setattr(service, 'ask', ask)
    rows = [{'id': 'case', 'error_type': '예외', 'question': '질문', 'article_ids': '근로기준법 제26조'}]
    output = io.StringIO()
    with pytest.raises(service.AskError) as failed:
        ev.run_questions(SimpleNamespace(), 'pro', 'concise', rows, output)
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
