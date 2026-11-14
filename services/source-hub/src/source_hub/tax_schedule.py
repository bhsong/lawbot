"""Read the official NTS schedule table without fetching its external calendar links."""

import hashlib
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from lawbot_common.tax_schedule import NTS_HOST as HOST
from lawbot_common.tax_schedule import year_path
from source_hub.materials import Article

PAYMENT_PATH = '/nts/cm/cntnts/cntntsView.do?mi=12242&cntntsId=8631'
TOPICS = re.compile(r'원천세|지급명세서|연말정산')


class Tables(HTMLParser):
    def __init__(self, data: bytes):
        super().__init__()
        self.years: set[str] = set()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.feed(data.decode('utf-8-sig'))
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            url = urlsplit(dict(attrs).get('href') or '')
            if url.path == '/nts/ad/taxSchdul/selectCalender.do' and not url.netloc:
                self.years.update(parse_qs(url.query).get('taxYear', []))
        if tag == 'tr':
            self.row = []
        if tag == 'td' and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None and self.row is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse_year(data: bytes, year: int) -> list[Article]:
    page = Tables(data)
    if page.years != {str(year)}:
        raise ValueError('Unexpected NTS schedule year')
    source = HOST + year_path(year)
    results = {}
    for row in page.rows:
        if not 4 <= len(row) <= 5 or not all(re.fullmatch(r'[0-9]{1,2}', value) for value in row[:2]):
            continue
        month, day, title, period = row[:4]
        deadline = date(year, int(month), int(day)).isoformat()
        if not TOPICS.search(title + ' ' + period):
            continue
        if not 1 <= len(title) <= 300 or len(period) > 300:
            raise ValueError('Unexpected NTS schedule row')
        key = hashlib.sha256('\0'.join((deadline, title, period)).encode()).hexdigest()[:32]
        results[key] = Article(key, title, source, None, None, deadline, period or None)
    return list(results.values())


def payment_guide(data: bytes) -> list[Article]:
    rows = [row for row in Tables(data).rows if 2 <= len(row) <= 4 and TOPICS.search(' '.join(row))]
    if not 1 <= len(rows) <= 60:
        raise ValueError('Missing NTS payment guide table')
    evidence = hashlib.sha256(repr(rows).encode()).hexdigest()
    return [
        Article('submission-guide', '지급명세서 제출 안내', HOST + PAYMENT_PATH, None, None, evidence_hash=evidence)
    ]
