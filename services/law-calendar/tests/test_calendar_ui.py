"""Run the public calendar at its own origin with no Lawbot backend available."""

import datetime
import json
import subprocess
import sys
from io import BytesIO

import pytest
from PIL import Image
from playwright.sync_api import expect

import calendar_ui_support as support
from law_calendar.assembly_ingest import import_assembly, review_assembly
from law_calendar.sync import synchronize
from test_calendar_api import dataset as dataset
from test_calendar_assembly import register
from test_calendar_notice_content import detail_html
from test_calendar_sync import scheduled as scheduled
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
    expect(demo.get_by_text('이 날짜에 표시할 입법예고가 없습니다.')).to_be_visible()
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


@pytest.mark.parametrize('width', [390, 1280])
def test_assembly_notice_and_bill_dates_remain_separate(scheduled, tmp_path, calendar_page, width, monkeypatch):
    (database, home, _, _), state = scheduled
    register(home, state)
    bill = register(home, state, source='assembly_bill', date='2026-11-05')
    with BytesIO() as buffer:
        Image.new('RGB', (600, 800), 'white').save(buffer, format='PNG')
        png = buffer.getvalue()
    html = detail_html('2200012', '2026-11-04~2026-11-05', '정식 적용 범위를 확대하는 안입니다.')
    monkeypatch.setattr(
        'law_calendar.notice_content.fetch_official', lambda _url, *, pdf=False: b'%PDF-test' if pdf else html.encode()
    )
    monkeypatch.setattr('law_calendar.notice_content.comparison_images', lambda _: [(5, png)])
    assert synchronize(database, home)['notice_auto_published'] == 1
    review_assembly(database, bill, 'approve', reviewer='tester', note='공식 날짜 확인')
    page = calendar_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.clock.install(time=datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC))
    with support.serve(tmp_path, development=False, database=database) as (url, _):
        page.goto(url)
        notice_panel = page.get_by_role('region', name='입법예고 1건')
        expect(notice_panel.get_by_text('예고 종료일', exact=False)).to_be_visible()
        bill_panel = page.get_by_role('region', name='법률안 1건')
        expect(bill_panel.get_by_role('button')).to_contain_text('발의·제안일')
        notice_panel.get_by_role('button').click()
        dialog = page.get_by_role('dialog')
        expect(dialog.get_by_text('소관위원회', exact=True)).to_be_visible()
        expect(dialog.get_by_role('heading', name='주요 변경 내용')).to_be_visible()
        expect(dialog.get_by_text('정식 적용 범위를 확대하는 안입니다.')).to_be_visible()
        image = dialog.get_by_role('img', name='공식 신·구조문대비표 원문 5쪽', include_hidden=True)
        expect(image).not_to_be_visible()
        dialog.get_by_text('공식 신·구조문대비표', exact=True).click()
        expect(image).to_be_visible()
        page.wait_for_function("document.querySelector('.lc-notice-pages img')?.naturalWidth === 600")
        assert dialog.evaluate('(element) => element.scrollWidth <= element.clientWidth')
        expect(dialog.get_by_role('link', name='공식 원문 확인', exact=False)).to_have_attribute(
            'href', 'https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=PRC_NOTICE'
        )
        page.keyboard.press('Escape')
        expect(notice_panel.get_by_role('button')).to_be_focused()
        page.get_by_role('button', pressed=True).focus()
        page.keyboard.press('ArrowLeft')
        expect(notice_panel.get_by_text('예고 시작일', exact=False)).to_be_visible()
        expect(bill_panel).to_have_count(0)


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


@pytest.mark.parametrize('width', [390, 1280])
def test_public_holds_dialog_is_undated_read_only_and_keyboard_accessible(scheduled, tmp_path, calendar_page, width):
    workflow, state = scheduled
    db, home, path, event = workflow
    event['context']['addenda_added'] = ['제1조(시행일) 이 법은 대통령령으로 정하는 날부터 시행한다.']
    path.write_text(json.dumps(event), encoding='utf-8')
    import_one(workflow)
    notice = register(home, state, start=None)
    snapshot = json.loads((home / f'{notice}.json').read_text())
    import_assembly(db, 'assembly_notice', snapshot['detected_at'], [snapshot])
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
        dialog.get_by_role('button', name='입법예고 검토 대기').click()
        expect(dialog.get_by_text('총 1건', exact=False)).to_be_visible()
        expect(dialog.get_by_text('의안번호 2200012')).to_be_visible()
        expect(dialog.get_by_text('예고 시작일 또는 종료일 확인 필요')).to_be_visible()
        expect(dialog.get_by_role('link', name='공식 원문 확인 (새 창)')).to_have_attribute(
            'href', snapshot['context']['source_url']
        )
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
        dialog.get_by_role('button', name='시행일 보류').click()
        expect(dialog.get_by_text('총 3건', exact=False)).to_be_visible()
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
