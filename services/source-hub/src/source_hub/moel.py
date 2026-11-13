"""Collect metadata from the three RSS channels published by the Ministry of Employment and Labor."""

import fcntl
import os
import re
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
from xml.etree import ElementTree

from source_hub import materials, moel_board, robots
from source_hub.materials import Article
from source_hub.materials import documents as documents
from source_hub.store import KST, Registry, Run

HOST = moel_board.HOST
ROBOTS = HOST + '/robots.txt'
USER_AGENT = robots.PRODUCT + '/1.0'
CHANNELS = {
    'moel-rss-notice': ('/rss/notice.do', '/news/notice/noticeView.do', '/news/notice/noticeList.do'),
    'moel-rss-policy': ('/rss/policy.do', '/policy/policydata/view.do', '/policy/policydata/list.do'),
    'moel-rss-lawinfo': ('/rss/lawinfo.do', '/info/lawinfo/lawmaking/view.do', '/info/lawinfo/lawmaking/list.do'),
}
ROBOTS_LIMIT = 512_000
FEED_LIMIT = 2_097_152
RSS_MEDIA = {'text/xml', 'application/xml', 'application/rss+xml'}
DC_DATE = '{http://purl.org/dc/elements/1.1/}date'


class FetchError(Exception):
    """A bounded fetch failure safe to persist in the run log."""


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


def is_blocked(error: str) -> bool:
    return error.startswith('robots_') or error in {'http_401', 'http_403', 'http_429'}


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


@contextmanager
def collect_lock(registry: Registry):
    db = registry.database.resolve()
    lock = db.with_name(db.name + '.moel.lock')
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def collect(
    registry: Registry, database: Path, source_id: str, *, now: datetime | None = None, reconcile: bool = False
) -> dict:
    if source_id not in CHANNELS:
        raise ValueError('지원되는 고용노동부 RSS 수집원 ID를 선택하세요')
    plan = registry.snapshot()
    source = next((row for row in plan['sources'] if row['source_id'] == source_id), None)
    if source is None or not source['effective_enabled']:
        return {'outcome': 'disabled'}
    feed_path, article_path, board_path = CHANNELS[source_id]
    feed_url = HOST + feed_path
    if not source['runnable'] or (
        source['group'],
        source['adapter'],
        source['mode'],
        source['source_url'],
        source['parser_version'],
    ) != (
        'erp',
        'moel',
        'rss',
        feed_url,
        '1',
    ):
        return {'outcome': 'unverified'}
    path = database.resolve()
    registry_db = registry.database.resolve()
    if path == registry_db or (path.exists() and registry_db.exists() and path.samefile(registry_db)):
        raise ValueError('등록부와 수집 자료 DB에는 별도 경로를 사용하세요')
    started = now or datetime.now(KST)
    with collect_lock(registry) as locked:
        if not locked:
            return {'outcome': 'busy'}
        materials.initialize(path)
        if previous := materials.latest_success(path, source_id):
            registry.record_run(previous)
        latest = registry.runs(source_id)
        if latest and (started - datetime.fromisoformat(latest[0]['started_at'])).total_seconds() < (
            source['interval_minutes'] * 60
        ):
            return {'outcome': 'not_due'}
        outcome, error = 'ok', None
        counts = {}
        articles: list[Article] = []
        board: list[Article] | None = None
        reconciliation = {}
        board_attempted = False
        try:
            rules = robots_rules()
            articles = parse_feed(checked_read(rules, feed_path, RSS_MEDIA), article_path)
        except FetchError as exc:
            error = str(exc)
            outcome = 'blocked' if is_blocked(error) else 'error'
        except ElementTree.ParseError, ValueError:
            error, outcome = 'invalid_feed', 'error'
        else:
            if reconcile:
                last = materials.last_board_attempt(path, source_id)
                board_attempted = last is None or (started - datetime.fromisoformat(last)).total_seconds() >= (
                    source['reconcile_interval_minutes'] * 60
                )
                if board_attempted:
                    board, reconciliation = reconcile_board(rules, board_path, article_path, articles)
                    if board is None:
                        outcome, error = 'partial', 'board_' + reconciliation['error']
                else:
                    reconciliation = {'outcome': 'not_due'}
        run = Run(
            run_id=uuid4().hex,
            source_id=source_id,
            config_version=plan['config_version'],
            started_at=started,
            finished_at=now or datetime.now(KST),
            outcome=outcome,
            new_count=0 if outcome == 'ok' else None,
            modified_count=0 if outcome == 'ok' else None,
            error=error,
        )
        if articles:
            run, counts = materials.save(
                path,
                articles,
                run,
                source['baseline_policy'],
                board=board,
                board_attempted=board_attempted,
            )
        registry.record_run(run)
    result = {'outcome': outcome, 'run_id': run.run_id, **counts}
    if error:
        result['error'] = error
    if reconciliation:
        result['reconciliation'] = reconciliation
    return result
