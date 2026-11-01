# tests/integration_server.py  →  인증 통합 테스트용 서버 (test_auth_integration.py가 별도 프로세스로 실행)
# 실제 lawbot.app.main()을 가짜 모델로 띄운다. 인증·주소 설정은 app 그대로, 포트만 테스트용으로 바꾼다
# 사용법: python tests/integration_server.py <포트> <호출 기록 파일>
import os
import sys
import types
from pathlib import Path

from fakes import FakeLLM, FakeOpenAI, FakeRetriever, FakeServerClient

port, call_log = int(sys.argv[1]), Path(sys.argv[2])


class RecordingLLM(FakeLLM):
    """chat이 실제로 실행됐는지 테스트 프로세스가 알 수 있도록 질문을 한 줄씩 기록"""

    def stream(self, question, hits, **kw):
        with call_log.open('a', encoding='utf-8') as f:
            f.write(question.replace('\n', ' ') + '\n')
        yield from super().stream(question, hits, **kw)


def fake_module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


# 검색기·답변 모델 대신 가짜 모듈, 모델 서버 클라이언트도 가짜 (접속하지 않음): 기동이 빠름
sys.modules['lawbot.retriever'] = fake_module('lawbot.retriever', Retriever=FakeRetriever)
sys.modules['lawbot.llm'] = fake_module('lawbot.llm', LLM=RecordingLLM)
os.environ['LAWBOT_MODEL_SERVER'] = 'http://127.0.0.1:65535'  # 가짜 클라이언트라 접속하지 않음
os.environ['LAWBOT_MODEL_TOKEN'] = 'test-only-model-token-0123'  # 테스트 전용 가짜 값

import lawbot.app as app  # 가짜 모듈을 넣은 뒤에 import해야 함
import lawbot.security as security
import lawbot.service as service

service.ModelServerClient, service.OpenAI = FakeServerClient, FakeOpenAI
security.SERVER_PORT = port
app.main()
