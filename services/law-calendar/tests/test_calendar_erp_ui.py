"""ERP tab in the production calendar build: synthetic feed and calendar DB, local server only."""

import datetime
import json

import pytest
from playwright.sync_api import expect

import calendar_ui_support as support
from test_calendar_api import dataset as dataset
from test_calendar_erp import FEED, ITEM
from test_calendar_news import snapshot

pytest_plugins = ['calendar_test_fixtures']
pytestmark = [pytest.mark.e2e_stub, pytest.mark.usefixtures('calendar_network')]
KST = datetime.timezone(datetime.timedelta(hours=9))
SHA = 'c1b533d6' + '0' * 56


@pytest.fixture
def feed(tmp_path):
    current = {**ITEM, 'effectiveFrom': '2000-01-01'}
    upcoming = {
        **ITEM,
        'id': 'item-2',
        'sourceKey': 'INS-03',
        'title': '[예시] 2999년도 가상 요율',
        'effectiveFrom': '2999-01-01',
    }
    form = {
        **ITEM,
        'id': 'item-3',
        'sourceKey': 'TAX-01',
        'kind': 'form',
        'title': '[예시] 가상 간이세액표',
        'lawName': '[예시] 가상법',
        'article': '별표 2',
        'effectiveFrom': None,
        'asOf': '2026-10-01',
        'excerpt': None,
        'attachments': [{**ITEM['attachments'][0], 'sha256': SHA, 'bytes': 397806}],
    }
    history = [
        *FEED['history'],
        {
            'itemId': 'item-3',
            'sourceKey': 'TAX-01',
            'title': form['title'],
            'at': '2026-11-12T20:01:00+09:00',
            'action': 'attached',
            'origin': 'file sha256:c1b533d60000',
        },
    ]
    path = tmp_path / 'erp_calendar_public.json'
    path.write_text(json.dumps({**FEED, 'items': [current, upcoming, form], 'history': history}), encoding='utf-8')
    return path


def move_to(page, month: str):
    """Step the related-law month from the current KST month to `month` (bounded)."""
    today = datetime.datetime.now(KST).date()
    target = datetime.date.fromisoformat(month + '-01')
    steps = (target.year - today.year) * 12 + target.month - today.month
    button = page.get_by_role('button', name='다음 달' if steps > 0 else '이전 달')
    for _ in range(abs(steps)):
        button.click()
    expect(page.get_by_role('heading', name=f'관련 법령 일정 · {target.year}년 {target.month:02d}월')).to_be_visible()


def test_erp_tab_shows_items_related_laws_and_history(dataset, feed, tmp_path, calendar_page, request):
    page = calendar_page
    with support.serve(tmp_path, development=False, database=dataset, erp_feed=feed) as (url, log):
        try:
            page.goto(url + '/#erp')
            expect(page.get_by_role('tab', name='ERP 자료')).to_have_attribute('aria-selected', 'true')
            current = page.get_by_role('region', name='현재 적용 중')
            expect(current.get_by_role('heading', name='[예시] 가상 연금 상한액')).to_be_visible()
            expect(current.get_by_text(f'SHA-256 {SHA}')).to_be_visible()
            expect(current.get_by_text('현행 법령본 기준일')).to_be_visible()
            upcoming = page.get_by_role('region', name='적용 예정')
            expect(upcoming.get_by_role('heading', name='[예시] 2999년도 가상 요율')).to_be_visible()

            move_to(page, '2026-11')
            related = page.get_by_role('region', name='관련 법령 일정 · 2026년 11월')
            related.get_by_role('button', name='[예시] 가상법').first.click()
            expect(page.get_by_role('dialog')).to_be_visible()
            page.get_by_role('button', name='상세 닫기').click()

            page.get_by_role('button', name='수집 이력').click()
            dialog = page.get_by_role('dialog', name='수집 이력')
            expect(dialog.get_by_role('row')).to_have_count(3)
            expect(dialog.get_by_role('row').nth(1)).to_contain_text('원문 PDF 등록')
            expect(dialog.get_by_role('row').nth(2)).to_contain_text('admrule-kr@0123456789ab')
            dialog.get_by_role('button', name='수집 이력 닫기').click()

            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth') <= 390

            page.get_by_role('tab', name='법령 캘린더').click()
            expect(page.get_by_role('heading', level=1, name='법령 변경 캘린더')).to_be_visible()
        finally:
            support.keep_on_failure(request, log, page)


def test_missing_feed_leaves_the_law_calendar_working(dataset, tmp_path, calendar_page, request):
    page = calendar_page
    with support.serve(tmp_path, development=False, database=dataset) as (url, log):
        try:
            page.goto(url + '/#erp')
            expect(page.get_by_text('ERP 자료가 아직 준비되지 않았습니다.')).to_be_visible()
            hometax = page.get_by_role('link', name='홈택스 공식 홈')
            expect(hometax).to_have_attribute('href', 'https://www.hometax.go.kr/')
            expect(page.get_by_text('전체메뉴 → 기타 → 고객센터 → 자료실')).to_be_visible()
            page.get_by_role('tab', name='법령 캘린더').click()
            expect(page.get_by_role('heading', level=1, name='법령 변경 캘린더')).to_be_visible()
            expect(page.get_by_role('grid')).to_be_visible()
        finally:
            support.keep_on_failure(request, log, page)


def test_verified_rule_card_keeps_current_and_future_together(dataset, feed, tmp_path, calendar_page, request):
    page = calendar_page
    rules = tmp_path / 'rules.json'
    mark = {'at': '2000-01-01T00:00:00+09:00', 'decision': 'confirmed'}

    def edition(name, start, text):
        return {
            'version': {
                'ruleVersionId': name,
                'sourceRevisionId': 1,
                'effectiveFrom': start,
                'appliesFrom': start,
                'evidenceKind': 'final_notice',
                'evidenceUrl': 'https://www.law.go.kr/행정규칙/' + name,
                'valueText': text,
            },
            'reviews': [mark],
        }

    rules.write_text(
        json.dumps(
            {
                'schemaVersion': 2,
                'generatedAt': mark['at'],
                'rules': [
                    {
                        'ruleId': 'INS-01',
                        'sourceKey': 'INS-01',
                        'title': '가상 보험 기준',
                        'scopes': [
                            {
                                'scopeId': 'standard',
                                'scope': '일반 가입자',
                                'applicability': 'insurance_month',
                                'editions': [
                                    edition('current', '2000-01-01', '가상 현행 기준'),
                                    edition('next', '2999-01-01', '가상 예정 기준'),
                                    edition('later', '2999-07-01', '가상 추가 변경'),
                                ],
                            }
                        ],
                        'proposalLinks': [
                            {
                                'ruleVersionId': 'draft',
                                'evidenceUrl': 'https://pal.assembly.go.kr/napal/lgsltpa/예시',
                            }
                        ],
                    }
                ],
            }
        )
    )
    with support.serve(tmp_path, development=False, database=dataset, erp_feed=feed, rules_feed=rules) as (url, log):
        try:
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(url + '/#erp')
            card = page.get_by_role('article', name='가상 보험 기준')
            expect(card).to_have_count(1)
            expect(card.get_by_text('가상 현행 기준', exact=True)).to_be_visible()
            expect(card.get_by_text('가상 예정 기준', exact=True)).to_be_visible()
            expect(card.get_by_role('link', name='현행 공식 원문', exact=False)).to_have_attribute('target', '_blank')
            expect(card.get_by_role('heading', name='검토 중')).to_be_visible()
            expect(card.get_by_text('가상 추가 변경')).to_be_hidden()
            card.get_by_text('이후 변경 1건').press('Enter')
            expect(card.get_by_text('가상 추가 변경')).to_be_visible()
            card.get_by_text('기존 자료와 첨부 1건').click()
            expect(card.get_by_role('link', name='별지 PDF', exact=False)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth') <= 390
            page.get_by_role('tab', name='법령 캘린더').click()
            expect(page.get_by_role('heading', level=1, name='법령 변경 캘린더')).to_be_visible()
        finally:
            support.keep_on_failure(request, log, page)


def test_agency_news_overlays_erp_and_supports_mouse_keyboard_and_touch(dataset, tmp_path, calendar_pages, request):
    page = calendar_pages(viewport={'width': 1280, 'height': 850})
    news_feed = tmp_path / 'agency-news.json'
    news = snapshot()
    news['agencies']['moel'] = {
        'boards': [{'name': '공지', 'status': 'partial', 'lastSuccessAt': None}],
        'items': [
            {
                'id': 'news-1',
                'title': '검토된 공지',
                'board': '공지',
                'publishedOn': None,
                'officialUrl': 'https://www.moel.go.kr/news/notice/noticeView.do?bbs_seq=123',
            }
        ],
    }
    news_feed.write_text(json.dumps(news))
    with support.serve(tmp_path, development=False, database=dataset, news_feed=news_feed) as (url, log):
        try:
            page.goto(url + '/#erp')
            requests = []
            page.on('request', lambda http_request: requests.append(http_request.url))
            width = page.get_by_role('main').bounding_box()['width']
            button = page.get_by_role('button', name='고용노동부', exact=True)
            button.hover()
            panel = page.get_by_role('dialog', name='고용노동부 최신 소식')
            link = panel.get_by_role('link', name='검토된 공지 원문 열기')
            expect(link).to_have_attribute('href', news['agencies']['moel']['items'][0]['officialUrl'])
            expect(panel.get_by_text('게시일 미확인', exact=False)).to_be_visible()
            expect(panel.get_by_text('공지 · 일부 수집 실패')).to_be_visible()
            link.hover()
            expect(panel).to_be_visible()
            assert page.get_by_role('main').bounding_box()['width'] == width
            assert requests and all(path.startswith(url + '/') for path in requests)

            page.get_by_role('heading', level=1, name='ERP 자료').click()
            expect(panel).to_have_count(0)
            page.get_by_role('link', name='홈택스 공식 홈').focus()
            page.keyboard.press('Tab')
            page.keyboard.press('Tab')
            expect(button).to_be_focused()
            expect(panel).to_be_visible()
            page.keyboard.press('Enter')
            expect(panel.get_by_role('button', name='닫기')).to_be_focused()
            page.keyboard.press('Escape')
            expect(panel).to_have_count(0)
            expect(button).to_be_focused()

            touch = calendar_pages(viewport={'width': 390, 'height': 844}, has_touch=True, reduced_motion='reduce')
            touch.goto(url + '/#erp')
            touch.get_by_role('button', name='홈택스', exact=True).tap()
            home = touch.get_by_role('dialog', name='홈택스 최신 소식')
            expect(home.get_by_text('전체메뉴 → 기타 → 고객센터 → 자료실')).to_be_visible()
            expect(home.get_by_role('link', name='홈택스 누리집 열기')).to_have_attribute(
                'href', 'https://www.hometax.go.kr/'
            )
            assert touch.evaluate('document.documentElement.scrollWidth') <= 390
            assert home.evaluate('(element) => getComputedStyle(element).transitionDuration') == '0s'
            home.get_by_role('button', name='닫기').tap()
            expect(home).to_have_count(0)
        finally:
            support.keep_on_failure(request, log, page)
