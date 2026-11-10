import httpx
import openai
import pytest

from lawbot.api_client import request_timeout
from lawbot.calc_router import route_calculation
from lawbot.llm import LLM
from lawbot_common.api import SERVER_BUSY
from lawbot_tools import eval_calc_router


def test_router_inherits_configured_read_limit():
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                'id': 'route',
                'object': 'chat.completion',
                'created': 0,
                'model': 'pro',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{}'}, 'finish_reason': 'stop'}],
            },
        )

    with openai.OpenAI(
        base_url='http://127.0.0.1:9/v1',
        api_key='fixture',
        max_retries=0,
        timeout=request_timeout(90),  # pyright: ignore[reportArgumentType]
        http_client=httpx.Client(transport=httpx.MockTransport(reply)),  # pyright: ignore[reportArgumentType]
    ) as client:
        assert LLM('pro', client, 'system').route_calculation('질문') == '{}'

    assert len(requests) == 1
    assert requests[0].extensions['timeout']['connect'] == 5
    assert requests[0].extensions['timeout']['read'] == 90


@pytest.mark.parametrize(
    ('error_type', 'reason'),
    [
        (httpx.ConnectTimeout, 'connect_timeout'),
        (httpx.ReadTimeout, 'read_timeout'),
        (httpx.WriteTimeout, 'write_timeout'),
        (httpx.PoolTimeout, 'pool_timeout'),
        (None, 'queue_full'),
    ],
)
def test_router_failure_reports_only_known_transport_or_queue_phase(error_type, reason):
    requests = []

    def fail_request(request):
        requests.append(request)
        if error_type is None:
            return httpx.Response(503, json={'error': {'type': SERVER_BUSY, 'message': 'fixture queue full'}})
        raise error_type('fixture timeout', request=request)

    with openai.OpenAI(
        base_url='http://127.0.0.1:9/v1',
        api_key='fixture',
        max_retries=0,
        timeout=request_timeout(90),  # pyright: ignore[reportArgumentType]
        http_client=httpx.Client(transport=httpx.MockTransport(fail_request)),  # pyright: ignore[reportArgumentType]
    ) as client:
        llm = LLM('pro', client, 'system')
        outcome = route_calculation('질문', llm.route_calculation, {})

    assert len(requests) == 1
    assert outcome.reason == reason
    assert outcome.payload['status'] == 'none'


def test_router_latency_percentiles_and_missing_measurements():
    assert eval_calc_router._latency_summary([None, 100, 200, 300]) == {'n': 3, 'p50': 200, 'p95': 290}
    assert eval_calc_router._latency_summary([100]) == {'n': 1, 'p50': 100, 'p95': 100}
    assert eval_calc_router._latency_summary([None]) == {'n': 0, 'p50': None, 'p95': None}


def test_answer_sample_compares_two_paths_on_same_question(monkeypatch):
    calls = []

    def ask(app, question, model, *, calculation_candidate=False):
        calls.append((question, model, calculation_candidate))
        timing = {'ttft': 0.040, 'first_text': 0.120}
        if calculation_candidate:
            timing = {'ttft': 0.050, 'first_text': 0.250, 'calculation_metrics': {'router_ms': 90}}
        yield 'done', [], '답변', timing

    monkeypatch.setattr(eval_calc_router, '_time_guard', lambda: None)
    monkeypatch.setattr(eval_calc_router.service, 'ask', ask)
    case = {'id': 'CR001', 'question': '질문'}
    answer = eval_calc_router._answer_pair(object(), case, routed_first=False)

    assert calls == [('질문', 'pro', False), ('질문', 'pro', True)]
    assert answer['ttft_ms'] == 50
    assert answer['baseline']['first_text_ms'] == 120
    assert answer['ttft_delta_ms'] == 10
    assert answer['first_text_delta_ms'] == 130
    assert eval_calc_router._latency_report([], [answer])['answer_first_text_ms']['delta']['p95'] == 130
