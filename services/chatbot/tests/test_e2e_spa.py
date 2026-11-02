# tests/test_e2e_spa.py  →  새 화면(SPA) 브라우저 E2E (대역): Playwright → lawbot-web(빌드한 SPA) → 실제 모델 서버 앱 + 가짜 모델
# 세 추론 호출(임베딩·리랭크·chat) 모두 가짜. 이 결과를 "실제 모델 E2E 통과"로 보고하지 않는다
# 시나리오는 test_e2e_stub.py(Gradio 화면)와 같다. 화면 요소는 역할·라벨·테스트 id로 찾는다. 접속은 127.0.0.1만
import threading
import time

import httpx
import pytest

import e2e_support as e2e
from lawbot.view import EXAMPLES

pytestmark = pytest.mark.e2e_stub

QUESTION = '해고 예고 없이 갑자기 해고되면 어떻게 하나요?'
FAKE_ANSWER = '가짜 모델 답변입니다.'  # e2e_model_server.answer_chunks
READ_TIMEOUT_S = 2  # 웹앱 LAWBOT_MODEL_READ_TIMEOUT (slow 모드의 토큰 간격 5초보다 짧게)
PHONE = {'viewport': {'width': 390, 'height': 844}, 'is_mobile': True, 'has_touch': True}


@pytest.fixture(scope='module')
def stack(tmp_path_factory):
    s = e2e.Stack(tmp_path_factory.mktemp('e2e_spa'), read_timeout=READ_TIMEOUT_S, start=e2e.start_web)
    yield s
    s.stop()


def login(page, url, user=e2e.USER, pw=e2e.PW):
    page.goto(url)
    page.get_by_label('사용자명').fill(user)
    page.get_by_label('비밀번호').fill(pw)
    page.get_by_role('button', name='로그인').click()


def textbox(page):
    return page.get_by_role('textbox', name='질문')


def ask(page, question):
    textbox(page).fill(question)
    textbox(page).press('Enter')


def answer(page):
    return page.get_by_test_id('answer').last


def stats(page):
    return page.get_by_test_id('lawbot-stats').last


def sources(page):
    return page.get_by_test_id('lawbot-sources').first


def wait_done(page, timeout=e2e.ANSWER_TIMEOUT):
    stats(page).get_by_text('완료').wait_for(timeout=timeout)


def alert(page, text, timeout=e2e.ANSWER_TIMEOUT):
    answer(page).get_by_role('alert').get_by_text(text).wait_for(timeout=timeout)


@pytest.fixture
def ui(stack, page, request):
    """정상 모드 모델 서버 + 로그인한 화면. 실패하면 진단 산출물을 results/e2e_stub/에 남긴다"""
    stack.ensure('normal')
    login(page, stack.url)
    textbox(page).wait_for()
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


@pytest.fixture
def fresh(stack, page, request):
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


def new_log_rows(stack, before):
    return e2e.request_log(stack.request_log)[before:]


# ---- 로그인 ----
def test_login_required(stack, fresh):
    fresh.goto(stack.url)
    fresh.get_by_label('비밀번호').wait_for()
    assert textbox(fresh).count() == 0  # 로그인 전에는 질문 입력창이 없다
    login(fresh, stack.url, pw='wrong-pw')
    fresh.get_by_role('alert').wait_for()
    assert textbox(fresh).count() == 0
    login(fresh, stack.url)
    textbox(fresh).wait_for()


# ---- 정상 질문 ----
def test_question_streams_answer(stack, ui):
    stack.ensure('stream')  # 토큰 사이 지연: 답이 조각으로 늘어나는지 보이게
    ask(ui, QUESTION)
    seen = []
    deadline = time.monotonic() + e2e.ANSWER_TIMEOUT / 1000
    while not ui.get_by_test_id('lawbot-stats').get_by_text('완료').count():
        assert time.monotonic() < deadline, f'답변이 끝나지 않음 (본 화면: {seen})'
        text = answer(ui).inner_text() if ui.get_by_test_id('answer').count() else ''
        if text and (not seen or seen[-1] != text):
            seen.append(text)
        ui.wait_for_timeout(50)
    assert FAKE_ANSWER in answer(ui).inner_text()
    assert any(FAKE_ANSWER not in t and t.strip() for t in seen)  # 끝나기 전 중간 상태가 화면에 보였음


def test_sources_and_stats_shown(stack, ui):
    ask(ui, QUESTION)
    wait_done(ui)
    st = stats(ui).inner_text()
    for part in ('Lawbot Pro', '검색', '리랭킹', '첫 토큰', '완료'):
        assert part in st, (part, st)
    src = sources(ui)
    assert src.get_by_text('✅ 답변에서 인용한 조문').count() == 1  # 가짜 답은 첫 번째 조문을 인용
    assert src.get_by_text('검색만 된 조문').count() == 1
    cards = [c.inner_text() for c in src.get_by_test_id('source-card').all()]
    law_cards = [c for c in cards if '〔' not in c]
    assert len(law_cards) == 3, cards  # 법률 조문 3개 (하위법령 카드는 〔시행령〕·〔시행규칙〕 표시)
    assert all('리랭커' in c for c in law_cards)


def test_example_click(stack, ui):
    ui.get_by_role('button', name=EXAMPLES[0]).click()
    wait_done(ui)
    assert FAKE_ANSWER in answer(ui).inner_text()


def test_request_log_api_mode(stack, ui):
    before = len(e2e.request_log(stack.request_log))
    ask(ui, QUESTION)
    wait_done(ui)
    rows = new_log_rows(stack, before)
    assert len(rows) == 1, rows
    assert rows[0]['mode'] == 'api' and rows[0]['status'] == 'ok' and rows[0]['error_type'] is None
    assert QUESTION not in stack.request_log.read_text(encoding='utf-8')  # 질문 원문은 남기지 않음


def test_stop_button(stack, ui):
    stack.ensure('stream')
    before = len(e2e.request_log(stack.request_log))
    ask(ui, QUESTION)
    answer(ui).get_by_text('가짜 모델').wait_for(timeout=e2e.ANSWER_TIMEOUT)
    ui.get_by_role('button', name='중지').click()
    answer(ui).get_by_text('답변을 중지했습니다').wait_for()
    deadline = time.monotonic() + 15
    while not new_log_rows(stack, before):
        assert time.monotonic() < deadline, '요청 로그가 남지 않음'
        ui.wait_for_timeout(100)
    assert [r['status'] for r in new_log_rows(stack, before)] == ['cancelled']


# ---- 휴대폰 ----
def test_phone_sources_sheet(stack, browser, request):
    stack.ensure('normal')
    ctx = browser.new_context(**PHONE)
    page = ctx.new_page()
    try:
        login(page, stack.url)
        ask(page, QUESTION)
        wait_done(page)
        assert not page.get_by_role('complementary', name='근거 조문').is_visible()  # 옆 패널은 숨김
        page.get_by_role('button', name='근거 조문').last.click()
        sheet = page.get_by_role('dialog', name='근거 조문')
        sheet.get_by_text('✅ 답변에서 인용한 조문').wait_for()
        sheet.get_by_role('button', name='닫기').click()
        sheet.wait_for(state='detached')
    finally:
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
        ctx.close()


# ---- 오류 안내 ----
def test_error_timeout(stack, ui):
    stack.ensure('slow')
    before = len(e2e.request_log(stack.request_log))
    ask(ui, QUESTION)
    alert(ui, '모델 서버 응답이 없습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server_timeout']


def test_error_generation(stack, ui):
    stack.ensure('chat_error')
    before = len(e2e.request_log(stack.request_log))
    ask(ui, QUESTION)
    alert(ui, '답변을 생성하지 못했습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['generation']
    assert FAKE_ANSWER not in answer(ui).inner_text()


def test_error_server_down(stack, ui):
    stack.stop_model_server()
    before = len(e2e.request_log(stack.request_log))
    ask(ui, QUESTION)
    alert(ui, '모델 서버가 준비되지 않았습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server']


def test_error_busy(stack, ui):
    """동시 생성 1·대기 1: 첫 요청이 생성을 시작한 뒤 둘째가 대기 자리를 채우면, 화면 질문은 503 server_busy"""
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
    assert codes == [200], codes
    before = len(e2e.request_log(stack.request_log))
    ask(ui, QUESTION)
    alert(ui, '모델 서버가 바쁩니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server_busy']
    stack.stop_model_server()
    for t in threads:
        t.join(timeout=10)
