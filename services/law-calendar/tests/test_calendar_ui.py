"""Run the public calendar at its own origin with no Lawbot backend available."""

import datetime
import json
import subprocess
import sys

import pytest
from playwright.sync_api import expect

import calendar_ui_support as support
from test_calendar_api import dataset as dataset
from test_calendar_workflow import approval, import_one
from test_calendar_workflow import workflow as workflow

pytest_plugins = ['calendar_test_fixtures']
pytestmark = [pytest.mark.e2e_stub, pytest.mark.usefixtures('calendar_network')]
FRONTEND = support.FRONTEND


@pytest.fixture(scope='module')
def development(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('law_calendar_dev')
    with support.serve(tmp, development=True) as server:
        yield server


@pytest.fixture(scope='module')
def production(tmp_path_factory):
    with support.serve(tmp_path_factory.mktemp('law_calendar_prod'), development=False) as server:
        yield server


@pytest.fixture
def demo(development, calendar_page, request):
    url, log = development
    calendar_page.goto(url + '/')
    calendar_page.get_by_text('예시 / 실제 법령 아님', exact=True).wait_for()
    yield calendar_page
    support.keep_on_failure(request, log, calendar_page)


def test_standalone_production_needs_no_login_or_backend(production, calendar_page, request):
    page = calendar_page
    url, log = production
    page.route(
        '**/api/law-**',
        lambda route: route.fulfill(
            json=(
                {'collectedAt': None, 'verifiedAt': None, 'delayed': False}
                if 'status' in route.request.url
                else {'items': [], 'total': 0, 'page': 1}
                if 'law-events' in route.request.url
                else {'days': []}
            )
        ),
    )
    calls = []
    page.on('request', lambda req: calls.append(req.url) if '/api/' in req.url else None)
    try:
        response = page.goto(url + '/?demo=true')
        expect(page.get_by_role('heading', name='법령 변경 캘린더', exact=True)).to_be_visible()
        expect(page.get_by_text('이 달에 공개 승인된', exact=False)).to_be_visible()
        assert len(calls) == 3 and all('/api/law-' in url for url in calls)
        assert response is not None and response.status == 200
        assert 'set-cookie' not in response.headers
        assert page.context.cookies() == []
        assert page.get_by_role('link', name='Lawbot 챗봇', exact=False).count() == 0
        assert page.get_by_label('비밀번호').count() == 0
        assert page.get_by_text('예시 / 실제 법령 아님', exact=True).count() == 0
        assets = list((FRONTEND / 'dist' / 'assets').glob('*.js'))
        assert assets
        for asset in assets:
            text = asset.read_text(encoding='utf-8')
            assert 'LAW_CALENDAR_SYNTHETIC_ONLY' not in text, asset.name
            assert '가상 업무절차법' not in text, asset.name
            for private in ('/api/me', '/api/login', '/api/chat', 'lawbot_session'):
                assert private not in text, asset.name
    finally:
        support.keep_on_failure(request, log, page)


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


def test_database_to_public_calendar_and_comparison(dataset, tmp_path, calendar_page):
    page = calendar_page
    page.clock.install(time=datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=dataset) as (url, _):
        page.goto(url)
        expect(page.get_by_text('예시 요약').first).to_be_visible()
        expect(page.get_by_role('button', pressed=True)).to_contain_text('예고 1')
        page.get_by_role('region', name='시행 조문 1건').get_by_role('button').click()
        detail = page.get_by_role('dialog')
        expect(detail.get_by_text('원문 이전', exact=True)).to_be_visible()
        expect(detail.get_by_role('link', name='공식 원문 확인', exact=False)).to_have_attribute(
            'href', 'https://www.law.go.kr/법령/예시'
        )
        page.get_by_role('button', name='상세 닫기').click()
        page.get_by_role('button', pressed=True).focus()
        page.keyboard.press('ArrowRight')
        expect(page.get_by_role('region', name='시행 조문 1건')).to_be_visible()


def test_review_cli_controls_public_browser_visibility(workflow, tmp_path, calendar_page):
    db = workflow[0]
    event_id = import_one(workflow)
    manifest = tmp_path / 'review.json'
    payload = approval(db, event_id)
    page = calendar_page
    page.clock.install(time=datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=db) as (url, _):
        page.goto(url)
        expect(page.get_by_text('이 달에 공개 승인된', exact=False)).to_be_visible()
        for decision in ('approve', 'withdraw'):
            payload['decision'] = decision
            manifest.write_text(json.dumps(payload), encoding='utf-8')
            subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'law_calendar.manage',
                    '--database',
                    str(db),
                    'review',
                    str(manifest),
                    '--reviewer',
                    'test-reviewer',
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            page.reload()
            if decision == 'approve':
                panel = page.get_by_role('region', name='시행 조문 2건')
                expect(panel).to_be_visible()
                panel.get_by_role('button').filter(has_text='제1조').click()
                dialog = page.get_by_role('dialog')
                expect(dialog).to_contain_text('이전 원문')
                expect(dialog).to_contain_text('이후 원문')
                page.get_by_role('button', name='상세 닫기').click()
            else:
                expect(page.get_by_text('이 달에 공개 승인된', exact=False)).to_be_visible()
