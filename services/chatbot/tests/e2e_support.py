# tests/e2e_support.py  →  브라우저 E2E(e2e_stub·e2e_live) 공통: 프로세스 띄우기·정리, 로그인·질문, 실패 진단 산출물
# 계획: docs/archives/e2e-playwright.md. 산출물은 tmp에 두고, 실패한 테스트만 results/<층>/<시각>-<테스트>/로 옮긴다
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import TEST_TOKEN, free_port
from lawbot_common import envfile

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).resolve().parent
USER, PW = 'tester', 'test-only-pw'  # 테스트 전용 가짜 값
START_TIMEOUT = 90  # 웹앱 시작(조문 임베딩 포함)까지
ANSWER_TIMEOUT = 30_000  # ms, 질문 하나가 끝날 때까지 (e2e_live는 따로 늘림)


def example_env(name='.env.example'):
    """.env.example의 값이 있는 설정 (운영 구성과 같게. 비밀값·자리표시자 '<…>'는 뺌)"""
    values = envfile.read(ROOT / name, envfile.lawbot_keys) or {}
    return {k: v for k, v in values.items() if v and '<' not in v}


def clean_env(**kw):
    """개발자 셸의 LAWBOT_* 설정을 빼고 시작 (레포 .env도 읽지 않음)"""
    env = {k: v for k, v in os.environ.items() if not k.startswith('LAWBOT_')}
    env.update(LAWBOT_ENV_FILE='')
    env.update(kw)
    return env


class Proc:
    """별도 프로세스 하나. 출력은 log_dir/<이름>.log (비밀번호는 output()에서 가림)"""

    def __init__(self, name, args, env, log_dir):
        self.name, self.log = name, Path(log_dir) / f'{name}.log'
        with self.log.open('w') as out:
            self.p = subprocess.Popen(
                args, env=env, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
            )

    def alive(self):
        return self.p.poll() is None

    def output(self):
        return self.log.read_text(encoding='utf-8', errors='replace').replace(PW, '***')

    def wait_http(self, url, ok, timeout=START_TIMEOUT, headers=None):
        deadline = time.monotonic() + timeout
        while True:
            if not self.alive():
                pytest.fail(f'{self.name} 프로세스가 종료됨 (code {self.p.returncode})\n{self.output()}')
            try:
                if ok(httpx.get(url, timeout=1, headers=headers)):
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f'{self.name}가 {timeout}초 안에 준비되지 않음\n{self.output()}')
            time.sleep(0.2)

    def stop(self):
        if self.alive():
            self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()


def start_web(log_dir, port, **env):
    """실제 웹앱(lawbot-app, 빌드한 화면 포함)을 127.0.0.1:<port>에 띄우고 로그인 확인 API가 401을 줄 때까지 기다린다
    화면 빌드가 없거나 소스와 다르면 웹앱이 시작을 거부하고 테스트가 실패한다 (CI는 테스트 전에 npm run build)"""
    web = Proc(
        'web',
        [str(Path(sys.executable).with_name('lawbot-app')), '--port', str(port)],
        clean_env(**(env | {'LAWBOT_USER': USER, 'LAWBOT_PW': PW})),  # 테스트 로그인이 .env 값보다 우선
        log_dir,
    )
    ready = False
    try:
        web.wait_http(f'http://127.0.0.1:{port}/api/me', lambda r: r.status_code == 401)
        ready = True
        return web
    finally:
        if not ready:
            web.stop()


class Stack:
    """웹앱 1개 + 가짜 모델 서버 1개. 모델 서버는 같은 포트에서 모드만 바꿔 다시 띄운다 (웹앱은 그대로)
    extra_env: 웹앱 환경 변수 추가"""

    def __init__(self, tmp, read_timeout, example='.env.example', **extra_env):
        self.tmp, self.ms, self.mode, self.n = tmp, None, None, 0
        self.token = TEST_TOKEN  # 모델 서버에는 환경 변수로 넘김 (e2e_model_server를 이 프로세스에서 import하지 않음)
        self.ms_port, self.web_port = free_port(), free_port()
        self.url = f'http://127.0.0.1:{self.web_port}'
        self.ms_url = f'http://127.0.0.1:{self.ms_port}'
        self.request_log = tmp / 'requests.jsonl'
        self.serve = {k: v for k, v in example_env().items() if k.startswith('LAWBOT_SERVE_')}
        web_env = {k: v for k, v in example_env(example).items() if not k.startswith('LAWBOT_SERVE_')}
        web_env.update(
            LAWBOT_MODEL_SERVER=self.ms_url,
            LAWBOT_MODEL_TOKEN=self.token,
            LAWBOT_EMBED_CACHE='0',
            LAWBOT_REQUEST_LOG=str(self.request_log),
            LAWBOT_FEEDBACK='0',
            LAWBOT_MODEL_READ_TIMEOUT=str(read_timeout),
            LAWBOT_MODEL_SERVER_WAIT='30',
        )
        web_env.update(extra_env)
        self.web = None
        try:
            self.ensure('normal')
            self.web = start_web(tmp, self.web_port, **web_env)
        finally:
            if self.web is None:
                self.stop_model_server()

    def ensure(self, mode):
        """모델 서버가 mode로 떠 있게 한다 (이미 그 모드로 살아 있으면 그대로)"""
        if self.ms is not None and self.ms.alive() and self.mode == mode:
            return
        self.stop_model_server()
        self.n += 1
        self.ms = Proc(
            f'model-server-{self.n}-{mode}',
            [sys.executable, str(TESTS / 'e2e_model_server.py'), str(self.ms_port), mode],
            clean_env(LAWBOT_MODEL_TOKEN=self.token, **self.serve),
            self.tmp,
        )
        self.mode = mode
        self.ms.wait_http(
            self.ms_url + '/health',
            lambda r: r.status_code == 200 and r.json().get('ready'),
            headers={'Authorization': f'Bearer {self.token}'},  # /health도 토큰 필요
        )

    def stop_model_server(self):
        if self.ms is not None:
            self.ms.stop()

    def logs(self):
        return [self.web.log, self.request_log, *sorted(self.tmp.glob('model-server-*.log'))]

    def stop(self):
        self.web.stop()
        self.stop_model_server()


def request_log(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ---- 화면 조작 (역할·라벨·테스트 id로 찾는다) ----
def textbox(page):
    return page.get_by_role('textbox', name='질문')


def login(page, url, user=USER, pw=PW):
    page.goto(url)
    page.get_by_label('사용자명').fill(user)
    page.get_by_label('비밀번호').fill(pw)
    page.get_by_role('button', name='로그인').click()


def ask(page, question):
    textbox(page).fill(question)
    textbox(page).press('Enter')


def answer(page):
    return page.get_by_test_id('answer').last


def stats(page):
    return page.get_by_test_id('lawbot-stats').last


def sources(page):
    """데스크톱 오른쪽 조문 패널 (휴대폰 시트에도 같은 test id가 있어 첫 번째)"""
    return page.get_by_test_id('lawbot-sources').first


def law_cards(page):
    """조문 패널의 법률 조문 카드 글 (하위법령 카드는 〔시행령〕·〔시행규칙〕 표시가 있어 뺀다)"""
    cards = [c.inner_text() for c in sources(page).get_by_test_id('source-card').all()]
    return [c for c in cards if '〔' not in c]


def wait_done(page, timeout=ANSWER_TIMEOUT):
    """측정 줄에 '완료'가 나오면 질문 하나가 끝난 것 (fmt_stats의 done 단계)"""
    stats(page).get_by_text('완료').wait_for(timeout=timeout)


# ---- 실패 진단 산출물 ----
def keep_on_failure(request, layer, files, page=None):
    """테스트가 실패했으면 files(프로세스 출력·요청 로그)와 화면 스크린샷·HTML을 results/<layer>/<시각>-<테스트>/로 복사
    성공하면 아무것도 남기지 않는다 (tmp는 pytest가 정리)"""
    rep = getattr(request.node, 'rep_call', None)
    if rep is None or not rep.failed:
        return None
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    out = ROOT / 'results' / layer / f'{stamp}-{request.node.name}'
    out.mkdir(parents=True, exist_ok=True)
    for f in files:
        f = Path(f)
        if f.exists():
            (out / f.name).write_text(f.read_text(encoding='utf-8', errors='replace').replace(PW, '***'))
    if page is not None:
        try:
            page.screenshot(path=str(out / 'screen.png'), full_page=True)
            (out / 'page.html').write_text(page.content(), encoding='utf-8')
        except Exception as e:  # 화면이 이미 닫혔으면 파일만
            (out / 'screen-error.txt').write_text(repr(e))
    return out


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
