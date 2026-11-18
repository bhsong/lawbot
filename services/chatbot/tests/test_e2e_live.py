# Opt-in real-model browser E2E against separately configured generation and embedding/rerank servers.
# Never run this suite without approval. Configuration comes only from the supplied environment.
# Revisions are captured before and after the run; generation has no loaded/type fields.
import csv
import datetime
import hashlib
import json
import os
import re
import time
from zoneinfo import ZoneInfo

import httpx
import pytest

import e2e_support as e2e
from conftest import free_port
from lawbot import deploy, llm, manifest
from lawbot.paths import ROOT
from lawbot_common.llm_endpoint import endpoint, generation_model
from lawbot_model_server import revision

pytestmark = pytest.mark.e2e_live

ANSWER_TIMEOUT = 180_000
UPDATE_WINDOW = (datetime.time(6, 20), datetime.time(6, 50))  # 조문 자동 갱신(06:30) 앞뒤
CITATION = re.compile(r'\S+ 제\d+조')  # 근거 조문 형식 '법령명 제N조'
LOCAL = ('LAWBOT_PORT', 'LAWBOT_REQUEST_LOG', 'LAWBOT_FEEDBACK', 'LAWBOT_FEEDBACK_DIR', 'LAWBOT_INSTANCE')


def first_row(path, keep=lambda r: True):
    with (ROOT / path).open(encoding='utf-8-sig') as f:
        return next(r for r in csv.DictReader(f) if keep(r))


# Keep the labor smoke question independent of the cards on the start screen.
SCENARIOS = {
    'labor': '해고 예고 없이 갑자기 해고되면 어떻게 하나요?',
    'out_of_scope': first_row('data/oos_eval.csv', lambda r: r['category'] == 'other_law')['question'],  # 범위 밖
    'decree': first_row('data/v4/decree_ctx_val200.csv')['question'],  # 시행령·시행규칙 단계가 도는 질문 (Pro)
}


def sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest() if path and (ROOT / path).is_file() else None


class Live:
    """Record an explicitly configured live stack without reading the repository .env."""

    def __init__(self, tmp, env=None):
        """Unit tests pass env directly; approved live runs use the process environment."""
        self.tmp = tmp
        if env is None:
            env = {k: v for k, v in os.environ.items() if k.startswith('LAWBOT_') and k != 'LAWBOT_ENV_FILE'}
        self.env = env | {'LAWBOT_MODELS': 'pro'}
        self.server = self.env.get('LAWBOT_MODEL_SERVER')
        self.token = self.env.get('LAWBOT_MODEL_TOKEN')
        self.generation_server = self.env.get('LAWBOT_LLM_BASE_URL', '').rstrip('/')
        self.generation_token = self.env.get('LAWBOT_LLM_API_KEY')
        self.request_log = tmp / 'requests.jsonl'
        self.web, self.url, self.started, self.before, self.results = None, None, None, None, {}
        self.chat_model = self.env.get('LAWBOT_PRO_MODEL') or 'qwen3.8-27b-4bit'

    def get(self, path, *, generation=False):
        server = self.generation_server if generation else self.server
        token = self.generation_token if generation else self.token
        r = httpx.get(server + path, headers={'Authorization': f'Bearer {token}'}, timeout=10, trust_env=False)
        r.raise_for_status()
        return r.json()

    def preflight(self, now=None):
        """질문을 보내기 전 확인. 문제가 있으면 이유 목록 (비어 있으면 통과)"""
        now = now or datetime.datetime.now(ZoneInfo('Asia/Seoul')).time()
        if UPDATE_WINDOW[0] <= now <= UPDATE_WINDOW[1]:
            return [f'조문 자동 갱신 시간대({UPDATE_WINDOW[0]:%H:%M}–{UPDATE_WINDOW[1]:%H:%M})']
        if not (self.server and self.token):
            return ['LAWBOT_MODEL_SERVER·LAWBOT_MODEL_TOKEN이 없음']
        if not (self.generation_server and self.generation_token):
            return ['LAWBOT_LLM_BASE_URL·LAWBOT_LLM_API_KEY가 없음']
        try:
            external = endpoint(self.env)[2]
        except (RuntimeError, ValueError) as exc:
            return [str(exc)]
        try:
            health = self.get('/health')
            if not health.get('ready'):
                return ['서버 미준비: /health ready가 아님']
            models = {m['id']: m for m in self.get('/v1/models')['data']}
            generation_models = (
                {} if external else {model['id']: model for model in self.get('/models', generation=True)['data']}
            )
        except httpx.HTTPError as e:
            return [f'서버 미준비: {type(e).__name__}']
        profile = deploy.get_profile(self.env)
        rr = deploy.reranker(profile, self.env)
        need = [profile.emb_model]
        if rr:
            need.append(rr.model)
        reasons = [f'모델 없음: {m}' for m in need if m and m not in models]
        if external:
            return reasons
        if self.chat_model not in generation_models:
            reasons.append(f'생성 모델 없음: {self.chat_model}')
        elif not generation_models[self.chat_model].get('revision'):
            reasons.append(f'생성 모델 revision 없음: {self.chat_model}')
        else:
            try:
                generation_model(generation_models.values(), self.chat_model)
            except ValueError as exc:
                reasons.append(str(exc))
        return reasons

    def config(self):
        """실행 구성 (비밀값 제외). 시작 전·후를 비교해 다르면 결과 무효"""
        health, models = self.get('/health'), self.get('/v1/models')['data']
        external = endpoint(self.env)[2]
        generation_models = [] if external else self.get('/models', generation=True)['data']
        chat_model = {} if external else generation_model(generation_models, self.chat_model)
        profile = deploy.get_profile(self.env)
        rr, dc = deploy.reranker(profile, self.env), deploy.decree(profile, self.env)
        m = manifest.load(profile.articles) or {}
        return {
            'webapp_commit': revision.git_commit(ROOT),
            'model_server_commit': health.get('commit'),
            'server_max_concurrent': chat_model.get('max_concurrent'),
            'generation_server_mlx_lm_version': None,
            'generation_server_mlx_lm_version_note': 'Confirm manually at the generation server management location.',
            'generation_models': [
                {key: model.get(key) for key in ('id', 'object', 'revision')} for model in generation_models
            ],
            'models': [{k: x.get(k) for k in ('id', 'type', 'loaded', 'revision')} for x in models],
            'dataset_version': m.get('dataset_version'),
            'articles_sha256': sha256(profile.articles),
            'system_sha256': sha256(profile.models[0].system_file),
            'full_system_sha256': sha256(profile.system_file),
            'temperature': llm.TEMPERATURE,
            'max_tokens': 1024,
            'settings': {
                'profile': profile.name,
                'embedding': profile.emb_model,
                'max_seq': profile.max_seq,
                'chat': profile.models[0].model,
                'reranker': None if rr is None else vars(rr),
                'decree': None if dc is None else vars(dc),
            },
        }

    def logs(self):
        return [self.request_log] + ([self.web.log] if self.web else [])


def comparable(cfg):
    """시작 전·후 비교: 상주 여부(loaded)는 운영 사용으로 바뀔 수 있어 뺀다"""
    return cfg | {'models': [{k: v for k, v in m.items() if k != 'loaded'} for m in cfg['models']]}


@pytest.fixture(scope='module')
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('e2e_live')
    s = Live(tmp)
    reasons = s.preflight()
    if reasons:
        pytest.fail('실행 전 확인 실패 (질문을 보내지 않음): ' + '; '.join(reasons))
    s.before = s.config()
    port = free_port()
    env = {k: v for k, v in s.env.items() if k not in LOCAL}
    env.update(LAWBOT_REQUEST_LOG=str(s.request_log), LAWBOT_FEEDBACK='0', LAWBOT_MODELS='pro')
    s.web = e2e.start_web(tmp, port, **env)
    s.url = f'http://127.0.0.1:{port}'
    yield s
    s.web.stop()
    after = s.config()
    valid = comparable(after) == comparable(s.before)
    record = {
        'started': s.started,
        'valid': valid,
        'before': s.before,
        'after': after,
        'scenarios': s.results,
    }
    out = ROOT / 'results' / 'e2e_live'
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'{datetime.datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n실행 구성·결과 기록: {path}')
    if not valid:
        pytest.fail(f'실행 중 구성이 바뀜: 결과 무효 ({path})')


@pytest.fixture
def ui(live, page, request):
    """실패하면 진단 산출물을 results/e2e_live/에 남긴다 (테스트 결과는 정리 단계에서야 알 수 있음)"""
    yield page
    e2e.keep_on_failure(request, 'e2e_live', live.logs(), page)


def run_scenario(live, page, name):
    """질문 하나: 로그인 → 질문 → 끝날 때까지 → 계약 확인. 결과를 live.results에 남긴다"""
    before = len(e2e.request_log(live.request_log))
    row = {'question': SCENARIOS[name], 'passed': False}
    live.results[name] = row
    e2e.login(page, live.url)
    e2e.textbox(page).wait_for()
    t0 = time.monotonic()
    e2e.ask(page, SCENARIOS[name])
    e2e.wait_done(page, timeout=ANSWER_TIMEOUT)
    row['browser_s'] = round(time.monotonic() - t0, 1)
    text, st, src = e2e.answer(page).inner_text(), e2e.stats(page).inner_text(), e2e.sources(page).inner_text()
    logs = e2e.request_log(live.request_log)[before:]
    assert len(logs) == 1, logs
    log = logs[0]
    row.update({k: log.get(k) for k in ('status', 'error_type', 'total_ms', 'ttft_ms', 'n_decree', 'refused')})
    row.update(answer=text, sources=[c.inner_text() for c in e2e.sources(page).get_by_test_id('source-card').all()])
    assert text.strip() and not text.startswith('⏳'), text  # 스트리밍이 끝나고 비어 있지 않은 답
    assert (log['mode'], log['status'], log['error_type']) == ('api', 'ok', None), log
    assert len(e2e.law_cards(page)) == 3, src  # 법률 조문 카드 3개
    for part in ('검색', '리랭킹', '첫 토큰', '완료'):
        assert part in st, (part, st)
    if name != 'out_of_scope':
        assert CITATION.search(text), text  # 근거 조문 형식 (맞는 조문인지는 품질 평가)
    if name == 'decree':
        assert '시행령·시행규칙' in st, st  # 하위법령 단계가 돌았음 (몇 개 붙었는지는 n_decree로 기록만)
    row['passed'] = True


def test_labor_question(live, ui):
    live.started = datetime.datetime.now().isoformat(timespec='seconds')
    run_scenario(live, ui, 'labor')


def test_out_of_scope_question(live, ui):
    run_scenario(live, ui, 'out_of_scope')


def test_decree_question(live, ui):
    run_scenario(live, ui, 'decree')
