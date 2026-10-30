# src/lawbot/remote.py  →  원격 모드 웹앱의 모델 서버 클라이언트 (LAWBOT_MODEL_SERVER가 있을 때 service.load_app이 씀)
# Retriever·Reranker·LLM·ModelManager와 같은 인터페이스를 HTTP로 감싼다: service.ask·app.py·eval_v4는 그대로 동작
# 모델 라이브러리(torch·mlx)와 gradio를 import하지 않는다. 표준 라이브러리 urllib만 사용
import json
import time
import types
import urllib.error
import urllib.request
import numpy as np
from lawbot import manifest

API_VERSION = 1                      # model_server.API_VERSION과 같아야 함 (모델 서버 모듈은 import하지 않음)
GEN_FIELDS = ('text', 'generation_tps', 'prompt_tokens', 'prompt_tps', 'generation_tokens', 'peak_memory',
              'finish_reason')

class ModelServerError(RuntimeError):
    """모델 서버가 보낸 오류 (kind: 서버 쪽 예외 이름, base: 'OSError'·'ValueError'·None 계열)"""
    def __init__(self, kind, message, status=None, base=None):
        super().__init__(f'모델 서버 오류 {kind}: {message}')
        self.kind, self.status, self.base = kind, status, base

class RemoteLoadError(OSError):
    """모델 서버에서 답변 모델을 불러오지 못함 (app.py가 OSError로 받아 기존 안내를 보여 줌)"""

class RemoteValueError(ValueError):
    """모델 서버에서 답변 모델을 불러오지 못함: 형식 오류 등 ValueError 계열"""

class ModelServerClient:
    def __init__(self, url, token, timeout=None):
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout
        # HTTP_PROXY 같은 프록시 환경 변수를 쓰지 않는다: 질문이 맥 밖(프록시)으로 나가지 않게 127.0.0.1에 직접 접속
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path, body=None, timeout=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(self.url + path, data=data, method='GET' if body is None else 'POST',
                                     headers={'Authorization': f'Bearer {self.token}',
                                              'Content-Type': 'application/json; charset=utf-8'})
        try:
            return self.opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 401:            # 토큰 값은 출력하지 않음
                raise ModelServerError('Unauthorized', '토큰이 맞지 않습니다. 웹앱과 모델 서버의 LAWBOT_MODEL_TOKEN을 '
                                       '같은 값으로 적으세요.', 401) from None
            try:
                err = json.loads(e.read())['error']
                kind, message, base = err['type'], err['message'], err.get('base')
            except Exception:
                kind, message, base = 'HTTPError', f'HTTP {e.code}', None
            raise ModelServerError(kind, message, e.code, base) from None
        except (urllib.error.URLError, ConnectionError) as e:
            raise ConnectionError(f'모델 서버에 연결할 수 없습니다: {self.url} '
                                  f'(uv run lawbot-model-server 실행 여부 확인, {e})') from None

    def get_json(self, path, timeout=None):
        with self._request(path, timeout=timeout) as r:
            return json.loads(r.read())

    def post_json(self, path, body):
        with self._request(path, body) as r:
            return json.loads(r.read())

    def post_stream(self, path, body):
        """NDJSON 한 줄씩 dict. 제너레이터를 닫으면 연결을 끊는다 (서버가 생성을 멈추고 락을 풂)"""
        r = self._request(path, body)
        try:
            for line in r:
                if line.strip():
                    yield json.loads(line)
        finally:
            r.close()

class RemoteRetriever:
    """Retriever와 같은 속성·메서드. 조문 목록(ids·titles·docs·laws)은 시작할 때 한 번 받아 numpy 배열로 둔다"""
    def __init__(self, client):
        self.client = client
        a = client.get_json('/articles')
        self.ids, self.titles = np.array(a['ids'], dtype=object), np.array(a['titles'], dtype=object)
        self.docs = list(a['docs'])
        self.laws = None if a['laws'] is None else np.array(a['laws'], dtype=object)

    def search(self, query, k=3):
        return self.client.post_json('/search', {'question': query, 'k': int(k)})['hits']

    def rank_all(self, queries):
        return np.array(self.client.post_json('/rank_all', {'questions': [str(q) for q in queries]})['rank'],
                        dtype=np.int64)

class RemoteReranker:
    def __init__(self, client, candidates):
        self.client, self.candidates = client, candidates

    def rerank(self, question, candidates, top_k=3):
        return self.client.post_json('/rerank', {'question': question, 'candidates': list(candidates),
                                                 'top_k': int(top_k)})['hits']

class RemoteLLM:
    """LLM.stream과 같은 응답(text·generation_tps·…·finish_reason 속성)을 낸다
    system: 이 모델의 기본 system (웹앱 쪽 파일에서 읽음). 요청마다 서버로 보낸다"""
    def __init__(self, client, key, system):
        self.client, self.key, self.system = client, key, system

    def stream(self, question, hits, max_tokens=1024, system=None):
        body = {'model': self.key, 'question': question, 'hits': list(hits),
                'system': system or self.system, 'max_tokens': max_tokens}
        lines = self.client.post_stream('/generate', body)
        try:
            for d in lines:
                if 'error' in d:
                    raise RuntimeError(f"모델 서버 생성 오류 {d['error']['type']}: {d['error']['message']}")
                yield types.SimpleNamespace(**{k: d.get(k) for k in GEN_FIELDS})
        finally:
            lines.close()

class RemoteModelManager:
    """ModelManager와 같은 인터페이스. 상주·교체(LRU)는 서버가 한다
    specs는 웹앱 프로필 기준 (화면 표시·형식). system_for(spec)은 모델 기본 system 문구 (웹앱 쪽 파일에서 읽음)
    RemoteLLM은 모델마다 하나를 처음 get할 때 만들어 계속 쓴다 (로컬처럼 llm.system을 바꾸면 다음 요청에도 적용)"""
    def __init__(self, specs, client, system_for):
        self.specs = {s.key: s for s in specs}
        self.client, self.system_for = client, system_for
        self.max_resident = client.get_json('/info')['max_resident']
        self.llms = {}

    @property
    def loaded(self):
        """서버에 상주 중인 모델 key (로딩 순서). 원격 모드에서는 key 목록만"""
        return [k for k in self.client.get_json('/info')['loaded'] if k in self.specs]

    def is_loaded(self, key):
        return key in self.client.get_json('/info')['loaded']

    def get(self, key):
        """(RemoteLLM, 이번에 로딩한 초). 서버의 로딩 실패는 OSError·ValueError 계열로 올림 (app.py 안내)"""
        spec = self.specs[key]           # 없는 key는 KeyError (로컬과 같음)
        if key not in self.llms:         # system 파일이 없으면 FileNotFoundError (로컬의 로딩 실패와 같은 안내)
            self.llms[key] = RemoteLLM(self.client, key, self.system_for(spec))
        try:
            load_s = self.client.post_json('/load', {'model': key})['load_s']
        except ModelServerError as e:
            if e.base == 'OSError':      # 모델 폴더 없음 등
                raise RemoteLoadError(str(e)) from e
            if e.base == 'ValueError':   # 모델 형식 오류 등
                raise RemoteValueError(str(e)) from e
            raise
        return self.llms[key], load_s

WAIT_INTERVAL = 1.0                  # 준비 확인 간격 (초)

def wait_ready(client, wait_s):
    """서버가 ready가 될 때까지 최대 wait_s초 (27B 로딩 대기). 넘기면 RuntimeError"""
    deadline, last = time.monotonic() + wait_s, None
    while True:
        try:
            if client.get_json('/health', timeout=5)['ready']:
                return
            last = '모델을 불러오는 중'
        except ConnectionError as e:     # 서버가 아직 안 떴을 수 있음 (launchd로 동시에 시작)
            last = str(e)
        if time.monotonic() >= deadline:
            raise RuntimeError(f'모델 서버가 {wait_s:g}초 안에 준비되지 않았습니다 ({last}). '
                               'LAWBOT_MODEL_SERVER_WAIT로 대기 시간을 늘리거나 모델 서버 로그를 확인하세요.')
        time.sleep(WAIT_INTERVAL)

def check_info(info, profile):
    """서버와 웹앱 설정이 맞는지. 다른 항목 목록 (비어 있으면 일치)"""
    diff = []
    if info.get('api_version') != API_VERSION:
        diff.append(f"api_version: 서버 {info.get('api_version')} / 웹앱 {API_VERSION} (두 쪽 코드 버전을 맞추세요)")
    sp = info.get('profile') or {}
    if sp.get('name') != profile.name:
        diff.append(f"프로필: 서버 {sp.get('name')} / 웹앱 {profile.name} (LAWBOT_PROFILE)")
    if sp.get('doc_style') != profile.doc_style:
        diff.append(f"doc_style: 서버 {sp.get('doc_style')} / 웹앱 {profile.doc_style}")
    missing = [s.key for s in profile.models if s.key not in (info.get('models') or [])]
    if missing:
        diff.append(f"웹앱 모델 {missing}이 서버 모델 {info.get('models')}에 없음 (LAWBOT_MODELS)")
    return diff

def dataset_warnings(info, profile):
    """서버 /info의 dataset과 웹앱 레포의 매니페스트가 다른 항목 (경고용, 시작은 막지 않음)
    dataset이 없는 이전 서버나, 웹앱 쪽 매니페스트가 없으면 빈 목록"""
    server, web = info.get('dataset'), manifest.summary(manifest.load(profile.articles))
    if not server or not web:
        return []
    return [f'{k}: 서버 {server.get(k)} / 웹앱 {web[k]} (모델 서버와 웹앱의 레포 버전을 맞추세요)'
            for k in web if server.get(k) != web[k]]
