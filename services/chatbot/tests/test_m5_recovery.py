import io
import json
import signal
import subprocess
import sys
from pathlib import Path

import httpx
import openai
import pytest

from lawbot import service
from lawbot.api_client import ModelServerClient
from lawbot.inference import OBSERVER, InferenceStopped
from lawbot_tools import eval_m5 as m5
from lawbot_tools import eval_m5_score as scoring
from test_clarify import ASK, NO_ASK, Client, make_app
from test_eval_m5 import item


@pytest.mark.parametrize(
    ('set_name', 'model_output', 'next_turn'),
    [
        ('general', NO_ASK, 'initial'),
        ('general', 'bad json', 'initial'),
        ('fact_missing', ASK, 'resumed'),
        ('general', ASK, 'diagnostic'),
    ],
)
@pytest.mark.parametrize('stop_reason', ['time_limit', 'interrupted'])
def test_guard_blocks_next_send_after_judgment(tmp_path, set_name, model_output, next_turn, stop_reason):
    client = Client(model_output)
    out, journal = io.StringIO(), io.StringIO()
    app = make_app(tmp_path / 'requests', client)
    result = m5.run_questions(
        app,
        [item(set_name)],
        'Aprime',
        {'I': '가상 답'},
        out,
        journal=journal,
        stop=lambda: stop_reason if client.calls else None,
    )
    assert result == (stop_reason, None)
    events = [json.loads(line) for line in journal.getvalue().splitlines()]
    assert [(e['stage'], e['event']) for e in events] == [('clarify', 'send'), ('clarify', 'complete')]
    assert len(client.calls) == 1
    record = json.loads(out.getvalue())
    assert record['invalid'] == 'incomplete_item' and record[next_turn]['verdict'] == 'interrupted'


def test_search_send_is_guarded_before_http_transport():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    def observe(event, stage, request_id, exc):
        if event == 'send':
            assert stage in ('embedding', 'rerank')
            raise InferenceStopped('time_limit')

    token = OBSERVER.set(observe)
    try:
        with httpx.Client(base_url='http://test.invalid', transport=httpx.MockTransport(respond)) as http:
            client = ModelServerClient('', 'fake', http=http)
            with pytest.raises(InferenceStopped):
                client.embed('emb', ['test'])
            with pytest.raises(InferenceStopped):
                client.rerank('rank', 'test', ['test'])
    finally:
        OBSERVER.reset(token)
    assert requests == []


def test_sigkill_recovery_preserves_sent_and_completed_rows_then_continues(tmp_path):
    code = """
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from lawbot_tools import eval_m5 as m5
from test_clarify import Client, make_app
from test_eval_m5 import item

class KillSecondWriter(Client):
    def create(self, **kw):
        if self.calls:
            os.kill(os.getpid(), signal.SIGKILL)
        return super().create(**kw)

path = Path(sys.argv[1])
with (path / m5.ANSWERS).open('w') as out, (path / m5.STEPS).open('w') as journal:
    m5.run_questions(make_app(path / 'requests', KillSecondWriter()),
                     [item('general', str(i)) for i in range(3)], 'A', {}, out, journal=journal)
"""
    child = subprocess.run(
        [sys.executable, '-c', code, str(tmp_path), str(Path(__file__).parent)], capture_output=True, timeout=15
    )
    assert child.returncode == -signal.SIGKILL
    events = m5.read_jsonl(tmp_path / m5.STEPS)
    assert [(e['index'], e['event']) for e in events] == [(0, 'send'), (0, 'complete'), (1, 'send')]
    with (tmp_path / m5.ANSWERS).open('ab') as stream:
        stream.write(b'{"partial"')
    rows = [item('general', str(i)) for i in range(3)]
    manifest = {'segment_start': 0, 'invalid_segments': []}
    prior = m5.recover_segment(tmp_path, rows, manifest)
    assert prior[0]['initial']['verdict'] == 'shown'
    assert prior[1]['invalid'] == 'unverified_inflight'
    assert manifest['invalid_segments'] == [{'start': 0, 'end': 2, 'reason': 'missing_final_configuration'}]
    client = Client()
    with (tmp_path / m5.ANSWERS).open('a') as stream:
        result = m5.run_questions(
            make_app(tmp_path / 'requests', client), m5.remaining_items(rows, prior), 'A', {}, stream, offset=len(prior)
        )
    assert result == ('complete', None) and len(client.calls) == 1
    assert [r['id'] for r in m5.valid_records(m5.read_answers(tmp_path), manifest)] == ['2']


def test_reflected_writer_http_error_has_only_failure_metadata(tmp_path, capsys):
    reply = '사용자만 아는 합성 비공개 사실'

    class ReflectAfterClarification(Client):
        def create(self, **kw):
            if kw['stream']:
                content = kw['messages'][-1]['content']
                request = httpx.Request('POST', '/')
                response = httpx.Response(400, request=request, json={'error': {'message': content}})
                raise openai.BadRequestError(content, response=response, body=response.json())
            return super().create(**kw)

    app = make_app(tmp_path / 'requests', ReflectAfterClarification(ASK))
    result, exc = m5.run_item(app, item('fact_missing'), 'Aprime', {'I': reply}, set())
    assert isinstance(exc, service.AskError)
    rid = result['resumed']['request_id']
    m5.report_failure(exc.stage, exc, tmp_path, request_id=rid)
    captured = capsys.readouterr()
    dump = (tmp_path / 'failure.txt').read_text()
    assert dump == captured.err
    fields = {'request_id': rid, 'stage': 'generation', 'exception': 'BadRequestError', 'status_code': 400}
    assert json.loads(dump) == fields
    request_log = (tmp_path / 'requests').read_text()
    assert json.loads(request_log.splitlines()[-1])['failure'] == fields
    assert not any(reply in output for output in (captured.out, captured.err, dump, request_log))


def test_exposure_and_citation_counts_include_visible_question_not_diagnostic(tmp_path):
    ask = json.dumps({'ask': True, 'missing': ['조건'], 'question': '사회복지사업법 제999조에 해당하나요?'})
    fact = item('fact_missing', missing_facts=['조건'])
    other = item(
        'general', 'G', article_ids=['근로기준법 제26조'], answer_key={'must_include': [], 'must_not_include': []}
    )
    app = make_app(tmp_path / 'requests', Client(ask, ask))
    resumed, _ = m5.run_item(app, fact, 'Aprime', {'I': '가상 답'}, set())
    diagnostic, _ = m5.run_item(app, other, 'Aprime', {}, set())
    initial = resumed['initial']
    assert '제999조⚠확인되지 않은 인용' in initial['shown']
    assert initial['unmarked_exposure'] == 0 and initial['conclusion_citations'][0]['verdict'] == 'outside'
    initial['shown'] = initial['clarification_raw']
    initial['unmarked_exposure'] = m5.unmarked_exposure(initial['shown'], None, [], set())
    diagnostic['diagnostic']['unmarked_exposure'] = 7
    summary, _ = scoring.score([fact, other], [resumed, diagnostic])
    assert summary['overall']['unmarked_exposure'] == 1
    assert summary['overall']['citation_verdicts']['conclusion']['outside'] == 2
