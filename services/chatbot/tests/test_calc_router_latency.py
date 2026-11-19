import httpx
import openai
import pytest

from lawbot.api_client import request_timeout
from lawbot.calc_router import route_calculation
from lawbot.llm import LLM
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
        # An unusable stage-1 selection ends routing without a second request.
        assert LLM('pro', client, 'system').route_calculation('질문') is None

    assert len(requests) == 1
    assert requests[0].extensions['timeout']['connect'] == 5
    assert requests[0].extensions['timeout']['read'] == 90


def test_router_rejects_completion_without_finish_reason():
    def reply(request):
        return httpx.Response(
            200,
            json={
                'id': 'route',
                'object': 'chat.completion',
                'created': 0,
                'model': 'pro',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'lookup'}, 'finish_reason': None}],
            },
        )

    with openai.OpenAI(
        base_url='http://127.0.0.1:9/v1',
        api_key='fixture',
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(reply)),
    ) as client:
        outcome = route_calculation('질문', LLM('pro', client, 'system').route_calculation, {})
    assert outcome.reason == 'model_error'
    assert outcome.payload['status'] == 'none'


@pytest.mark.parametrize(
    ('error_type', 'reason'),
    [
        (httpx.ConnectTimeout, 'connect_timeout'),
        (httpx.ReadTimeout, 'read_timeout'),
        (httpx.WriteTimeout, 'write_timeout'),
        (httpx.PoolTimeout, 'pool_timeout'),
        (None, 'model_error'),
    ],
)
def test_router_failure_reports_transport_phase_or_generation_error(error_type, reason):
    requests = []

    def fail_request(request):
        requests.append(request)
        if error_type is None:
            return httpx.Response(503, json={'error': {'type': 'server_error', 'message': 'generation unavailable'}})
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

    def ask(app, question, *, calculation_candidate=False):
        calls.append((question, calculation_candidate))
        timing = {'ttft': 0.040, 'first_text': 0.120}
        if calculation_candidate:
            timing = {'ttft': 0.050, 'first_text': 0.250, 'calculation_metrics': {'router_ms': 90}}
        yield 'done', [], '답변', timing

    monkeypatch.setattr(eval_calc_router, '_time_guard', lambda: None)
    monkeypatch.setattr(eval_calc_router.service, 'ask', ask)
    case = {'id': 'CR001', 'question': '질문'}
    answer = eval_calc_router._answer_pair(object(), case, routed_first=False)

    assert calls == [('질문', False), ('질문', True)]
    assert answer['ttft_ms'] == 50
    assert answer['baseline']['first_text_ms'] == 120
    assert answer['ttft_delta_ms'] == 10
    assert answer['first_text_delta_ms'] == 130
    assert eval_calc_router._latency_report([], [answer])['answer_first_text_ms']['delta']['p95'] == 130


@pytest.mark.parametrize('concurrent', [1, 2, None, True, '1', 0])
@pytest.mark.parametrize('mlx_lm_version', ['0.31.2', None])
def test_live_configuration_checks_only_embedding_health_and_residency(monkeypatch, concurrent, mlx_lm_version):
    from types import SimpleNamespace

    cfg = eval_calc_router.settings.load(
        {
            'LAWBOT_PROFILE': 'v5',
            'LAWBOT_MODEL_SERVER': 'http://127.0.0.1:19001',
            'LAWBOT_MODEL_TOKEN': 'embedding-only-secret',
            'LAWBOT_LLM_BASE_URL': 'http://127.0.0.1:19/v1',
            'LAWBOT_LLM_API_KEY': 'generation-only-secret',
            'LAWBOT_RERANKER_ENABLED': '0',
        }
    )
    pro = cfg.profile.model.model
    calls = []

    def generation_models(client):
        assert str(client.base_url) == 'http://127.0.0.1:19/v1/'
        assert client.api_key == 'generation-only-secret'
        calls.append('generation_models')
        return [
            {'id': pro, 'revision': 'generation-rev', 'max_concurrent': concurrent, 'mlx_lm_version': mlx_lm_version}
        ]

    def health(request):
        assert request.url.path == '/health'
        return httpx.Response(200, json={'ready': True, 'commit': 'embedding-commit'})

    monkeypatch.setattr(eval_calc_router, '_time_guard', lambda: None)
    monkeypatch.setattr(eval_calc_router.service, 'generation_models', generation_models)
    monkeypatch.setattr(eval_calc_router.subprocess, 'check_output', lambda *args, **kwargs: 'webapp-commit')
    with httpx.Client(base_url=cfg.model_server, transport=httpx.MockTransport(health)) as http:
        client = SimpleNamespace(
            http=http,
            models=lambda: [
                {'id': cfg.profile.emb_model, 'type': 'embedding', 'loaded': True, 'revision': 'embedding-rev'}
            ],
        )
        if type(concurrent) is not int or concurrent < 1:
            with pytest.raises(ValueError, match='max_concurrent'):
                eval_calc_router._configuration(cfg, client)
            return
        if not mlx_lm_version:
            with pytest.raises(SystemExit, match='mlx_lm_version'):
                eval_calc_router._configuration(cfg, client)
            return
        configuration = eval_calc_router._configuration(cfg, client)
    assert configuration['models'][pro] == {'revision': 'generation-rev'}
    assert configuration['model_residency'] == {cfg.profile.emb_model: True}
    assert configuration['generation_server_mlx_lm_version'] == '0.31.2'
    assert configuration['server_max_concurrent'] == concurrent
    assert calls == ['generation_models']
