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


class E2EChat(ms_fakes.FakeChat):
    mode = 'normal'

    def stream(self, messages, **kw):
        chunks = answer_chunks(messages[-1]['content'])
        system = messages[0]['content']
        if '근로소득 세금, 4대보험' in system:
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
