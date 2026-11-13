"""Collect link metadata from published Ministry of Finance and Economy sources."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from xml.etree import ElementTree

from source_hub import collector, robots
from source_hub.collector import FetchError
from source_hub.materials import Article
from source_hub.materials import documents as documents
from source_hub.moel import RssTree, published_date, read_response
from source_hub.store import Registry

HOST = 'https://mofe.go.kr'
RSS_MEDIA = {'text/xml', 'application/xml', 'application/rss+xml'}
POST_ID = r'MOSF_[0-9]{15}'
POST_LINK = re.compile(
    r"""javascript:fn_egov_select\((['"])(""" + POST_ID + r""")\1(?:,\s*(['"])(MOSFBBS_[0-9]{12})\3)?\);"""
)
LIST_QUERY = '/com/bbs/ComtnbbsList.do'
NEWS = '/nw/nes/nesdta.do'
NEWS_DETAIL = '/nw/nes/detailNesDtaView.do'
GENERAL_DETAIL = '/com/bbs/detailComtnbbsView.do'


@dataclass(frozen=True)
class Channel:
    board: int
    menu: int
    detail: str
    listing: str | None
    rss: bool = True
    notice_period: bool = False

    @property
    def board_id(self) -> str:
        return f'MOSFBBS_{self.board:012}'

    @property
    def board_path(self) -> str | None:
        return f'{self.listing}?bbsId={self.board_id}&menuNo={self.menu}' if self.listing else None

    @property
    def source_path(self) -> str:
        if self.rss:
            return f'/com/detailRssTagService.do?bbsId={self.board_id}'
        assert self.board_path is not None
        return self.board_path

    def link(self, post_id: str) -> str:
        if self.rss:
            query = urlencode({'searchBbsId': self.board_id, 'menuNo': self.menu, 'searchNttId': post_id})
        else:
            board_key = 'bbsId' if self.notice_period else 'searchBbsId1'
            query = urlencode({board_key: self.board_id, 'menuNo': self.menu, 'searchNttId1': post_id})
        return f'{HOST}{self.detail}?{query}'


CHANNELS = {
    'mofe-rss-press': Channel(28, 4010100, NEWS_DETAIL, NEWS),
    'mofe-rss-explanation': Channel(29, 4010200, NEWS_DETAIL, NEWS),
    'mofe-rss-notice': Channel(30, 4050100, NEWS_DETAIL, NEWS),
    'mofe-rss-policy': Channel(39, 5020200, GENERAL_DETAIL, LIST_QUERY),
    'mofe-rss-economy': Channel(44, 5020300, '/pl/policydta/pblictn/detailPblictnbbsView.do', None),
    'mofe-laws': Channel(59, 7080200, GENERAL_DETAIL, LIST_QUERY, rss=False),
    'mofe-notice': Channel(120, 7090200, '/lw/denm/detailTbDenmView.do', '/lw/denm/TbDenmList.do', rss=False),
    'mofe-tax-reform': Channel(
        55, 7050300, '/lw/lap/detailTbPrvntcView.do', '/lw/lap/TbPrvntcList.do', rss=False, notice_period=True
    ),
}


def parse_feed(data: bytes, channel: Channel) -> list[Article]:
    root = ElementTree.fromstring(data, parser=ElementTree.XMLParser(target=RssTree()))
    entries = root.findall('./channel/item')
    if root.tag != 'rss' or not 1 <= len(entries) <= 100:
        raise ValueError('Unexpected RSS structure or empty channel')
    articles: dict[str, Article] = {}
    for entry in entries:
        title = ' '.join(entry.findtext('title', '').split())
        link = entry.findtext('link', '').strip()
        url = urlsplit(link)
        params = parse_qsl(url.query, max_num_fields=3, keep_blank_values=True, strict_parsing=True)
        query = dict(params)
        post_id = query.get('searchNttId', '')
        if (
            not 1 <= len(title) <= 500
            or re.search(r'[\s\\\x00-\x1f\x7f]', link)
            or url.scheme not in {'http', 'https'}
            or url.netloc != 'mofe.go.kr'
            or url.path != channel.detail
            or url.fragment
            or len(params) != 3
            or query.get('searchBbsId') != channel.board_id
            or query.get('menuNo') != str(channel.menu)
            or not re.fullmatch(POST_ID, post_id)
        ):
            raise ValueError('RSS entry does not identify an official post in this channel')
        guid = entry.findtext('guid', '').strip() or None
        if guid and len(guid) > 1000:
            raise ValueError('RSS identifier too long')
        article = Article(
            post_id, title, channel.link(post_id), published_date(entry.findtext('pubDate', '').strip()), guid
        )
        if articles.setdefault(post_id, article) != article:
            raise ValueError('Conflicting RSS entries')
    return list(articles.values())


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
        self.second_page = False

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
            elif not self.depth and href == '?pageIndex=2':
                self.second_page = True
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
    mode = 'rss' if channel.rss else 'html'
    source, version, outcome = collector.source_plan(
        registry, source_id, 'mofe', mode, HOST + channel.source_path, plan=plan
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
        if channel.rss:
            return parse_feed(get(rules, channel.source_path, RSS_MEDIA), channel)
        return list(parse_board(get(rules, channel.source_path, {'text/html'}), channel).articles.values())

    def read_board(rules: list[str], feed: list[Article]) -> tuple[list[Article] | None, dict]:
        path = channel.board_path
        assert path is not None
        pages = 0
        try:
            first = parse_board(get(rules, path, {'text/html'}), channel)
            pages = 1
            listed = first.articles
            if first.second_page:
                second = parse_board(get(rules, path + '&pageIndex=2', {'text/html'}), channel).articles
                if second.keys() <= listed.keys() or any(
                    listed[key] != second[key] for key in listed.keys() & second.keys()
                ):
                    raise ValueError('Repeated page or conflicting board rows')
                listed = listed | second
                pages = 2
        except FetchError as exc:
            error = str(exc)
            return None, {
                'outcome': 'blocked' if collector.is_blocked(error) else 'error',
                'error': error,
                'pages': pages,
            }
        except ValueError:
            return None, {'outcome': 'error', 'error': 'invalid_board', 'pages': pages}
        only = listed.keys() - {article.upstream_id for article in feed}
        return list(listed.values()), {
            'outcome': 'ok',
            'pages': pages,
            'listed_count': len(listed),
            'board_only_count': len(only),
        }

    return collector.collect(
        registry,
        database,
        source,
        version,
        read_rules=read_rules,
        read_articles=read_articles,
        read_board=read_board if reconcile and channel.rss and channel.board_path else None,
        invalid_source='invalid_feed' if channel.rss else 'invalid_board',
        now=now,
        kst_days=plan is not None,
    )
