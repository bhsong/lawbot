# tests/test_e2e_live.py  →  브라우저 E2E (실제 모델): Playwright → 개발 웹앱(이 worktree) → 공유 모델 서버(실제 모델)
# uv run pytest -m e2e_live services/chatbot/tests/test_e2e_live.py   (기본 실행·CI에서 제외. 사용자 승인 후 맥 미니에서만)
# 공유 모델 서버는 운영 서버다: 질문 3개를 순서대로, 재시도 없이. 실행 전 확인(preflight)이 하나라도 실패하면 질문을 보내지
# 않고 실패로 끝낸다 (건너뛰기로 처리하지 않음). 계약은 연결·표시만 보고 답의 정확성은 보지 않는다 (품질 평가는 docs/evals)
# 실행 구성은 시작 전·후에 읽어 results/e2e_live/<시각>.json에 남긴다. 둘이 다르면 결과는 무효 (docs/archives/e2e-playwright.md 3절)
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
from lawbot import deploy, envfile, llm, manifest
from lawbot.paths import ROOT
from lawbot.view import EXAMPLES
from lawbot_model_server import revision

pytestmark = pytest.mark.e2e_live

ANSWER_TIMEOUT = 180_000  # ms, 27B 한 질문 (모델 교체는 preflight에서 막음)
UPDATE_WINDOW = (datetime.time(6, 20), datetime.time(6, 50))  # 조문 자동 갱신(06:30) 앞뒤
CITATION = re.compile(r'\S+ 제\d+조')  # 근거 조문 형식 '법령명 제N조'
RUNTIME_HEALTH = '/health?runtime=1'
LOCAL = ('LAWBOT_PORT', 'LAWBOT_REQUEST_LOG', 'LAWBOT_FEEDBACK', 'LAWBOT_FEEDBACK_DIR', 'LAWBOT_INSTANCE')


def first_row(path, keep=lambda r: True):
    with (ROOT / path).open(encoding='utf-8-sig') as f:
        return next(r for r in csv.DictReader(f) if keep(r))


# 시나리오: 고정 파일의 고정 행 (파일이 바뀌면 질문도 바뀐다: 결과 기록에 질문 원문을 남김)
SCENARIOS = {
    'labor': EXAMPLES[0],  # 일반 노동법 질문
    'out_of_scope': first_row('data/oos_eval.csv', lambda r: r['category'] == 'other_law')['question'],  # 범위 밖
    'decree': first_row('data/v4/decree_ctx_val200.csv')['question'],  # 시행령·시행규칙 단계가 도는 질문 (Pro)
}


def sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest() if path and (ROOT / path).is_file() else None


def server_max_concurrent(health):
    value = health.get('max_concurrent')
    return value if type(value) is int and value > 0 else None


class Live:
    """레포 .env의 모델 서버·웹앱 설정. 웹앱은 이 worktree 코드로 빈 포트에, 요청 로그·피드백은 tmp로"""

    def __init__(self, tmp, env=None):
        """env: .env 값 (없으면 레포 .env를 읽음. 단위 테스트는 직접 넘김)"""
        self.tmp = tmp
        if env is None:
            envfile.load()
            env = {k: v for k, v in os.environ.items() if k.startswith('LAWBOT_') and k != 'LAWBOT_ENV_FILE'}
        self.env = env | {'LAWBOT_MODELS': 'pro'}
        self.server = self.env.get('LAWBOT_MODEL_SERVER')
        self.token = self.env.get('LAWBOT_MODEL_TOKEN')
        self.request_log = tmp / 'requests.jsonl'
        self.web, self.url, self.started, self.before, self.results = None, None, None, None, {}
        self.chat_model = self.env.get('LAWBOT_PRO_MODEL') or 'qwen3.8-27b-4bit'

    def get(self, path):
        r = httpx.get(
            self.server + path, headers={'Authorization': f'Bearer {self.token}'}, timeout=10, trust_env=False
        )
        r.raise_for_status()
        return r.json()

    def preflight(self, now=None):
        """질문을 보내기 전 확인. 문제가 있으면 이유 목록 (비어 있으면 통과)"""
        now = now or datetime.datetime.now(ZoneInfo('Asia/Seoul')).time()
        if UPDATE_WINDOW[0] <= now <= UPDATE_WINDOW[1]:
            return [f'조문 자동 갱신 시간대({UPDATE_WINDOW[0]:%H:%M}–{UPDATE_WINDOW[1]:%H:%M})']
        if not (self.server and self.token):
            return ['레포 .env에 LAWBOT_MODEL_SERVER·LAWBOT_MODEL_TOKEN이 없음']
        try:
            health = self.get(RUNTIME_HEALTH)
            if not health.get('ready'):
                return ['서버 미준비: /health ready가 아님']
            if server_max_concurrent(health) is None:
                return ['서버 동시 생성 상한을 확인할 수 없음']
            models = {m['id']: m for m in self.get('/v1/models')['data']}
        except httpx.HTTPError as e:
            return [f'서버 미준비: {type(e).__name__}']
        profile = deploy.get_profile(self.env)
        rr = deploy.reranker(profile, self.env)
        need = [profile.emb_model, self.chat_model]
        if rr:
            need.append(rr.model)
        reasons = [f'모델 없음: {m}' for m in need if m and m not in models]
        if self.chat_model in models and not models[self.chat_model]['loaded']:
            reasons.append(f'{self.chat_model}가 올라 있지 않음: 모델 교체로 운영 응답이 느려지므로 중단')
        return reasons

    def config(self):
        """실행 구성 (비밀값 제외). 시작 전·후를 비교해 다르면 결과 무효"""
        health, models = self.get(RUNTIME_HEALTH), self.get('/v1/models')['data']
        profile = deploy.get_profile(self.env)
        rr, dc = deploy.reranker(profile, self.env), deploy.decree(profile, self.env)
        m = manifest.load(profile.articles) or {}
        return {
            'webapp_commit': revision.git_commit(ROOT),
            'model_server_commit': health.get('commit'),
            'server_max_concurrent': server_max_concurrent(health),
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
    if s.before['server_max_concurrent'] is None:
        pytest.fail('실행 시작 구성에서 서버 동시 생성 상한을 확인할 수 없음 (질문을 보내지 않음)')
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
