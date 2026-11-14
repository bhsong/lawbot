"""Read metadata and the second-page link from the reviewed MOEL list layout."""

import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlsplit

from source_hub.materials import Article

HOST = 'https://www.moel.go.kr'


def post_link(link: str, article_path: str, key: str = 'bbs_seq') -> tuple[str, str]:
    url = urlsplit(link)
    query = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    if (
        not link
        or len(link) > 1000
        or re.search(r'[\s\\\x00-\x1f\x7f]', link)
        or url.scheme != 'https'
        or url.netloc != 'www.moel.go.kr'
        or url.path != article_path
        or url.fragment
        or len(query) != 1
        or query[0][0] != key
        or not re.fullmatch(r'[0-9]{1,32}', query[0][1])
    ):
        raise ValueError('Entry does not link to the reviewed official board')
    upstream_id = query[0][1]
    return upstream_id, f'{HOST}{article_path}?{key}={upstream_id}'


class BoardParser(HTMLParser):
    def __init__(self, url: str, article_path: str, key: str = 'bbs_seq'):
        super().__init__()
        self.url = url
        self.article_path = article_path
        self.key = key
        self.table_count = 0
        self.in_table = self.in_body = self.in_row = self.in_title = False
        self.cell = ''
        self.link = ''
        self.title: list[str] = []
        self.posted: list[str] = []
        self.articles: dict[str, Article] = {}
        self.has_second_page = False

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'table' and 'tstyle_list' in (fields.get('class') or '').split():
            self.table_count += 1
            self.in_table = True
        elif tag == 'tbody' and self.in_table:
            self.in_body = True
        elif tag == 'tr' and self.in_body:
            self.in_row = True
            self.link, self.title, self.posted = '', [], []
        elif tag == 'td' and self.in_row:
            self.cell = fields.get('aria-label') or ''
        elif tag == 'a':
            href = fields.get('href') or ''
            if self.in_row and self.cell == '제목':
                if self.link or not href:
                    raise ValueError('Unexpected title link')
                self.link = href
                self.in_title = True
            if not self.in_table and href == '?pageIndex=2':
                self.has_second_page = True

    def handle_endtag(self, tag):
        if tag == 'a':
            self.in_title = False
        elif tag == 'td':
            self.cell = ''
        elif tag == 'tr' and self.in_row:
            self.in_row = False
            title = ' '.join(''.join(self.title).split())
            posted = ''.join(self.posted).strip()
            if (
                not self.link
                or re.search(r'[\s\\\x00-\x1f\x7f]', self.link)
                or not title
                or len(title) > 500
                or not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}', posted)
            ):
                raise ValueError('Incomplete board row')
            upstream, link = post_link(urljoin(self.url, self.link), self.article_path, self.key)
            article = Article(upstream, title, link, date.fromisoformat(posted.replace('.', '-')).isoformat(), None)
            if self.articles.setdefault(upstream, article) != article:
                raise ValueError('Conflicting board rows')
        elif tag == 'tbody':
            self.in_body = False
        elif tag == 'table':
            self.in_table = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif self.in_row and self.cell in ('등록일', '게시일'):
            self.posted.append(data)


def parse_board(data: bytes, url: str, article_path: str, key: str = 'bbs_seq') -> BoardParser:
    parser = BoardParser(url, article_path, key)
    parser.feed(data.decode('utf-8-sig'))
    parser.close()
    if parser.table_count != 1 or parser.in_table or parser.in_row or not 1 <= len(parser.articles) <= 30:
        raise ValueError('Unexpected board table or empty list')
    return parser
