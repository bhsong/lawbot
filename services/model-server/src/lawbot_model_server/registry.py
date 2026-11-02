# lawbot_model_server/registry.py  →  제공 모델 목록과 상주 관리
# embedding·rerank 모델은 시작할 때 모두 올려 상주. chat 모델은 처음 요청 때 올리고 LRU로 최대 max_resident개 (챗봇의 이전 ModelManager)
# 로딩·교체는 lock으로 한 번에 하나. 모델 연산의 직렬화는 api.py가 한다
import gc
import threading
import time
from collections import OrderedDict

from lawbot_common.logs import logger

log = logger('lawbot_model_server', 'model-server')


class UnknownModel(LookupError):
    """없는 모델 id이거나 다른 종류의 모델 (404)"""


def free_memory():
    """내린 모델의 Metal 버퍼를 반납 (MLX는 해제한 버퍼를 캐시에 남겨 둠)"""
    gc.collect()
    try:
        import mlx.core as mx

        mx.clear_cache()
    except ImportError:
        pass


def default_loaders():
    """종류별 로더. 모델 라이브러리는 여기서 처음 import (api.py·테스트는 가짜 로더를 넘김)"""

    def chat(path):
        from lawbot_model_server.chat import ChatModel

        return ChatModel(path)

    def embedding(path):
        from lawbot_model_server.embeddings import Embedder

        return Embedder(path)

    def rerank(path):
        from lawbot_model_server.rerank import Reranker

        return Reranker(path)

    return {'chat': chat, 'embedding': embedding, 'rerank': rerank}


class Registry:
    def __init__(self, entries, loaders=None, max_resident=2):
        self.entries = {e.id: e for e in entries}
        self.loaders = loaders or default_loaders()
        self.max_resident = max_resident
        self.fixed = {}  # embedding·rerank id → 모델
        self.chat = OrderedDict()  # chat id → 모델 (오래 안 쓴 순)
        self.lock = threading.Lock()

    def entry(self, model_id, kind):
        e = self.entries.get(model_id)
        if e is None or e.kind != kind:
            have = [i for i, x in self.entries.items() if x.kind == kind]
            raise UnknownModel(f'{kind} 모델이 아닙니다: {model_id!r} (서버의 {kind} 모델 {have})')
        return e

    def is_loaded(self, model_id):
        return model_id in self.fixed or model_id in self.chat

    def load_fixed(self):
        """embedding·rerank 모두 (실패는 그대로 올려 시작 중단)"""
        for e in self.entries.values():
            if e.kind != 'chat':
                t0 = time.perf_counter()
                self.fixed[e.id] = self.loaders[e.kind](e.path)
                log.info(f'{e.kind} {e.id} 불러오기 {time.perf_counter() - t0:.1f}초')

    def preload(self, ids):
        """첫 번째 실패는 그대로 올리고(시작 중단), 나머지는 경고만"""
        for n, i in enumerate(ids):
            try:
                _, s = self.get(i, 'chat')
                log.info(f'chat {i} 불러오기 {s:.1f}초')
            except Exception as e:
                if n == 0:
                    raise
                log.warning(f'chat {i} 미리 불러오기 실패: {type(e).__name__}: {e}')

    def get(self, model_id, kind):
        """(모델, 이번에 불러온 초 또는 None)"""
        e = self.entry(model_id, kind)
        if kind != 'chat':
            return self.fixed[e.id], None
        with self.lock:
            if e.id in self.chat:
                self.chat.move_to_end(e.id)
                return self.chat[e.id], None
            while len(self.chat) >= self.max_resident:  # 새 모델을 올리기 전에 내려서 최대 메모리를 줄임
                self.chat.popitem(last=False)
                free_memory()
            t0 = time.perf_counter()
            m = self.loaders['chat'](e.path)
            self.chat[e.id] = m
            return m, time.perf_counter() - t0

    def listing(self):
        """/v1/models의 data (OpenAI 형식 + 확장 필드 type·loaded)"""
        return [
            {
                'id': i,
                'object': 'model',
                'created': 0,
                'owned_by': 'lawbot',
                'type': e.kind,
                'loaded': self.is_loaded(i),
            }
            for i, e in self.entries.items()
        ]
