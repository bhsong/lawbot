import numpy as np, pandas as pd, torch
from sentence_transformers import SentenceTransformer
from lawbot.paths import ARTICLES, EMB

# 검색 문서 문자열: 임베딩 학습 때와 글자 단위로 같아야 한다
DOC_STYLES = {
    'v1': lambda law: law['article_id'] + ' ' + law['title'] + '\n' + law['text'],        # 제26조 해고의 예고\n본문
    'v4': lambda law: law['law'] + ' ' + law['article_id'] + '(' + law['title'] + ')\n' + law['text'],  # 근로기준법 제26조(해고의 예고)\n본문
}

class Retriever:
    def __init__(self, model_dir=EMB, law_csv=ARTICLES, max_seq_length=512, doc_style='v1'):
        law = pd.read_csv(law_csv, encoding='utf-8-sig')
        law = law[law['deleted'] == 0].reset_index(drop=True)   # v1 130개, v4 415개 조문
        if 'article_no' in law:      # v4 CSV는 article_id에 법령명이 들어 있음('근로기준법 제26조') → 번호만 쓴다
            law['article_id'] = law['article_no']
        self.ids = law['article_id'].to_numpy()
        self.titles = law['title'].to_numpy()
        self.laws = law['law'].to_numpy() if doc_style == 'v4' else None   # v4: 법령마다 조문 번호가 겹침
        self.docs = DOC_STYLES[doc_style](law).tolist()
        self.starter = set(law.loc[law['starter'] == 1, 'article_id']) if 'starter' in law else set()
        self.m = SentenceTransformer(str(model_dir), device='mps',
                                     model_kwargs={'torch_dtype': torch.float16})
        self.m.max_seq_length = max_seq_length
        self.D = self.m.encode(self.docs, normalize_embeddings=True, batch_size=4,
                               convert_to_numpy=True).astype(np.float32)
        torch.mps.empty_cache()                                  # 조문 인코딩에 쓴 임시 메모리 반납

    def _encode_queries(self, queries, batch_size=16):
        return self.m.encode(list(queries), prompt_name='query', normalize_embeddings=True,
                             batch_size=batch_size, convert_to_numpy=True).astype(np.float32)

    def rank_all(self, queries):
        return np.argsort(-(self._encode_queries(queries) @ self.D.T), axis=1)

    def _hit(self, i, score):
        h = {'article_id': self.ids[i], 'title': self.titles[i], 'doc': self.docs[i], 'score': score}
        return h if self.laws is None else h | {'law': self.laws[i]}

    def search(self, query, k=3):
        s = self.D @ self._encode_queries([query])[0]
        return [self._hit(i, float(s[i])) for i in np.argsort(-s)[:k]]
