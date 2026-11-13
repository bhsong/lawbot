"""Collect private metadata from reviewed public National Tax Service pages."""

import hashlib
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

from source_hub import collector, robots
from source_hub.collector import FetchError
from source_hub.materials import Article
from source_hub.moel import read_response
from source_hub.store import Registry

HOST = 'https://www.nts.go.kr'
LIST = '/nts/na/ntt/selectNttList.do'
DETAIL = '/nts/na/ntt/selectNttInfo.do'
GUIDE = '/nts/cm/cntnts/cntntsView.do?mi=2304&cntntsId=238938'
BOARDS = {
    'nts-notice': (2207, 1011),
    'nts-rules': (2205, 1120),
    'nts-tax-references': (2306, 30014),
    'nts-withholding': (2425, 30013),
    'nts-tools': (2544, 1099),
}
GUIDE_TOPICS = {
    'nts-tax-02': '신고안내(책자)',
    'nts-tax-amendments': '개정세법 요약',
    'nts-developer-notices': '프로그램 개발자 설명회',
}
CHANNELS = {
    **{key: f'{LIST}?bbsId={board}&mi={menu}' for key, (menu, board) in BOARDS.items()},
    **dict.fromkeys(GUIDE_TOPICS, GUIDE),
}


class BoardParser(HTMLParser):
    def __init__(self, menu: int):
        super().__init__()
        self.menu = menu
        self.tables = 0
        self.in_wrapper = False
        self.in_table = self.in_body = self.in_row = False
        self.cell = ''
        self.post_id = self.title = ''
        self.posted: list[str] = []
        self.articles: dict[str, Article] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'div' and 'bbs_ListA' in (values.get('class') or '').split():
            self.in_wrapper = True
        elif tag == 'table' and self.in_wrapper:
            self.in_table = True
            self.tables += 1
        elif tag == 'tbody' and self.in_table:
            self.in_body = True
        elif tag == 'tr' and self.in_body:
            self.in_row = True
            self.post_id, self.title, self.posted = '', '', []
        elif tag == 'td' and self.in_row:
            self.cell = values.get('data-table') or ''
        elif tag == 'a' and self.in_row and self.cell == 'subject':
            if 'nttInfoBtn' in (values.get('class') or '').split():
                if self.post_id:
                    raise ValueError('Multiple post identifiers in a row')
                self.post_id = values.get('data-id') or ''
                self.title = ' '.join((values.get('title') or '').split())

    def handle_data(self, data):
        if self.in_row and self.cell == 'date':
            self.posted.append(data)

    def handle_endtag(self, tag):
        if tag == 'td':
            self.cell = ''
        elif tag == 'tr' and self.in_row:
            self.in_row = False
            if not re.fullmatch(r'[0-9]{1,16}', self.post_id) or not 1 <= len(self.title) <= 500:
                raise ValueError('Incomplete NTS board row')
            posted = ''.join(self.posted).strip()
            if posted and not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}\.?', posted):
                raise ValueError('Unexpected NTS date format')
            published = date.fromisoformat(posted.rstrip('.').replace('.', '-')).isoformat() if posted else None
            link = f'{HOST}{DETAIL}?mi={self.menu}&nttSn={self.post_id}'
            article = Article(self.post_id, self.title, link, published, None)
            if self.articles.setdefault(self.post_id, article) != article:
                raise ValueError('Conflicting NTS board rows')
        elif tag == 'tbody':
            self.in_body = False
        elif tag == 'table' and self.in_table:
            self.in_table = self.in_wrapper = False


def parse_board(data: bytes, menu: int) -> list[Article]:
    parser = BoardParser(menu)
    parser.feed(data.decode('utf-8-sig'))
    parser.close()
    if parser.tables != 1 or parser.in_table or parser.in_row or not 1 <= len(parser.articles) <= 30:
        raise ValueError('Unexpected NTS board or empty page')
    return list(parser.articles.values())


class GuideParser(HTMLParser):
    def __init__(self, topic: str):
        super().__init__()
        self.topic = topic
        self.depth = 0
        self.label: list[str] | None = None
        self.articles: dict[str, Article] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif 'totalWrap' in (values.get('class') or '').split():
                self.depth = 1
        elif tag == 'a' and self.depth:
            if (values.get('href') or '').startswith('/comm/nttFileDownload.do?fileKey='):
                self.label = []

    def handle_data(self, data):
        if self.label is not None:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag == 'div' and self.depth:
            self.depth -= 1
        elif tag == 'a' and self.label is not None:
            title = ' '.join(''.join(self.label).split())
            self.label = None
            if self.topic in title and 1 <= len(title) <= 500:
                post_id = hashlib.sha256(title.encode()).hexdigest()[:32]
                self.articles[post_id] = Article(post_id, title, HOST + GUIDE, None, None)


def parse_guide(data: bytes, source_id: str) -> list[Article]:
    parser = GuideParser(GUIDE_TOPICS[source_id])
    parser.feed(data.decode('utf-8-sig'))
    parser.close()
    if parser.depth or not 1 <= len(parser.articles) <= 20:
        raise ValueError('No confirmed document label on the NTS guide')
    return list(parser.articles.values())


def collect(
    registry: Registry,
    database: Path,
    source_id: str,
    *,
    now: datetime | None = None,
    reconcile: bool = False,
    plan: dict | None = None,
) -> dict:
    path = CHANNELS[source_id]
    source, version, outcome = collector.source_plan(registry, source_id, 'nts', 'html', HOST + path, plan=plan)
    if source is None:
        return {'outcome': outcome}

    def read_rules() -> list[str]:
        try:
            return read_response(HOST + '/robots.txt', {'text/plain'}, 512_000).decode('utf-8-sig').splitlines()
        except (FetchError, UnicodeError) as exc:
            raise FetchError('robots_unavailable') from exc

    def read_articles(rules: list[str]) -> list[Article]:
        if error := robots.access_failure(rules, path):
            raise FetchError(error)
        data = read_response(HOST + path, {'text/html'}, 2_097_152)
        return parse_board(data, BOARDS[source_id][0]) if source_id in BOARDS else parse_guide(data, source_id)

    return collector.collect(
        registry,
        database,
        source,
        version,
        read_rules=read_rules,
        read_articles=read_articles,
        invalid_source='invalid_board',
        now=now,
        kst_days=plan is not None,
    )
