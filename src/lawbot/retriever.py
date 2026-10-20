from datetime import datetime
import numpy as np, torch
from sentence_transformers import SentenceTransformer
from lawbot.lawdata import article_meta, load_articles
from lawbot.paths import ARTICLES, EMB

class Retriever:
    def __init__(self, model_dir=EMB, law_csv=ARTICLES):
        law, self.snapshot = load_articles(law_csv)              # 열·조문 번호 검증, 출처 메타데이터
        law = law[law['deleted'] == 0].reset_index(drop=True)   # 130개 조문
        self.ids = law['article_id'].to_numpy()
        self.titles = law['title'].to_numpy()
        self.docs = (law['article_id'] + ' ' + law['title'] + '\n' + law['text']).tolist()
        self.starter = set(law.loc[law['starter'] == 1, 'article_id'])
        self.meta = [article_meta(a, t, x, self.snapshot)
                     for a, t, x in zip(law['article_id'], law['title'], law['text'])]
        self.m = SentenceTransformer(str(model_dir), device='mps',
                                     model_kwargs={'torch_dtype': torch.float16})
        self.m.max_seq_length = 512
        self.D = self.m.encode(self.docs, normalize_embeddings=True, batch_size=4,
                               convert_to_numpy=True).astype(np.float32)
        torch.mps.empty_cache()                                  # 조문 인코딩에 쓴 임시 메모리 반납
        # 색인은 실행 때마다 메모리에 새로 만든다: 어떤 데이터·모델로 만들었는지 기록
        self.index_info = {'data_file': self.snapshot.data_file, 'data_sha256': self.snapshot.sha256,
                           'docs': len(self.docs), 'model_dir': str(model_dir),
                           'built_at': datetime.now().isoformat(timespec='seconds')}

    def _encode_queries(self, queries, batch_size=16):
        return self.m.encode(list(queries), prompt_name='query', normalize_embeddings=True,
                             batch_size=batch_size, convert_to_numpy=True).astype(np.float32)

    def rank_all(self, queries):
        return np.argsort(-(self._encode_queries(queries) @ self.D.T), axis=1)

    def search(self, query, k=3):
        s = self.D @ self._encode_queries([query])[0]
        return [{'article_id': self.ids[i], 'title': self.titles[i],
                 'doc': self.docs[i], 'score': float(s[i]), 'meta': self.meta[i]}
                for i in np.argsort(-s)[:k]]
