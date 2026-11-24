import io
import json
import signal
from dataclasses import dataclass
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from lawbot import service
from lawbot_tools import eval_m5 as m5
from lawbot_tools import eval_m5_score as scoring
from test_clarify import ASK, NO_ASK, Client, make_app


def item(group, ident='I', **kwargs):
    return {'set': group, 'id': ident, 'question': '합성 질문', **kwargs}


def test_fixed_sets_order_and_replies():
    rows, replies = m5.load_items()
    assert [(name, sum(r['set'] == name for r in rows)) for name in m5.SETS] == [
        ('reasoning', 32),
        ('fact_missing', 15),
        ('out_of_scope', 15),
        ('general', 200),
    ]
    assert [r['id'] for r in rows if r['set'] == 'general'] == m5.read_json(m5.SET_DIR / 'general_sample200.json')[
        'ids'
    ]
    assert list(dict.fromkeys(r['set'] for r in rows)) == list(m5.SETS)
    assert replies.keys() == {r['id'] for r in rows if r['set'] == 'fact_missing'}


@pytest.mark.parametrize('group', ['fact_missing', 'general'])
def test_run_keeps_primary_clarification_separate_from_writer(tmp_path, group):
    row = item(group)
    client = Client(ASK)
    app = make_app(tmp_path / 'requests', client)
    result, error = m5.run_item(app, row, 'Aprime', {'I': '가상 사실'}, set())
    assert error is None
    initial = result['initial']
    assert initial['verdict'] == 'clarify' and initial['clarification']['attempts'][0]['raw'] == ASK
    assert initial['writer_messages'] == []
    assert initial['provided'][0]['rank'] == 1
    followup = result['resumed' if group == 'fact_missing' else 'diagnostic']
    assert followup['verdict'] == 'shown' and followup['ground_citations'][0]['verdict'] == 'ok'
    assert len(client.calls) == 2 and followup['writer_messages'] == client.calls[-1]['messages']
    assert result['unnecessary_clarification'] == (group != 'fact_missing')
    if group == 'fact_missing':
        assert followup['writer_messages'][1]['content'].endswith('[사용자 답]\n가상 사실')
        assert result['system_seconds'] == initial['timing']['complete'] + followup['timing']['complete']
    else:
        assert '[확인 질문]' not in followup['writer_messages'][1]['content']
        assert result['system_seconds'] == initial['timing']['complete']
    assert len((tmp_path / 'requests').read_text().splitlines()) == 2


def test_run_records_request_error_and_does_not_start_next_item(tmp_path, monkeypatch):
    app = make_app(tmp_path / 'requests', Client(NO_ASK))
    monkeypatch.setattr(app.retriever, 'search', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError('test')))
    out = io.StringIO()
    state, error = m5.run_questions(app, [item('general', '1'), item('general', '2')], 'A', {}, out)
    assert state == 'request_error' and isinstance(error, service.AskError)
    row = json.loads(out.getvalue())
    assert row['id'] == '1' and row['initial']['error']['stage'] == 'search'


def test_clarification_outage_scores_writer_and_stops_on_third_failure(tmp_path):
    connection_error = openai.APIConnectionError(request=httpx.Request('POST', '/'))
    client = Client(connection_error, connection_error, connection_error, NO_ASK)
    app = make_app(tmp_path / 'requests', client)
    rows = [
        item(
            'general',
            str(i),
            article_ids=['근로기준법 제26조'],
            answer_key={'must_include': [['예고']], 'must_not_include': []},
        )
        for i in range(4)
    ]
    out = io.StringIO()
    state, error = m5.run_questions(app, rows, 'Aprime', {}, out)
    assert (state, error) == ('clarify_unavailable', None)
    results = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(results) == 3 and len(client.calls) == 6
    assert [r['initial']['clarification']['failure'] for r in results] == ['call_error'] * 3
    score, _ = scoring.score(rows, results)
    assert score['general']['total'] == score['general']['cite_ok'] == 1
    assert score['overall']['clarify_failure_rate'] == 1
    assert score['overall']['timing_seconds']['verified_answer']['n'] == 3
    assert score['overall']['request_errors'] == 0


def test_router_call_failure_does_not_stop_writer_evaluation(tmp_path):
    client = Client(openai.APIConnectionError(request=httpx.Request('POST', '/')))
    app = make_app(tmp_path / 'requests', client)
    row = item('general', '1') | {'question': '총급여 4천만 원, 의료비 200만 원이면 의료비 세액공제액은 얼마인가요?'}
    out = io.StringIO()
    assert m5.run_questions(app, [row, item('general', '2')], 'A', {}, out) == ('complete', None)
    turn = json.loads(out.getvalue().splitlines()[0])['initial']
    assert turn['stage'] == 'done' and turn['router_reason'] == 'model_error'
    assert turn['calls']['router'] == turn['calls']['llm'] == 1


def test_stop_finishes_current_item_and_resume_never_replays_it(tmp_path):
    out = io.StringIO()
    client = Client()
    app = make_app(tmp_path / 'requests', client)
    rows = [item('general', '1'), item('general', '2')]
    status, exc = m5.run_questions(app, rows, 'A', {}, out, stop=lambda: 'interrupted' if client.calls else None)
    assert (status, exc) == ('interrupted', None)
    done = [json.loads(out.getvalue())]
    remaining = m5.remaining_items(rows, done)
    assert [r['id'] for r in remaining] == ['2']
    assert m5.run_questions(app, remaining, 'A', {}, out) == ('complete', None)
    assert [json.loads(line)['id'] for line in out.getvalue().splitlines()] == ['1', '2']


def test_kst_blackout_and_deadline():
    def at(hour, minute=0, second=0):
        return datetime(2026, 1, 1, hour, minute, second, tzinfo=m5.KST)

    with pytest.raises(ValueError, match='06:00'):
        m5.deadline(at(6, 59, 59), time(6))
    assert m5.deadline(at(5, 0), time(6)) == at(6)
    assert m5.deadline(at(7, 0), time(6)).day == 2
    assert m5.deadline(at(12, 0), time(12, 0, 1)) == at(12, 0, 1)


def test_scoring_uses_search_denominator_and_ignores_diagnostic(tmp_path):
    general = [
        item(
            'general',
            str(i),
            article_ids=['근로기준법 제26조'],
            answer_key={'must_include': [['예고']], 'must_not_include': [], 'must_not_in_conclusion': []},
        )
        for i in range(3)
    ]
    reasoning = item('reasoning', 'r', expected='answer', article_ids=['근로기준법 제26조', '근로기준법 제36조'])
    fact = item('fact_missing', 'f', missing_facts=['상시 근로자 수'])
    scope = item('out_of_scope', 'o')
    items = [reasoning, fact, scope, *general]
    app = make_app(tmp_path / 'requests', Client(ASK, ASK))
    answers = [
        m5.run_item(app, row, 'Aprime' if row is general[0] or row is fact else 'A', {'f': '7명'}, set())[0]
        for row in [fact, *general, reasoning, scope]
    ]
    answers[2]['initial']['provided'] = []  # Raw gold citation without gold retrieval is zero in total_all.
    scores, per_item = scoring.score(items, answers)
    assert scores['general']['search_hits'] == 2
    assert scores['general']['total'] == 0.5 and scores['general']['total_all'] == pytest.approx(1 / 3)
    assert per_item[('general', '0')]['total'] == 0
    assert scores['fact_missing']['ask_rate'] == 1 and scores['fact_missing']['review'][0]['reply'] == '7명'
    assert scores['reasoning']['all_provided'] == 1 and scores['reasoning']['all_cited'] == 0
    assert scores['out_of_scope']['correct'] == 0
    assert scores['overall']['generation_calls'] == 8


def test_visible_exposure_checks_actual_text():
    from fakes import HITS
    from lawbot.answer_format import review_answer

    raw = '【결론】근로기준법 제999조, 제999조.\n【근거 조문】근로기준법 제26조, 제998조'
    review = review_answer(raw, HITS)
    assert m5.unmarked_exposure(review.render(), review, HITS, set()) == 0
    broken = review.render().replace('⚠확인되지 않은 인용', '', 1)
    assert m5.unmarked_exposure(broken, review, HITS, set()) == 1


def test_cli_preflight_resume_and_compare_use_only_fake_inference(tmp_path, monkeypatch, capsys):
    rows = [
        item('reasoning', 'R', expected='answer', article_ids=['근로기준법 제26조']),
        item('fact_missing', 'F', missing_facts=['인원']),
        item('out_of_scope', 'O'),
        item(
            'general',
            'G',
            article_ids=['근로기준법 제26조'],
            answer_key={'must_include': [['예고']], 'must_not_include': [], 'must_not_in_conclusion': []},
        ),
    ]
    token = 'synthetic-secret-not-for-artifacts'
    rows[0]['question'] = f'테스트 {token}'

    @dataclass
    class FakeSettings:
        profile: object
        reranker: bool = True
        decree: bool = True
        llm_external: bool = False
        model_server: str = 'synthetic-embedding'
        llm_url: str = 'synthetic-generation'
        model_token: str = token
        llm_key: str = 'synthetic-key'
        server_wait: float = 0

    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime(2026, 1, 1, 12, tzinfo=tz)

    monkeypatch.setattr(m5, 'datetime', Clock)
    monkeypatch.setattr(m5, 'evaluation_env', lambda output: ({'log': output / 'requests.jsonl'}, (token,)))
    monkeypatch.setattr(m5.settings, 'load', lambda _env: FakeSettings(SimpleNamespace(name='v5')))
    monkeypatch.setattr(m5.httpx, 'Client', MagicMock())
    monkeypatch.setattr(m5, 'load_items', lambda: (rows, {'F': '일곱 명'}))
    metadata = {'models': [], 'sha256': m5.set_fingerprints(), 'webapp_commit': 'initial'}
    fail_preflight = True

    def capture(*args, preflight=False):
        if fail_preflight and preflight:
            raise RuntimeError(f'not ready: {token}')
        return dict(metadata)

    monkeypatch.setattr(m5, 'capture_config', capture)

    class InterruptingClient(Client):
        def create(self, **kw):
            result = super().create(**kw)
            signal.raise_signal(signal.SIGINT)
            return result

    clients = iter([InterruptingClient(), Client(), Client(NO_ASK, ASK, NO_ASK, NO_ASK), Client()])
    loaded = []

    def load_app(_config, *, cached_index_only):
        assert cached_index_only is True
        client = next(clients)
        loaded.append(client)
        return make_app(tmp_path / 'request-log', client)

    monkeypatch.setattr(service, 'load_app', load_app)
    base, new = tmp_path / 'a', tmp_path / 'b'
    base_cmd = ['run', '--condition', 'A', '--output', str(base)]
    assert m5.main(['run', '--condition', 'A', '--output', str(tmp_path / 'blocked')]) == 1
    assert not loaded and token not in (tmp_path / 'blocked' / 'failure.txt').read_text()
    fail_preflight = False
    assert m5.main(base_cmd) == 0
    assert m5.checked_run(base)['state'] == 'interrupted'
    assert [row['id'] for row in m5.read_answers(base)] == ['R']
    assert token not in (base / m5.ANSWERS).read_text()
    metadata['webapp_commit'] = 'drift'
    assert m5.main(base_cmd + ['--resume']) == 1 and len(loaded) == 1
    metadata['webapp_commit'] = 'initial'
    assert m5.main(base_cmd + ['--resume']) == 0
    assert [len(c.calls) for c in loaded] == [1, 3]
    assert m5.main(['run', '--condition', 'Aprime', '--output', str(new)]) == 0
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert m5.main(['score', str(new)]) == 0
    assert json.loads(capsys.readouterr().out)['fact_missing']['ask_rate'] == 1
    assert m5.main(['compare', str(base), str(new)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['gates']) == 6 and result['all_passed']
    assert result['pairs']['reasoning_cite_ok']['same'] == result['pairs']['general_total']['same'] == 1

    # Simulate a crash after the second writer finished and the third was sent.
    crash = tmp_path / 'crash'
    crash.mkdir()
    before = m5.checked_run(base)['before']
    m5.save_manifest(
        crash,
        {
            'state': 'running',
            'before': before,
            'after': None,
            'condition': 'A',
            'segment_start': 1,
            'invalid_segments': [],
        },
    )
    with (crash / m5.ANSWERS).open('w', encoding='utf-8') as answers:
        for record in m5.read_answers(base)[:2]:
            m5.append_record(answers, record)
    with (crash / m5.STEPS).open('w', encoding='utf-8') as journal:
        m5.append_record(journal, {'index': 2, 'event': 'send'})
    assert m5.main(['run', '--condition', 'A', '--output', str(crash), '--resume']) == 0
    answers = m5.read_answers(crash)
    assert [r['id'] for r in answers] == ['R', 'F', 'O', 'G'] and len(loaded[-1].calls) == 1
    assert [r['id'] for r in m5.valid_records(answers, m5.checked_run(crash))] == ['R', 'G']
    assert m5.main(['compare', str(crash), str(new)]) == 1
