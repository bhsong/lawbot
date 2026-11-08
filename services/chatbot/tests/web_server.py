# tests/web_server.py  →  새 화면 API 통합 테스트용 서버 (test_web_integration.py가 별도 프로세스로 실행)
# 실제 lawbot.web.main()을 가짜 모델로 띄운다. 인증·주소 설정은 web 그대로, 포트는 --port로
# 사용법: python tests/web_server.py <포트> <호출 기록 파일> <토큰 간격 초>
import os
import runpy
import sys
import time
import types
from pathlib import Path

from fakes import FakeLLM, FakeOpenAI, FakeRetriever, FakeServerClient

port, call_log, delay = sys.argv[1], Path(sys.argv[2]), float(sys.argv[3])


class SlowLLM(FakeLLM):
    """토큰 사이에 delay초 쉬고, 생성 시작·끝 시각을 한 줄씩 기록 (동시 생성 여부를 테스트 프로세스가 본다)"""

    # 결론 뒤 토큰이 여러 개: 간결형에서 화면 글이 바뀌지 않는 구간에도 연결 끊김을 알아채는지 본다
    chunks = ['【결론】\n예고해야', ' 합니다.', '\n\n【근거 조문】\n근로기준법 제26조', '\n\n【설명】\n'] + [
        '- 설명\n'
    ] * 6

    def stream(self, question, hits, system=None):
        with call_log.open('a', encoding='utf-8') as f:
            f.write(f'start {time.monotonic()}\n')
        try:
            for c in self.chunks:
                time.sleep(delay)
                yield types.SimpleNamespace(text=c)
        finally:
            with call_log.open('a', encoding='utf-8') as f:
                f.write(f'end {time.monotonic()}\n')


def fake_module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


sys.modules['lawbot.retriever'] = fake_module('lawbot.retriever', Retriever=FakeRetriever)
sys.modules['lawbot.llm'] = fake_module('lawbot.llm', LLM=SlowLLM)
os.environ['LAWBOT_MODEL_SERVER'] = 'http://127.0.0.1:65535'  # 가짜 클라이언트라 접속하지 않음
os.environ['LAWBOT_MODEL_TOKEN'] = 'test-only-model-token-0123'  # 테스트 전용 가짜 값

import lawbot.service as service  # 가짜 모듈을 넣은 뒤에 import해야 함

service.ModelServerClient, service.OpenAI = FakeServerClient, FakeOpenAI
if '--module' in sys.argv[4:]:
    sys.argv[1:] = ['--port', port]
    runpy.run_module('lawbot.web', run_name='__main__', alter_sys=True)
else:
    from lawbot.web import main

    main(['--port', port])
