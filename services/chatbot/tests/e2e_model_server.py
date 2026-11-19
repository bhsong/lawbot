# Test-only HTTP servers: protected generation fake and the real embedding/rerank app with fake models.
# Generation never imports the model-server package, MLX, or model loaders.
import asyncio
import hmac
import json
import os
import re
import sys
from pathlib import Path

STREAM_S, SLOW_S = 0.4, 5.0
FIRST = re.compile(r'^\[1\] (.+?)\(', re.M)  # build_prompt의 첫 조문 '[1] 근로기준법 제26조(해고의 예고)'


def answer_chunks(prompt):
    m = FIRST.search(prompt)
    cite = m.group(1) if m else '근로기준법 제26조'
    if '합성 근거 오류 사례' in prompt:
        cite = '근로기준법 제99999조'
    elif '합성 정의 인용 사례' in prompt:
        cite = '근로기준법 제2조 제1항 제6호'
    return ['【결론】', '가짜 모델 ', '답변입니다.', '\n【근거 조문】\n', cite]


ROUTES = (
    (
        '장기요양보험료 5만원',
        'midyear_income_tax',
        {'gross_pay': '2000만원', 'health_premium': '50만원', 'long_term_care_premium': '5만원'},
        {},
    ),
    (
        '의료비 300만 원이면 의료비 세액공제액',
        'medical_expense_credit',
        {'gross_pay': '5천만 원', 'medical_expenses': '300만 원'},
        {},
    ),
    ('월급 300만원으로 3년 일했습니다', 'severance_pay', {'monthly_wage': '300만원', 'service_period': '3년'}, {}),
    (
        '근속 2년을 마쳤고 연차 발생일에도 재직',
        'annual_leave_days',
        {'workplace_size': '5명', 'weekly_hours': '40시간', 'service_period': '2년', 'attendance_rate': '96%'},
        {'employed_at_accrual': '연차 발생일에도 재직'},
    ),
    ('근속 2년하고 11개월', 'annual_leave_days', {'service_period': '2년하고 11개월'}, {}),
    (
        '입사일은 2024년 2월 30일',
        'annual_leave_days',
        {
            'workplace_size': '8명',
            'weekly_hours': '40시간',
            'attendance_rate': '92%',
            'hire_date': '2024년 2월 30일',
            'as_of_date': '2026년 3월 1일',
        },
        {'employed_at_accrual': '재직 중'},
    ),
)
FIELD_TITLES = {
    'midyear_income_tax': '중도퇴사자',
    'medical_expense_credit': '의료비',
    'severance_pay': '법정 퇴직금',
    'annual_leave_days': '연차유급휴가',
}
MARK = re.compile(r'\[([0-9]+):([^\]]*)\]')


def route_chunks(prompt, system, kw):
    """Answer both router stages: a calculator name, then one label per numbered quantity."""
    assert kw['temperature'] == 0 and not kw['enable_thinking']
    if system.startswith('계산 질문 분류기'):
        assert kw['max_tokens'] == 12
        if '표준세액공제 법정 금액' in prompt:
            return ['lookup']
        return [next((name for marker, name, _, _ in ROUTES if marker in prompt), 'unsupported')]
    plain = MARK.sub(r'\2', prompt)
    _, name, spans, facts = next(route for route in ROUTES if route[0] in plain)
    fields = {span: field for field, span in spans.items()}
    lines = [f'{index}={fields.get(text, "x")}' for index, text in MARK.findall(prompt)]
    lines += [f'{field}=예|{evidence}' for field, evidence in facts.items()]
    assert system.startswith(FIELD_TITLES[name])
    return ['\n'.join(lines)]


def calculation_chunks(prompt, system, kw):
    if system.startswith('계산 질문 분류기') or '계산 입력 분류기' in system.split('\n', 1)[0]:
        return route_chunks(prompt, system, kw)
    cite = '\n【근거 조문】\n소득세법 제59조의4'
    if '계산 결과의 숫자를 그대로 쓰고 다시 계산하지 말' in prompt:
        if '장기요양보험료 5만원' in prompt:
            return ['【결론】', '결정세액은 146,750원이고 개인지방소득세는 14,675원입니다.', cite]
        if '월급 300만원으로 3년 일했습니다' in prompt:
            return [
                '【결론】',
                '계속근로 1년 이상이고 4주 평균 주 소정근로시간이 15시간 이상이라는 전제에서, '
                '최근 3개월 월급이 일정하고 평균임금을 적용하면 9,000,000원으로 추정됩니다. '
                '월급 전액을 통상임금으로 보고 월 209시간과 1일 8시간을 가정하면 10,334,928원으로 추정됩니다.',
                '\n【근거 조문】\n근로자퇴직급여 보장법 제8조',
            ]
        return ['【결론】', '의료비 세액공제액은 1원입니다.', cite]
    if '표의 결과 항목과 숫자·단위를 결론에 그대로 쓰고' in prompt:
        return ['【결론】', '연차유급휴가 일수는 15일입니다.', '\n【근거 조문】\n근로기준법 제60조']
    if '직접 계산하지 말고' in prompt:
        return ['【결론】', '계산에 필요한 자료와 근거 조문을 확인해 주세요.', *answer_chunks(prompt)[-2:]]
    return answer_chunks(prompt)


def create_chat_app(token, model, revision, mode, port):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def authenticate(request, call_next):
        if request.headers.get('host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            return JSONResponse(
                {'error': {'message': 'Invalid Host', 'type': 'invalid_request_error', 'code': 'invalid_host'}},
                status_code=400,
            )
        expected = f'Bearer {token}'.encode()
        supplied = request.headers.get('authorization', '').encode()
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(
                {'error': {'message': 'Unauthorized', 'type': 'authentication_error', 'code': 'invalid_api_key'}},
                status_code=401,
            )
        return await call_next(request)

    @app.get('/v1/models')
    async def models():
        return {
            'object': 'list',
            'data': [
                {'id': model, 'object': 'model', 'revision': revision, 'mlx_lm_version': '0.31.2', 'max_concurrent': 1}
            ],
        }

    @app.post('/v1/chat/completions')
    async def completions(request: Request):
        if mode == 'auth_error':
            return JSONResponse(
                {'error': {'message': 'Unauthorized', 'type': 'authentication_error', 'code': 'invalid_api_key'}},
                status_code=401,
            )
        if mode == 'busy':
            print('e2e chat busy', flush=True)
            return JSONResponse(
                {'error': {'message': 'Synthetic queue full', 'type': 'server_busy', 'code': 'queue_full'}},
                status_code=503,
            )
        body = await request.json()
        if any(field in body for field in ('adapters', 'draft_model')):
            return JSONResponse(
                {
                    'error': {
                        'message': 'Unsupported model override',
                        'type': 'invalid_request_error',
                        'code': 'unsupported_parameter',
                    }
                },
                status_code=400,
            )
        if body['model'] != model:
            return JSONResponse(
                {'error': {'message': 'Unknown model', 'type': 'invalid_request_error', 'code': 'model_not_found'}},
                status_code=404,
            )
        messages = body['messages']
        prompt = messages[-1]['content']
        system = next((message['content'] for message in messages if message['role'] == 'system'), '')
        kw = body | {'enable_thinking': body.get('chat_template_kwargs', {}).get('enable_thinking', True)}
        chunks = calculation_chunks(prompt, system, kw) if mode == 'calculation' else answer_chunks(prompt)
        if mode != 'calculation' and '근로소득 세금, 4대보험' in system:
            chunks[3:3] = [' 근로소득 세금, 4대보험']
            if '【설명】' in system:
                chunks += ['\n【설명】\n가짜 모델의 전체 설명', '\n【참고】\n가짜 모델의 참고 사항']
        if mode == 'long':
            chunks += ['\n【설명】\n'] + ['- 설명\n'] * 30
        usage = {'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17}
        base = {'id': 'chatcmpl-synthetic', 'created': 1, 'model': model}
        if not body.get('stream'):
            return base | {
                'object': 'chat.completion',
                'choices': [
                    {
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': ''.join(chunks),
                            'reasoning': 'private synthetic reasoning',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': usage,
            }

        def event(delta=None, finish=None, **extra):
            choices = [] if delta is None else [{'index': 0, 'delta': delta, 'finish_reason': finish}]
            data = base | {'object': 'chat.completion.chunk', 'choices': choices} | extra
            return f'data: {json.dumps(data, ensure_ascii=False)}\n\n'

        async def events():
            sent = 0
            try:
                yield event({'role': 'assistant', 'reasoning': 'private synthetic reasoning'})
                for piece in chunks:
                    await asyncio.sleep({'stream': STREAM_S, 'long': STREAM_S, 'slow': SLOW_S}.get(mode, 0))
                    if mode == 'truncated' and sent == 2:
                        return
                    if mode == 'chat_error' and sent:
                        yield (
                            'data: '
                            + json.dumps(
                                {
                                    'error': {
                                        'message': 'Synthetic generation error',
                                        'type': 'server_error',
                                        'code': 'RuntimeError',
                                    }
                                }
                            )
                            + '\n\n'
                        )
                        return
                    sent += 1
                    yield event({'content': piece})
                yield event({}, 'stop')
                if body.get('stream_options', {}).get('include_usage'):
                    yield event(usage=usage)
                yield 'data: [DONE]\n\n'
            finally:
                print(f'e2e chat end {sent}/{len(chunks)}', flush=True)

        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    return app


def run_model_server(port):
    import numpy as np

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'model-server' / 'tests'))
    import ms_fakes
    from lawbot_model_server import api, server
    from lawbot_model_server.config import parse_models
    from lawbot_model_server.registry import Registry

    class E2EEmbedder(ms_fakes.FakeEmbedder):
        def encode(self, texts, input_type='document', max_seq_length=None):
            vectors = super().encode(texts, input_type, max_seq_length)
            if input_type == 'query':
                vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
            return vectors

    ms_fakes.reset_fakes()
    entries = [
        entry
        for kind, var in (('embedding', 'EMBEDDING'), ('rerank', 'RERANK'))
        for entry in parse_models(kind, os.environ.get(f'LAWBOT_SERVE_{var}'))
    ]
    reg = Registry(entries, {'embedding': E2EEmbedder, 'rerank': ms_fakes.FakeReranker})
    reg.revisions = {entry.id: os.environ['LAWBOT_TEST_MODEL_REVISION'] for entry in entries}
    reg.load_fixed()
    app = api.create_app(os.environ['LAWBOT_MODEL_TOKEN'])
    api.set_registry(app, reg)
    server.build_server(app, port).run()


def main():
    port, service = int(sys.argv[1]), sys.argv[2]
    if service == 'models':
        run_model_server(port)
        return
    import uvicorn

    app = create_chat_app(
        os.environ['LAWBOT_LLM_API_KEY'],
        os.environ['LAWBOT_PRO_MODEL'],
        os.environ['LAWBOT_TEST_MODEL_REVISION'],
        service,
        port,
    )
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning', access_log=False, lifespan='off')


if __name__ == '__main__':
    main()
