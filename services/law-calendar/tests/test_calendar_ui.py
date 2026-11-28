"""Run the public calendar at its own origin with no Lawbot backend available."""

import datetime
import json
import sqlite3
import subprocess
import sys

import pytest
from playwright.sync_api import expect

import calendar_ui_support as support
from calendar_publish_fixtures import offline_calendar as offline_calendar
from calendar_publish_fixtures import review_environment
from law_calendar.db import migrate
from lawbot_common.erp_daily import replace_file, seal
from lawbot_common.tax_schedule import NTS_HOST, year_path
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
    tmp = tmp_path_factory.mktemp('law_calendar_prod')
    db = tmp / 'calendar.db'
    migrate(db)
    with support.serve(tmp, development=False, database=db) as server:
        yield server


@pytest.fixture
def demo(development, calendar_page, request):
    url, log = development
    calendar_page.goto(url + '/')
    calendar_page.get_by_text('예시 / 실제 법령 아님', exact=True).wait_for()
    yield calendar_page
    support.keep_on_failure(request, log, calendar_page)


@pytest.mark.parametrize('width', [390, 1280])
def test_tax_schedule_is_visible_and_keyboard_accessible(dataset, tmp_path, calendar_page, width):
    feed = tmp_path / 'tax-snapshot.json'
    official = NTS_HOST + year_path(2026)
    stamp = '2026-11-14T06:30:00+09:00'
    replace_file(
        feed,
        seal(
            {
                'schemaVersion': 1,
                'dayKst': '2026-11-14',
                'cutoffAt': stamp,
                'runId': 'daily-fixture',
                'configVersion': 0,
                'configHash': '0' * 64,
                'status': 'ok',
                'sources': [],
                'changes': [],
                'feeds': {
                    'taxSchedule': {
                        'confirmedAt': stamp,
                        'items': [
                            {
                                'upstream_id': 'a' * 32,
                                'title': '일용근로소득지급명세서 제출',
                                'deadline_on': '2026-11-30',
                                'target_period': '2026년 10월 지급분',
                                'canonical_url': official,
                            }
                        ],
                    }
                },
            }
        ),
    )
    page = calendar_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.clock.install(time=datetime.datetime(2026, 11, 29, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=dataset, daily_feed=feed) as (url, _):
        page.goto(url)
        day = page.get_by_role('button', name='2026년 11월 30일, 시행 0건, 세무일정 1건')
        expect(day.get_by_text('세무 1')).to_be_visible()
        page.get_by_role('button', pressed=True).focus()
        page.keyboard.press('ArrowRight')
        expect(day).to_be_focused()
        panel = page.get_by_role('region', name='세무일정 1건')
        expect(panel.get_by_text('2026년 10월 지급분', exact=False)).to_be_visible()
        expect(panel.get_by_role('link', name='국세청 원문', exact=False)).to_have_attribute('href', official)


def test_standalone_production_needs_no_login_or_backend(production, calendar_page, request):
    page = calendar_page
    url, log = production
    calls = []
    page.on('request', lambda req: calls.append(req.url))
    try:
        response = page.goto(url + '/?demo=true')
        expect(page.get_by_role('heading', name='법령 변경 캘린더', exact=True)).to_be_visible()
        expect(page.get_by_text('이 달에 공개 승인된', exact=False)).to_be_visible()
        assert any(path.endswith('/data/law-calendar/months.json') for path in calls)
        assert not any('/api/' in path for path in calls)
        assert response is not None and response.status == 200
        assert "script-src 'self'" in response.headers['content-security-policy']
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['referrer-policy'] == 'strict-origin-when-cross-origin'
        assert response.headers['permissions-policy'] == 'camera=(), microphone=(), geolocation=()'
        data = page.request.get(url + '/data/law-calendar/status.json')
        assert data.headers['cache-control'] == 'public, max-age=0, must-revalidate'
        script = page.locator('script[src]').get_attribute('src')
        assert script is not None
        asset = page.request.get(url + script)
        assert asset.headers['cache-control'] == 'public, max-age=31536000, immutable'
        license_path = page.get_by_role('link', name='라이선스').get_attribute('href')
        assert license_path is not None
        licenses = page.request.get(url + license_path).body()
        font_license = (FRONTEND / 'node_modules/pretendard/dist/LICENSE.txt').read_bytes()
        assert font_license in licenses
        for notice in (
            'SIL OPEN FONT LICENSE Version 1.1',
            '참고: Pretendard의 기반 글꼴',
            "with Reserved Font Name 'Source'.",
            "with Reserved Font Name 'Inter'.",
            "with Reserved Font Name 'M PLUS 1'.",
            'react (MIT License)',
            'react-dom (MIT License)',
            'scheduler (MIT License)',
            'legalize-kr#라이선스',
            'admrule-kr#라이선스',
        ):
            assert notice.encode() in licenses
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


@pytest.mark.parametrize('path', ['/data/law-changes/missing.json', '/api/law-calendar/status', '/docs'])
def test_unmatched_static_requests_return_the_404_page(production, calendar_page, path):
    url, _ = production
    target = url + path
    if path.startswith('/api/'):
        response = calendar_page.request.get(target)
        assert response.status == 404 and '페이지를 찾을 수 없습니다' in response.text()
        return
    response = calendar_page.goto(target)
    assert response is not None and response.status == 404
    expect(calendar_page.get_by_role('heading', name='페이지를 찾을 수 없습니다')).to_be_visible()


def test_development_data_mode_reads_a_local_export(dataset, tmp_path, calendar_page):
    site = support.export_calendar(tmp_path, dataset)
    page = calendar_page
    page.clock.set_fixed_time(datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=True, site=site) as (url, _):
        page.goto(url + '/?source=data')
        expect(page.get_by_text('예시 요약')).to_be_visible()
        expect(page.get_by_text('예시 / 실제 법령 아님', exact=True)).to_have_count(0)


@pytest.mark.parametrize('width', [390, 1280])
def test_date_selection_and_dialog_focus(demo, width):
    demo.set_viewport_size({'width': width, 'height': 844})
    current = demo.get_by_role('button', pressed=True)
    current.focus()
    demo.keyboard.press('ArrowRight')
    expect(demo.get_by_text('이 날짜에 시행되는 조문이 없습니다.')).to_be_visible()
    demo.get_by_role('button', name='오늘', exact=True).click()
    effective = demo.get_by_role('region', name='시행 조문 4건')
    opener = effective.get_by_role('button').first
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
    effective.get_by_role('button', name='비교 원문 확인', exact=False).click()
    expect(demo.get_by_role('dialog').get_by_text('비교 불가', exact=False)).to_be_visible()
    demo.get_by_role('button', name='상세 닫기').click()
    assert demo.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.fixture
def mobile_sheet_page(development, calendar_pages, request):
    url, log = development
    page = calendar_pages(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
    page.clock.set_fixed_time(datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    if not getattr(request, 'param', True):
        page.add_init_script("Object.defineProperty(window, 'navigation', {value: undefined})")
    page.goto(url + '/?fixture=calendar#calendar')
    expect(page.get_by_role('region', name='시행 조문 4건')).to_be_visible()
    yield page
    support.keep_on_failure(request, log, page)


@pytest.mark.parametrize('mobile_sheet_page', [True, False], indirect=True, ids=['navigation', 'history'])
def test_date_sheet_history_matches_the_visible_layers(mobile_sheet_page):
    page = mobile_sheet_page
    url = page.url
    before = page.evaluate('history.length')
    day = page.get_by_role('button', name='2026년 11월 5일, 시행 4건')
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    detail_close = page.get_by_role('button', name='상세 닫기')
    day.tap()
    expect(sheet).to_be_visible()
    expect(sheet.get_by_text('예시 / 실제 법령 아님', exact=True)).to_be_visible()
    assert page.evaluate('history.length') == before + 1
    sheet.get_by_role('button', name='가상 업무절차법 제1조', exact=False).tap()
    expect(detail_close).to_be_visible()
    assert page.evaluate('history.length') == before + 2
    assert page.url == url
    page.go_back()
    expect(detail_close).not_to_be_visible()
    expect(sheet).to_be_visible()
    page.go_back()
    expect(sheet).not_to_be_visible()
    expect(day).to_be_focused()
    page.go_forward()
    expect(sheet).to_be_visible()
    expect(detail_close).not_to_be_visible()
    page.go_forward()
    expect(detail_close).to_be_visible()
    assert page.url == url
    page.reload(timeout=5000)
    expect(detail_close).to_be_visible(timeout=3000)
    page.go_back(timeout=3000)
    expect(sheet).to_be_visible(timeout=3000)
    page.reload(timeout=5000)
    expect(sheet).to_be_visible(timeout=3000)
    page.go_forward(timeout=3000)
    expect(detail_close).to_be_visible(timeout=3000)
    assert page.evaluate('history.length') == before + 2
    page.go_back()
    expect(sheet).to_be_visible(timeout=3000)
    page.go_back(timeout=3000)
    expect(sheet).not_to_be_visible()
    assert page.url == url


@pytest.mark.parametrize('mobile_sheet_page', [True, False], indirect=True, ids=['navigation', 'history'])
def test_date_sheet_forward_from_erp_without_a_new_date_tap(mobile_sheet_page):
    page = mobile_sheet_page
    page.get_by_role('button', name='2026년 11월 5일, 시행 4건').tap()
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    sheet.get_by_role('button', name='가상 업무절차법 제1조', exact=False).tap()
    expect(page.get_by_role('button', name='상세 닫기')).to_be_visible()
    before = page.evaluate('history.length')
    page.go_back(timeout=3000)
    page.go_back(timeout=3000)
    expect(sheet).not_to_be_visible(timeout=3000)
    erp = page.get_by_role('tab', name='ERP 자료')
    erp.tap()
    expect(erp).to_have_attribute('aria-selected', 'true', timeout=3000)
    page.go_forward(timeout=3000)
    expect(sheet).to_be_visible(timeout=3000)
    page.go_forward(timeout=3000)
    expect(page.get_by_role('button', name='상세 닫기')).to_be_visible(timeout=3000)
    assert page.evaluate('history.length') == before


def test_date_sheet_skips_stale_date_in_both_history_directions(mobile_sheet_page):
    page = mobile_sheet_page
    base = page.url
    page.get_by_role('button', name='2026년 11월 5일, 시행 4건').tap()
    page.get_by_role('dialog').get_by_role('button', name='가상 업무절차법 제1조', exact=False).tap()
    expect(page.get_by_role('button', name='상세 닫기')).to_be_visible()
    # A same-document destination beyond the stale sheet and detail.
    page.evaluate("location.hash = 'after'")
    page.clock.set_fixed_time(datetime.datetime(2026, 11, 6, tzinfo=datetime.UTC))
    page.reload(timeout=5000)
    expect(page.get_by_role('heading', name='2026년 11월 6일')).to_be_visible(timeout=3000)
    page.go_back(timeout=3000)
    # The stale layers share the calendar URL; wait for the original entry before going forward.
    page.wait_for_function('(base) => location.href === base && !history.state?.lawCalendar', arg=base, timeout=3000)
    expect(page).to_have_url(base, timeout=3000)
    expect(page.get_by_role('dialog')).to_have_count(0, timeout=3000)
    page.go_forward(timeout=3000)
    expect(page).to_have_url(base.split('#')[0] + '#after', timeout=3000)
    expect(page.get_by_role('dialog')).to_have_count(0, timeout=3000)


@pytest.mark.parametrize(
    ('mobile_sheet_page', 'tail_hash'),
    [(False, 'erp'), (False, 'after')],
    indirect=['mobile_sheet_page'],
    ids=['erp', 'plain-hash'],
)
def test_date_sheet_back_skips_stale_layers_from_unpositioned_hash(mobile_sheet_page, tail_hash):
    page = mobile_sheet_page
    base = page.url
    page.get_by_role('button', name='2026년 11월 5일, 시행 4건').tap()
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    sheet.get_by_role('button', name='가상 업무절차법 제1조', exact=False).tap()
    expect(page.get_by_role('button', name='상세 닫기')).to_be_visible()

    page.evaluate('(hash) => { location.hash = hash }', tail_hash)
    page.clock.set_fixed_time(datetime.datetime(2026, 11, 6, tzinfo=datetime.UTC))
    page.reload(timeout=5000)
    tail = base.split('#')[0] + '#' + tail_hash
    expect(page).to_have_url(tail, timeout=3000)
    assert page.evaluate('window.navigation === undefined')

    visited = []
    page.on('framenavigated', lambda frame: visited.append(frame.url))
    page.go_back(timeout=3000)
    # The stale layers share the calendar URL; wait for the original entry itself.
    page.wait_for_function('(base) => location.href === base && !history.state?.lawCalendar', arg=base, timeout=3000)
    expect(page).to_have_url(base, timeout=3000)
    expect(page.get_by_role('heading', name='2026년 11월 6일', exact=True)).to_be_visible(timeout=3000)
    expect(page.get_by_role('dialog')).to_have_count(0, timeout=3000)
    assert tail not in visited


def test_date_sheet_close_inputs_and_inline_detail_back(mobile_sheet_page):
    page = mobile_sheet_page
    day = page.get_by_role('button', name='2026년 11월 5일, 시행 4건')
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    close = sheet.get_by_role('button', name='날짜 내역 닫기')
    day.tap()
    expect(close).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(sheet.get_by_role('button', name='가상 업무절차법 제4조', exact=False)).to_be_focused()
    page.keyboard.press('Tab')
    expect(close).to_be_focused()
    sheet.get_by_role('button', name='가상 업무절차법 제1조', exact=False).tap()
    page.get_by_role('button', name='상세 닫기').tap()
    expect(sheet).to_be_visible()
    page.touchscreen.tap(1, 1)
    expect(sheet).not_to_be_visible()
    expect(day).to_be_focused()
    day.tap()
    sheet.get_by_role('button', name='가상 업무절차법 제1조', exact=False).tap()
    page.keyboard.press('Escape')
    expect(sheet).to_be_visible()
    page.keyboard.press('Escape')
    expect(sheet).not_to_be_visible()
    day.tap()
    close.tap()
    expect(sheet).not_to_be_visible()
    page.get_by_role('button', name='이전 달').tap()
    page.get_by_role('button', name='다음 달').tap()
    page.get_by_role('button', name='오늘', exact=True).tap()
    expect(sheet).not_to_be_visible()
    opener = page.get_by_role('region', name='시행 조문 4건').get_by_role('button').first
    opener.tap()
    expect(page.get_by_role('button', name='상세 닫기')).to_be_visible()
    page.go_back()
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(opener).to_be_focused()


@pytest.fixture
def populated_sheet(dataset, tmp_path, calendar_pages, request):
    with sqlite3.connect(dataset) as connection:
        example = next(row for row in connection.execute('SELECT * FROM article_changes') if row[0] == 'effective-0')
        connection.executemany(
            'INSERT INTO article_changes VALUES (?,?,?,?,?,?,?,?,?,?)',
            [(f'fixture-{index}', *example[1:]) for index in range(16)],
        )
    site = support.export_calendar(tmp_path, dataset)
    page = calendar_pages(
        viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True, reduced_motion='reduce'
    )
    page.set_default_timeout(3000)
    page.clock.set_fixed_time(datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=True, site=site) as (url, log):
        page.goto(url + '/?source=data')
        expect(page.get_by_role('region', name='시행 조문 17건')).to_be_visible(timeout=3000)
        yield page
        support.keep_on_failure(request, log, page)


def test_date_sheet_scroll_lock(populated_sheet):
    page = populated_sheet
    day = page.get_by_role('button', name='2026년 11월 5일, 시행 17건')
    day.scroll_into_view_if_needed(timeout=3000)
    initial = page.evaluate('scrollY')
    assert page.evaluate('scrollY + innerHeight < document.documentElement.scrollHeight')
    page.mouse.move(1, 1)
    page.mouse.wheel(0, 300)
    page.wait_for_function('(before) => scrollY > before', arg=initial, timeout=3000)
    page.evaluate('(before) => scrollTo(0, before)', initial)
    page.wait_for_function('(before) => scrollY === before', arg=initial, timeout=3000)
    day.tap(timeout=3000)
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    expect(sheet).to_be_visible(timeout=3000)
    locked = page.evaluate('scrollY')
    assert locked == initial
    page.mouse.wheel(0, 300)
    # Observe a bounded interval after the wheel; this is not an iOS touch gesture.
    page.wait_for_timeout(250)
    assert page.evaluate('scrollY') == locked
    assert page.evaluate('document.documentElement.scrollWidth - innerWidth') == 0
    assert sheet.evaluate('(element) => element.scrollWidth - element.clientWidth', timeout=3000) == 0


@pytest.mark.parametrize('count', [8, 16])
def test_date_sheet_more_focuses_first_new_item(populated_sheet, count):
    page = populated_sheet
    page.get_by_role('button', name='2026년 11월 5일, 시행 17건').tap()
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    more = sheet.get_by_role('button', name='더 보기', exact=False)
    if count == 16:
        more.click()
    more.focus()
    page.keyboard.press('Enter')
    items = sheet.get_by_role('button', name='가상법 제1조', exact=False)
    expect(items).to_have_count(min(count + 8, 17), timeout=3000)
    expect(items.nth(count)).to_be_focused(timeout=3000)


def test_date_sheet_detail_focus_survives_history_round_trip(populated_sheet):
    page = populated_sheet
    page.get_by_role('button', name='2026년 11월 5일, 시행 17건').tap()
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    for _ in range(2):
        sheet.get_by_role('button', name='더 보기', exact=False).tap()
    opener = sheet.get_by_role('button', name='가상법 제1조', exact=False).nth(16)
    opener.scroll_into_view_if_needed()
    original_scroll = sheet.evaluate('(element) => element.scrollTop')
    assert original_scroll > 0
    opener.tap()
    detail = page.get_by_role('button', name='상세 닫기')
    expect(detail).to_be_visible()
    page.go_back(timeout=3000)
    expect(opener).to_be_focused(timeout=3000)
    assert sheet.evaluate('(element) => element.scrollTop') == original_scroll
    page.go_back(timeout=3000)
    expect(sheet).not_to_be_visible(timeout=3000)
    page.go_forward(timeout=3000)
    expect(sheet).to_be_visible(timeout=3000)
    page.go_forward(timeout=3000)
    expect(detail).to_be_visible(timeout=3000)
    page.go_back(timeout=3000)
    expect(opener).to_be_focused(timeout=3000)
    assert sheet.evaluate('(element) => element.scrollTop') == original_scroll


def test_date_sheet_delayed_detail_records_step_before_response(populated_sheet):
    page = populated_sheet
    held = []
    page.route('**/data/law-changes/*.json', lambda route: held.append(route))
    day = page.get_by_role('button', name='2026년 11월 5일, 시행 17건')
    day.tap()
    sheet = page.get_by_role('dialog', name='2026년 11월 5일')
    item = sheet.get_by_role('button', name='가상법 제1조', exact=False).first
    before = page.evaluate('history.length')
    with page.expect_request('**/data/law-changes/*.json', timeout=3000) as pending:
        item.tap()
    expect(sheet.get_by_role('status')).to_contain_text('상세 정보를 불러오는 중입니다.')
    assert page.evaluate('history.length') == before + 1
    with page.expect_event('requestfailed', predicate=lambda request: request == pending.value, timeout=3000):
        page.go_back(timeout=3000)
    expect(item).to_be_focused()
    expect(sheet.get_by_role('status')).to_have_count(0)

    with page.expect_request('**/data/law-changes/*.json', timeout=3000):
        page.go_forward(timeout=3000)
    expect(sheet.get_by_role('status')).to_contain_text('상세 정보를 불러오는 중입니다.')
    held[-1].fulfill(status=503)
    expect(sheet.get_by_role('alert')).to_contain_text('상세 정보를 확인하지 못했습니다.')
    expect(item).to_be_focused()
    page.go_back(timeout=3000)
    expect(sheet).not_to_be_visible()
    expect(day).to_be_focused()


def test_desktop_date_click_keeps_inline_results_without_random_uuid(development, calendar_pages):
    url, _ = development
    demo = calendar_pages(viewport={'width': 1280, 'height': 844})
    demo.add_init_script("Object.defineProperty(Crypto.prototype, 'randomUUID', {value: undefined})")
    demo.goto(url, timeout=5000)
    expect(demo.get_by_role('region', name='시행 조문 4건')).to_be_visible()
    day = demo.get_by_role('button', name=demo.get_by_role('button', pressed=True).get_attribute('aria-label'))
    day.focus()
    demo.keyboard.press('ArrowRight')
    day.click()
    expect(demo.get_by_role('dialog')).to_have_count(0)
    expect(demo.get_by_role('region', name='시행 조문 4건')).to_be_visible()
    assert demo.evaluate('document.documentElement.scrollWidth - innerWidth') == 0


def test_preview_loading_error_and_delay_states(demo):
    demo.get_by_text('개발용 예시 화면 설정', exact=True).click()
    scenario = demo.get_by_label('화면 상태')
    scenario.select_option('loading')
    expect(demo.get_by_role('status')).to_contain_text('불러오는 중')
    scenario.select_option('error')
    expect(demo.get_by_role('alert')).to_contain_text('불러오지 못했습니다')
    demo.get_by_role('button', name='다시 시도').click()
    expect(demo.get_by_role('region', name='시행 조문 4건')).to_be_visible()
    scenario.select_option('delayed')
    expect(demo.get_by_role('status').filter(has_text='갱신이 지연')).to_be_visible()


def test_database_to_public_calendar_and_comparison(dataset, tmp_path, calendar_page):
    page = calendar_page
    page.clock.install(time=datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=dataset) as (url, _):
        page.goto(url)
        expect(page.get_by_text('예시 요약').first).to_be_visible()
        expect(page.get_by_role('button', pressed=True)).to_contain_text('시행 1')
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
        for title in ('2026년 12월', '2027년 1월'):
            page.get_by_role('button', name='다음 달').click()
            expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
            expect(page.get_by_text('이 달에 공개 승인된', exact=False)).to_be_visible()
        page.get_by_role('button', name='오늘', exact=True).click()
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
                env=review_environment(),
                timeout=15,
                capture_output=True,
                text=True,
            )
            support.export_calendar(tmp_path, db)
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


@pytest.mark.parametrize('width', [390, 1280])
def test_public_holds_dialog_is_undated_read_only_and_keyboard_accessible(workflow, tmp_path, calendar_page, width):
    db, home, path, event = workflow
    event['context']['addenda_added'] = ['제1조(시행일) 이 법은 대통령령으로 정하는 날부터 시행한다.']
    path.write_text(json.dumps(event), encoding='utf-8')
    import_one(workflow)
    page = calendar_page
    page.set_viewport_size({'width': width, 'height': 844})
    page.clock.install(time=datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=db) as (url, _):
        page.goto(url)
        expect(page.get_by_text('이 달에 공개 승인된', exact=False)).to_be_visible()
        opener = page.get_by_role('button', name='보류 목록', exact=True)
        opener.click()
        dialog = page.get_by_role('dialog', name='보류 목록', exact=True)
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text('총 3건', exact=False)).to_be_visible()
        expect(dialog.get_by_text('대통령령 등 별도로', exact=False).first).to_be_visible()
        expect(dialog.get_by_role('button', name='보류 목록 닫기')).to_be_focused()
        assert dialog.get_by_role('textbox').count() == 0
        assert dialog.get_by_role('button', name='확정', exact=False).count() == 0
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
        page.keyboard.press('Shift+Tab')
        assert dialog.evaluate('(el) => el.contains(document.activeElement)')
        page.keyboard.press('Tab')
        expect(dialog.get_by_role('button', name='보류 목록 닫기')).to_be_focused()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(opener).to_be_focused()


@pytest.mark.parametrize('prior_amendment', [False, True], ids=['conditional', 'prior-amendment'])
def test_numbered_commencement_shows_changed_scope_and_separate_hold(
    workflow, tmp_path, calendar_page, prior_amendment
):
    db, home, path, event = workflow
    before, after = '**①** 종전 본문\n\n', '**①** 개정 본문\n\n'
    old_rest, new_rest = '**②** 종전 제한\n', '**②** 개정 제한\n'
    header, addendum = '##### 제1조\n\n', ''
    if prior_amendment:
        event['target']['law_name'] = '가상법'
        before = '**①** 종전 본문 <신설 2025.4.7>\n\n'
        after = '**①** 개정 본문 <개정 2026.10.1>\n\n'
        old_rest = '**②** 종전 제한 <신설 2025.5.7>\n'
        new_rest = '**②** 개정 제한 <개정 2026.10.1>\n'
        addendum = '\n## 부칙\n\n부칙 <제123호,2025.4.7>\n\n이 법은 2027년 1월 1일부터 시행한다.\n'
    (home / 'data/files/old.md').write_text(header + before + old_rest, encoding='utf-8')
    frontmatter = "---\n공포번호: '124'\n---\n\n" if prior_amendment else ''
    (home / 'data/files/new.md').write_text(frontmatter + header + after + new_rest + addendum, encoding='utf-8')
    event['diff']['chunks'] = [{'article': '제1조'}]
    event['context'].pop('lawgo')
    targets = (
        '1. 법률 제123호 가상법 일부개정법률 제1조의 개정규정: 2027년 1월 1일'
        if prior_amendment
        else '1. 제1조제1항: 2027년 1월 1일\n2. 제1조제2항(상시 50명 미만인 사업장으로 한정한다): 2027년 1월 1일'
    )
    event['context']['addenda_added'] = [
        '이 법은 공포한 날부터 시행한다. 다만, 다음 각 호의 개정규정은 해당 호에서 정하는 날부터 시행한다.\n' + targets
    ]
    path.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
    import_one(workflow)
    page = calendar_page
    page.clock.set_fixed_time(datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=db) as (url, _):
        page.goto(url)
        panel = page.get_by_role('region', name='시행 조문 1건')
        panel.get_by_role('button', name='제1조 제1항', exact=False).click()
        dialog = page.get_by_role('dialog')
        comparison = dialog.get_by_role('group', name='저장 원문 비교')
        expect(comparison).to_be_visible()
        assert comparison.locator('pre').all_text_contents() == [before, after]
        dialog.get_by_role('button', name='상세 닫기').click()
        page.get_by_role('button', name='보류 목록', exact=True).click()
        holds = page.get_by_role('dialog', name='보류 목록', exact=True)
        expect(holds.get_by_text('총 1건', exact=False)).to_be_visible()
        expect(holds.get_by_text('제1조 제2항', exact=False)).to_be_visible()
