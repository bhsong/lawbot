# tests/fakes.py  →  웹앱 테스트용 가짜 검색기·답변 모델·모델 서버 클라이언트
# test_pipeline.py(같은 프로세스)와 integration_server.py(별도 서버 프로세스)가 함께 쓴다
import os
import types

import numpy as np

HITS = [
    {
        'article_id': '제26조',
        'title': '해고의 예고',
        'score': 0.91234,
        'doc': '제26조 해고의 예고\n사용자는 근로자를 해고하려면 적어도 30일 전에 예고를 하여야 한다.',
    },
    {
        'article_id': '제36조',
        'title': '금품 청산',
        'score': 0.5,
        'doc': '제36조 금품 청산\n사용자는 근로자가 사망 또는 퇴직한 경우에는 14일 이내에 지급하여야 한다.',
    },
]


class FakeRetriever:
    created = 0
    ids = [h['article_id'] for h in HITS]

    def __init__(self, *args, **kw):  # service.load_app이 넘기는 경로·설정을 기록
        FakeRetriever.created += 1
        self.args = (args, kw)

    def search(self, query, k=3):
        self.query = query
        return HITS[:k]


class FakeLLM:
    created = 0
    chunks = ['【결론】', '예고해야', ' 합니다.']

    def __init__(self, path, client=None, **kw):  # path: API 모델 id (service가 LLM(spec.model, client=…)로 만든다)
        FakeLLM.created += 1
        self.path, self.client, self.kw = path, client, kw

    def stream(self, question, hits, system=None):
        self.args = (question, hits)
        self.system = system
        for c in FakeLLM.chunks:
            if isinstance(c, Exception):
                raise c
            yield types.SimpleNamespace(text=c)


class FakeServerClient:
    """api_client.ModelServerClient 대역 (service.load_app이 만든다). 접속하지 않음
    available: /v1/models 목록 (None이면 현재 프로필에 필요한 모델 전부), loaded: 상주 중인 chat id (None이면 전부)"""

    available = None
    loaded = None
    instances = []

    @classmethod
    def reset(cls):
        cls.available, cls.loaded, cls.instances = None, None, []

    def __init__(self, url, token, http=None):
        self.url, self.token, self.calls = url, token, []
        FakeServerClient.instances.append(self)

    def ready(self):
        return True

    def models(self):
        if self.available is not None:
            return [dict(m) for m in self.available]
        from lawbot import deploy

        rr = os.environ.get('LAWBOT_RERANK_MODEL') or 'qwen3-reranker-4b'
        out = [{'id': rr, 'type': 'rerank', 'loaded': True}]
        for p in (deploy.get_profile('v1'), deploy.get_profile('v4')):  # 두 프로필의 모델 모두 (check_deploy v4 등)
            out.append({'id': p.emb_model, 'type': 'embedding', 'loaded': True})
            out += [
                {'id': s.model, 'type': 'chat', 'loaded': self.loaded is None or s.model in self.loaded}
                for s in p.models + p.disabled
            ]
        return out

    def embed(self, model, texts, input_type='document', max_seq_length=None):
        """글자 해시 bag-of-chars (같은 글자를 많이 공유할수록 유사도가 높다)"""
        self.calls.append(('embed', model, len(texts), input_type, max_seq_length))
        v = np.zeros((len(texts), 512), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                v[i, ord(ch) % 512] += 1
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    def rerank(self, model, query, documents, instruction=None):
        self.calls.append(('rerank', model, query, len(documents), instruction))
        return [1 / (1 + len(d)) for d in documents]


class FakeOpenAI:
    """openai.OpenAI 대역: 만들 때 인자만 기록 (생성은 FakeLLM이 대신함)"""

    def __init__(self, **kw):
        self.kw = kw
