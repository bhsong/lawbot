# tests/fakes.py  →  웹앱 테스트용 가짜 검색기·sLLM
# test_pipeline.py(같은 프로세스)와 integration_server.py(별도 서버 프로세스)가 함께 쓴다
import types

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

    def __init__(self, path, **kw):
        FakeLLM.created += 1
        self.path, self.kw = path, kw

    def stream(self, question, hits, system=None):
        self.args = (question, hits)
        self.system = system
        for c in FakeLLM.chunks:
            if isinstance(c, Exception):
                raise c
            yield types.SimpleNamespace(text=c)
