"""Collect metadata from the three RSS channels published by the Ministry of Employment and Labor."""

import fcntl
import os
import re
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from uuid import uuid4
from xml.etree import ElementTree

from source_hub import materials
from source_hub.materials import Article
from source_hub.materials import documents as documents
from source_hub.store import KST, Registry, Run

HOST = 'https://www.moel.go.kr'
ROBOTS = HOST + '/robots.txt'
USER_AGENT = 'LawbotSourceHub/1.0'
CHANNELS = {
    'moel-rss-notice': ('/rss/notice.do', '/news/notice/noticeView.do'),
    'moel-rss-policy': ('/rss/policy.do', '/policy/policydata/view.do'),
    'moel-rss-lawinfo': ('/rss/lawinfo.do', '/info/lawinfo/lawmaking/view.do'),
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
    except HTTPError as exc:
        exc.close()
        raise FetchError(f'http_{exc.code}') from exc
    except (URLError, OSError) as exc:
        raise FetchError('network_error') from exc
    return data


def access_failure(feed_url: str) -> str | None:
    try:
        lines = read_response(ROBOTS, {'text/plain'}, ROBOTS_LIMIT).decode('utf-8-sig').splitlines()
    except FetchError, UnicodeError:
        return 'robots_unavailable'
    if not any(re.match(r'^\s*user-agent\s*:\s*[^\s#]+', line, flags=re.I) for line in lines):
        return 'robots_invalid'
    rules = RobotFileParser()
    rules.parse(lines)
    if not rules.can_fetch(USER_AGENT, feed_url):
        return 'robots_denied'
    if rules.crawl_delay(USER_AGENT) or rules.request_rate(USER_AGENT):
        return 'robots_rate_review'
    return None


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
        url = urlsplit(link)
        query = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        if (
            not title
            or len(title) > 500
            or not link
            or len(link) > 1000
            or re.search(r'[\s\\\x00-\x1f\x7f]', link)
            or url.scheme != 'https'
            or url.netloc != 'www.moel.go.kr'
            or url.path != article_path
            or url.fragment
            or len(query) != 1
            or query[0][0] != 'bbs_seq'
            or not re.fullmatch(r'[0-9]{1,32}', query[0][1])
        ):
            raise ValueError('RSS entry does not link to the reviewed official board')
        upstream_id = query[0][1]
        guid = entry.findtext('guid', '').strip() or None
        if guid and len(guid) > 1000:
            raise ValueError('RSS identifier too long')
        article = Article(
            upstream_id,
            title,
            f'{HOST}{article_path}?bbs_seq={upstream_id}',
            published_date(entry.findtext(DC_DATE, entry.findtext('pubDate', '')).strip()),
            guid,
        )
        if articles.setdefault(upstream_id, article) != article:
            raise ValueError('Conflicting metadata for the same official post')
    return list(articles.values())


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


def collect(registry: Registry, database: Path, source_id: str, *, now: datetime | None = None) -> dict:
    if source_id not in CHANNELS:
        raise ValueError('지원되는 고용노동부 RSS 수집원 ID를 선택하세요')
    plan = registry.snapshot()
    source = next((row for row in plan['sources'] if row['source_id'] == source_id), None)
    if source is None or not source['effective_enabled']:
        return {'outcome': 'disabled'}
    feed_path, article_path = CHANNELS[source_id]
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
        outcome = 'blocked'
        error = access_failure(feed_url)
        counts = {}
        articles: list[Article] = []
        if error is None:
            try:
                articles = parse_feed(read_response(feed_url, RSS_MEDIA, FEED_LIMIT), article_path)
            except FetchError as exc:
                error = str(exc)
                outcome = 'blocked' if error in {'http_401', 'http_403', 'http_429'} else 'error'
            except ElementTree.ParseError, ValueError:
                error, outcome = 'invalid_feed', 'error'
            else:
                outcome = 'ok'
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
        if outcome == 'ok':
            run, counts = materials.save(path, articles, run, source['baseline_policy'])
        registry.record_run(run)
    return {'outcome': outcome, 'run_id': run.run_id, **(counts if error is None else {'error': error})}
