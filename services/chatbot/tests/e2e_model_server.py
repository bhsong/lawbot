# tests/e2e_model_server.py  →  e2e_stub용 가짜 모델 서버 (test_e2e_stub.py가 별도 프로세스로 실행)
# 실제 모델 서버 앱(lawbot_model_server.api)에 가짜 임베딩·리랭커·chat 모델을 붙인다: HTTP·SSE·토큰 인증·오류 본문은 실제 코드
# 모델 id는 환경 변수 LAWBOT_SERVE_*에서 (test_e2e_stub.py가 .env.example 값을 넘김: v4 운영 구성과 같음)
# 사용법: LAWBOT_MODEL_TOKEN=<테스트 토큰> python tests/e2e_model_server.py <포트> <모드>
# 테스트 프로세스에서 import하지 않는다 (ms_fakes가 다른 이름으로 한 번 더 로드되어 모델 서버 테스트의 가짜 상태가 갈라짐)
#   normal     바로 답한다. 답은 프롬프트의 첫 번째 조문을 근거로 든다 (어떤 조문이 검색돼도 인용·검색만 구분이 보임)
#   stream     normal과 같은 답, 토큰마다 STREAM_S초 (화면에서 답이 조각으로 늘어나는지 보이게)
#   long       stream과 같은 간격, 결론 뒤 설명 30줄 (중지가 모델 서버 생성까지 멈추는지 보이게)
#   slow       토큰마다 SLOW_S초 (웹앱 read timeout보다 길게)
#   chat_error 첫 토큰 뒤 생성 중 예외
#   busy       동시 생성 1·대기 1, 토큰마다 BUSY_S초 (테스트가 요청 2개로 자리를 채움)
#   calculation  Router JSON and fixed answers for the calculation-card browser tests.
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'model-server' / 'tests'))  # ms_fakes

import ms_fakes
from lawbot_model_server import api, server
from lawbot_model_server.config import parse_models
from lawbot_model_server.registry import Registry

STREAM_S, SLOW_S, BUSY_S = 0.4, 5.0, 0.5
FIRST = re.compile(r'^\[1\] (.+?)\(', re.M)  # build_prompt의 첫 조문 '[1] 근로기준법 제26조(해고의 예고)'


def answer_chunks(prompt):
    m = FIRST.search(prompt)
    cite = m.group(1) if m else '근로기준법 제26조'
    return ['【결론】', '가짜 모델 ', '답변입니다.', '\n【근거 조문】\n', cite]


def calculation_chunks(prompt, system, kw):
    if system.startswith('계산 질문 분류기'):
        assert kw['max_tokens'] == 128 and kw['temperature'] == 0 and not kw['enable_thinking']
        if '표준세액공제 법정 금액' in prompt:
            return ['{"calculator":"none","kind":"lookup"}']
        if '장기요양보험료 5만원' in prompt:
            calculator = 'midyear_income_tax'
            spans = {'gross_pay': '2000만원', 'health_premium': '50만원', 'long_term_care_premium': '5만원'}
        elif '의료비 300만 원이면 의료비 세액공제액' in prompt:
            calculator = 'medical_expense_credit'
            spans = {'gross_pay': '5천만 원', 'medical_expenses': '300만 원', 'year': '2026년'}
        elif '월급 300만원으로 3년 일했습니다' in prompt:
            calculator = 'severance_pay'
            spans = {'monthly_wage': '300만원', 'service_period': '3년'}
        elif '근속 2년을 마쳤고 연차 발생일에도 재직' in prompt:
            calculator = 'annual_leave_days'
            spans = {
                'workplace_size': '5명',
                'weekly_hours': '40시간',
                'service_period': '2년',
                'employed_at_accrual': '연차 발생일에도 재직',
                'attendance_rate': '96%',
            }
        elif '근속 2년하고 11개월' in prompt:
            calculator = 'annual_leave_days'
            spans = {'service_period': '2년하고 11개월'}
        else:
            return ['{"calculator":"none","kind":"calculation"}']
        inputs = {field: span for field, span in spans.items() if span in prompt}
        return [json.dumps({'calculator': calculator, 'inputs': inputs}, ensure_ascii=False)]
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
        if '근속 2년하고 11개월' in prompt:
            cite = '\n【근거 조문】\n근로기준법 제60조'
        return ['【결론】', '계산에 필요한 자료와 근거 조문을 확인해 주세요.', cite]
    return answer_chunks(prompt)


class E2EChat(ms_fakes.FakeChat):
    mode = 'normal'

    def stream(self, messages, **kw):
        prompt = messages[-1]['content']
        system = messages[0]['content']
        chunks = calculation_chunks(prompt, system, kw) if self.mode == 'calculation' else answer_chunks(prompt)
        if self.mode != 'calculation' and '근로소득 세금, 4대보험' in system:
            chunks[3:3] = [' 근로소득 세금, 4대보험']
            if '【설명】' in system:
                chunks += ['\n【설명】\n가짜 모델의 전체 설명', '\n【참고】\n가짜 모델의 참고 사항']
        if self.mode == 'chat_error':
            chunks = [chunks[0], RuntimeError('가짜 생성 오류')]
        elif self.mode == 'busy':
            chunks = ['.'] * 40
        elif self.mode == 'long':
            chunks = chunks + ['\n【설명】\n'] + ['- 설명\n'] * 30
        self.chunks = chunks
        sent = 0
        try:
            for piece in super().stream(messages, **kw):
                sent += 1
                yield piece
        finally:  # 테스트가 로그로 확인: 중지하면 끝까지 생성하지 않음
            print(f'e2e chat end {sent}/{len(chunks)}', flush=True)


def main():
    port, mode = int(sys.argv[1]), sys.argv[2]
    ms_fakes.reset_fakes()
    E2EChat.mode = mode
    ms_fakes.FakeChat.delay = {'stream': STREAM_S, 'long': STREAM_S, 'slow': SLOW_S, 'busy': BUSY_S}.get(mode, 0.0)
    # 모델 id는 실제 서버와 같은 변수(LAWBOT_SERVE_*, 테스트가 .env.example 값을 넘김)에서. 폴더는 쓰지 않음 (가짜 모델)
    entries = [
        e
        for kind, var in (('chat', 'CHAT'), ('embedding', 'EMBEDDING'), ('rerank', 'RERANK'))
        for e in parse_models(kind, os.environ.get(f'LAWBOT_SERVE_{var}'))
    ]
    loaders = {'chat': E2EChat, 'embedding': ms_fakes.FakeEmbedder, 'rerank': ms_fakes.FakeReranker}
    reg = Registry(entries, loaders, 1)
    reg.load_fixed()
    reg.preload([next(e.id for e in entries if e.kind == 'chat')])
    max_queued = 1 if mode == 'busy' else 32
    app = api.create_app(os.environ['LAWBOT_MODEL_TOKEN'], max_concurrent=1, max_queued=max_queued)
    api.set_registry(app, reg)
    print(f'e2e 가짜 모델 서버 {mode} 127.0.0.1:{port}', flush=True)
    server.build_server(app, port).run()


if __name__ == '__main__':
    main()
