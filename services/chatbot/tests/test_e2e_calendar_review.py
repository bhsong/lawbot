"""Administrator reviews a synthetic law through both real HTTP services."""

import json
import secrets
import sys

import pytest
from playwright.sync_api import expect

import e2e_support as e2e
from admin_test_data import seed_qa
from conftest import ADMIN_PW, ADMIN_USER, free_port
from law_calendar.db import migrate
from law_calendar.import_failures import ImportInputError, record_import_failure
from law_calendar.ingest import import_files
from lawbot.db import cli
from lawbot.db.connection import connect

pytestmark = pytest.mark.e2e_stub
SOURCE = 'https://www.law.go.kr/법령/가상검토법'


@pytest.fixture
def calendar_stack(tmp_path, page, request, admin_hash):
    calendar_db = tmp_path / 'calendar.db'
    migrate(calendar_db)
    for name, body in [('old', '시행 전 시험 원문'), ('new', '시행 후 시험 원문')]:
        (tmp_path / f'{name}.md').write_text(f'# 가상검토법\n\n##### 제1조 (목적)\n\n{body}\n', encoding='utf-8')
    path = tmp_path / 'event.json'
    path.write_text(
        json.dumps(
            {
                'event_id': 'fixture-calendar-hold',
                'source': 'legalize_law',
                'change_type': 'law_amended',
                'detected_at': '2026-11-10T06:31:00+09:00',
                'target': {'law_id': 'fixture', 'law_name': '가상검토법'},
                'dates': {'promulgation': '2026-11-10'},
                'old': {'file': 'old.md', 'version_key': 'old-blob'},
                'new': {'file': 'new.md', 'version_key': 'new-blob'},
                'diff': {'chunks': [{'article': '제1조'}]},
                'context': {'source_url': SOURCE},
            }
        ),
        encoding='utf-8',
    )
    assert import_files(calendar_db, tmp_path, [path])['imported'] == 1
    record_import_failure(
        calendar_db, 'assembly_notice', 'fixture-failed-notice', ImportInputError('Invalid notice period')
    )
    web_db = tmp_path / 'private' / 'lawbot.db'
    cli.main(['init', '--db', str(web_db)])
    seed_qa(web_db)

    port = free_port()
    url = f'http://127.0.0.1:{port}'
    token = secrets.token_urlsafe()
    calendar = e2e.Proc(
        'calendar',
        [sys.executable, '-m', 'law_calendar.server', '--database', str(calendar_db), '--port', str(port)],
        e2e.clean_env(LAWBOT_CALENDAR_ADMIN_TOKEN=token),
        tmp_path,
    )
    stack = None
    try:
        calendar.wait_http(f'{url}/api/law-holds', lambda response: response.is_success, timeout=10)
        stack = e2e.Stack(
            tmp_path,
            read_timeout=2,
            LAWBOT_DB=str(web_db),
            LAWBOT_ADMIN_USER=ADMIN_USER,
            LAWBOT_ADMIN_PW_HASH=admin_hash,
            LAWBOT_CALENDAR_API_URL=url,
            LAWBOT_CALENDAR_ADMIN_TOKEN=token,
        )
        yield stack, web_db
        e2e.keep_on_failure(request, 'e2e_stub', [calendar.log, *stack.logs()], page)
    finally:
        if stack is not None:
            stack.stop()
        calendar.stop()


@pytest.mark.parametrize('width', [390, 1280])
def test_admin_calendar_hold_approval(calendar_stack, page, width):
    stack, web_db = calendar_stack
    page.set_viewport_size({'width': width, 'height': 844})
    e2e.login(page, stack.url, user=ADMIN_USER, pw=ADMIN_PW)
    page.get_by_role('radio', name='피드백 & 법령 관리').click()
    panel = page.get_by_role('region', name='법령 캘린더')
    panel.get_by_role('button', name='보류 목록').click()
    holds = page.get_by_role('dialog', name='보류 목록')
    expect(holds.get_by_text('총 1건', exact=False)).to_be_visible()
    holds.get_by_role('button', name='검토 열기 · 가상검토법 제1조').click()
    expect(holds.get_by_role('heading', name='시행일 보류 검토')).to_be_visible()
    expect(holds.get_by_role('link', name='공식 원문 확인 (새 창)').first).to_have_attribute('href', SOURCE)
    expect(holds.get_by_text('시행 전 시험 원문', exact=False)).to_be_visible()
    expect(holds.get_by_text('시행 후 시험 원문', exact=False)).to_be_visible()
    expect(holds.get_by_label('공포일', exact=True)).to_have_value('2026-11-10')
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')

    holds.get_by_label('검토 메모').fill('공식 부칙과 저장 원문 확인')
    holds.get_by_label('공포일과 부칙을 확인했습니다').check()
    holds.get_by_label('시행일', exact=True).fill('2026-11-20')
    holds.get_by_label('근거 URL', exact=True).fill('https://example.invalid/evidence')
    holds.get_by_label('변경 요약').fill('제1조 시험 개정')
    holds.get_by_label('시행 전후 원문을 비교했습니다').check()
    holds.get_by_role('button', name='승인', exact=True).click()
    expect(holds.get_by_role('alert')).to_contain_text('changes[1].evidence_url: An official HTTPS evidence URL')
    expect(holds.get_by_label('검토 메모')).to_have_value('공식 부칙과 저장 원문 확인')

    holds.get_by_label('근거 URL', exact=True).fill(SOURCE)
    holds.get_by_role('button', name='승인', exact=True).click()
    expect(holds.get_by_text('검토 결정을 저장했습니다.')).to_be_visible()
    expect(holds.get_by_text('현재 공개된 보류 항목이 없습니다.')).to_be_visible()
    holds.get_by_role('button', name='보류 목록 닫기').click()
    expect(panel.get_by_role('button', name='가상검토법 제1조', exact=False)).to_be_visible()
    panel.get_by_role('button', name='가져오기 실패', exact=True).click()
    failures = page.get_by_role('dialog', name='가져오기 실패')
    expect(failures.get_by_text('fixture-failed-notice', exact=True)).to_be_visible()
    expect(failures.get_by_text('Invalid notice period', exact=True)).to_be_visible()
    with connect(web_db) as conn:
        assert conn.execute("SELECT actor FROM audit_events WHERE action='calendar_review'").fetchall() == [
            (ADMIN_USER,)
        ]
