# tests/fakes.py  →  웹앱 테스트용 가짜 검색기·sLLM
# test_pipeline.py(같은 프로세스)와 integration_server.py(별도 서버 프로세스)가 함께 쓴다
import types
from lawbot.lawdata import Snapshot, article_meta

SNAPSHOT = Snapshot(data_file='fake_articles.csv', sha256='0' * 64, law_name='근로기준법', as_of='2026-10-01')
HITS = [{'article_id': a, 'title': t, 'score': s, 'doc': f'{a} {t}\n{x}',
         'meta': article_meta(a, t, x, SNAPSHOT)} for a, t, s, x in [
    ('제26조', '해고의 예고', 0.91234, '사용자는 근로자를 해고하려면 적어도 30일 전에 예고를 하여야 한다. <개정 2019.1.15>'),
    ('제36조', '금품 청산', 0.5, '사용자는 근로자가 사망 또는 퇴직한 경우에는 14일 이내에 지급하여야 한다.')]]

class FakeRetriever:
    created = 0
    snapshot = SNAPSHOT
    index_info = {'data_file': SNAPSHOT.data_file, 'data_sha256': SNAPSHOT.sha256, 'docs': len(HITS),
                  'model_dir': 'models/fake', 'built_at': '2026-10-20T00:00:00'}

    def __init__(self):
        FakeRetriever.created += 1

    def search(self, query, k=3):
        self.query = query
        return HITS[:k]

class FakeLLM:
    created = 0
    chunks = ['【결론】', '예고해야', ' 합니다.']

    def __init__(self, path):
        FakeLLM.created += 1
        self.path = path

    def stream(self, question, hits):
        self.args = (question, hits)
        for c in FakeLLM.chunks:
            if isinstance(c, Exception):
                raise c
            yield types.SimpleNamespace(text=c)
