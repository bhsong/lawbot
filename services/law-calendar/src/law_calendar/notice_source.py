"""Read official notice HTML and identify its bill PDF link."""

import re
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from law_calendar.assembly_ingest import NOTICE_ENDPOINTS

SUMMARY_HEADING = '제안이유 및 주요내용'
DETAIL_PATHS = {path for _, path in NOTICE_ENDPOINTS.values()}
PDF_PATH = '/filegate/servlet/FileGate'
PDF_HOST = 'likms.assembly.go.kr'
MAX_BYTES = 8 * 1024 * 1024


class NoticeSourceError(ValueError):
    """Official evidence was unavailable or inconsistent with the stored notice."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_official_pdf_url(url: str) -> bool:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    ids = query.get('bookId', [])
    return (
        parsed.scheme == 'https'
        and parsed.netloc == PDF_HOST
        and parsed.path == PDF_PATH
        and not parsed.fragment
        and query.get('type') == ['1']
        and set(query) == {'bookId', 'type'}
        and len(ids) == 1
        and re.fullmatch(r'[A-Za-z0-9-]{1,80}', ids[0]) is not None
    )


def fetch_official(url: str) -> bytes:
    """Fetch only the official notice detail page, without following redirects."""
    parsed = urlsplit(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    ids = params.get('lgsltPaId', [])
    if (
        parsed.scheme != 'https'
        or parsed.netloc != 'pal.assembly.go.kr'
        or parsed.path not in DETAIL_PATHS
        or parsed.fragment
        or set(params) != {'lgsltPaId'}
        or len(ids) != 1
        or not re.fullmatch(r'[A-Za-z0-9_]+', ids[0])
    ):
        raise NoticeSourceError('Invalid official notice link')
    deadline = time.monotonic() + 12
    opener = build_opener(NoRedirect)
    request = Request(url, headers={'User-Agent': 'law-calendar/0.1', 'Accept-Encoding': 'identity'})
    try:
        with opener.open(request, timeout=8) as response:
            payload = bytearray()
            while chunk := response.read1(65536):
                payload.extend(chunk)
                if len(payload) > MAX_BYTES or time.monotonic() >= deadline:
                    raise NoticeSourceError('Official response exceeded its size or time limit')
            return bytes(payload)
    except HTTPError as exc:
        exc.close()
        raise NoticeSourceError(f'Official server returned HTTP {exc.code}') from None
    except (URLError, OSError) as exc:
        raise NoticeSourceError('Official source read failed') from exc


class NoticeDetail(HTMLParser):
    """Read the official period table and the marked content block as plain text."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[tuple[str, list[str]]]] = []
        self.row: list[tuple[str, list[str]]] | None = None
        self.cell: list[str] | None = None
        self.links: list[str] = []
        self.section = False
        self.depth = 0
        self.skip = False
        self.body: list[str] = []

    def handle_comment(self, data):
        marker = data.strip()
        if marker == SUMMARY_HEADING:
            self.section = True
        elif marker == f'//{SUMMARY_HEADING}':
            self.section = False
            self.depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
            self.links = []
        elif tag == 'a' and self.cell is not None and (link := attributes.get('href')):
            self.links.append(link)
        if self.section and tag == 'div' and (self.depth or 'desc' in (attributes.get('class') or '').split()):
            self.depth += 1
        if self.depth and tag in ('br', 'p', 'li'):
            self.body.append('\n')
        if self.depth and tag in ('script', 'style'):
            self.skip = True

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        if self.depth and not self.skip:
            self.body.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.row is not None and self.cell is not None:
            self.row.append((' '.join(''.join(self.cell).split()), self.links))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag in ('script', 'style'):
            self.skip = False
        if self.depth and tag == 'div':
            self.depth -= 1
        if self.depth and tag in ('p', 'li'):
            self.body.append('\n')


def parse_notice_detail(html: str, bill_no: str, start: str | None, end: str) -> tuple[str | None, str | None]:
    parser = NoticeDetail()
    parser.feed(html)
    matches = []
    for headers, row in zip(parser.rows, parser.rows[1:]):
        labels = [value for value, _ in headers]
        if len(labels) != len(row) or not {'의안번호', '입법예고기간'} <= set(labels):
            continue
        if row[labels.index('의안번호')][0] != bill_no:
            continue
        period = re.fullmatch(
            r'(\d{4}-\d{2}-\d{2})\s*[~～]\s*(\d{4}-\d{2}-\d{2})', row[labels.index('입법예고기간')][0]
        )
        if period and period[2] == end and (start is None or period[1] == start) and period[1] <= period[2]:
            matches.append(row[labels.index('법률안원문')][1] if '법률안원문' in labels else [])
    if len(matches) != 1:
        raise NoticeSourceError('Official bill number or notice period did not match')

    lines = [' '.join(line.split()) for line in ''.join(parser.body).strip().splitlines()]
    lines = [line for line in lines if line != SUMMARY_HEADING]
    for index, line in enumerate(lines):
        if re.fullmatch(r'주요\s*내용', line):
            lines = lines[index + 1 :]
            break
    summary = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    if len(summary) > 30000:
        raise NoticeSourceError('Official notice body exceeded the storage limit')
    pdf_urls = {url for url in matches[0] if _is_official_pdf_url(url)}
    return summary or None, next(iter(pdf_urls)) if len(pdf_urls) == 1 else None
