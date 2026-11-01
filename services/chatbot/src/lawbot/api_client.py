# lawbot/api_client.py  →  모델 서버 클라이언트 (임베딩·리랭크·모델 목록·준비 확인). 생성은 llm.py가 openai SDK로
# 표준 OpenAI 형식 + 확장 필드 (docs/archives/monorepo-openai-api.md): /v1/embeddings의 input_type·max_seq_length, /v1/rerank
# 프록시 환경 변수를 쓰지 않는다 (trust_env=False): 질문이 맥 밖(프록시)으로 나가지 않게 127.0.0.1에 직접 접속
import base64
import time

import httpx
import numpy as np
from pydantic import ValidationError

from lawbot_common import api

CONNECT_TIMEOUT = 5.0
TIMEOUT = httpx.Timeout(
    None, connect=CONNECT_TIMEOUT
)  # 기본: 연결만 5초. 시작할 때 조문 인덱스 인코딩은 오래 걸려 읽기 제한 없음


def request_timeout(read_s):
    """질문 처리 중 호출(질문 임베딩·리랭크·생성)의 시간 상한: 응답 조각 사이가 read_s초를 넘으면 ModelServerTimeout
    (LAWBOT_MODEL_READ_TIMEOUT, service.read_timeout). 연결은 5초"""
    return httpx.Timeout(read_s, connect=CONNECT_TIMEOUT)


class ModelServerError(RuntimeError):
    """모델 서버가 보낸 오류 (status: HTTP 상태, kind: error.type, code: error.code)"""

    def __init__(self, message, status=None, kind=None, code=None):
        super().__init__(message)
        self.status, self.kind, self.code = status, kind, code


class ModelServerTimeout(TimeoutError):
    """질문 처리 중 모델 서버(또는 외부 생성 API)가 시간 상한 안에 응답하지 않음. 웹앱은 '응답이 없습니다'로 안내"""


class ModelLoadError(OSError):
    """모델 서버가 답변 모델을 불러오지 못함 (폴더 없음·형식 오류). 웹앱은 '모델 불러오기 실패'로 안내"""


def error_from_body(status, body):
    err = (body or {}).get('error') or {}
    kind, code, msg = err.get('type'), err.get('code'), err.get('message') or f'HTTP {status}'
    if status == 401:
        return ModelServerError(
            '모델 서버 토큰이 맞지 않습니다. 웹앱과 모델 서버의 LAWBOT_MODEL_TOKEN을 같은 값으로 적으세요.',
            401,
            kind,
            code,
        )
    if kind == api.MODEL_LOAD:
        return ModelLoadError(f'모델 서버 오류 {code}: {msg}')
    return ModelServerError(f'모델 서버 오류 {code or kind or status}: {msg}', status, kind, code)


class ModelServerClient:
    def __init__(self, url, token, http=None):
        self.url = url.rstrip('/')
        self.http = http or httpx.Client(
            base_url=self.url, headers={'Authorization': f'Bearer {token}'}, timeout=TIMEOUT, trust_env=False
        )
        if http is not None:
            self.http.headers['Authorization'] = f'Bearer {token}'

    def _call(self, method, path, body=None, timeout=None):
        timeout = timeout or TIMEOUT
        try:
            r = self.http.request(method, path, json=body, timeout=timeout)
        except httpx.ConnectTimeout:
            raise ConnectionError(
                f'모델 서버에 연결할 수 없습니다: {self.url} (uv run lawbot-model-server 실행 여부 확인, ConnectTimeout)'
            ) from None
        except httpx.TimeoutException:
            read = timeout.read if isinstance(timeout, httpx.Timeout) else timeout
            raise ModelServerTimeout(f'모델 서버가 {read:g}초 동안 응답하지 않았습니다 ({path})') from None
        except httpx.TransportError as e:
            raise ConnectionError(
                f'모델 서버에 연결할 수 없습니다: {self.url} (uv run lawbot-model-server 실행 여부 확인, {type(e).__name__})'
            ) from None
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = None
            raise error_from_body(r.status_code, body)
        return r.json()

    def _parsed(self, model, method, path, body=None, timeout=None):
        """_call + 응답 계약 검증 (lawbot_common.api). 형식이 다르면 ModelServerError (서버·웹앱 버전 불일치 등)"""
        data = self._call(method, path, body, timeout)
        try:
            return model.model_validate(data)
        except ValidationError as e:
            loc = '.'.join(map(str, e.errors()[0].get('loc', ())))
            raise ModelServerError(f'모델 서버 응답 형식이 아닙니다 ({path}: {loc or "본문"})') from None

    def ready(self):
        return self._parsed(api.HealthResponse, 'GET', api.HEALTH_PATH, timeout=5).ready

    def models(self):
        """[{id, type, loaded, …}] (/v1/models)"""
        return [m.model_dump() for m in self._parsed(api.ModelsResponse, 'GET', api.MODELS_PATH).data]

    def embed(self, model, texts, input_type='document', max_seq_length=None, timeout=None):
        """(len(texts), dim) float32. base64로 받아 비트 그대로. timeout: 질문이면 request_timeout(…), 없으면 제한 없음"""
        req = api.EmbeddingRequest(
            model=model,
            input=[str(t) for t in texts],
            encoding_format='base64',
            input_type=input_type,
            **({'max_seq_length': int(max_seq_length)} if max_seq_length else {}),
        )
        res = self._parsed(
            api.EmbeddingResponse, 'POST', api.EMBEDDINGS_PATH, req.model_dump(exclude_unset=True), timeout
        )
        data = sorted(res.data, key=lambda d: d.index)
        return np.stack([np.frombuffer(base64.b64decode(d.embedding), dtype='<f4') for d in data]).astype(np.float32)

    def rerank(self, model, query, documents, instruction=None, timeout=None):
        """문서 순서대로 점수 목록 (정렬은 호출한 쪽이 한다)"""
        req = api.RerankRequest(
            model=model, query=query, documents=list(documents), **({'instruction': instruction} if instruction else {})
        )
        res = self._parsed(api.RerankResponse, 'POST', api.RERANK_PATH, req.model_dump(exclude_unset=True), timeout)
        scores = [None] * len(documents)
        for item in res.results:
            scores[item.index] = item.relevance_score
        if any(s is None for s in scores):
            raise ModelServerError('모델 서버 rerank 응답에 빠진 문서가 있습니다')
        return scores


class Embedder:
    """retriever.Retriever가 쓰는 인코더: 모델 서버의 한 임베딩 모델
    query_timeout: 질문 임베딩(input_type='query')에만 거는 시간 상한. 조문 인덱스(document)는 제한 없음"""

    def __init__(self, client, model, query_timeout=None):
        self.client, self.model, self.query_timeout = client, model, query_timeout

    def encode(self, texts, input_type='document', max_seq_length=None):
        timeout = self.query_timeout if input_type == 'query' else None
        return self.client.embed(self.model, texts, input_type, max_seq_length, timeout)


WAIT_INTERVAL = 1.0  # 준비 확인 간격 (초)


def wait_ready(client, wait_s):
    """서버가 ready가 될 때까지 최대 wait_s초 (27B 로딩 대기). 넘기면 RuntimeError"""
    deadline, last = time.monotonic() + wait_s, None
    while True:
        try:
            if client.ready():
                return
            last = '모델을 불러오는 중'
        except ConnectionError as e:  # 서버가 아직 안 떴을 수 있음 (launchd로 동시에 시작)
            last = str(e)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f'모델 서버가 {wait_s:g}초 안에 준비되지 않았습니다 ({last}). '
                'LAWBOT_MODEL_SERVER_WAIT로 대기 시간을 늘리거나 모델 서버 로그를 확인하세요.'
            )
        time.sleep(WAIT_INTERVAL)
