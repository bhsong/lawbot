# lawbot/embed_cache.py  →  조문 임베딩 캐시: 웹앱을 다시 시작할 때 조문 전체(812개, 수십 초)를 다시 임베딩하지 않는다
# 키: 임베딩 모델 id + max_seq + 문서 문자열 전체의 sha256 (조문 CSV·문서 형식이 한 글자만 바뀌어도 새 키)
# 같은 id로 모델 폴더가 바뀐 경우: 캐시를 만들 때 첫 문서 1개만 따로 임베딩한 '확인용 벡터'를 함께 저장하고,
#   시작할 때 같은 방식(1개만)으로 다시 임베딩해 비트 단위로 비교한다. 다르면 전체를 다시 임베딩해 캐시를 바꾼다
#   (1개만 임베딩한 값은 batch 구성이 달라 전체 임베딩의 같은 행과 fp16에서 다를 수 있으므로, 같은 호출끼리 비교)
# 캐시를 읽거나 쓰지 못하면 경고만 하고 지금처럼 매번 임베딩한다. LAWBOT_EMBED_CACHE=0으로 끔 (lawbot.settings)
import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np

from lawbot.paths import ROOT
from lawbot_common.logs import logger

CACHE_DIR = ROOT / 'cache' / 'embeddings'  # .gitignore의 cache/
log = logger('lawbot', 'lawbot')


def cache_key(model, max_seq, texts):
    h = hashlib.sha256(f'{model}\0{max_seq}\0'.encode())
    for t in texts:
        h.update(t.encode('utf-8') + b'\0')
    return h.hexdigest()[:32]


class EmbedCache:
    def __init__(self, folder=CACHE_DIR):
        self.folder = Path(folder)

    def encode(self, encoder, texts, max_seq):
        """encoder.encode(texts, 'document', max_seq)와 같은 float32 배열 (캐시가 맞으면 확인용 1개만 임베딩)"""
        texts = list(texts)
        path = self.folder / f'{cache_key(encoder.model, max_seq, texts)}.npz'
        cached = self._read(path, len(texts))
        if cached is not None:
            vectors, probe = cached
            now = np.asarray(encoder.encode(texts[:1], 'document', max_seq), dtype=np.float32)
            if np.array_equal(now, probe):
                log.info(f'조문 임베딩 캐시 사용 ({len(texts)}개, {path.name[:12]}…)')
                return vectors
            log.warning(
                f'경고: 조문 임베딩 캐시의 확인용 벡터가 지금 모델과 달라 다시 임베딩합니다 ({path.name[:12]}…)'
            )
        vectors = np.asarray(encoder.encode(texts, 'document', max_seq), dtype=np.float32)
        probe = np.asarray(encoder.encode(texts[:1], 'document', max_seq), dtype=np.float32)
        self._write(path, vectors, probe)
        return vectors

    def _read(self, path, n):
        if not path.is_file():
            return None
        try:
            with np.load(path) as z:
                vectors, probe = z['vectors'], z['probe']
        except Exception as e:  # 깨진 파일: 다시 만든다
            log.warning(f'경고: 조문 임베딩 캐시를 읽지 못해 다시 임베딩합니다 ({path.name[:12]}…: {type(e).__name__})')
            return None
        if vectors.dtype != np.float32 or len(vectors) != n:
            return None
        return vectors, probe

    def _write(self, path, vectors, probe):
        """임시 파일에 쓴 뒤 바꿔 넣는다 (쓰는 중에 멈춰도 깨진 캐시가 남지 않게)"""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.folder, suffix='.tmp')
            with os.fdopen(fd, 'wb') as f:
                np.savez(f, vectors=vectors, probe=probe)
            os.replace(tmp, path)
        except OSError as e:
            log.warning(f'경고: 조문 임베딩 캐시를 쓰지 못했습니다 ({self.folder}: {type(e).__name__})')
