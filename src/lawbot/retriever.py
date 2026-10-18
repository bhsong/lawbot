import numpy as np, pandas as pd, torch
from sentence_transformers import SentenceTransformer
from lawbot.paths import ARTICLES, EMB

class Retriever:
    def __init__(self, model_dir=EMB, law_csv=ARTICLES):
        law = pd.read_csv(law_csv, encoding='utf-8-sig')
        law = law[law['deleted'] == 0].reset_index(drop=True)   # 130개 조문
        self.ids = law['article_id'].to_numpy()
        self.titles = law['title'].to_numpy()
        self.docs = (law['article_id'] + ' ' + law['title'] + '\n' + law['text']).tolist()
        self.starter = set(law.loc[law['starter'] == 1, 'article_id'])
        self.m = SentenceTransformer(str(model_dir), device='mps',
                                     model_kwargs={'torch_dtype': torch.float16})
        self.m.max_seq_length = 512
        self.D = self.m.encode(self.docs, normalize_embeddings=True, batch_size=4,
                               convert_to_numpy=True).astype(np.float32)
        torch.mps.empty_cache()                                  # 조문 인코딩에 쓴 임시 메모리 반납

    def _encode_queries(self, queries, batch_size=16):
        return self.m.encode(list(queries), prompt_name='query', normalize_embeddings=True,
                             batch_size=batch_size, convert_to_numpy=True).astype(np.float32)

    def rank_all(self, queries):
        return np.argsort(-(self._encode_queries(queries) @ self.D.T), axis=1)

    def search(self, query, k=3):
        s = self.D @ self._encode_queries([query])[0]
        return [{'article_id': self.ids[i], 'title': self.titles[i],
                 'doc': self.docs[i], 'score': float(s[i])} for i in np.argsort(-s)[:k]]
