"""Build a static site from the public ASGI response contracts, then atomically install it."""

import argparse
import asyncio
import ctypes
import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from ipaddress import ip_address
from pathlib import Path
from socket import inet_aton
from tempfile import TemporaryDirectory
from urllib.parse import SplitResult, unquote, unquote_plus, urlsplit
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from pydantic import BaseModel, ValidationError

from law_calendar import validation
from law_calendar.db import read_database
from law_calendar.models import (
    CalendarIndex,
    CalendarMonth,
    CalendarStatus,
    ChangePage,
    ErpNews,
    ErpUpdates,
    HoldPage,
    PublicDetail,
    PublicTaxSchedule,
)
from law_calendar.publish_state import LockTimeout, locked_file, output_lock_path, private_directory
from lawbot_common.erp_daily import LIMIT
from lawbot_common.erp_news import AGENCIES, Agency, official_url
from lawbot_common.erp_rules import CONFIRMATION_HOSTS, ErpRules

MAX_FILES = 20_000
MAX_BYTES = 25 * 1024 * 1024
LOCK_TIMEOUT = 30.0
MARKER = '.law-calendar-site'
MARKER_CONTENT = b'law-calendar static site v1\n'
BUILD_MARKER = 'data/site-build.json'
BUILD_FILES = {'index.html', '404.html', 'licenses.txt'}
ASSET_SUFFIXES = {
    '.js',
    '.css',
    '.woff',
    '.woff2',
    '.ttf',
    '.otf',
    '.svg',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.avif',
    '.ico',
}
FORBIDDEN = {'_worker.js', '_routes.json', 'functions'}
HEADERS = """/*
  Content-Security-Policy: default-src 'self'; img-src 'self' data:; font-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
/data/*
  Cache-Control: public, max-age=0, must-revalidate
/assets/*
  Cache-Control: public, max-age=31536000, immutable
"""
URL_FIELDS = {'sourceUrl', 'officialUrl', 'officialHome', 'evidenceUrl', 'pdfUrl'}
WEB_URL = re.compile(r"""(?<![a-z0-9_:/])(?:[a-z][a-z0-9+.-]*://|//)[^\s<>"']+""", re.I)
EMAIL = re.compile(r"[\w.+%'-]+@[\w.-]+\.[a-z]{2,}", re.IGNORECASE)
LOCAL_NAME = re.compile(r'(?<![a-z0-9_-])(?:ip6-)?localhost(?![a-z0-9_-])', re.I | re.ASCII)
LOCAL_DOMAINS = {'local', 'localhost', 'localdomain', 'internal', 'lan', 'home', 'corp', 'private', 'intranet'}
IP_TOKEN = re.compile(
    r'(?<![a-z0-9_.])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f.:]*:[0-9a-f.:]+)(?![a-z0-9_]|\.[a-z0-9_])',
    re.I | re.ASCII,
)
LOCAL_PATH = re.compile(
    r"""(?<![a-z0-9_])file:|(?:^|[\s(<\[="'`:;,?&+#|])"""
    r"""(?:[a-z]:[/\\]|~/|/(?:Users|private|var|tmp|opt|home|etc|Volumes)/)""",
    re.I | re.ASCII,
)
ENCODED_SEPARATOR = re.compile(r'%(?:25)*(?:2f|5c)', re.I)


class ExportError(ValueError):
    """Only a logical artifact, schema location and fixed code may be reported."""

    def __init__(self, path: str, code: str, field: str = '$') -> None:
        super().__init__(f'{path} {field} {code}')


def link_code(value: str, url: SplitResult, agency: Agency | None, *, law: bool) -> str | None:
    if url.scheme != 'https':
        return 'url_scheme'
    if any(char.isspace() or ord(char) < 32 or char in '\\\x7f' for char in value):
        return 'url_path'
    if law:
        if url.netloc not in validation.HOSTS:
            return 'url_host'
        try:
            validation.official_url(value)
        except ValueError:
            return 'url_path'
    elif agency is not None:
        if not official_url(value, agency):
            return 'url_host'
    elif url.netloc == 'github.com':
        decoded = unquote(url.path)
        segments = [part for part in decoded.split('/') if part not in ('', '.')]
        if '..' in segments or ENCODED_SEPARATOR.search(url.path) or ENCODED_SEPARATOR.search(decoded):
            return 'url_path'
        if not segments or segments[0] != 'legalize-kr':
            return 'url_host'
    elif url.netloc not in CONFIRMATION_HOSTS:
        return 'url_host'
    return None


def private_ip(value: str, *, url_host: bool = False) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        # Browsers also recognize short, integer, octal and hexadecimal IPv4 hosts.
        if not url_host:
            return False
        try:
            address = ip_address(inet_aton(value))
        except OSError:
            return False
    return not address.is_global or address.is_multicast


def private_text_code(text: str, *, paths: bool = True) -> str | None:
    if EMAIL.search(text):
        return 'email'
    if LOCAL_NAME.search(text):
        return 'url_host'
    if any(private_ip(token[0].rstrip('.')) for token in IP_TOKEN.finditer(text)):
        return 'private_ip'
    if paths and LOCAL_PATH.search(text):
        return 'local_path'
    return None


def url_text_code(url: SplitResult) -> str | None:
    return (
        private_text_code(unquote(url.path), paths=False)
        or private_text_code(unquote_plus(url.query))
        or private_text_code(unquote(url.fragment))
    )


def text_code(value: str) -> str | None:
    start = 0
    for link in WEB_URL.finditer(value):
        if code := private_text_code(unquote(value[start : link.start()])):
            return code
        try:
            url = urlsplit(link[0].rstrip(').,;'))
        except ValueError:
            return 'url_host'
        if url.scheme == 'file':
            return 'local_path'
        host = unquote(url.hostname or '').lower().rstrip('.')
        if private_ip(host, url_host=True):
            return 'private_ip'
        if (
            ('.' not in host and ':' not in host)
            or host.rsplit('.', 1)[-1] in LOCAL_DOMAINS
            or host.endswith('.home.arpa')
        ):
            return 'url_host'
        if code := private_text_code(unquote(url.netloc), paths=False) or url_text_code(url):
            return code
        start = link.end()
    return private_text_code(unquote(value[start:]))


def check_strings(value, path: str, agency: Agency | None, *, law: bool, field: str = '$', link: bool = False) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            check_strings(item, path, agency, law=law, field=f'{field}.{key}', link=key in URL_FIELDS)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_strings(item, path, agency, law=law, field=f'{field}[{index}]', link=link)
    elif isinstance(value, str):
        if link:
            try:
                url = urlsplit(value)
            except ValueError as exc:
                raise ExportError(path, 'url_host', field) from exc
            code = link_code(value, url, agency, law=law) or url_text_code(url)
        else:
            code = text_code(value)
        if code:
            raise ExportError(path, code, field)


def check_file(path: str, size: int, count: int) -> None:
    if size >= MAX_BYTES or count > MAX_FILES:
        raise ExportError(path, 'limit')


def build_files(dist: Path) -> list[Path]:
    pending = list(dist.iterdir())
    if not BUILD_FILES <= {path.name for path in pending}:
        raise ExportError('<frontend>', 'build')
    files = []
    while pending:
        path = pending.pop()
        relative = path.relative_to(dist)
        asset = relative.parts[0] == 'assets'
        if path.is_symlink() or path.name.casefold() in FORBIDDEN:
            raise ExportError('<frontend>', 'build')
        if asset and path.is_dir():
            pending.extend(path.iterdir())
        elif path.is_file() and (
            relative.as_posix() in BUILD_FILES or (asset and len(relative.parts) > 1 and path.suffix in ASSET_SUFFIXES)
        ):
            files.append(relative)
            check_file('<frontend>', path.stat().st_size, len(files))
        else:
            raise ExportError('<frontend>', 'build')
    return files


def schema_error(path: str, errors, *, response: bool = False) -> ExportError:
    error = errors[0]
    loc = error['loc'][int(response) :]
    # Extra property names are input, not part of the public schema.
    if error['type'] == 'extra_forbidden':
        loc = loc[:-1]
    field = '$' + ''.join(f'[{part}]' if isinstance(part, int) else f'.{part}' for part in loc)
    return ExportError(path, 'schema', field)


async def get[M: BaseModel](
    client: httpx.AsyncClient, route: str, model: type[M], *, path: str | None = None, **params: str | int
) -> tuple[M, dict]:
    path = path or f'{route}.json'
    try:
        response = await client.get('/api/' + route, params=params)
        response.raise_for_status()
    except ResponseValidationError as exc:
        raise schema_error(path, exc.errors(), response=True) from exc
    except ValidationError as exc:
        raise schema_error(path, exc.errors(include_input=False)) from exc
    except Exception as exc:
        # Endpoint failures may carry private inputs in their tracebacks.
        raise ExportError(path, 'io') from exc
    check_file(path, len(response.content), 0)
    try:
        parsed = model.model_validate_json(response.content, strict=True, extra='forbid')
    except ValidationError as exc:
        raise schema_error(path, exc.errors(include_input=False)) from exc
    return parsed, parsed.model_dump(mode='json', exclude_unset=True)


async def all_pages[M: (ChangePage, HoldPage)](
    client: httpx.AsyncClient, route: str, model: type[M], page_size: int, *, path: str, **params: str
) -> dict:
    first, result = await get(client, route, model, path=path, page=1, page_size=page_size, **params)
    items = result['items']
    for page in range(1, max(1, (first.total + page_size - 1) // page_size) + 1):
        if page == 1:
            current = first
        else:
            current, payload = await get(client, route, model, path=path, page=page, page_size=page_size, **params)
            items.extend(payload['items'])
        if (
            current.total != first.total
            or current.page != page
            or current.page_size != page_size
            or len(current.items) != min(page_size, first.total - (page - 1) * page_size)
        ):
            raise ExportError(path, 'schema', '$.items')
    if first.total != len(items) or len({item['id'] for item in items}) != len(items):
        raise ExportError(path, 'schema', '$.items')
    result['page_size'] = max(page_size, first.total)
    return result


async def write_data(app: FastAPI, data: Path, count: int) -> int:
    def save(path: str, payload: dict, *, agency: Agency | None = None, law: bool = False) -> None:
        nonlocal count
        check_strings(payload, path, agency, law=law)
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        count += 1
        check_file(path, len(raw), count)
        target = data / path
        with filesystem(path):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://calendar', trust_env=False
    ) as client:
        _, status = await get(client, 'law-calendar/status', CalendarStatus)
        save('law-calendar/status.json', status)
        index, payload = await get(client, 'law-calendar/months', CalendarIndex)
        save('law-calendar/months.json', payload)
        for slot, month in enumerate(index.months):
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
                raise ExportError('law-calendar/months.json', 'schema', f'$.months[{slot}]')
            path = f'law-calendar/{month}.json'
            _, calendar = await get(client, 'law-calendar', CalendarMonth, path=path, month=month)
            save(path, calendar)
            path = f'law-events/{month}.json'
            changes = await all_pages(client, 'law-events', ChangePage, 100, path=path, month=month)
            save(path, changes, law=True)
            for position, item in enumerate(changes['items']):
                key = item['id']
                if not re.fullmatch(r'[a-zA-Z0-9_-]+', key):
                    raise ExportError(path, 'schema', f'$.items[{position}].id')
                _, detail = await get(client, f'law-changes/{key}', PublicDetail)
                save(f'law-changes/{key}.json', detail, law=True)
            path = f'tax-schedule/{month}.json'
            _, taxes = await get(client, 'tax-schedule', PublicTaxSchedule, path=path, month=month)
            save(path, taxes, agency='nts')
        holds = await all_pages(client, 'law-holds', HoldPage, 20, path='law-holds.json', type='effective')
        save('law-holds.json', holds, law=True)
        _, erp = await get(client, 'erp-updates', ErpUpdates)
        save('erp-updates.json', erp)
        for slot, day in enumerate(index.erpRuleDates):
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
                raise ExportError('law-calendar/months.json', 'schema', f'$.erpRuleDates[{slot}]')
            path = f'erp-rules/{day}.json'
            _, rules = await get(client, 'erp-updates/v2', ErpRules, path=path, asOf=day)
            save(path, rules)
        for agency in AGENCIES:
            path = f'erp-news/{agency}.json'
            news, payload = await get(client, 'erp-news/latest', ErpNews, path=path, agency=agency)
            if news.agency != agency:
                raise ExportError(path, 'schema', '$.agency')
            save(path, payload, agency=agency)
    return count


@contextmanager
def filesystem(path: str) -> Iterator[None]:
    try:
        yield
    except OSError as exc:
        raise ExportError(path, 'io') from exc


def replaceable(out: Path) -> bool:
    if out.is_symlink():
        raise ExportError('<output>', 'destination')
    if not out.exists():
        return False
    marker = out / MARKER
    if out.is_dir() and not marker.is_symlink() and marker.is_file():
        with marker.open('rb') as stream:
            if stream.read(len(MARKER_CONTENT) + 1) == MARKER_CONTENT:
                return True
    raise ExportError('<output>', 'destination')


@contextmanager
def output_lock(timeout: float | None = None) -> Iterator[None]:
    # Keep the lock file: unlinking it would let writers lock different inodes.
    try:
        with locked_file(output_lock_path(), LOCK_TIMEOUT if timeout is None else timeout):
            yield
    except LockTimeout:
        raise ExportError('<output>', 'lock_timeout') from None


def install_site(site: Path, out: Path) -> None:
    if not replaceable(out):
        site.rename(out)
        return
    # POSIX rename cannot replace a nonempty directory. Never fall back to two renames.
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        swap = libc.renamex_np
        swap.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = swap(os.fsencode(site), os.fsencode(out), 0x00000002)  # RENAME_SWAP
    elif sys.platform == 'linux' and hasattr(libc, 'renameat2'):
        swap = libc.renameat2
        swap.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = swap(-100, os.fsencode(site), -100, os.fsencode(out), 2)  # AT_FDCWD, RENAME_EXCHANGE
    else:
        raise ExportError('<output>', 'io')
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


@contextmanager
def exported_site(
    source: AbstractContextManager[FastAPI],
    dist: Path,
    out: Path,
    *,
    protected: tuple[Path, ...] = (),
    lock_timeout: float | None = None,
) -> Iterator[tuple[Path, int]]:
    """Install the public site and retain the output lock while a caller uses it."""
    if out.is_symlink():
        raise ExportError('<output>', 'destination')
    with filesystem('<output>'):
        out = out.resolve()
        if any(path.resolve().is_relative_to(out) for path in protected):
            raise ExportError('<output>', 'destination')
        state = private_directory()
        if out.is_relative_to(state) or state.is_relative_to(out):
            raise ExportError('<output>', 'destination')
    with filesystem('<frontend>'):
        dist = dist.resolve()
    if out.is_relative_to(dist) or dist.is_relative_to(out) or Path.cwd().is_relative_to(out):
        raise ExportError('<output>', 'destination')
    with filesystem('<output>'):
        out.parent.mkdir(parents=True, exist_ok=True)
        with output_lock(lock_timeout):
            replaceable(out)
            with filesystem('<frontend>'):
                files = build_files(dist)
            with source as app, TemporaryDirectory(dir=out.parent, ignore_cleanup_errors=True) as tmp:
                site = Path(tmp) / 'site'
                for relative in files:
                    target = site / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with filesystem('<frontend>'):
                        shutil.copyfile(dist / relative, target)
                    check_file('<frontend>', target.stat().st_size, len(files))
                count = len(files)
                for name, content in (('_headers', HEADERS.encode()), (MARKER, MARKER_CONTENT)):
                    count += 1
                    check_file(name, len(content), count)
                    (site / name).write_bytes(content)
                count = asyncio.run(write_data(app, site / 'data', count))
                count += 1
                marker = json.dumps({'id': uuid4().hex}, separators=(',', ':')).encode()
                check_file(BUILD_MARKER, len(marker), count)
                (site / BUILD_MARKER).write_bytes(marker)
                install_site(site, out)
            yield out, count - 1  # The installation marker is not public.


@contextmanager
def snapshot_app(database: Path, daily_feed: Path) -> Iterator[FastAPI]:
    from law_calendar.server import create_app

    # All requests must see the same approval state and daily snapshot, even during a refresh.
    with TemporaryDirectory() as tmp:
        db, daily = Path(tmp) / 'calendar.db', Path(tmp) / 'daily.json'
        with filesystem('<database>'):
            try:
                with read_database(database) as source, closing(sqlite3.connect(db)) as target:
                    source.backup(target)
            except sqlite3.Error as exc:
                raise ExportError('<database>', 'io') from exc
            except ValueError as exc:
                raise ExportError('<database>', 'schema') from exc
        with filesystem('<erp-daily-feed>'):
            with daily_feed.open('rb') as source:
                snapshot = source.read(LIMIT + 1)
            if len(snapshot) > LIMIT:
                raise ExportError('<erp-daily-feed>', 'limit')
            daily.write_bytes(snapshot)
        yield create_app(db, daily_feed=daily)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog='law-calendar export', description='Export a static calendar site')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--erp-daily-feed', type=Path, required=True)
    parser.add_argument('--frontend-dist', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with exported_site(
            snapshot_app(args.database, args.erp_daily_feed),
            args.frontend_dist,
            args.out,
            protected=(args.database, args.erp_daily_feed),
        ):
            return
    except ExportError as exc:
        parser.exit(1, f'Calendar export failed: {exc}\n')
    except ValueError:
        parser.exit(1, 'Calendar export failed: <export> $ schema\n')
