# tests/test_e2e_stub.py  →  브라우저 E2E (대역): Playwright → 실제 웹앱 프로세스 → 실제 모델 서버 앱 + 가짜 모델
# 세 추론 호출(임베딩·리랭크·chat) 모두 가짜. 이 결과를 "실제 모델 E2E 통과"로 보고하지 않는다 (docs/archives/e2e-playwright.md)
# 웹앱 설정은 .env.example(운영 구성: Pro, 리랭커, 시행령·시행규칙 첨부)과 같다. 접속은 127.0.0.1만
import sys
import threading
import time

import httpx
import pytest

import e2e_support as e2e
from conftest import TEST_TOKEN, free_port
from lawbot.app import EXAMPLES

pytestmark = pytest.mark.e2e_stub

QUESTION = '해고 예고 없이 갑자기 해고되면 어떻게 하나요?'
FAKE_ANSWER = '가짜 모델 답변입니다.'  # e2e_model_server.answer_chunks
READ_TIMEOUT_S = 2  # 웹앱 LAWBOT_MODEL_READ_TIMEOUT (slow 모드의 토큰 간격 5초보다 짧게)


class Stack:
    """웹앱 1개 + 가짜 모델 서버 1개. 모델 서버는 같은 포트에서 모드만 바꿔 다시 띄운다 (웹앱은 그대로)"""

    def __init__(self, tmp):
        self.tmp, self.ms, self.mode, self.n = tmp, None, None, 0
        self.token = TEST_TOKEN  # 모델 서버에는 환경 변수로 넘김 (e2e_model_server를 이 프로세스에서 import하지 않음)
        self.ms_port, self.web_port = free_port(), free_port()
        self.url = f'http://127.0.0.1:{self.web_port}'
        self.ms_url = f'http://127.0.0.1:{self.ms_port}'
        self.request_log = tmp / 'requests.jsonl'
        self.serve = {k: v for k, v in e2e.example_env().items() if k.startswith('LAWBOT_SERVE_')}
        self.ensure('normal')
        web_env = {k: v for k, v in e2e.example_env().items() if not k.startswith('LAWBOT_SERVE_')}
        web_env.update(
            LAWBOT_MODEL_SERVER=self.ms_url,
            LAWBOT_MODEL_TOKEN=self.token,
            LAWBOT_EMBED_CACHE='0',
            LAWBOT_REQUEST_LOG=str(self.request_log),
            LAWBOT_FEEDBACK='0',
            LAWBOT_MODEL_READ_TIMEOUT=str(READ_TIMEOUT_S),
            LAWBOT_MODEL_SERVER_WAIT='30',
        )
        self.web = e2e.start_webapp(tmp, self.web_port, **web_env)

    def ensure(self, mode):
        """모델 서버가 mode로 떠 있게 한다 (이미 그 모드로 살아 있으면 그대로)"""
        if self.ms is not None and self.ms.alive() and self.mode == mode:
            return
        self.stop_model_server()
        self.n += 1
        self.ms = e2e.Proc(
            f'model-server-{self.n}-{mode}',
            [sys.executable, str(e2e.TESTS / 'e2e_model_server.py'), str(self.ms_port), mode],
            e2e.clean_env(LAWBOT_MODEL_TOKEN=self.token, **self.serve),
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


@pytest.fixture(scope='module')
def stack(tmp_path_factory):
    s = Stack(tmp_path_factory.mktemp('e2e_stub'))
    yield s
    s.stop()


@pytest.fixture
def ui(stack, page, request):
    """정상 모드 모델 서버 + 로그인한 화면. 실패하면 진단 산출물을 results/e2e_stub/에 남긴다"""
    stack.ensure('normal')
    e2e.login(page, stack.url)
    e2e.textbox(page).wait_for()
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


def new_log_rows(stack, before):
    return e2e.request_log(stack.request_log)[before:]


@pytest.fixture
def fresh(stack, page, request):
    """로그인하지 않은 화면. 실패하면 진단 산출물을 남긴다"""
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


# ---- 로그인 ----
def test_login_required(stack, fresh):
    fresh.goto(stack.url)
    fresh.locator('input[type=password]').wait_for()
    assert e2e.textbox(fresh).count() == 0  # 로그인 전에는 질문 입력창이 없다
    e2e.login(fresh, stack.url, pw='wrong-pw')
    fresh.wait_for_timeout(1000)
    assert e2e.textbox(fresh).count() == 0
    e2e.login(fresh, stack.url)
    e2e.textbox(fresh).wait_for()


# ---- 정상 질문 ----
def test_question_streams_answer(stack, ui):
    stack.ensure('stream')  # 토큰 사이 지연: 답이 조각으로 늘어나는지 보이게
    e2e.ask(ui, QUESTION)
    seen = []
    deadline = time.monotonic() + e2e.ANSWER_TIMEOUT / 1000
    while not e2e.stats(ui).get_by_text('완료').count():
        assert time.monotonic() < deadline, f'답변이 끝나지 않음 (본 화면: {seen})'
        text = e2e.answer(ui).inner_text() if e2e.answer(ui).count() else ''
        if text and (not seen or seen[-1] != text):
            seen.append(text)
        ui.wait_for_timeout(50)
    final = e2e.answer(ui).inner_text()
    assert FAKE_ANSWER in final
    assert any(FAKE_ANSWER not in t and t.strip() for t in seen)  # 끝나기 전 중간 상태가 화면에 보였음


def test_sources_and_stats_shown(stack, ui):
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    st = e2e.stats(ui).inner_text()
    for part in ('Lawbot Pro', '검색', '리랭킹', '첫 토큰', '완료'):
        assert part in st, (part, st)
    src = e2e.sources(ui).inner_text()
    assert '✅ 답변에서 인용한 조문' in src  # 가짜 답은 첫 번째 조문을 인용
    assert '검색만 된 조문' in src
    law_cards = [line for line in src.splitlines() if '유사도' in line and '〔' not in line]
    assert len(law_cards) == 3, src  # 법률 조문 3개 (하위법령 카드는 〔시행령〕·〔시행규칙〕 표시)
    assert all('리랭커' in line for line in law_cards)


def test_example_click(stack, ui):
    ui.get_by_role('button', name=f'Select example 1: {EXAMPLES[0]}').click()
    e2e.wait_done(ui)
    assert FAKE_ANSWER in e2e.answer(ui).inner_text()


def test_request_log_api_mode(stack, ui):
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    rows = new_log_rows(stack, before)
    assert len(rows) == 1, rows
    assert rows[0]['mode'] == 'api' and rows[0]['status'] == 'ok' and rows[0]['error_type'] is None
    assert QUESTION not in stack.request_log.read_text(encoding='utf-8')  # 질문 원문은 남기지 않음


# ---- 오류 안내 ----
def error_toast(page, text, timeout=e2e.ANSWER_TIMEOUT):
    """gr.Error 안내 (화면 위 알림)"""
    page.get_by_text(text).first.wait_for(timeout=timeout)


def test_error_timeout(stack, ui):
    stack.ensure('slow')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    error_toast(ui, '모델 서버 응답이 없습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server_timeout']


def test_error_generation(stack, ui):
    """생성 중 오류: 안내 문구가 뜨고 가짜 답을 보이지 않는다 (예전에는 안내 없이 'Error' 표시만: 2026-11-02 발견·수정)"""
    stack.ensure('chat_error')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    error_toast(ui, '답변을 생성하지 못했습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['generation']
    assert FAKE_ANSWER not in e2e.answer(ui).inner_text()


def test_error_server_down(stack, ui):
    stack.stop_model_server()
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    error_toast(ui, '모델 서버가 준비되지 않았습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server']


def test_error_busy(stack, ui):
    """동시 생성 1·대기 1: 첫 요청이 생성을 시작한 뒤 둘째가 대기 자리를 채우면, 브라우저 질문은 503 server_busy
    (둘을 동시에 보내면 첫 요청이 생성을 시작하기 전에 둘째가 503을 받아 자리가 비는 경쟁이 있음)"""
    stack.ensure('busy')
    body = {'model': stack.serve['LAWBOT_SERVE_CHAT'].split('=')[0], 'messages': [{'role': 'user', 'content': 'x'}]}
    codes, generating = [], threading.Event()

    def occupy():
        try:
            with httpx.stream(
                'POST',
                stack.ms_url + '/v1/chat/completions',
                json=body | {'stream': True},
                headers={'Authorization': f'Bearer {stack.token}'},
                timeout=60,
            ) as r:
                codes.append(r.status_code)
                for _ in r.iter_bytes():
                    generating.set()
        except httpx.TransportError:  # 테스트 끝에 모델 서버를 내려 끊는 것은 예상한 동작
            pass

    threads = [threading.Thread(target=occupy, daemon=True) for _ in range(2)]
    threads[0].start()
    assert generating.wait(10), '첫 요청이 생성을 시작하지 않음'
    threads[1].start()
    ui.wait_for_timeout(1000)  # 둘째 요청이 대기 자리를 잡을 때까지
    assert codes == [200], codes  # 둘째는 아직 대기 중 (응답 헤더 전)
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    error_toast(ui, '모델 서버가 바쁩니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server_busy']
    stack.stop_model_server()  # 자리를 채운 요청을 끝낸다 (다음 테스트는 normal로 다시 띄움)
    for t in threads:
        t.join(timeout=10)
