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
from contextlib import closing
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

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
from lawbot_common.erp_daily import LIMIT
from lawbot_common.erp_news import AGENCIES, Agency, official_url
from lawbot_common.erp_rules import CONFIRMATION_HOSTS, ErpRules

MAX_FILES = 20_000
MAX_BYTES = 25 * 1024 * 1024
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
WEB_URL = re.compile(r"""(?<![\w:/])(?:[a-z][a-z0-9+.-]*://|//)[^\s<>"']+""", re.IGNORECASE)
UNSAFE_SCHEME = re.compile(r'\b(?:javascript|vbscript|data|file|mailto)\s*:', re.IGNORECASE)
PRIVATE_TEXT = re.compile(r'\b(?:localhost|ip6-localhost)\b|[\w.+%\x27-]+@[\w.-]+\.[a-z]{2,}', re.IGNORECASE)
IP_TOKEN = re.compile(r'(?<![\w.])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f.:]*:[0-9a-f.:]+)(?!\w|\.\w)', re.I)
LOCAL_PATH = re.compile(r"""(?:^|[\s(<\[="':])(?:[a-z]:[\\/]|\\\\|~/|/)[^\s/\\<>=]""", re.IGNORECASE)


class ExportError(ValueError):
    """An export failure with a deliberately input-free diagnostic."""


def check_url(value: str, agency: Agency | None, *, law: bool) -> None:
    if any(char.isspace() or ord(char) < 32 or char in '\\\x7f' for char in value):
        raise ExportError('Invalid public URL')
    if law:
        validation.official_url(value)
        return
    if agency is not None:
        if official_url(value, agency):
            return
    else:
        url = urlsplit(value)
        if url.scheme == 'https' and (
            url.netloc in CONFIRMATION_HOSTS or (url.netloc == 'github.com' and url.path.startswith('/legalize-kr/'))
        ):
            return
    raise ExportError('Unapproved public URL')


def check_strings(value, agency: Agency | None, *, law: bool = False, field: str = '') -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            check_strings(key, agency, law=law)
            check_strings(item, agency, law=law, field=key)
    elif isinstance(value, list):
        for item in value:
            check_strings(item, agency, law=law, field=field)
    elif isinstance(value, str):
        if field in URL_FIELDS:
            check_url(value, agency, law=law)
        text = unquote(value)
        if PRIVATE_TEXT.search(text) or UNSAFE_SCHEME.search(text):
            raise ExportError('Private or unsafe text in public response')
        for token in IP_TOKEN.finditer(text):
            try:
                address = ip_address(token[0].rstrip('.'))
            except ValueError:
                continue
            if not address.is_global:
                raise ExportError('Non-public IP address in public response')
        if field not in URL_FIELDS:
            for link in WEB_URL.finditer(text):
                check_url(link[0].rstrip(').,;'), agency, law=law)
            if LOCAL_PATH.search(WEB_URL.sub('', text)):
                raise ExportError('Local path in public response')
        else:
            url = urlsplit(value)
            if LOCAL_PATH.search(unquote(url.query)) or LOCAL_PATH.search(unquote(url.fragment)):
                raise ExportError('Local path in public URL')


def check_file(size: int, count: int) -> None:
    if size >= MAX_BYTES or count > MAX_FILES:
        raise ExportError('Static site exceeds Pages file limits')


def validate_build(dist: Path) -> int:
    if not all((dist / name).is_file() for name in ('index.html', 'licenses.txt')):
        raise ExportError('Frontend build must contain index.html and licenses.txt')
    if any((dist / name).exists() for name in ('data', '_headers')):
        raise ExportError('Frontend build contains a reserved export path')
    count = 0
    for folder, dirs, files in dist.walk():
        if FORBIDDEN.intersection(name.casefold() for name in dirs + files):
            raise ExportError('Frontend build contains Pages routing or functions')
        for name in files:
            path = folder / name
            if path.is_symlink() or not path.is_file():
                raise ExportError('Frontend build contains a non-regular file')
            count += 1
            check_file(path.stat().st_size, count)
    return count


async def get[M: BaseModel](
    client: httpx.AsyncClient, route: str, model: type[M], **params: str | int
) -> tuple[M, dict]:
    response = await client.get('/api/' + route, params=params)
    response.raise_for_status()
    if len(response.content) >= MAX_BYTES:
        raise ExportError('Public response exceeds Pages file size limit')
    parsed = model.model_validate_json(response.content, strict=True, extra='forbid')
    return parsed, parsed.model_dump(mode='json', exclude_unset=True)


async def all_pages[M: (ChangePage, HoldPage)](
    client: httpx.AsyncClient, route: str, model: type[M], page_size: int, **params: str
) -> dict:
    first, result = await get(client, route, model, page=1, page_size=page_size, **params)
    items = result['items']
    for page in range(1, max(1, (first.total + page_size - 1) // page_size) + 1):
        if page == 1:
            current = first
        else:
            current, payload = await get(client, route, model, page=page, page_size=page_size, **params)
            items.extend(payload['items'])
        if (
            current.total != first.total
            or current.page != page
            or current.page_size != page_size
            or len(current.items) != min(page_size, first.total - (page - 1) * page_size)
        ):
            raise ExportError('Inconsistent public pagination')
    if first.total != len(items) or len({item['id'] for item in items}) != len(items):
        raise ExportError('Incomplete public pagination')
    result['page_size'] = max(page_size, first.total)
    return result


async def write_data(app: FastAPI, data: Path, count: int) -> None:
    def save(path: str, payload: dict, *, agency: Agency | None = None, law: bool = False) -> None:
        nonlocal count
        check_strings(payload, agency, law=law)
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        count += 1
        check_file(len(raw), count)
        target = data / (path + '.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://calendar', trust_env=False
    ) as client:
        _, status = await get(client, 'law-calendar/status', CalendarStatus)
        save('law-calendar/status', status)
        index, payload = await get(client, 'law-calendar/months', CalendarIndex)
        save('law-calendar/months', payload)
        for month in index.months:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
                raise ExportError('Invalid public month')
            _, calendar = await get(client, 'law-calendar', CalendarMonth, month=month)
            save(f'law-calendar/{month}', calendar)
            changes = await all_pages(client, 'law-events', ChangePage, 100, month=month)
            save(f'law-events/{month}', changes, law=True)
            for item in changes['items']:
                key = item['id']
                if not re.fullmatch(r'[a-zA-Z0-9_-]+', key):
                    raise ExportError('Invalid public change ID')
                _, detail = await get(client, f'law-changes/{key}', PublicDetail)
                save(f'law-changes/{key}', detail, law=True)
            _, taxes = await get(client, 'tax-schedule', PublicTaxSchedule, month=month)
            save(f'tax-schedule/{month}', taxes, agency='nts')
        holds = await all_pages(client, 'law-holds', HoldPage, 20, type='effective')
        save('law-holds', holds, law=True)
        _, erp = await get(client, 'erp-updates', ErpUpdates)
        save('erp-updates', erp)
        for day in index.erpRuleDates:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
                raise ExportError('Invalid public ERP date')
            _, rules = await get(client, 'erp-updates/v2', ErpRules, asOf=day)
            save(f'erp-rules/{day}', rules)
        for agency in AGENCIES:
            news, payload = await get(client, 'erp-news/latest', ErpNews, agency=agency)
            if news.agency != agency:
                raise ExportError('Unexpected public news agency')
            save(f'erp-news/{agency}', payload, agency=news.agency)


def install_site(site: Path, out: Path) -> None:
    if not out.exists():
        site.rename(out)
        return
    if not out.is_dir() or out.is_symlink():
        raise ExportError('Export destination must be a real directory')
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
        raise ExportError('Atomic directory replacement is unavailable')
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def write_site(app: FastAPI, dist: Path, out: Path) -> None:
    """Render the app's public reads without exposing it over a network socket."""
    if out.is_symlink():
        raise ExportError('Export destination must be a real directory')
    dist, out = dist.resolve(), out.resolve()
    if out.is_relative_to(dist) or dist.is_relative_to(out) or Path.cwd().is_relative_to(out):
        raise ExportError('Export destination overlaps the build or working directory')
    validate_build(dist)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent, ignore_cleanup_errors=True) as tmp:
        site = Path(tmp) / 'site'
        shutil.copytree(dist, site, symlinks=True)
        count = validate_build(site) + 1
        check_file(len(HEADERS), count)
        (site / '_headers').write_text(HEADERS, encoding='utf-8')
        asyncio.run(write_data(app, site / 'data', count))
        install_site(site, out)


def main(argv: list[str]) -> None:
    from law_calendar.server import create_app

    parser = argparse.ArgumentParser(prog='law-calendar export', description='Export a static calendar site')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--erp-daily-feed', type=Path, required=True)
    parser.add_argument('--frontend-dist', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if any(path.resolve().is_relative_to(args.out.resolve()) for path in (args.database, args.erp_daily_feed)):
            raise ExportError('Export destination overlaps an input file')
        # All requests must see the same approval state and daily snapshot, even during a refresh.
        with TemporaryDirectory() as tmp:
            database, daily = Path(tmp) / 'calendar.db', Path(tmp) / 'daily.json'
            with read_database(args.database) as source, closing(sqlite3.connect(database)) as target:
                source.backup(target)
            with args.erp_daily_feed.open('rb') as source:
                snapshot = source.read(LIMIT + 1)
            if len(snapshot) > LIMIT:
                raise ExportError('ERP daily snapshot exceeds its size limit')
            daily.write_bytes(snapshot)
            write_site(create_app(database, daily_feed=daily), args.frontend_dist, args.out)
    except ExportError as exc:
        parser.exit(1, f'Calendar export failed: {exc}\n')
    except OSError, ValueError, sqlite3.Error, httpx.HTTPError:
        parser.exit(1, 'Calendar export failed: input or public response could not be validated or written\n')
