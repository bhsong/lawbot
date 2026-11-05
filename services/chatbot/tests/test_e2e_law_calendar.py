"""Exercise calendar UI and production fixture exclusion without any real model calls."""

import shutil

import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from conftest import free_port

pytestmark = pytest.mark.e2e_stub
FRONTEND = e2e.ROOT / 'services' / 'chatbot' / 'frontend'


@pytest.fixture(scope='module')
def development(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('law_calendar_dev')
    port = free_port()
    npm = shutil.which('npm')
    assert npm, 'npm is required for the existing Vite development server'
    proc = e2e.Proc(
        'calendar-vite',
        [npm, '--prefix', str(FRONTEND), 'run', 'dev', '--', '--port', str(port), '--strictPort'],
        e2e.clean_env(),
        tmp,
    )
    url = f'http://127.0.0.1:{port}'
    try:
        proc.wait_http(url + '/law-calendar', lambda response: response.status_code == 200)
        yield url, proc
    finally:
        proc.stop()


@pytest.fixture(scope='module')
def production(tmp_path_factory):
    stack = e2e.Stack(tmp_path_factory.mktemp('law_calendar_prod'), read_timeout=10)
    yield stack
    stack.stop()


@pytest.fixture
def demo(development, page, request):
    url, proc = development
    page.goto(url + '/law-calendar')
    page.get_by_text('예시 / 실제 법령 아님', exact=True).wait_for()
    yield page
    e2e.keep_on_failure(request, 'e2e_stub', [proc.log], page)


def test_production_calendar_is_public_but_chat_remains_private(production, page, request):
    calls = []
    page.on('request', lambda req: calls.append(req.url) if '/api/' in req.url else None)
    try:
        page.goto(production.url + '/law-calendar?demo=true')
        expect(page.get_by_role('heading', name='법령 변경 캘린더', exact=True)).to_be_visible()
        expect(page.get_by_text('승인 데이터 연결 전', exact=False)).to_be_visible()
        assert calls == [], calls
        assert page.get_by_text('예시 / 실제 법령 아님', exact=True).count() == 0
        assets = list((FRONTEND / 'dist' / 'assets').glob('*.js'))
        assert assets
        for asset in assets:
            text = asset.read_text(encoding='utf-8')
            assert 'LAW_CALENDAR_SYNTHETIC_ONLY' not in text, asset.name
            assert '가상 업무절차법' not in text, asset.name
        page.get_by_role('link', name='Lawbot 챗봇').click()
        expect(page.get_by_label('비밀번호')).to_be_visible()
        e2e.login(page, production.url)
        e2e.ask(page, '해고 예고는 언제 하나요?')
        e2e.wait_done(page)
        expect(e2e.answer(page)).to_contain_text('가짜 모델 답변입니다.')
    finally:
        e2e.keep_on_failure(request, 'e2e_stub', production.logs(), page)


@pytest.mark.parametrize('width', [390, 1280])
def test_date_selection_and_dialog_focus(demo, width):
    demo.set_viewport_size({'width': width, 'height': 844})
    current = demo.get_by_role('button', pressed=True)
    current.focus()
    demo.keyboard.press('ArrowRight')
    expect(demo.get_by_text('이 날짜에 게시된 입법예고가 없습니다.')).to_be_visible()
    expect(demo.get_by_text('이 날짜에 시행되는 조문이 없습니다.')).to_be_visible()
    demo.get_by_role('button', name='오늘', exact=True).click()
    notice = demo.get_by_role('region', name='입법예고 10건')
    effective = demo.get_by_role('region', name='시행 조문 4건')
    if width == 390:
        assert effective.bounding_box()['y'] > notice.bounding_box()['y']
    else:
        assert effective.bounding_box()['x'] > notice.bounding_box()['x']
    opener = notice.get_by_role('button').first
    opener.click()
    dialog = demo.get_by_role('dialog')
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role('button', name='상세 닫기')).to_be_focused()
    demo.keyboard.press('Shift+Tab')
    assert dialog.evaluate('(el) => el.contains(document.activeElement)')
    demo.keyboard.press('Tab')
    expect(dialog.get_by_role('button', name='상세 닫기')).to_be_focused()
    before = dialog.get_by_role('heading', name='Before', exact=False).bounding_box()
    after = dialog.get_by_role('heading', name='After', exact=False).bounding_box()
    if width == 390:
        assert after['y'] > before['y']
    else:
        assert after['x'] > before['x']
    assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
    demo.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(opener).to_be_focused()
    expect(demo.get_by_role('button', name='입법예고 더 보기', exact=False)).to_be_visible()
    demo.get_by_role('button', name='입법예고 더 보기', exact=False).click()
    assert notice.get_by_role('button').count() == 10
    effective.get_by_role('button', name='비교 원문 확인', exact=False).click()
    expect(demo.get_by_role('dialog').get_by_text('비교 불가', exact=False)).to_be_visible()
    demo.get_by_role('button', name='상세 닫기').click()
    assert demo.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_preview_loading_error_and_delay_states(demo):
    demo.get_by_text('개발용 예시 화면 설정', exact=True).click()
    scenario = demo.get_by_label('화면 상태')
    scenario.select_option('loading')
    expect(demo.get_by_role('status')).to_contain_text('불러오는 중')
    scenario.select_option('error')
    expect(demo.get_by_role('alert')).to_contain_text('불러오지 못했습니다')
    demo.get_by_role('button', name='다시 시도').click()
    expect(demo.get_by_role('region', name='입법예고 10건')).to_be_visible()
    scenario.select_option('delayed')
    expect(demo.get_by_role('status').filter(has_text='갱신이 지연')).to_be_visible()
