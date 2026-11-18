"""Collect link metadata from published Ministry of Finance and Economy sources."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

from source_hub import collector, robots
from source_hub.collector import FetchError
from source_hub.materials import Article
from source_hub.materials import documents as documents
from source_hub.moel import read_response
from source_hub.store import Registry

HOST = 'https://mofe.go.kr'
POST_ID = r'MOSF_[0-9]{15}'
POST_LINK = re.compile(
    r"""javascript:fn_egov_select\((['"])(""" + POST_ID + r""")\1(?:,\s*(['"])(MOSFBBS_[0-9]{12})\3)?\);"""
)
LIST_QUERY = '/com/bbs/ComtnbbsList.do'
GENERAL_DETAIL = '/com/bbs/detailComtnbbsView.do'
PUBLICATION_PATH = (
    '/pl/policydta/pblictn/ComtBalbbsList.do?bbsId=MOSFBBS_000000000004&menuNo=5020300&policyCategory=PUB05'
)
PUBLICATION_DETAIL = '/pl/policydta/pblictn/detailPblictnbbsView.do'


@dataclass(frozen=True)
class Channel:
    board: int
    menu: int
    detail: str
    listing: str
    notice_period: bool = False
    publication: bool = False

    @property
    def board_id(self) -> str:
        return f'MOSFBBS_{self.board:012}'

    @property
    def board_path(self) -> str:
        if self.publication:
            return PUBLICATION_PATH
        return f'{self.listing}?bbsId={self.board_id}&menuNo={self.menu}'

    def link(self, post_id: str) -> str:
        board_key = 'bbsId' if self.notice_period else 'searchBbsId1'
        query = urlencode({board_key: self.board_id, 'menuNo': self.menu, 'searchNttId1': post_id})
        return f'{HOST}{self.detail}?{query}'


CHANNELS = {
    'mofe-laws': Channel(59, 7080200, GENERAL_DETAIL, LIST_QUERY),
    'mofe-notice': Channel(120, 7090200, '/lw/denm/detailTbDenmView.do', '/lw/denm/TbDenmList.do'),
    'mofe-tax-reform': Channel(
        55, 7050300, '/lw/lap/detailTbPrvntcView.do', '/lw/lap/TbPrvntcList.do', notice_period=True
    ),
    'mofe-publications': Channel(4, 5020300, PUBLICATION_DETAIL, PUBLICATION_PATH, publication=True),
}


class BoardParser(HTMLParser):
    def __init__(self, channel: Channel):
        super().__init__()
        self.channel = channel
        self.depth = 0
        self.lists = 0
        self.post_id = ''
        self.field = ''
        self.title: list[str] = []
        self.posted: list[str] = []
        self.articles: dict[str, Article] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'ul':
            if self.depth:
                self.depth += 1
            elif 'boardType3' in (attrs.get('class') or '').split():
                self.depth = 1
                self.lists += 1
        elif self.depth == 1 and tag == 'li':
            self.post_id, self.title, self.posted = '', [], []
        elif tag == 'a':
            href = attrs.get('href') or ''
            match = POST_LINK.fullmatch(href)
            if self.depth == 1 and match:
                if self.post_id or match[4] not in {None, self.channel.board_id}:
                    raise ValueError('Unexpected post or board identifier')
                self.post_id, self.field = match[2], 'title'
        elif self.depth == 1 and tag == 'span' and 'date' in (attrs.get('class') or '').split():
            self.field = 'date'

    def handle_data(self, data):
        if self.field == 'title':
            self.title.append(data)
        elif self.field == 'date':
            self.posted.append(data)

    def handle_endtag(self, tag):
        if (tag, self.field) in {('a', 'title'), ('span', 'date')}:
            self.field = ''
        if tag == 'ul' and self.depth:
            self.depth -= 1
        elif self.depth == 1 and tag == 'li':
            title, posted = ' '.join(''.join(self.title).split()), ''.join(self.posted).strip()
            if not self.post_id or not 1 <= len(title) <= 500:
                raise ValueError('Incomplete board row')
            if self.channel.notice_period:
                published = None
            else:
                if not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}\.?', posted):
                    raise ValueError('Invalid published date')
                published = date.fromisoformat(posted.rstrip('.').replace('.', '-')).isoformat()
            article = Article(self.post_id, title, self.channel.link(self.post_id), published, None)
            if self.articles.setdefault(self.post_id, article) != article:
                raise ValueError('Conflicting board rows')


def parse_board(data: bytes, channel: Channel) -> BoardParser:
    parser = BoardParser(channel)
    parser.feed(data.decode('utf-8-sig'))
    parser.close()
    if parser.lists != 1 or parser.depth or not 1 <= len(parser.articles) <= 30:
        raise ValueError('Unexpected board layout or empty list')
    return parser


class PublicationParser(HTMLParser):
    def __init__(self, channel: Channel):
        super().__init__()
        self.channel = channel
        self.link: str | None = None
        self.text: list[str] = []
        self.articles: dict[str, Article] = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.link = dict(attrs).get('href') or ''
            self.text = []

    def handle_data(self, data):
        if self.link is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag != 'a' or self.link is None:
            return
        title = ' '.join(''.join(self.text).split())
        if re.fullmatch(r'20\d{2}\s*간추린\s*개정세법', title):
            match = POST_LINK.fullmatch(self.link)
            if match and match[4] in {None, self.channel.board_id}:
                post = match[2]
                self.articles[post] = Article(post, title, self.channel.link(post), None, None)
        self.link = None


def collect(
    registry: Registry,
    database: Path,
    source_id: str,
    *,
    now: datetime | None = None,
    reconcile: bool = False,
    plan: dict | None = None,
) -> dict:
    channel = CHANNELS[source_id]
    source, version, outcome = collector.source_plan(
        registry, source_id, 'mofe', 'html', HOST + channel.board_path, plan=plan
    )
    if source is None:
        return {'outcome': outcome}

    def read_rules() -> list[str]:
        try:
            return read_response(HOST + '/robots.txt', {'text/plain'}, 512_000).decode('utf-8-sig').splitlines()
        except (FetchError, UnicodeError) as exc:
            raise FetchError('robots_unavailable') from exc

    def get(rules: list[str], path: str, media: set[str]) -> bytes:
        if error := robots.access_failure(rules, path):
            raise FetchError(error)
        return read_response(HOST + path, media, 2_097_152)

    def read_articles(rules: list[str]) -> list[Article]:
        if channel.publication:
            parser = PublicationParser(channel)
            parser.feed(get(rules, channel.board_path, {'text/html'}).decode('utf-8-sig'))
            parser.close()
            if not 1 <= len(parser.articles) <= 30:
                raise ValueError('Missing MOFE publication titles')
            return list(parser.articles.values())
        return list(parse_board(get(rules, channel.board_path, {'text/html'}), channel).articles.values())

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
