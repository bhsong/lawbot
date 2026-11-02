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

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).resolve().parent
USER, PW = 'tester', 'test-only-pw'  # 테스트 전용 가짜 값
START_TIMEOUT = 90  # 웹앱 시작(조문 임베딩 포함)까지
ANSWER_TIMEOUT = 30_000  # ms, 질문 하나가 끝날 때까지 (e2e_live는 따로 늘림)


def example_env():
    """.env.example의 값이 있는 설정 (운영 구성과 같게. 비밀값·자리표시자 '<…>'는 뺌)"""
    out = {}
    for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines():
        k, sep, v = line.strip().partition('=')
        if sep and not k.startswith('#') and v and '<' not in v:
            out[k] = v
    return out


def clean_env(**kw):
    """개발자 셸의 LAWBOT_*·Gradio 설정을 빼고 시작 (레포 .env도 읽지 않음)"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LAWBOT_', 'GRADIO_'))}
    env.update(GRADIO_ANALYTICS_ENABLED='False', LAWBOT_ENV_FILE='')
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


def start_webapp(log_dir, port, **env):
    """실제 웹앱(lawbot.app.main)을 127.0.0.1:<port>에 띄우고 로그인 화면이 뜰 때까지 기다린다"""
    web = Proc(
        'webapp',
        [sys.executable, '-c', 'from lawbot.app import main; main()'],
        clean_env(
            **(env | {'LAWBOT_USER': USER, 'LAWBOT_PW': PW, 'LAWBOT_PORT': str(port)})
        ),  # 테스트 로그인·포트가 .env 값보다 우선
        log_dir,
    )
    web.wait_http(f'http://127.0.0.1:{port}/', lambda r: r.status_code == 200)
    return web


def request_log(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ---- 화면 조작 (Gradio 6.30: 0단계 확인 결과, docs/archives/e2e-playwright.md 5절) ----
def textbox(page):
    """질문 입력창. 로그인 화면의 사용자명 칸도 data-testid=textbox(input)라서 textarea로 좁힌다"""
    return page.locator('textarea[data-testid=textbox]')


def login(page, url, user=USER, pw=PW):
    page.goto(url)
    page.locator('input[type=password]').wait_for()
    page.locator('input[type=text]').first.fill(user)
    page.locator('input[type=password]').fill(pw)
    page.get_by_role('button', name='Login').click()


def ask(page, question):
    textbox(page).fill(question)
    textbox(page).press('Enter')


def answer(page):
    return page.get_by_test_id('bot').last


def stats(page):
    return page.locator('#lawbot-stats')


def sources(page):
    return page.locator('#lawbot-sources')


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
