# tests/model_fakes.py  →  모델 서버 테스트용 가짜 검색기·리랭커·답변 모델과 서버 띄우기 (모델 파일·네트워크 불필요)
# test_model_server.py(서버)와 test_remote.py(원격 모드 웹앱)가 함께 쓴다
import contextlib
import threading
import time
import types

import numpy as np

from lawbot import model_server
from lawbot.prompts import SYSTEM

TOKEN = 'test-only-model-token-0123'     # 테스트 전용 가짜 값
ARTS = [('제26조', '해고의 예고', '사용자는 근로자를 해고하려면 적어도 30일 전에 예고를 하여야 한다.'),
        ('제36조', '금품 청산', '사용자는 근로자가 사망 또는 퇴직한 경우에는 14일 이내에 지급하여야 한다.'),
        ('제60조', '연차 유급휴가', '사용자는 1년간 80퍼센트 이상 출근한 근로자에게 15일의 유급휴가를 주어야 한다.'),
        ('제56조', '연장·야간 및 휴일 근로', '사용자는 연장근로에 대하여는 통상임금의 100분의 50 이상을 가산하여 지급하여야 한다.')]

class Ret:
    """Retriever와 같은 속성(numpy 배열)·메서드. 질문 길이로 순서를 정하는 결정적 검색"""
    def __init__(self, *args, **kw):
        self.ids = np.array([a[0] for a in ARTS], dtype=object)
        self.titles = np.array([a[1] for a in ARTS], dtype=object)
        self.docs = [f'{a} {t}\n{x}' for a, t, x in ARTS]
        self.laws = None

    def rank_all(self, queries):
        return np.array([np.roll(np.arange(len(ARTS)), -(len(q) % len(ARTS))) for q in queries])

    def search(self, query, k=3):
        order = self.rank_all([query])[0][:k]
        return [{'article_id': self.ids[i], 'title': self.titles[i], 'doc': self.docs[i],
                 'score': np.float32(0.9 - 0.1 * j)} for j, i in enumerate(order)]   # numpy 값도 JSON으로

class Reranker:
    def __init__(self, candidates=4):
        self.candidates = candidates

    def rerank(self, question, candidates, top_k=3):
        return [c | {'rerank_score': 0.8 - 0.1 * j, 'emb_rank': len(candidates) - j}
                for j, c in enumerate(reversed(candidates))][:top_k]

class GenLLM:
    """LLM과 같은 생성자·stream 인자. 받은 인자를 기록하고 mlx_lm 응답과 같은 속성을 낸다"""
    chunks = ['【결론】', '예고해야', ' 합니다.']
    delay = 0.0

    def __init__(self, path, system=SYSTEM, prompt_style='v1'):   # 기본 system은 LLM과 같이 v1 SYSTEM
        self.path, self.system = path, system
        self.calls, self.closed, self.yielded = [], False, 0

    def stream(self, question, hits, max_tokens=1024, system=None):
        self.calls.append({'question': question, 'hits': hits, 'max_tokens': max_tokens,
                           'system': system or self.system})
        try:
            for i, c in enumerate(self.chunks):
                if isinstance(c, Exception):
                    raise c
                time.sleep(self.delay)
                self.yielded += 1
                yield types.SimpleNamespace(text=c, generation_tps=20.0, prompt_tokens=11, prompt_tps=100.0,
                                            generation_tokens=i + 1, peak_memory=1.5,
                                            finish_reason='stop' if i == len(self.chunks) - 1 else None)
        finally:
            self.closed = True

@contextlib.contextmanager
def serve(app, token=TOKEN):
    """create_server(app, 0, token)를 스레드로 띄우고 (server, url)을 돌려준다. 끝나면 정리"""
    server = model_server.create_server(app, 0, token)
    t = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    t.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        t.join(5)
