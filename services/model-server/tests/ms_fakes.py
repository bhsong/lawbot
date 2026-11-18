# Fake embedding/rerank models and registry; no model files are needed.
import threading
import time
from pathlib import Path

import numpy as np

from lawbot_model_server.config import ModelEntry
from lawbot_model_server.registry import Registry

TOKEN = 'test-only-model-token-0123'  # 테스트 전용 가짜 값
AUTH = {'Authorization': f'Bearer {TOKEN}'}


class Compute:
    """모델 연산 구간 표시. 두 연산이 동시에 돌면 overlaps에 남는다 (GPU 스레드 하나 검사)"""

    lock = threading.Lock()
    running = 0
    overlaps = 0
    threads = set()

    @classmethod
    def reset(cls):
        cls.running, cls.overlaps, cls.threads = 0, 0, set()

    def __enter__(self):
        with self.lock:
            Compute.running += 1
            Compute.overlaps += Compute.running > 1
            Compute.threads.add(threading.get_ident())

    def __exit__(self, *exc):
        with self.lock:
            Compute.running -= 1


class FakeEmbedder:
    """글자 해시 bag-of-chars (결정적). 받은 인자를 기록"""

    delay = 0.0

    def __init__(self, path):
        self.path, self.calls = path, []

    def encode(self, texts, input_type='document', max_seq_length=None):
        self.calls.append((list(texts), input_type, max_seq_length))
        with Compute():
            time.sleep(self.delay)
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
        with Compute():
            pass
        return [self.scores[d] if self.scores else 1 / (1 + len(d)) for d in docs]


ENTRIES = (
    ModelEntry('emb', 'embedding', Path('/models/emb')),
    ModelEntry('rr', 'rerank', Path('/models/rr')),
)


def reset_fakes():
    FakeEmbedder.delay = 0.0
    FakeReranker.scores = None
    Compute.reset()


def registry(loads=None):
    def load(cls):
        def f(path):
            if loads is not None:
                loads.append(path)
            return cls(path)

        return f

    reg = Registry(ENTRIES, {'embedding': load(FakeEmbedder), 'rerank': load(FakeReranker)})
    reg.load_fixed()
    return reg
