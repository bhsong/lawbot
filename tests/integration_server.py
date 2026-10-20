# tests/integration_server.py  →  인증 통합 테스트용 서버 (test_auth_integration.py가 별도 프로세스로 실행)
# 실제 lawbot.app.main()을 가짜 모델로 띄운다. 인증·주소 설정은 app 그대로, 포트만 테스트용으로 바꾼다
# 사용법: python tests/integration_server.py <포트> <호출 기록 파일>
import sys
import types
from pathlib import Path
from fakes import FakeLLM, FakeRetriever

port, call_log = int(sys.argv[1]), Path(sys.argv[2])

class RecordingLLM(FakeLLM):
    """chat이 실제로 실행됐는지 테스트 프로세스가 알 수 있도록 질문을 한 줄씩 기록"""
    def stream(self, question, hits):
        with call_log.open('a', encoding='utf-8') as f:
            f.write(question.replace('\n', ' ') + '\n')
        yield from super().stream(question, hits)

def fake_module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m

# 실제 모델 모듈(torch·sentence_transformers·mlx_lm) 대신 가짜 모듈: 모델 파일 불필요, 기동이 빠름
sys.modules['lawbot.retriever'] = fake_module('lawbot.retriever', Retriever=FakeRetriever)
sys.modules['lawbot.llm'] = fake_module('lawbot.llm', LLM=RecordingLLM)

import lawbot.security as security
import lawbot.app as app   # 가짜 모듈을 넣은 뒤에 import해야 함

security.SERVER_PORT = port
app.main()
