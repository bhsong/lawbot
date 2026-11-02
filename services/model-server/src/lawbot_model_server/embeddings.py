# lawbot_model_server/embeddings.py  →  sentence-transformers 임베딩 (MPS, fp16)
# 챗봇의 이전 retriever.Retriever와 같은 encode 호출: 목록 하나 = encode 한 번 (길이 정렬·batch·패딩이 같아 fp16 값이 같음)
#   document: batch 4 · prompt 없음 / query: batch 16 · prompt_name='query'. 둘 다 정규화, float32
from typing import Any

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

BATCH = {'document': 4, 'query': 16}


class Embedder:
    def __init__(self, path):
        self.path = path
        self.m = SentenceTransformer(str(path), device='mps', model_kwargs={'torch_dtype': torch.float16})
        self.default_max_seq = self.m.max_seq_length

    def encode(self, texts, input_type='document', max_seq_length=None):
        """(len(texts), dim) float32. max_seq_length는 이번 요청에만 쓰고 되돌린다"""
        if input_type not in BATCH:
            raise ValueError(f"input_type은 'query' 또는 'document': {input_type!r}")
        self.m.max_seq_length = max_seq_length or self.default_max_seq
        try:
            kw: dict[str, Any] = {'prompt_name': 'query'} if input_type == 'query' else {}
            v = self.m.encode(
                list(texts), normalize_embeddings=True, batch_size=BATCH[input_type], convert_to_numpy=True, **kw
            )
        finally:
            self.m.max_seq_length = self.default_max_seq
        if input_type == 'document':
            torch.mps.empty_cache()  # 문서 인코딩에 쓴 임시 메모리 반납
        return np.asarray(v, dtype=np.float32)
