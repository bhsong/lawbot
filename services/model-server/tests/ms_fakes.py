# services/model-server/tests/ms_fakes.py  →  가짜 chat·embedding·rerank 모델과 레지스트리 (모델 파일 불필요)
import threading
import time
import types
from pathlib import Path

import numpy as np

from lawbot_model_server.config import ModelEntry
from lawbot_model_server.registry import Registry

TOKEN = 'test-only-model-token-0123'  # 테스트 전용 가짜 값
AUTH = {'Authorization': f'Bearer {TOKEN}'}


class FakeChat:
    """ChatModel과 같은 stream 인자. 받은 인자를 기록하고 mlx_lm 응답과 같은 속성을 낸다"""

    chunks = ['【결론】', '예고해야', ' 합니다.']
    delay = 0.0
    finish = 'stop'

    def __init__(self, path):
        self.path, self.calls, self.closed, self.yielded = path, [], False, 0
        self.active = threading.Event()  # 생성 중 표시 (동시 실행 검사)

    def stream(self, messages, **kw):
        self.calls.append({'messages': messages, **kw})
        self.active.set()
        try:
            for i, c in enumerate(self.chunks):
                if isinstance(c, Exception):
                    raise c
                time.sleep(self.delay)
                self.yielded += 1
                yield types.SimpleNamespace(
                    text=c,
                    generation_tps=20.0,
                    prompt_tokens=11,
                    prompt_tps=100.0,
                    generation_tokens=i + 1,
                    peak_memory=1.5,
                    finish_reason=self.finish if i == len(self.chunks) - 1 else None,
                )
        finally:
            self.active.clear()
            self.closed = True


class FakeEmbedder:
    """글자 해시 bag-of-chars (결정적). 받은 인자를 기록"""

    def __init__(self, path):
        self.path, self.calls = path, []

    def encode(self, texts, input_type='document', max_seq_length=None):
        self.calls.append((list(texts), input_type, max_seq_length))
        v = np.zeros((len(texts), 8), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 8] += 1.0 / 3  # 이진 표현이 긴 값 (base64·float 왕복 검사)
        return v


class FakeReranker:
    scores = None  # 문서별 고정 점수 (없으면 문서 길이 기준)

    def __init__(self, path):
        self.path, self.calls = path, []

    def score(self, query, docs, instruction=None):
        self.calls.append((query, list(docs), instruction))
        return [self.scores[d] if self.scores else 1 / (1 + len(d)) for d in docs]


ENTRIES = (
    ModelEntry('chat-a', 'chat', Path('/models/a')),
    ModelEntry('chat-b', 'chat', Path('/models/b')),
    ModelEntry('emb', 'embedding', Path('/models/emb')),
    ModelEntry('rr', 'rerank', Path('/models/rr')),
)


def registry(max_resident=1, chat=FakeChat, loads=None):
    def load(cls):
        def f(path):
            if loads is not None:
                loads.append(path)
            return cls(path)

        return f

    reg = Registry(
        ENTRIES, {'chat': load(chat), 'embedding': load(FakeEmbedder), 'rerank': load(FakeReranker)}, max_resident
    )
    reg.load_fixed()
    return reg
