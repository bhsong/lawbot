# lawbot_model_server/registry.py  →  제공 모델 목록과 상주 관리
# Embedding/rerank models remain resident; api.py serializes inference on one worker.
import time

from lawbot_common.logs import logger
from lawbot_model_server import revision

log = logger('lawbot_model_server', 'model-server')


class UnknownModel(LookupError):
    """없는 모델 id이거나 다른 종류의 모델 (404)"""


def default_loaders():
    """종류별 로더. 모델 라이브러리는 여기서 처음 import (api.py·테스트는 가짜 로더를 넘김)"""

    def embedding(path):
        from lawbot_model_server.embeddings import Embedder

        return Embedder(path)

    def rerank(path):
        from lawbot_model_server.rerank import Reranker

        return Reranker(path)

    return {'embedding': embedding, 'rerank': rerank}


class Registry:
    def __init__(self, entries, loaders=None):
        self.entries = {e.id: e for e in entries}
        self.loaders = loaders or default_loaders()
        self.revisions = {e.id: revision.fingerprint(e.path) for e in entries}  # 시작할 때 한 번 (/v1/models)
        self.fixed = {}  # embedding·rerank id → 모델

    def entry(self, model_id, kind):
        e = self.entries.get(model_id)
        if e is None or e.kind != kind:
            have = [i for i, x in self.entries.items() if x.kind == kind]
            raise UnknownModel(f'{kind} 모델이 아닙니다: {model_id!r} (서버의 {kind} 모델 {have})')
        return e

    def is_loaded(self, model_id):
        return model_id in self.fixed

    def load_fixed(self):
        """embedding·rerank 모두 (실패는 그대로 올려 시작 중단)"""
        for e in self.entries.values():
            t0 = time.perf_counter()
            self.fixed[e.id] = self.loaders[e.kind](e.path)
            log.info(f'{e.kind} {e.id} 불러오기 {time.perf_counter() - t0:.1f}초')

    def get(self, model_id, kind):
        """Return a resident model of the requested kind."""
        return self.fixed[self.entry(model_id, kind).id]

    def listing(self):
        """/v1/models의 data (OpenAI 형식 + 확장 필드 type·loaded·revision)"""
        return [
            {
                'id': i,
                'object': 'model',
                'created': 0,
                'owned_by': 'lawbot',
                'type': e.kind,
                'loaded': self.is_loaded(i),
                'revision': self.revisions[i],
            }
            for i, e in self.entries.items()
        ]
