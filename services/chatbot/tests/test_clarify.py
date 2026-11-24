import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from fakes import HITS, FakeRetriever
from lawbot import service
from lawbot.llm import LLM
from test_pipeline import FakeClient, chunk

QUESTION = '상담 원문을 로그에 넣지 마세요.'
FOLLOWUP = '상시 근로자가 몇 명인가요?'
REPLY = '일곱 명입니다.'
ASK = json.dumps({'ask': True, 'missing': ['상시 근로자 수'], 'question': FOLLOWUP}, ensure_ascii=False)
NO_ASK = '{"ask":false,"missing":[],"question":""}'
ANSWER = '【결론】예고해야 합니다.\n【근거 조문】근로기준법 제26조'


def completion(text, finish='stop'):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=23, completion_tokens=9),
    )


class Client(FakeClient):
    def __init__(self, *replies):
        super().__init__([chunk(ANSWER, role=True), chunk(finish='stop'), chunk(usage=(20, 8))])
        self.replies = iter(replies)

    def create(self, **kw):
        if kw['stream']:
            return super().create(**kw)
        self.calls.append(kw)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return completion(reply) if isinstance(reply, str) else reply


def make_app(path, client):
    return service.LawbotApp(
        SimpleNamespace(name='v5', model=SimpleNamespace(key='m')),
        FakeRetriever(),
        lambda: LLM('m', client, 'writer system'),
        settings=SimpleNamespace(request_log=path),
    )


def wire(client):
    return json.dumps(client.calls[-1]['messages'], ensure_ascii=False).encode()


@pytest.mark.parametrize(
    ('replies', 'status', 'reason', 'retried'),
    [
        ([NO_ASK], 'not_asked', None, 0),
        (['```json\n' + NO_ASK + '\n```', NO_ASK], 'not_asked', None, 1),
        (
            ['{"ask":0,"missing":[],"question":""}', '{"ask":true,"missing":[],"question":" "}'],
            'failed_open',
            'invalid_json',
            1,
        ),
        ([completion(NO_ASK, 'length')], 'failed_open', 'length', 0),
        ([openai.APIConnectionError(request=httpx.Request('POST', '/'))], 'failed_open', 'call_error', 0),
    ],
)
def test_no_followup_keeps_writer_input_identical(tmp_path, replies, status, reason, retried):
    plain = Client()
    assert list(service.ask(make_app(tmp_path / 'a', plain), QUESTION))[-1][2] == ANSWER
    client = Client(*replies)
    log = tmp_path / 'b'
    _, _, raw, st = list(service.ask(make_app(log, client), QUESTION, clarify=True, trace=True))[-1]
    assert raw == ANSWER and wire(client) == wire(plain)
    assert st['_writer_messages'] == plain.calls[0]['messages']
    row = json.loads(log.read_text())
    assert (row['clarify_status'], row['clarify_failure'], row['clarify_retries']) == (status, reason, retried)
    assert row['clarify_calls'] == 1 + retried
    assert row['clarify_prompt_tokens'] == (0 if reason == 'call_error' else 23 * (1 + retried))
    for call in client.calls[:-1]:
        assert call['stream'] is False and call['temperature'] == 0 and call['max_tokens'] == 256
        assert call['extra_body'] == {'chat_template_kwargs': {'enable_thinking': False}}
        assert call['extra_headers'] == client.calls[-1]['extra_headers']
    if retried:
        assert '형식 오류' in client.calls[1]['messages'][-1]['content']
    assert len(st['_clarify_attempts']) == 1 + retried


def test_followup_resumes_same_articles_once_and_keeps_trace_out_of_log(tmp_path, monkeypatch, capsys):
    client = Client(ASK)
    path = tmp_path / 'requests'
    app = make_app(path, client)
    ((stage, hits, followup, stats),) = service.ask(app, QUESTION, clarify=True)
    assert (stage, followup, len(client.calls)) == ('clarify', FOLLOWUP, 1)
    assert stats['_clarify_attempts'][0]['raw'] == ASK
    assert stats['_clarify_attempts'][0]['parsed']['question'] == FOLLOWUP
    assert stats['_clarify_attempts'][0]['messages'] == client.calls[0]['messages']
    assert HITS[0]['title'] in client.calls[0]['messages'][1]['content']

    def unexpected_search(*args, **kwargs):
        pytest.fail('resume must not retrieve')

    monkeypatch.setattr(app.retriever, 'search', unexpected_search)
    stage, resumed_hits, raw, st = list(
        service.answer_after_clarification(app, QUESTION, hits, followup, REPLY, trace=True)
    )[-1]
    assert (stage, resumed_hits, raw) == ('done', hits, ANSWER)
    assert resumed_hits is hits and len(client.calls) == 2
    assert st['_writer_messages'] == client.calls[1]['messages']
    msg = st['_writer_messages'][1]['content']
    assert msg.endswith(f'[질문]\n{QUESTION}\n\n[확인 질문]\n{FOLLOWUP}\n\n[사용자 답]\n{REPLY}')
    assert msg.startswith('[참고 조문]\n')
    records = path.read_text()
    assert [json.loads(line)['clarify_status'] for line in records.splitlines()] == ['asked', 'skipped']
    assert not any(text in records for text in (QUESTION, FOLLOWUP, REPLY, '상시 근로자 수', 'messages'))
    assert not any(text in capsys.readouterr().out for text in (QUESTION, FOLLOWUP, REPLY))


def test_calculation_guidance_skips_clarification(tmp_path):
    client = Client('{"calculator":"unsupported"}')
    stages = list(service.ask(make_app(tmp_path / 'log', client), QUESTION, clarify=True, calculation_candidate=True))
    assert stages[0][0] == 'calculation'
    assert stages[-1][0] == 'done'
    assert len(client.calls) == 2 and not client.calls[0]['stream'] and client.calls[1]['stream']
    assert json.loads((tmp_path / 'log').read_text())['clarify_status'] == 'skipped'
