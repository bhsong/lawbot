# lawbot/service.py  →  UI와 무관한 질의응답 흐름 (Gradio 앱과 평가가 씀)
# 모델은 모두 모델 서버(OpenAI 호환 API)에 있다: 임베딩·리랭크는 LAWBOT_MODEL_SERVER, 생성은 같은 서버 또는 LAWBOT_LLM_BASE_URL
# 조문 인덱스·검색·하위법령 필터·프롬프트 조립은 여기(웹앱)에서 한다
# 이 모듈은 gradio를 import하지 않는다 (tests/test_pipeline.py가 검사)
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import OpenAI

from lawbot import decree as decree_filter
from lawbot import deploy, manifest, security
from lawbot.api_client import Embedder, ModelLoadError, ModelServerClient, ModelServerError, wait_ready
from lawbot.llm import LLM, SYSTEM
from lawbot.paths import ROOT
from lawbot.reranker import Reranker
from lawbot.retriever import Retriever

TOP_K = 3  # 답변 근거로 넘기는 조문 수 (Fast·Pro 공통)
# 테스트는 Retriever·Reranker·LLM·ModelServerClient·OpenAI를 이 모듈의 속성으로 바꿔 끼운다 (load_app이 호출 때 읽음)


class ChatModels:
    """답변 모델 key → LLM (요청마다 key로 고른다: '현재 모델' 같은 공유 상태가 없음)
    모델 불러오기·상주(LRU)는 모델 서버가 한다. is_loaded는 서버 /v1/models의 loaded (외부 생성 API는 항상 True)"""

    def __init__(self, specs, factory, loaded=None):
        self.specs = {s.key: s for s in specs}
        self.factory, self._loaded = factory, loaded
        self.llms = {}

    def is_loaded(self, key):
        if self._loaded is None:
            return True
        try:
            return self.specs[key].model in self._loaded()
        except Exception:  # 확인 실패는 생성 요청에서 같은 오류로 드러남
            return True

    def get(self, key):
        """(LLM, None). 없는 key는 KeyError. 두 번째 값은 이전 인터페이스(로딩 초)와 맞춘 자리 (서버가 첫 요청 때 불러옴)"""
        spec = self.specs[key]
        if key not in self.llms:  # system 파일이 없으면 FileNotFoundError (화면에서 오류 안내)
            self.llms[key] = self.factory(spec)
        return self.llms[key], None


@dataclass
class LawbotApp:
    profile: deploy.Profile
    retriever: object
    models: ChatModels
    reranker: object = None  # None: 임베딩 상위 TOP_K개 그대로 (LAWBOT_RERANKER_ENABLED=0, 기본)
    full_system: str = None  # '전체(4항목)' system: 프로필 system 파일, 없으면 llm.SYSTEM (v1)
    decree: object = None  # deploy.DecreeConfig: 시행령·시행규칙 첨부 (None: 법률 3개만, LAWBOT_DECREE_ENABLED=0 기본)
    llm_mode: str = 'api'  # 생성 위치: 'api'(모델 서버) / 'external'(LAWBOT_LLM_BASE_URL의 외부 API)


def uses_decrees(app, model_key):
    """이 답변 모델에 하위법령을 붙이는지 (LAWBOT_DECREE_MODELS, 기본 Pro만)"""
    dc = getattr(app, 'decree', None)
    return dc is not None and model_key in dc.models


def system_text(profile, spec):
    """모델별 system 파일 → 프로필 system 파일 → None(llm.SYSTEM, v1 4섹션) 순서"""
    f = spec.system_file or profile.system_file
    return f.read_text(encoding='utf-8').strip() if f else None


def check_profile_files(p):
    """프로필의 조문 CSV·기본 system 파일이 있는지 모델 서버에 접속하기 전에 확인
    조문 CSV가 매니페스트(조문 수·sha256)와 다르면 거부, 매니페스트가 없으면 경고만 (manifest.check)"""
    missing = [str(f) for f in (p.articles, p.system_file) if f is not None and not f.is_file()]
    if missing:
        raise RuntimeError(
            f'프로필 {p.name}에 필요한 파일이 없습니다: {", ".join(missing)}. '
            'uv run python -m lawbot.check_deploy로 확인하거나, '
            'v4 자산 준비 전에는 LAWBOT_PROFILE=v1로 실행하세요.'
        )
    manifest.check(p.articles)


def server_wait():
    try:
        return float(os.environ.get('LAWBOT_MODEL_SERVER_WAIT') or 300)
    except ValueError:
        raise ValueError(f'LAWBOT_MODEL_SERVER_WAIT는 초(숫자): {os.environ["LAWBOT_MODEL_SERVER_WAIT"]!r}') from None


def missing_models(available, p, rr, external):
    """모델 서버 /v1/models에 없는 필요한 모델 [(종류, id)]. 외부 생성 API면 chat 모델은 서버에서 찾지 않음"""
    have = {(m['id'], m.get('type')) for m in available}
    need = [(p.emb_model, 'embedding')] + ([(rr.model, 'rerank')] if rr else [])
    if not external:
        need += [(s.model, 'chat') for s in p.models]
    return [(kind, mid) for mid, kind in need if (mid, kind) not in have]


def load_app(profile=None):
    """웹앱·평가용 LawbotApp: 조문 인덱스(임베딩은 모델 서버) + 켜져 있으면 리랭커 + 답변 모델(Fast/Pro) 레지스트리
    모델 서버 주소(LAWBOT_MODEL_SERVER)와 토큰(LAWBOT_MODEL_TOKEN)이 필요하다. 서버가 준비될 때까지 기다린 뒤,
    필요한 모델 id가 서버 /v1/models에 있는지 확인하고 없으면 시작을 거부한다"""
    p = profile or deploy.get_profile()
    check_profile_files(p)
    rr = deploy.reranker(p)  # 꺼져 있으면 None
    if rr and rr.candidates < TOP_K:
        raise ValueError(f'LAWBOT_RERANKER_CANDIDATES는 {TOP_K} 이상: {rr.candidates}')
    dc = deploy.decree(p)  # 꺼져 있으면 None. 켰는데 리랭커가 꺼져 있으면 ValueError
    url = security.model_server_url()
    if url is None:
        raise RuntimeError(
            '모델 서버 주소가 없습니다: .env에 LAWBOT_MODEL_SERVER=http://127.0.0.1:<모델 서버 포트>와 '
            'LAWBOT_MODEL_TOKEN을 적고, uv run lawbot-model-server로 모델 서버를 띄우세요.'
        )
    llm_url, llm_key, external = security.llm_endpoint(url)
    client = ModelServerClient(url, security.model_token())
    wait = server_wait()
    print(f'[lawbot] 모델 서버 {url} 준비 대기 (최대 {wait:g}초)', flush=True)
    wait_ready(client, wait)
    missing = missing_models(client.models(), p, rr, external)
    if missing:
        raise RuntimeError(
            '모델 서버에 필요한 모델이 없습니다: '
            + ', '.join(f'{kind} {mid}' for kind, mid in missing)
            + ' (모델 서버 .env의 LAWBOT_SERVE_*와 웹앱의 LAWBOT_*_MODEL을 맞추세요)'
        )
    t0 = time.perf_counter()
    retriever = Retriever(
        Embedder(client, p.emb_model),
        p.articles,
        max_seq_length=p.max_seq,
        doc_style=p.doc_style,
        **({'decree_max_seq': dc.max_seq} if dc else {}),
    )
    print(
        f'[lawbot] 조문 인덱스 {time.perf_counter() - t0:.1f}초 · 법률 {getattr(retriever, "n_law", len(retriever.ids))}개'
        + (f' + 하위법령 {len(retriever.ids) - retriever.n_law}개 (max_seq {dc.max_seq})' if dc else '')
    )
    reranker = Reranker(client, rr.model, rr.candidates) if rr else None
    # 외부 API는 셸의 프록시 설정을 따른다. 모델 서버(127.0.0.1)는 프록시를 거치지 않음
    oa = OpenAI(base_url=llm_url, api_key=llm_key, max_retries=0, http_client=httpx.Client(trust_env=external))

    def factory(spec):  # system 파일이 없으면 FileNotFoundError (화면에서 오류 안내)
        system = system_text(p, spec)
        return LLM(spec.model, client=oa, prompt_style=p.doc_style, **({'system': system} if system else {}))

    def loaded():
        return {m['id'] for m in client.models() if m.get('loaded')}

    models = ChatModels(p.models, factory, None if external else loaded)
    print(
        f'[lawbot] 프로필 {p.name} · 조문 {len(retriever.ids)}개 · 리랭커 {rr.model if rr else "꺼짐"} · '
        f'하위법령 {decree_text(dc)} · 생성 {"외부 " + llm_url if external else "모델 서버"} '
        f'({", ".join(s.model for s in p.models)})',
        flush=True,
    )
    full = p.system_file.read_text(encoding='utf-8').strip() if p.system_file else SYSTEM
    return LawbotApp(p, retriever, models, reranker, full, dc, 'external' if external else 'api')


def decree_text(dc):
    return f'켜짐 (τ {dc.tau:g} · 최대 {dc.k}개 · 모델 {",".join(dc.models) or "없음"})' if dc else '꺼짐'


def format_system(app, model_key, fmt):
    """화면 형식에 맞춘 이번 요청의 system (None이면 모델 기본 system)
    간결형이 기본인 모델(튜닝 전 Pro)에서 '전체'를 고르면 4항목 system으로 생성한다 (간결형 출력을 그대로 보여 주지 않음)
    4항목으로 학습한 Fast는 system을 바꾸지 않는다: 간결형은 화면 후처리(answer_format.concise)"""
    if fmt == 'full' and app.models.specs[model_key].fmt == 'concise':
        return app.full_system
    return None


def retrieve(app, question, k, st, model_key=None):
    """(법률 근거 조문 k개, 첨부할 하위법령 목록). 리랭커가 없으면 기존과 같은 임베딩 상위 k개,
    있으면 임베딩 상위 reranker.candidates개를 리랭킹해 점수순 k개. 측정값 search·rerank(초)를 st에 적는다
    model_key가 하위법령 첨부 모델이면(uses_decrees) 하위법령 후보 decree.candidates개를 같은 리랭커로 채점하고
    decree.attach로 거른다 (측정값 decree_search·decree_rerank, 첨부 수 n_decree, 후보 전체 decree_candidates)
    법률 부분은 하위법령을 켜도 같은 호출이다: 순위·점수·프롬프트가 지금과 같음
    리랭커 오류는 임베딩 순위로 몰래 되돌리지 않고 그대로 올린다 (생성 오류와 같이 화면에 오류로 표시):
    되돌리면 화면의 조문이 어느 방식으로 고른 것인지 알 수 없고, 켜 둔 설정과 다른 결과가 섞인다"""
    st['_stage'] = 'search'
    t0 = time.perf_counter()
    if app.reranker is None:
        hits = app.retriever.search(question, k=k)
        st['search'] = time.perf_counter() - t0
        return hits, []
    candidates = app.retriever.search(question, k=app.reranker.candidates)
    st['search'] = time.perf_counter() - t0
    st['_stage'] = 'rerank'
    t1 = time.perf_counter()
    hits = app.reranker.rerank(question, candidates, top_k=k)
    st['rerank'] = time.perf_counter() - t1
    if not uses_decrees(app, model_key):
        return hits, []
    return hits, retrieve_decrees(app, question, hits[0]['law'], st)


def retrieve_decrees(app, question, top1_law, st):
    """하위법령 후보 검색 → 리랭킹(후보 전부 점수) → 필터. 첨부 목록 (평가는 st['decree_candidates']도 씀)"""
    dc = app.decree
    st['_stage'] = 'search'
    t0 = time.perf_counter()
    cands = app.retriever.search_decrees(question, k=dc.candidates)
    st['decree_search'] = time.perf_counter() - t0
    st['_stage'] = 'rerank'
    t1 = time.perf_counter()
    scored = app.reranker.rerank(question, cands, top_k=len(cands))
    st['decree_rerank'] = time.perf_counter() - t1
    att = decree_filter.attach(scored, top1_law, dc.tau, dc.k)
    st['decree_candidates'], st['n_decree'] = scored, len(att)
    return att


def log_timing(st):
    """질문마다 단계별 시간 한 줄 (질문 내용은 남기지 않음)"""
    parts = [
        f'{name} {st[key]:.2f}s'
        for name, key in (
            ('검색', 'search'),
            ('리랭킹', 'rerank'),
            ('하위법령 검색', 'decree_search'),
            ('하위법령 리랭킹', 'decree_rerank'),
            ('모델 전환', 'load'),
            ('첫 토큰', 'ttft'),
            ('첫 글자', 'first_text'),
            ('완료', 'total'),
        )
        if st.get(key) is not None
    ]
    print(f'[lawbot] {st["model"]} · ' + ' · '.join(parts))


# ---- 요청 로그 (JSONL) ----
# 질문마다 한 줄: 단계별 시간·토큰·상태·오류 유형. 질문 원문과 답변은 남기지 않는다 (콘솔 로그·모델 서버 로그와 같은 정책)
# 기본 logs/requests.jsonl (gitignore). LAWBOT_REQUEST_LOG로 경로를 바꾸고, 빈 값이면 기록하지 않음
REQUEST_LOG = ROOT / 'logs' / 'requests.jsonl'
MS_FIELDS = ('search', 'rerank', 'decree_search', 'decree_rerank', 'load', 'ttft', 'first_text', 'total')


def request_log_path():
    value = os.environ.get('LAWBOT_REQUEST_LOG')
    if value is None:
        return REQUEST_LOG
    return Path(value).expanduser() if value.strip() else None


def error_type(e, stage):
    """오류 유형 (로그·화면 안내 공통). stage: 오류가 난 단계 'search'·'rerank'·'load'·'generation'
    model_server: 모델 서버(생성 API) 연결 실패·토큰 불일치(401)·로딩 중(503). 서버 쪽 연산 오류(500)는 단계로 나눈다
    model_load: 답변 모델 불러오기 실패 (서버의 모델 폴더 없음·형식 오류 = ModelLoadError, 웹앱의 system 파일 없음)
    config: 없는 모델 key, 서버에 없는 모델 id(404)"""
    if isinstance(e, ConnectionError) or (isinstance(e, ModelServerError) and e.status in (401, 503)):
        return 'model_server'
    if isinstance(e, ModelLoadError):
        return 'model_load'
    if isinstance(e, KeyError) or (isinstance(e, ModelServerError) and e.status == 404):
        return 'config'
    return 'model_load' if stage == 'load' else stage


def request_record(app, st, fmt, status, error):
    spec = app.models.specs.get(st['model'])
    rec = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'request_id': st['request_id'],
        'mode': getattr(app, 'llm_mode', 'api'),
        'caller': Path(sys.argv[0]).name,
        'profile': app.profile.name,
        'model': st['model'],
        'fmt': fmt or (spec.fmt if spec else None),
        'reranker': app.reranker is not None,
        'decree': uses_decrees(app, st['model']),
        'n_decree': st.get('n_decree'),
    }
    rec |= {f'{k}_ms': None if st.get(k) is None else round(st[k] * 1000) for k in MS_FIELDS}
    rec |= {k: st.get(k) for k in ('prompt_tokens', 'gen_tokens', 'peak_gb', 'finish')}
    rec |= {k: st.get(k) for k in ('refused', 'n_cited', *CITE_FIELDS)}
    return rec | {'status': status, 'error_type': error}


CITE_FIELDS = ('cite_ok', 'cite_outside', 'cite_wrong_law', 'cite_nonexistent', 'cite_ambiguous')


def answer_check(app, raw, hits):
    """요청 로그용 답변 요약 (내용 없이 수만): 거절 여부, 인용 수, citation.check 판정별 인용 수
    전체 조문은 검색기 기준. 판정에 실패해도 답변은 막지 않는다 (값 없음)"""
    try:
        from lawbot import citation

        cites = citation.check(citation.citations(raw), hits, citation.article_keys(app.retriever))
        verdicts = [v for _, _, v, _ in cites]
        return {'refused': citation.refused(raw), 'n_cited': len(cites)} | {
            f: verdicts.count(f.removeprefix('cite_')) for f in CITE_FIELDS
        }
    except Exception:
        return {}


LOG_LOCK = threading.Lock()  # 동시 질문(LAWBOT_CHAT_CONCURRENCY·평가 --concurrency)의 줄이 섞이지 않게


def log_request(app, st, fmt, status, error=None):
    """JSONL 한 줄. 기록 실패는 답변을 막지 않고 콘솔에 경고만"""
    path = request_log_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with LOG_LOCK, path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(request_record(app, st, fmt, status, error), ensure_ascii=False, default=str) + '\n')
    except OSError as e:
        print(f'[lawbot] 경고: 요청 로그를 쓰지 못했습니다 ({path}: {type(e).__name__})')


def ask(app, question, model_key, k=TOP_K, fmt=None):
    """질문 하나: (단계, 검색 조문, 답변 원문, 측정값)을 차례로 돌려준다
    단계 'load'(모델 전환 대기) → 'generate'(토큰마다) → 'done'. 조문은 생성 전에 정해짐
    fmt: 화면 형식 ('full'/'concise'). 주면 format_system으로 system을 고름 (None이면 모델 기본, 평가 스크립트)
    hits: 법률 k개 + 첨부한 하위법령 (하위법령 첨부 모델일 때만, 없으면 법률 k개만 = 지금과 같은 리스트)
    측정값(초): search, rerank(리랭커가 켜져 있을 때만), decree_search·decree_rerank(하위법령 첨부 모델일 때만), load(이번에 로딩했을 때만), ttft(생성 호출→첫 토큰, 프리필),
    first_text(질문 수신→첫 글자), total / tps, prompt_tokens, prompt_tps(프리필 속도), gen_tokens, peak_gb, finish
    질문마다 요청 로그 한 줄 (log_request): 성공 ok / 오류 error(유형은 예외의 lawbot_error에도 붙여 화면 안내에 씀) /
    중간에 멈춤 cancelled (정지 버튼 등으로 생성기를 닫음)"""
    t0 = time.perf_counter()
    st = {'model': model_key, 'load': None, 'request_id': uuid.uuid4().hex[:12]}
    stage, logged = 'search', False
    try:
        law_hits, decree_hits = retrieve(app, question, k, st, model_key)
        hits = law_hits + decree_hits
        stage = 'load'
        if not app.models.is_loaded(model_key):
            yield 'load', hits, '', st
        llm, st['load'] = app.models.get(model_key)
        stage = 'generation'
        system = format_system(app, model_key, fmt)
        raw, last, t_gen = '', None, time.perf_counter()
        for r in llm.stream(question, hits, **({'system': system} if system else {})):
            if last is None:
                st['ttft'] = time.perf_counter() - t_gen
            last = r
            if r.text and not raw:
                st['first_text'] = time.perf_counter() - t0
            raw += r.text
            yield 'generate', hits, raw, st
        st['total'] = time.perf_counter() - t0
        for name, attr in (
            ('tps', 'generation_tps'),
            ('prompt_tokens', 'prompt_tokens'),
            ('prompt_tps', 'prompt_tps'),
            ('gen_tokens', 'generation_tokens'),
            ('peak_gb', 'peak_memory'),
            ('finish', 'finish_reason'),
        ):
            st[name] = getattr(last, attr, None)
        st |= answer_check(app, raw, hits)
        log_timing(st)
        log_request(app, st, fmt, 'ok')
        logged = True
        yield 'done', hits, raw, st
    except GeneratorExit:
        if not logged:
            st['total'] = time.perf_counter() - t0
            log_request(app, st, fmt, 'cancelled')
            logged = True
        raise
    except Exception as e:
        if stage == 'search':
            stage = st.get('_stage', 'search')  # 법률·하위법령 검색('search') 또는 리랭킹('rerank')에서 실패
        e.lawbot_error = error_type(e, stage)
        if not logged:
            st['total'] = time.perf_counter() - t0
            log_request(app, st, fmt, 'error', e.lawbot_error)
        raise
