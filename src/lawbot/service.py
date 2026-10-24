# src/lawbot/service.py  →  UI와 무관한 질의응답 흐름 (Gradio 앱이 쓰고, 이후 API 서버도 그대로 씀)
# 이 모듈은 gradio를 import하지 않는다 (tests/test_pipeline.py가 검사)
import gc
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from lawbot import deploy
from lawbot.paths import LLM_4BIT
from lawbot.retriever import Retriever
from lawbot.llm import LLM

TOP_K = 3                            # 답변 근거로 넘기는 조문 수 (Fast·Pro 공통)

def load_models():
    """검색기·sLLM을 불러온다. LLM_PATH 환경 변수로 다른 sLLM을 지정 (평가·기존 호출용, 웹앱은 load_app)"""
    return Retriever(), LLM(os.environ.get('LLM_PATH', LLM_4BIT))

def answer_stream(retriever, llm, question, k=TOP_K):
    """(근거 조문 목록, 답변 조각 생성기). 조문은 생성 전에 정해지므로 먼저 돌려준다"""
    hits = retriever.search(question, k=k)
    return hits, (r.text for r in llm.stream(question, hits))

def free_memory():
    """내린 모델의 Metal 버퍼를 반납 (MLX는 해제한 버퍼를 캐시에 남겨 둠)"""
    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except ImportError:              # 테스트의 가짜 모듈 환경
        pass

class ModelManager:
    """답변 모델 지연 로딩 + LRU. 상주 모델 수는 max_resident (deploy.max_resident)
    선택은 요청마다 key로 받는다: '현재 모델' 같은 공유 상태를 두지 않아 이전 선택이 다음 질문에 섞이지 않음"""
    def __init__(self, specs, loader, max_resident=2):
        self.specs = {s.key: s for s in specs}
        self.loader, self.max_resident = loader, max_resident
        self.loaded = OrderedDict()
        self.lock = threading.Lock()     # 로딩·교체를 한 번에 하나만 (생성 자체는 Gradio concurrency_limit=1)

    def is_loaded(self, key):
        return key in self.loaded

    def get(self, key):
        """(모델, 이번에 로딩한 초). 이미 올라 있으면 로딩 초는 None"""
        spec = self.specs[key]           # 없는 key는 KeyError
        with self.lock:
            if key in self.loaded:
                self.loaded.move_to_end(key)
                return self.loaded[key], None
            while len(self.loaded) >= self.max_resident:   # 새 모델을 올리기 전에 내려서 최대 메모리를 줄임
                self.loaded.popitem(last=False)
                free_memory()
            t0 = time.perf_counter()
            llm = self.loader(spec)
            self.loaded[key] = llm
            return llm, time.perf_counter() - t0

@dataclass
class LawbotApp:
    profile: deploy.Profile
    retriever: object
    models: ModelManager
    early_stop: bool = False

def system_text(profile, spec):
    """모델별 system 파일 → 프로필 system 파일 → None(llm.SYSTEM, v1 4섹션) 순서"""
    f = spec.system_file or profile.system_file
    return f.read_text(encoding='utf-8').strip() if f else None

def load_app(profile=None):
    """웹앱용: 검색기 1개 + Fast/Pro 레지스트리. 기본 모델(Fast)은 미리 올리고, 2개 상주면 Pro도 시도"""
    p = profile or deploy.get_profile()
    retriever = Retriever(p.emb, p.articles, max_seq_length=p.max_seq, doc_style=p.doc_style)

    def loader(spec):                    # system 파일이 없으면 FileNotFoundError (화면에서 오류 안내)
        system = system_text(p, spec)
        return LLM(spec.path, prompt_style=p.doc_style, **({'system': system} if system else {}))
    models = ModelManager(p.models, loader, deploy.max_resident())
    models.get(p.models[0].key)          # 기본 모델이 없으면 여기서 실행 중단
    if models.max_resident >= 2:
        for spec in p.models[1:]:
            try:
                models.get(spec.key)
            except Exception as e:       # Pro 준비 전에도 Fast로는 실행 (선택하면 오류 안내)
                print(f'[lawbot] {spec.label} 미리 불러오기 실패: {type(e).__name__}: {e}')
    print(f"[lawbot] 프로필 {p.name} · 조문 {len(retriever.ids)}개 · 상주 {list(models.loaded)}")
    return LawbotApp(p, retriever, models, deploy.early_stop())

def ask(app, question, model_key, k=TOP_K, early_stop=False):
    """질문 하나: (단계, 검색 조문, 답변 원문, 측정값)을 차례로 돌려준다
    단계 'load'(모델 전환 대기) → 'generate'(토큰마다) → 'done'. 조문은 생성 전에 정해짐
    측정값(초): search, load(이번에 로딩했을 때만), ttft(생성 호출→첫 토큰, 프리필), first_text(질문 수신→첫 글자),
    total, early_stop_at(간결형 조기 종료 시점) / tps, prompt_tokens, prompt_tps(프리필 속도), gen_tokens, peak_gb, finish"""
    t0 = time.perf_counter()
    st = {'model': model_key, 'load': None}
    hits = app.retriever.search(question, k=k)
    st['search'] = time.perf_counter() - t0
    if not app.models.is_loaded(model_key):
        yield 'load', hits, '', st
    llm, st['load'] = app.models.get(model_key)
    raw, last, t_gen = '', None, time.perf_counter()
    for r in llm.stream(question, hits):
        if last is None:
            st['ttft'] = time.perf_counter() - t_gen
        last = r
        if r.text and not raw:
            st['first_text'] = time.perf_counter() - t0
        raw += r.text
        yield 'generate', hits, raw, st
        if early_stop and '【설명】' in raw:     # 간결형은 결론·근거 조문까지만 보여 줌
            st['early_stop_at'] = time.perf_counter() - t0
            break
    st['total'] = time.perf_counter() - t0
    for name, attr in (('tps', 'generation_tps'), ('prompt_tokens', 'prompt_tokens'), ('prompt_tps', 'prompt_tps'),
                       ('gen_tokens', 'generation_tokens'), ('peak_gb', 'peak_memory'),
                       ('finish', 'finish_reason')):
        st[name] = getattr(last, attr, None)
    yield 'done', hits, raw, st
