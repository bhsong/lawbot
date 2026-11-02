# lawbot/retriever.py  →  조문 인덱스와 코사인 검색 (numpy). 벡터는 모델 서버의 임베딩 모델이 만든다 (api_client.Embedder)
# 인코딩 호출은 모델 서버로 옮기기 전과 같다: 법률·하위법령 문서 목록을 각각 한 번에, 질문은 query 프롬프트로
# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
# ↑ pandas 필터 결과를 ndarray로 잘못 추론 (pandas 타입 선언 한계). 이 파일의 다른 검사는 그대로
from typing import cast

import numpy as np
import pandas as pd

from lawbot.decree import LAW_KIND
from lawbot.hits import Hit


def doc_text(law):
    """검색 문서 문자열 '근로기준법 제26조(해고의 예고)\\n본문' (조문 DataFrame → Series). 임베딩 학습 때와 글자 단위로 같아야 한다"""
    return law['law'] + ' ' + law['article_id'] + '(' + law['title'] + ')\n' + law['text']


class Retriever:
    """법률 검색 (search·rank_all, 지금과 같음) + 켜져 있으면 하위법령 검색 (search_decrees)
    조문 배열(ids·titles·docs·laws·kinds·parent_laws)은 법률 먼저, 하위법령 뒤 (n_law개가 법률).
    kind 열이 없는 CSV(415·130조문)는 모두 법률
    법률 문서는 하위법령을 끈 웹앱과 같은 문서 목록·같은 호출·같은 max_seq_length로 따로 인코딩한다
    (812개를 함께 인코딩하면 길이 정렬 후 batch 구성·패딩이 바뀌어 fp16 값이 달라질 수 있음)
    decree_max_seq: 하위법령 인코딩 최대 토큰 (deploy.DECREE_MAX_SEQ). None이면 하위법령 행을 읽지 않음
    encoder: encode(texts, input_type, max_seq_length) → float32 배열 (api_client.Embedder, 테스트는 가짜)"""

    def __init__(self, encoder, law_csv, max_seq_length, decree_max_seq=None):
        law = pd.read_csv(law_csv, encoding='utf-8-sig')
        law = law[law['deleted'] == 0].reset_index(drop=True)  # 415개 조문 (+ 하위법령 397개)
        if 'article_no' in law:  # article_id에 법령명이 들어 있으면('근로기준법 제26조') 번호만 쓴다
            law['article_id'] = law['article_no']
        kind = law['kind'] if 'kind' in law else pd.Series(LAW_KIND, index=law.index)
        dec = law[kind != LAW_KIND].reset_index(drop=True) if decree_max_seq else law.iloc[:0]
        law = law[kind == LAW_KIND].reset_index(drop=True)
        if decree_max_seq and not len(dec):
            raise ValueError(f'하위법령(kind 시행령·시행규칙) 조문이 없습니다: {law_csv}')
        both = pd.concat([law, dec], ignore_index=True)
        self.n_law = len(law)
        self.ids = both['article_id'].to_numpy()
        self.titles = both['title'].to_numpy()
        self.laws = both['law'].to_numpy()  # 법령마다 조문 번호가 겹침
        self.kinds = (both['kind'] if 'kind' in both else pd.Series(LAW_KIND, index=both.index)).to_numpy()
        self.parent_laws = (
            both['parent_law'] if 'parent_law' in both else both.get('law', pd.Series(None, index=both.index))
        ).to_numpy()
        law_docs = doc_text(law).tolist()
        dec_docs = doc_text(dec).tolist() if len(dec) else []
        self.docs = law_docs + dec_docs
        self.starter = set(law.loc[law['starter'] == 1, 'article_id']) if 'starter' in law else set()
        self.encoder, self.max_seq_length = encoder, max_seq_length
        self.D = np.asarray(encoder.encode(law_docs, 'document', max_seq_length), dtype=np.float32)
        self.D_dec = None
        if dec_docs:
            self.D_dec = np.asarray(encoder.encode(dec_docs, 'document', decree_max_seq), dtype=np.float32)

    def _encode_queries(self, queries):
        """질문 목록 하나 = 서버 encode 한 번 (batch 16, query 프롬프트, 법률과 같은 max_seq)"""
        return np.asarray(self.encoder.encode(list(queries), 'query', self.max_seq_length), dtype=np.float32)

    def rank_all(self, queries):
        """법률 조문 순위 (순번 0~n_law-1 = ids 앞부분)"""
        return np.argsort(-(self._encode_queries(queries) @ self.D.T), axis=1)

    def _hit(self, i, score) -> Hit:
        return Hit(article_id=self.ids[i], title=self.titles[i], doc=self.docs[i], score=score, law=self.laws[i])

    def search(self, query, k=3) -> list[Hit]:
        """법률 조문 상위 k개 (하위법령을 켜도 같음)"""
        s = self.D @ self._encode_queries([query])[0]
        return [self._hit(i, float(s[i])) for i in np.argsort(-s)[:k]]

    def search_decrees(self, query, k=10) -> list[Hit]:
        """하위법령 조문 상위 k개. 법률 hit 형식 + kind('시행령'·'시행규칙')·parent_law('근로기준법')"""
        if self.D_dec is None:
            raise ValueError('하위법령 검색 인덱스가 없습니다 (LAWBOT_DECREE_ENABLED=1로 시작해야 함)')
        s = self.D_dec @ self._encode_queries([query])[0]
        return [
            cast(
                Hit,
                self._hit(self.n_law + i, float(s[i]))
                | {'kind': self.kinds[self.n_law + i], 'parent_law': self.parent_laws[self.n_law + i]},
            )
            for i in np.argsort(-s)[:k]
        ]
