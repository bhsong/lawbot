"""Official notice content and comparison images stay within the approved snapshot."""

import json
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from law_calendar.assembly_ingest import import_assembly, review_assembly
from law_calendar.db import SCHEMA, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, migrate
from law_calendar.notice_comparison import comparison_images
from law_calendar.notice_content import NoticeContent, refresh_notice_content
from law_calendar.notice_source import NoticeSourceError, parse_notice_detail
from law_calendar.server import create_app
from test_calendar_assembly import register
from test_calendar_sync import scheduled as scheduled
from test_calendar_workflow import workflow as workflow

PDF_URL = 'https://likms.assembly.go.kr/filegate/servlet/FileGate?bookId=123-abc&type=1'


def detail_html(bill_no, period, content, pdf_url=PDF_URL):
    return f'''
    <table><tr><th>의안번호</th><th>입법예고기간</th><th>법률안원문</th></tr>
    <tr><td>{bill_no}</td><td>{period}</td><td><a href="{pdf_url.replace('&', '&amp;')}"
    title="법률안.pdf"><span>PDF</span></a></td></tr></table>
    <!-- 제안이유 및 주요내용 -->
    <div class="item"><h4>제안이유 및 주요내용</h4><div class="desc">{content}</div></div>
    <!-- //제안이유 및 주요내용 -->
    <div>독자 의견</div>
    '''


@pytest.mark.parametrize(
    'body,summary',
    [
        ('제안이유 및 주요내용<br/><br/>종전 범위를 확대함.<br/>안 제10조', '종전 범위를 확대함.\n안 제10조'),
        ('제안이유<br/>제안 배경<br/><br/>주요내용<br/>안 제10조를 신설함.', '안 제10조를 신설함.'),
    ],
)
def test_notice_detail_uses_the_official_body_and_same_bill_attachment(body, summary):
    parsed = parse_notice_detail(
        detail_html('2220001', '2026-11-04~2026-11-05', body), '2220001', '2026-11-04', '2026-11-05'
    )
    assert parsed == (summary, PDF_URL)
    invalid = detail_html('2220001', '2026-11-04~2026-11-05', body, 'https://example.com/malicious.pdf')
    assert parse_notice_detail(invalid, '2220001', '2026-11-04', '2026-11-05') == (summary, None)
    with pytest.raises(NoticeSourceError):
        parse_notice_detail(invalid, '2220002', '2026-11-04', '2026-11-05')


def test_legacy_approved_notice_is_enriched_without_changing_its_calendar_rows(tmp_path, scheduled):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA + SCHEMA_V2 + SCHEMA_V3 + SCHEMA_V4 + 'PRAGMA user_version=4;')
    (_, home, _, _), state = scheduled
    snapshot = register(home, state)
    notice = json.loads((home / f'{snapshot}.json').read_text())
    stamp = notice['detected_at']
    import_assembly(path, 'assembly_notice', stamp, [notice])
    review_assembly(path, notice['event_id'], 'approve', reviewer='reviewer', note='날짜 확인')
    with TestClient(create_app(path)) as client:
        before = client.get('/api/law-events?month=2026-11&type=notice').json()
    migrate(path)
    assert path.with_suffix('.db.pre-v5.backup').exists()
    with BytesIO() as buffer:
        Image.new('RGB', (1, 1)).save(buffer, format='PNG')
        png = buffer.getvalue()
    calls = []

    def read(_url, bill_no, start, end):
        calls.append((bill_no, start, end))
        return NoticeContent('제10조의 적용 대상을 확대하는 안입니다.', PDF_URL, [(5, png)])

    assert refresh_notice_content(path, limit=10, read=read)['stored'] == 1
    assert refresh_notice_content(path, limit=10, read=read)['stored'] == 0
    assert len(calls) == 1
    with TestClient(create_app(path)) as client:
        listing = client.get('/api/law-events?month=2026-11&type=notice').json()
        assert listing == before
        for row in listing['items']:
            url = f'/api/law-changes/{row["id"]}'
            assert client.get(url).json()['noticeContent'] == {
                'summary': '제10조의 적용 대상을 확대하는 안입니다.',
                'pdfUrl': PDF_URL,
                'comparisonPages': [5],
            }
            page = client.get(f'{url}/notice-comparison/5')
            assert page.status_code == 200 and page.content == png
            assert page.headers['content-type'] == 'image/png'
            assert client.get(f'{url}/notice-comparison/6').status_code == 404
        review_assembly(path, notice['event_id'], 'withdraw', reviewer='reviewer', note='공개 철회')
        assert client.get(f'{url}/notice-comparison/5').status_code == 404


@pytest.mark.parametrize('preceding_pages', [1, 100])
def test_comparison_follows_only_the_contiguous_table_pages(monkeypatch, preceding_pages):
    class Text:
        def __init__(self, value):
            self.value = value

        def get_text_bounded(self):
            return self.value

        def close(self):
            return None

    class Line:
        def get_bounds(self):
            return 299.5, 80, 300.5, 680

    class Bitmap:
        def to_pil(self):
            return Image.new('RGB', (8, 12), 'white')

        def close(self):
            return None

    class Page:
        def __init__(self, text, divider=False):
            self.text, self.divider = text, divider

        def get_size(self):
            return 600, 800

        def get_textpage(self):
            return Text(self.text)

        def get_objects(self, **_options):
            return [Line()] if self.divider else []

        def render(self, **options):
            assert options['draw_annots'] is False
            return Bitmap()

        def close(self):
            return None

    from contextlib import nullcontext

    from law_calendar import notice_comparison

    pages = [
        *[Page('제안이유 및 주요내용') for _ in range(preceding_pages)],
        Page('- 2 - 신ㆍ구조문대비표 현 행 개 정 안', divider=True),
        Page('제2항 종전 문구와 개정 문구가 이어짐', divider=True),
        Page('비용추계서'),
        Page('다른 자료의 표', divider=True),
    ]
    monkeypatch.setattr(notice_comparison.pdfium, 'PdfDocument', lambda _: nullcontext(pages))
    result = comparison_images(b'%PDF-test')
    assert [number for number, _ in result] == [preceding_pages + 1, preceding_pages + 2]
    assert all(image.startswith(b'\x89PNG') for _, image in result)


def test_source_failure_does_not_replace_saved_comparison_or_approve_pending_items(scheduled):
    (path, home, _, _), state = scheduled
    snapshot = register(home, state)
    notice = json.loads((home / f'{snapshot}.json').read_text())
    import_assembly(path, 'assembly_notice', notice['detected_at'], [notice])
    assert refresh_notice_content(path, read=lambda *_: pytest.fail('Pending notice was read'))['selected'] == 0
    review_assembly(path, snapshot, 'approve', reviewer='reviewer', note='날짜 확인')
    refresh_notice_content(path, read=lambda *_: NoticeContent('보관된 주요 내용', PDF_URL, [(2, b'old image')]))
    result = refresh_notice_content(
        path, refresh=True, read=lambda *_: NoticeContent('다른 내용', PDF_URL, pdf_error='read failed')
    )
    assert result['stored'] == 0 and len(result['pdfErrors']) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT summary FROM assembly_notice_content').fetchone() == ('보관된 주요 내용',)
        assert db.execute('SELECT png FROM assembly_notice_pages').fetchone() == (b'old image',)
