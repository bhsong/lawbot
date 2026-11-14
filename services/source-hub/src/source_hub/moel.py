"""Collect metadata from the three RSS channels published by the Ministry of Employment and Labor."""

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from source_hub import collector, moel_board, robots
from source_hub.collector import FetchError as FetchError
from source_hub.collector import is_blocked
from source_hub.materials import Article
from source_hub.materials import documents as documents
from source_hub.store import KST, Registry

HOST = moel_board.HOST
ROBOTS = HOST + '/robots.txt'
USER_AGENT = robots.PRODUCT + '/1.0'
CHANNELS = {
    'moel-rss-notice': ('/rss/notice.do', '/news/notice/noticeView.do', '/news/notice/noticeList.do'),
    'moel-rss-policy': ('/rss/policy.do', '/policy/policydata/view.do', '/policy/policydata/list.do'),
    'moel-rss-lawinfo': ('/rss/lawinfo.do', '/info/lawinfo/lawmaking/view.do', '/info/lawinfo/lawmaking/list.do'),
}
BOARDS = {
    'moel-rules': ('/info/lawinfo/instruction/list.do', '/info/lawinfo/instruction/view.do', 'bbs_seq'),
    'moel-interpretations': (
        '/info/publicdata/qnrinfo/qnrInfoList.do',
        '/info/publicdata/qnrinfo/qnrInfoView.do',
        'qnr_uid',
    ),
    'moel-publications': (
        '/info/publicdata/majorpublish/majorPublishList.do',
        '/info/publicdata/majorpublish/majorPublishView.do',
        'bbs_seq',
    ),
    'moel-press': ('/news/enews/report/enewsList.do', '/news/enews/report/enewsView.do', 'news_seq'),
}
ROBOTS_LIMIT = 512_000
FEED_LIMIT = 2_097_152
RSS_MEDIA = {'text/xml', 'application/xml', 'application/rss+xml'}
DC_DATE = '{http://purl.org/dc/elements/1.1/}date'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_response(url: str, media: set[str], limit: int) -> bytes:
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': ', '.join(sorted(media))})
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            if response.status != 200 or response.headers.get_content_type() not in media:
                raise FetchError('unexpected_response')
            data = response.read(limit + 1)
            if len(data) > limit:
                raise FetchError('response_too_large')
            declared = response.headers.get('Content-Length')
            if declared is not None and not response.headers.get('Transfer-Encoding'):
                if not re.fullmatch(r'[0-9]{1,12}', declared) or int(declared) != len(data):
                    raise FetchError('incomplete_response')
    except HTTPError as exc:
        exc.close()
        raise FetchError(f'http_{exc.code}') from exc
    except IncompleteRead as exc:
        raise FetchError('incomplete_response') from exc
    except (URLError, OSError) as exc:
        raise FetchError('network_error') from exc
    return data


def robots_rules() -> list[str]:
    try:
        return read_response(ROBOTS, {'text/plain'}, ROBOTS_LIMIT).decode('utf-8-sig').splitlines()
    except FetchError, UnicodeError:
        raise FetchError('robots_unavailable') from None


def checked_read(rules: list[str], path: str, media: set[str]) -> bytes:
    if error := robots.access_failure(rules, path):
        raise FetchError(error)
    return read_response(HOST + path, media, FEED_LIMIT)


class RssTree(ElementTree.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError('RSS must not contain a document type')


def published_date(value: str) -> str | None:
    if not value:
        return None
    try:
        published = datetime.fromisoformat(value)
    except ValueError:
        published = parsedate_to_datetime(value)
    if published.tzinfo is not None:
        published = published.astimezone(KST)
    return published.date().isoformat()


def parse_feed(data: bytes, article_path: str) -> list[Article]:
    root = ElementTree.fromstring(data, parser=ElementTree.XMLParser(target=RssTree()))
    entries = root.findall('./channel/item')
    if root.tag != 'rss' or not entries or len(entries) > 100:
        raise ValueError('Unexpected RSS structure or empty channel')
    articles = {}
    for entry in entries:
        title = ' '.join(entry.findtext('title', '').split())
        link = entry.findtext('link', '').strip()
        if not title or len(title) > 500:
            raise ValueError('Unexpected RSS title')
        upstream_id, link = moel_board.post_link(link, article_path)
        guid = entry.findtext('guid', '').strip() or None
        if guid and len(guid) > 1000:
            raise ValueError('RSS identifier too long')
        article = Article(
            upstream_id,
            title,
            link,
            published_date(entry.findtext(DC_DATE, entry.findtext('pubDate', '')).strip()),
            guid,
        )
        if articles.setdefault(upstream_id, article) != article:
            raise ValueError('Conflicting metadata for the same official post')
    return list(articles.values())


def reconcile_board(
    rules: list[str], board_path: str, article_path: str, articles: list[Article]
) -> tuple[list[Article] | None, dict]:
    page_count = 0
    try:
        first = moel_board.parse_board(checked_read(rules, board_path, {'text/html'}), HOST + board_path, article_path)
        page_count = 1
        listed = first.articles
        if first.has_second_page:
            page_path = board_path + '?pageIndex=2'
            second = moel_board.parse_board(
                checked_read(rules, page_path, {'text/html'}), HOST + page_path, article_path
            )
            if second.articles.keys() <= listed.keys() or any(
                listed[key] != second.articles[key] for key in listed.keys() & second.articles.keys()
            ):
                raise ValueError('Repeated page or conflicting board rows')
            listed = listed | second.articles
            page_count = 2
    except FetchError as exc:
        error = str(exc)
        return None, {'outcome': 'blocked' if is_blocked(error) else 'error', 'error': error, 'pages': page_count}
    except ValueError:
        return None, {'outcome': 'error', 'error': 'invalid_board', 'pages': page_count}
    missing = listed.keys() - {article.upstream_id for article in articles}
    return list(listed.values()), {
        'outcome': 'ok',
        'pages': page_count,
        'listed_count': len(listed),
        'board_only_count': len(missing),
    }


def collect(
    registry: Registry,
    database: Path,
    source_id: str,
    *,
    now: datetime | None = None,
    reconcile: bool = False,
    plan: dict | None = None,
) -> dict:
    if source_id in BOARDS:
        path, detail, key = BOARDS[source_id]
        source, version, outcome = collector.source_plan(registry, source_id, 'moel', 'html', HOST + path, plan=plan)
        if source is None:
            return {'outcome': outcome}

        def read_list(rules: list[str]) -> list[Article]:
            parsed = moel_board.parse_board(checked_read(rules, path, {'text/html'}), HOST + path, detail, key)
            return list(parsed.articles.values())

        return collector.collect(
            registry,
            database,
            source,
            version,
            read_rules=robots_rules,
            read_articles=read_list,
            invalid_source='invalid_board',
            now=now,
            kst_days=plan is not None,
        )
    if source_id not in CHANNELS:
        raise ValueError('지원되는 고용노동부 RSS 수집원 ID를 선택하세요')
    feed_path, article_path, board_path = CHANNELS[source_id]
    source, version, outcome = collector.source_plan(registry, source_id, 'moel', 'rss', HOST + feed_path, plan=plan)
    if source is None:
        return {'outcome': outcome}

    def read_articles(rules: list[str]) -> list[Article]:
        return parse_feed(checked_read(rules, feed_path, RSS_MEDIA), article_path)

    def read_board(rules: list[str], articles: list[Article]) -> tuple[list[Article] | None, dict]:
        return reconcile_board(rules, board_path, article_path, articles)

    return collector.collect(
        registry,
        database,
        source,
        version,
        read_rules=robots_rules,
        read_articles=read_articles,
        read_board=read_board if reconcile else None,
        now=now,
        kst_days=plan is not None,
    )
