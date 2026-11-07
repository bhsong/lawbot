import io
import json
from collections import Counter
from types import SimpleNamespace

from lawbot import service
from lawbot_tools import eval_reasoning as ev
from lawbot_tools.eval_v4 import load_test


def test_reasoning_is_a_separate_fixed_validation_set():
    rows = ev.load_questions()
    assert [row['id'] for row in rows] == [f'ERR{i:03}' for i in range(1, 31)]
    assert sorted(Counter(row['error_type'] for row in rows).values()) == [7, 7, 8, 8]
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
