"""Read the Assembly Open API and verify notice periods against official detail pages."""

import json
import re
import time
from datetime import date
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BILLS = 'TVBPMBILL11'
ACTIVE = 'nknalejkafmvgzmpt'
CLOSED = 'nohgwtzsamojdozky'
SOURCES = ('assembly_bill', 'assembly_notice')
BASE = 'https://open.assembly.go.kr/portal/openapi/'
NOTICE_PATHS = {
    '/napal/lgsltpa/lgsltpaOngoing/view.do',
    '/napal/lgsltpa/lgsltpaDone/view.do',
}
MAX_BODY = 8 * 1024 * 1024


class AssemblyError(RuntimeError):
    """An upstream failure safe for logging without its request or response."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url: str, timeout: float) -> tuple[int, bytes]:
    """Use a transport that does not log URLs or forward credentials on redirects."""
    started = time.monotonic()
    request = Request(url, headers={'User-Agent': 'law-monitor/0.1', 'Accept': '*/*'})
    with build_opener(NoRedirect).open(request, timeout=timeout) as response:
        body = bytearray()
        while chunk := response.read1(65536):
            body.extend(chunk)
            if len(body) > MAX_BODY or time.monotonic() - started >= timeout:
                raise AssemblyError('Response exceeded size or time budget')
        return response.status, bytes(body)


def civil_date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('Expected an ISO civil date')
    return value


def official_identity(url: str, *, notice: bool) -> str:
    parsed = urlsplit(url)
    paths = NOTICE_PATHS if notice else {'/bill/billDetail.do'}
    host = 'pal.assembly.go.kr' if notice else 'likms.assembly.go.kr'
    key = 'lgsltPaId' if notice else 'billId'
    params = parse_qs(parsed.query, keep_blank_values=True)
    values = params.get(key, [])
    if (
        parsed.scheme != 'https'
        or parsed.netloc != host
        or parsed.path not in paths
        or parsed.fragment
        or set(params) != {key}
        or len(values) != 1
        or not re.fullmatch(r'[A-Za-z0-9_]+', values[0])
        or any(c.isspace() for c in url)
    ):
        raise ValueError('Invalid official Assembly detail link')
    return values[0]


class DetailTable(HTMLParser):
    """Read the labeled cells on the official notice details page."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('th', 'td') and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


class AssemblyClient:
    def __init__(self, key: str | None, *, fetch=fetch, page_size: int = 1000, attempts: int = 3, sleep=time.sleep):
        if not 1 <= page_size <= 1000 or not 1 <= attempts <= 3:
            raise ValueError('Invalid Assembly page size or retry budget')
        self.key, self.fetch, self.page_size, self.sleep = key, fetch, page_size, sleep
        self.attempts = attempts
        self.begin(50)

    def begin(self, seconds: int) -> None:
        self.deadline = time.monotonic() + seconds
        self.remaining = {'list': 32, 'detail': 40}

    def _get(self, label: str, url: str, budget: str) -> bytes:
        for attempt in range(self.attempts):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or self.remaining[budget] <= 0:
                raise AssemblyError(f'{label}: request budget exhausted')
            self.remaining[budget] -= 1
            status, body = None, b''
            try:
                status, body = self.fetch(url, min(8.0, remaining))
            except HTTPError as exc:
                status, body = exc.code, b''
                exc.close()
            except (URLError, OSError) as exc:
                failure = type(exc).__name__
            if status is not None:
                if 200 <= status < 300:
                    if self.key and self.key.encode() in body:
                        raise AssemblyError(f'{label}: credential echoed in response')
                    return body
                failure = f'HTTP {status}'
                if status < 500:
                    raise AssemblyError(f'{label}: {failure}')
            delay = 0.25 * (2**attempt)
            if attempt + 1 == self.attempts or time.monotonic() + delay >= self.deadline:
                raise AssemblyError(f'{label}: {failure}')
            self.sleep(delay)
        raise AssertionError('Unreachable retry state')

    def page(self, code: str, age: int, index: int) -> tuple[int, list[dict]]:
        if not self.key:
            raise AssemblyError('Missing LAW_ASSEMBLY_KEY')
        if code not in (BILLS, ACTIVE, CLOSED):
            raise ValueError('Unknown Assembly endpoint')
        params = {'KEY': self.key, 'Type': 'json', 'pIndex': index, 'pSize': self.page_size}
        if code != ACTIVE:
            params['AGE'] = age
        body = self._get(code, BASE + code + '?' + urlencode(params), 'list')
        try:
            payload = json.loads(body)
            if 'RESULT' in payload:
                result = payload['RESULT']['CODE']
                if result == 'INFO-200':
                    return 0, []
                safe = result if result in ('ERROR-290', 'ERROR-337', 'ERROR-336', 'INFO-300') else 'upstream error'
                raise AssemblyError(f'{code}: {safe}')
            head, data = payload[code]
            total, result = head['head']
            count = total['list_total_count']
            rows = data['row']
            if (
                result['RESULT']['CODE'] != 'INFO-000'
                or type(count) is not int
                or count < 0
                or not isinstance(rows, list)
                or not all(isinstance(row, dict) for row in rows)
            ):
                raise ValueError('Invalid page')
        except ValueError, TypeError, KeyError, AttributeError:
            raise AssemblyError(f'{code}: invalid JSON response') from None
        return count, rows

    def pages(self, code: str, age: int) -> list[dict]:
        total, first = self.page(code, age, 1)
        pages = max(1, (total + self.page_size - 1) // self.page_size)
        if len(first) != min(total, self.page_size) or pages > self.remaining['list'] + 1:
            raise AssemblyError(f'{code}: incomplete list or page budget exceeded')
        rows = list(first)
        for index in range(2, pages + 1):
            count, page = self.page(code, age, index)
            if count != total or len(page) != min(total - len(rows), self.page_size):
                raise AssemblyError(f'{code}: incomplete or changing list')
            rows.extend(page)
        return rows

    def notice_start(self, url: str, bill_no: str, ends_on: str) -> str | None:
        official_identity(url, notice=True)
        page = DetailTable()
        try:
            page.feed(self._get('notice detail', url, 'detail').decode('utf-8'))
        except AssemblyError, UnicodeError:
            return None
        for header, row in zip(page.rows, page.rows[1:]):
            if '의안번호' not in header or '입법예고기간' not in header or len(row) != len(header):
                continue
            if row[header.index('의안번호')] != bill_no:
                continue
            parts = row[header.index('입법예고기간')]
            period = re.fullmatch(r'(\d{4}-\d{2}-\d{2})\s*[~～]\s*(\d{4}-\d{2}-\d{2})', parts)
            if period and period[2] == ends_on:
                try:
                    start = civil_date(period[1])
                except ValueError:
                    return None
                return start if start <= ends_on else None
        return None
