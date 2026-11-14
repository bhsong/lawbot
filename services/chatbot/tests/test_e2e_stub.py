# tests/test_e2e_stub.py  →  브라우저 E2E (대역): Playwright → 실제 웹앱(lawbot-app, 빌드한 화면) → 실제 모델 서버 앱 + 가짜 모델
# 세 추론 호출(임베딩·리랭크·chat) 모두 가짜. 이 결과를 "실제 모델 E2E 통과"로 보고하지 않는다
# 화면 요소는 역할·라벨·테스트 id로 찾는다 (e2e_support). 접속은 127.0.0.1만
import json
import threading
import time

import httpx
import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from admin_test_data import add_feedback, seed_qa
from conftest import ADMIN_PW, ADMIN_USER
from lawbot.db import cli
from lawbot.db.connection import connect
from lawbot.deploy import article_paths
from lawbot.view import V5_EXAMPLES
from test_migrate_csv_to_sqlite import run as migrate

pytestmark = pytest.mark.e2e_stub

QUESTION = '해고 예고 없이 갑자기 해고되면 어떻게 하나요?'
FAKE_ANSWER = '가짜 모델 답변입니다.'  # e2e_model_server.answer_chunks
READ_TIMEOUT_S = 2  # 웹앱 LAWBOT_MODEL_READ_TIMEOUT (slow 모드의 토큰 간격 5초보다 짧게)
PHONE = {'viewport': {'width': 390, 'height': 844}, 'is_mobile': True, 'has_touch': True}


@pytest.fixture(scope='module')
def feedback_db(tmp_path_factory):
    path = tmp_path_factory.mktemp('e2e_feedback') / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(path)])
    return path


@pytest.fixture(scope='module')
def stack(tmp_path_factory, feedback_db):
    s = e2e.Stack(
        tmp_path_factory.mktemp('e2e_stub'),
        read_timeout=READ_TIMEOUT_S,
        LAWBOT_FEEDBACK='1',
        LAWBOT_DB=str(feedback_db),
    )
    yield s
    s.stop()


def alert(page, text, timeout=e2e.ANSWER_TIMEOUT):
    e2e.answer(page).get_by_role('alert').get_by_text(text).wait_for(timeout=timeout)


@pytest.fixture
def ui(stack, page, request):
    """정상 모드 모델 서버 + 로그인한 화면. 실패하면 진단 산출물을 results/e2e_stub/에 남긴다"""
    stack.ensure('normal')
    e2e.login(page, stack.url)
    e2e.textbox(page).wait_for()
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


@pytest.fixture
def fresh(stack, page, request):
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)


def new_log_rows(stack, before):
    return e2e.request_log(stack.request_log)[before:]


def test_feedback_button_saves_turn(stack, ui, feedback_db):
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    expect(e2e.answer(ui)).to_contain_text(FAKE_ANSWER)
    like = ui.get_by_role('button', name='좋아요')
    read_feedback = e2e.capture_response_body(ui, '/api/feedback')
    with ui.expect_response(f'{stack.url}/api/feedback', timeout=e2e.ANSWER_TIMEOUT) as response:
        like.click()
    assert response.value.ok and json.loads(read_feedback()) == {'saved': True}
    expect(like).to_have_attribute('aria-pressed', 'true')

    with connect(feedback_db) as conn:
        rows = conn.execute('SELECT record, origin FROM feedbacks').fetchall()
    assert len(rows) == 1
    raw, origin = rows[0]
    record = json.loads(raw)
    assert origin == 'web' and record['question'] == QUESTION and record['label'] == 'like'
    assert FAKE_ANSWER in record['answer_shown']
    assert record['request_id'] == e2e.request_log(stack.request_log)[-1]['request_id']


# ---- 로그인 ----
def test_login_required(stack, fresh):
    fresh.goto(stack.url)
    fresh.get_by_label('비밀번호').wait_for()
    assert e2e.textbox(fresh).count() == 0  # 로그인 전에는 질문 입력창이 없다
    e2e.login(fresh, stack.url, pw='wrong-pw')
    fresh.get_by_role('alert').wait_for()
    assert e2e.textbox(fresh).count() == 0
    e2e.login(fresh, stack.url)
    e2e.textbox(fresh).wait_for()


@pytest.fixture
def admin_stack(tmp_path, page, request, admin_hash):
    db = tmp_path / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(db)])
    seed_qa(db)
    s = e2e.Stack(
        tmp_path,
        read_timeout=READ_TIMEOUT_S,
        LAWBOT_DB=str(db),
        LAWBOT_ADMIN_USER=ADMIN_USER,
        LAWBOT_ADMIN_PW_HASH=admin_hash,
    )
    try:
        yield s, db
        e2e.keep_on_failure(request, 'e2e_stub', s.logs(), page)
    finally:
        s.stop()


def test_admin_can_log_in_on_existing_screen(admin_stack, page):
    s, db = admin_stack
    e2e.login(page, s.url, user=ADMIN_USER, pw=ADMIN_PW)
    e2e.textbox(page).wait_for(timeout=e2e.ANSWER_TIMEOUT)
    assert page.request.get(f'{s.url}/api/me', timeout=e2e.ANSWER_TIMEOUT).json() == {
        'user': ADMIN_USER,
        'role': 'admin',
    }
    with connect(db) as conn:
        assert conn.execute('SELECT actor, role, action FROM audit_events').fetchall() == [
            (ADMIN_USER, 'admin', 'login_ok')
        ]


@pytest.mark.parametrize('width', [390, 1280])
def test_admin_reviews_and_promotes_a_like(admin_stack, page, width):
    s, db = admin_stack
    add_feedback(db, 'admin-like')
    page.set_viewport_size({'width': width, 'height': 844})
    e2e.login(page, s.url, user=ADMIN_USER, pw=ADMIN_PW)
    page.get_by_role('radio', name='피드백 & 법령 관리').click(timeout=3000)
    calendar = page.get_by_role('region', name='법령 캘린더')
    likes = page.get_by_role('region', name='좋아요')
    flags = page.get_by_role('region', name='문제 신고')
    expect(calendar.get_by_text('캘린더 연결 안 됨')).to_be_visible(timeout=e2e.ANSWER_TIMEOUT)
    expect(likes.get_by_role('button', name='해고 예고가 필요한가요?')).to_be_visible()
    top, left, right = (panel.bounding_box() for panel in (calendar, likes, flags))
    assert top and left and right and top['y'] < left['y']
    if width == 390:
        assert left['y'] < right['y']
    else:
        assert left['y'] == right['y'] and left['x'] < right['x']
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')

    likes.get_by_role('button', name='해고 예고가 필요한가요?').click()
    dialog = page.get_by_role('dialog', name='좋아요 검수')
    expect(dialog.get_by_label('교정 질문')).to_be_focused()
    dialog.get_by_role('radio', name='정답', exact=True).check()
    dialog.get_by_label('근거 조문 검색').fill('근로기준법 제26조')
    dialog.get_by_role('button', name='근로기준법 제26조', exact=True).click()
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    review_path = '/api/admin/feedbacks/admin-like/review'
    read_review = e2e.capture_response_body(page, review_path)
    with page.expect_response(f'{s.url}{review_path}', timeout=e2e.ANSWER_TIMEOUT) as review:
        dialog.get_by_role('button', name='검수 저장', exact=True).click()
    assert review.value.status == 201 and json.loads(read_review())['status'] == 'reviewed'
    likes.get_by_label('상태').select_option('reviewed')
    likes.get_by_role('checkbox', name='해고 예고가 필요한가요? 선택').check()
    promotion_path = '/api/admin/feedbacks/promote-up'
    read_promotion = e2e.capture_response_body(page, promotion_path)
    with page.expect_response(f'{s.url}{promotion_path}', timeout=e2e.ANSWER_TIMEOUT) as promotion:
        likes.get_by_role('button', name='선택 승격 (1)').click()
    assert promotion.value.ok and json.loads(read_promotion())['results'][0]['ok']
    likes.get_by_label('상태').select_option('promoted')
    expect(likes.get_by_role('list', name='피드백 목록').get_by_text('승격됨', exact=True)).to_be_visible()

    page.context.clear_cookies()
    e2e.login(page, s.url)
    expect(e2e.textbox(page)).to_be_visible()
    expect(page.get_by_role('radio', name='피드백 & 법령 관리')).to_have_count(0)
    denied = page.request.get(f'{s.url}/api/admin/feedbacks', timeout=e2e.ANSWER_TIMEOUT)
    assert denied.status == 403 and denied.json()['code'] == 'admin_required'


# ---- 정상 질문 ----
def test_question_streams_answer(stack, ui):
    stack.ensure('stream')  # 토큰 사이 지연: 답이 조각으로 늘어나는지 보이게
    e2e.ask(ui, QUESTION)
    seen = []
    deadline = time.monotonic() + e2e.ANSWER_TIMEOUT / 1000
    while not ui.get_by_test_id('lawbot-stats').get_by_text('완료').count():
        assert time.monotonic() < deadline, f'답변이 끝나지 않음 (본 화면: {seen})'
        text = e2e.answer(ui).inner_text() if ui.get_by_test_id('answer').count() else ''
        if text and (not seen or seen[-1] != text):
            seen.append(text)
        ui.wait_for_timeout(50)
    assert FAKE_ANSWER in e2e.answer(ui).inner_text()
    assert any(FAKE_ANSWER not in t and t.strip() for t in seen)  # 끝나기 전 중간 상태가 화면에 보였음


def test_unverified_draft_is_replaced_at_completion(stack, ui):
    stack.ensure('stream')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, '합성 근거 오류 사례')
    answer = e2e.answer(ui)
    expect(answer.get_by_text('답변 초안', exact=True)).to_be_visible()
    e2e.wait_done(ui)
    expect(answer.get_by_role('status')).to_contain_text('근거')
    expect(answer).not_to_contain_text(FAKE_ANSWER)
    expect(answer.get_by_role('button', name='답변 복사')).to_have_count(0)
    expect(answer.get_by_role('button', name='좋아요')).to_have_count(0)
    expect(e2e.sources(ui).get_by_text('✅ 답변에서 인용한 조문')).to_have_count(0)
    assert new_log_rows(stack, before)[0]['answer_verdict'] == 'invalid_citation'


def test_sources_and_stats_shown(stack, ui):
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    st = e2e.stats(ui).inner_text()
    for part in ('Lawbot Pro', '검색', '리랭킹', '첫 토큰', '완료'):
        assert part in st, (part, st)
    src = e2e.sources(ui)
    assert src.get_by_text('✅ 답변에서 인용한 조문').count() == 1  # 가짜 답은 첫 번째 조문을 인용
    assert src.get_by_text('검색만 된 조문').count() == 1
    cards = [c.inner_text() for c in src.get_by_test_id('source-card').all()]
    law_cards = e2e.law_cards(ui)
    assert len(law_cards) == 3, cards  # 법률 조문 3개 (하위법령 카드는 〔시행령〕·〔시행규칙〕 표시)
    assert len(cards) > 3, cards  # 하위법령 카드도 있는 상태에서 법률 카드만 셈 (e2e_live와 같은 헬퍼)
    assert all('리랭커' in c for c in law_cards)


@pytest.fixture
def db_corpus_stack(tmp_path, page, request):
    db = tmp_path / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(db)])
    release = 'e2e-v5-release'
    law, _ = article_paths('v5')
    assert migrate(db, 'corpus', '--dir', law.parent, '--profile', 'v5', '--release-id', release, '--activate') == 0
    stack = e2e.Stack(
        tmp_path,
        read_timeout=READ_TIMEOUT_S,
        LAWBOT_CORPUS='db',
        LAWBOT_DB=str(db),
        LAWBOT_V5_ARTICLES=str(tmp_path / 'absent-law.csv'),
        LAWBOT_V5_DECREE_ARTICLES=str(tmp_path / 'absent-combined.csv'),
    )
    try:
        yield stack, release
        e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
    finally:
        stack.stop()


def test_db_corpus_question_shows_three_law_cards(db_corpus_stack, page):
    stack, release = db_corpus_stack
    e2e.login(page, stack.url)
    e2e.ask(page, QUESTION)
    e2e.wait_done(page)
    expect(e2e.answer(page)).to_contain_text(FAKE_ANSWER)
    assert len(e2e.law_cards(page)) == 3
    assert e2e.request_log(stack.request_log)[-1]['corpus_release_id'] == release


@pytest.mark.parametrize('question', V5_EXAMPLES)
def test_example_click(stack, ui, question):
    ui.get_by_role('button', name=question, exact=True).click()
    e2e.wait_done(ui)
    expect(ui.get_by_test_id('question').last).to_have_text(question)
    assert FAKE_ANSWER in e2e.answer(ui).inner_text()


def test_request_log_api_mode(stack, ui):
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    e2e.wait_done(ui)
    rows = new_log_rows(stack, before)
    assert len(rows) == 1, rows
    assert rows[0]['mode'] == 'api' and rows[0]['status'] == 'ok' and rows[0]['error_type'] is None
    assert QUESTION not in stack.request_log.read_text(encoding='utf-8')  # 질문 원문은 남기지 않음


@pytest.mark.parametrize('action', ['중지', '로그아웃'])
def test_leaving_a_running_answer_cancels_generation(stack, ui, action):
    stack.ensure('long')  # 결론 뒤 설명이 길다: 간결형은 그동안 화면 글이 바뀌지 않음
    before = len(e2e.request_log(stack.request_log))
    ended_before = sum(line.startswith('e2e chat end') for line in stack.ms.output().splitlines())
    e2e.ask(ui, QUESTION)
    e2e.answer(ui).get_by_text('가짜 모델').wait_for(timeout=e2e.ANSWER_TIMEOUT)
    ui.get_by_role('button', name=action).click()
    if action == '중지':
        e2e.answer(ui).get_by_text('답변을 중지했습니다').wait_for()
        expect(e2e.answer(ui).get_by_text('답변 초안', exact=True)).to_be_visible()
        expect(e2e.answer(ui).get_by_role('button', name='답변 복사')).to_have_count(0)
    else:
        expect(ui.get_by_role('button', name='로그인')).to_be_visible()
        assert ui.request.get(f'{stack.url}/api/me', timeout=e2e.ANSWER_TIMEOUT).status == 401
    deadline = time.monotonic() + 15
    while not new_log_rows(stack, before):
        assert time.monotonic() < deadline, '요청 로그가 남지 않음'
        ui.wait_for_timeout(100)
    assert [r['status'] for r in new_log_rows(stack, before)] == ['cancelled']
    deadline = time.monotonic() + 15  # 모델 서버는 다음 스텝에서 취소를 알아챈다
    while (
        len(ends := [line for line in stack.ms.output().splitlines() if line.startswith('e2e chat end')])
        <= ended_before
    ):
        assert time.monotonic() < deadline, f'모델 서버의 생성이 끝나지 않음\n{stack.ms.output()}'
        ui.wait_for_timeout(100)
    sent, total = map(int, ends[-1].split()[-1].split('/'))
    assert sent < total, ends  # 모델 서버도 끝까지 생성하지 않음 (끊김 → 모델 서버 취소)


# ---- 휴대폰 ----
def test_phone_sources_sheet(stack, browser, request):
    stack.ensure('normal')
    ctx = browser.new_context(**PHONE)
    page = ctx.new_page()
    try:
        e2e.login(page, stack.url)
        e2e.ask(page, QUESTION)
        e2e.wait_done(page)
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
    e2e.ask(ui, QUESTION)
    alert(ui, '모델 서버 응답이 없습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server_timeout']


def test_error_generation(stack, ui):
    stack.ensure('chat_error')
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
    alert(ui, '답변을 생성하지 못했습니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['generation']
    assert FAKE_ANSWER not in e2e.answer(ui).inner_text()


def test_error_server_down(stack, ui):
    stack.stop_model_server()
    before = len(e2e.request_log(stack.request_log))
    e2e.ask(ui, QUESTION)
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
    e2e.ask(ui, QUESTION)
    alert(ui, '모델 서버가 바쁩니다')
    assert [r['error_type'] for r in new_log_rows(stack, before)] == ['model_server_busy']
    stack.stop_model_server()
    for t in threads:
        t.join(timeout=10)


@pytest.fixture
def v5_ui(tmp_path, page, request):
    stack = e2e.Stack(tmp_path, read_timeout=READ_TIMEOUT_S, example='.env.dev.example')
    try:
        e2e.login(page, stack.url)
        yield page
    finally:
        try:
            e2e.keep_on_failure(request, 'e2e_stub', stack.logs(), page)
        finally:
            stack.stop()


def test_v5_default_and_full_answer_use_scoped_systems(v5_ui):
    page = v5_ui
    question = '연말정산에서 어떤 소득공제를 받을 수 있나요?'
    concise = page.get_by_role('radio', name='간결')
    assert concise.get_attribute('aria-checked') == 'true'
    e2e.ask(page, question)
    e2e.wait_done(page)
    assert '근로소득 세금, 4대보험' in e2e.answer(page).inner_text()
    assert '설명' not in e2e.answer(page).inner_text()
    assert len(e2e.law_cards(page)) == 3
    page.get_by_role('radio', name='전체 (4항목)').click()
    e2e.ask(page, question)
    expect(page.get_by_test_id('answer')).to_have_count(2)
    e2e.wait_done(page)
    answer = e2e.answer(page)
    expect(answer).to_contain_text('근로소득 세금, 4대보험')
    expect(answer).to_contain_text('가짜 모델의 전체 설명')
    expect(answer).to_contain_text('가짜 모델의 참고 사항')
