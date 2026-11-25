"""Deploy the locked public export and prune Pages history only after verifying production."""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from law_calendar.export import FORBIDDEN, exported_site, snapshot_app
from law_calendar.settings import (
    ACCOUNT_KEY,
    DAILY_KEY,
    DB_KEY,
    SERVICE,
    TOKEN_KEY,
    PagesSettings,
    load_environment,
    site_path,
)

STATUS_PATH = 'data/law-calendar/status.json'
API = 'https://api.cloudflare.com/client/v4'
ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
PROJECT = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
UPLOAD_TIMEOUT = 180
VERIFY_TIMEOUT = 120
CLEANUP_TIMEOUT = 120
PUBLISH_LOCK_TIMEOUT = UPLOAD_TIMEOUT + VERIFY_TIMEOUT + CLEANUP_TIMEOUT + 30
PER_PAGE = 25
ENV_LOG = logging.Logger('law_calendar.publish', logging.WARNING)


class PublishError(ValueError):
    """A fixed failure code safe to report without the underlying exception."""


class PublishParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PublishError('invalid_arguments')


@dataclass
class PublishResult:
    deployment_id: str | None = None
    deleted: int = 0
    files: int = 0

    def report(self, status: str, **detail: str) -> None:
        payload = {'status': status, 'deploymentId': self.deployment_id, 'deleted': self.deleted, 'files': self.files}
        print(json.dumps(payload | detail, separators=(',', ':')))


def stage_site(source: Path, target: Path) -> int:
    count = 0
    try:
        shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns('.*'))
        for parent, directories, files in target.walk(on_error=_raise_walk_error):
            for name in directories + files:
                path = parent / name
                if name.casefold() in FORBIDDEN or path.is_symlink():
                    raise PublishError('stage_failed')
            count += len(files)
    except OSError:
        raise PublishError('stage_failed') from None
    return count


def _raise_walk_error(error: OSError) -> None:
    raise error


def upload(settings: PagesSettings, dist: Path, stage: Path) -> str:
    scratch = stage.parent
    receipt = scratch / 'deploy.jsonl'
    node = shutil.which(os.environ.get('LAWBOT_CALENDAR_NODE') or 'node')
    wrangler = dist.resolve().parent / 'node_modules/wrangler/bin/wrangler.js'
    if node is None or not wrangler.is_file():
        raise PublishError('wrangler_unavailable')
    env = {
        'PATH': os.environ.get('PATH', os.defpath),
        'HOME': str(scratch),
        'TMPDIR': str(scratch),
        'XDG_CONFIG_HOME': str(scratch),
        'CI': 'true',
        ACCOUNT_KEY: settings.account,
        TOKEN_KEY: settings.token,
        'WRANGLER_SEND_METRICS': 'false',
        'WRANGLER_OUTPUT_FILE_PATH': str(receipt),
        'WRANGLER_LOG_PATH': str(scratch / 'logs'),
    }
    args = [
        str(Path(node).resolve()),
        str(wrangler),
        'pages',
        'deploy',
        str(stage),
        '--project-name',
        settings.project,
        '--branch',
        'main',
    ]
    try:
        with subprocess.Popen(
            args,
            cwd=stage,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                code = process.wait(timeout=UPLOAD_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Wrangler's entry point spawns another Node process.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                raise PublishError('upload_timeout') from None
    except OSError:
        raise PublishError('upload_failed') from None
    if code:
        raise PublishError('upload_failed')
    try:
        with receipt.open(encoding='utf-8') as stream:
            for line in stream:
                entry = json.loads(line)
                if (
                    isinstance(entry, dict)
                    and entry.get('type') == 'pages-deploy-detailed'
                    and entry.get('version') == 1
                    and entry.get('pages_project') == settings.project
                    and entry.get('environment') == 'production'
                ):
                    identifier = entry.get('deployment_id')
                    if isinstance(identifier, str) and ID.fullmatch(identifier):
                        return identifier
    except OSError, ValueError:
        raise PublishError('upload_receipt_invalid') from None
    raise PublishError('upload_receipt_invalid')


async def verify(client: httpx.AsyncClient, project: str, expected: bytes) -> None:
    try:
        async with asyncio.timeout(VERIFY_TIMEOUT):
            while True:
                try:
                    response = await client.get(
                        f'https://{project}.pages.dev/{STATUS_PATH}', headers={'Cache-Control': 'no-cache'}
                    )
                except httpx.RequestError:
                    await asyncio.sleep(2)
                    continue
                if response.status_code == 200 and response.content == expected:
                    return
                await asyncio.sleep(2)
    except TimeoutError:
        raise PublishError('verify_failed') from None


async def api_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError, ValueError:
        raise PublishError('cleanup_failed') from None
    if not isinstance(payload, dict) or payload.get('success') is not True or 'result' not in payload:
        raise PublishError('cleanup_failed')
    return payload


@dataclass(frozen=True)
class Deployment:
    id: str
    created: datetime
    production: bool
    successful: bool
    aliased: bool

    @classmethod
    def parse(cls, row: dict) -> Deployment:
        try:
            created = datetime.fromisoformat(row['created_on'])
            stage = row['latest_stage']
            identifier, environment = row['id'], row['environment']
            successful = stage['name'] == 'deploy' and stage['status'] == 'success'
            aliases = row.get('aliases')
            if not ID.fullmatch(identifier) or not created.tzinfo or environment not in ('production', 'preview'):
                raise ValueError
        except KeyError, TypeError, ValueError:
            raise PublishError('cleanup_failed') from None
        return cls(identifier, created, environment == 'production', successful, bool(aliases))


async def deployments(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> dict[str, Deployment]:
    found = {}
    page = 1
    while True:
        envelope = await api_request(client, 'GET', url, headers=headers, params={'page': page, 'per_page': PER_PAGE})
        rows = envelope['result']
        info = envelope.get('result_info', {})
        if not isinstance(rows, list) or not isinstance(info, dict):
            raise PublishError('cleanup_failed')
        for row in rows:
            item = Deployment.parse(row)
            if item.id in found:
                raise PublishError('cleanup_failed')
            found[item.id] = item
        pages, total = info.get('total_pages'), info.get('total_count')
        if type(total) is int and len(found) > total:
            raise PublishError('cleanup_failed')
        if type(pages) is int and page >= pages:
            if type(total) is int and len(found) != total:
                raise PublishError('cleanup_failed')
            return found
        if type(pages) is not int:
            if type(total) is int:
                if len(found) == total:
                    return found
            else:
                page_size = info.get('per_page', PER_PAGE)
                if type(page_size) is not int or page_size <= 0:
                    raise PublishError('cleanup_failed')
                if len(rows) < page_size:
                    return found
        if not rows:
            raise PublishError('cleanup_failed')
        page += 1


async def prune(client: httpx.AsyncClient, settings: PagesSettings, result: PublishResult, *, purge: bool) -> None:
    project = f'{API}/accounts/{settings.account}/pages/projects/{settings.project}'
    headers = {'Authorization': f'Bearer {settings.token}'}
    url = project + '/deployments'
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT):
            history = await deployments(client, url, headers)
            metadata = (await api_request(client, 'GET', project, headers=headers))['result']
            canonical = metadata.get('canonical_deployment') if isinstance(metadata, dict) else None
            if not isinstance(canonical, dict) or canonical.get('id') != result.deployment_id:
                raise PublishError('production_mismatch')
            current = history.get(canonical['id'])
            if current is None or not current.production or not current.successful:
                raise PublishError('production_mismatch')
            others = [item for item in history.values() if item.id != current.id]
            if any(item.production and item.created >= current.created for item in others):
                raise PublishError('production_mismatch')
            previous = (
                None
                if purge
                else max(
                    (item for item in others if item.production and item.successful),
                    key=lambda item: item.created,
                    default=None,
                )
            )
            for item in others:
                if item != previous:
                    params = {'force': 'true'} if not item.production and item.aliased else None
                    await api_request(client, 'DELETE', f'{url}/{item.id}', headers=headers, params=params)
                    result.deleted += 1
    except TimeoutError:
        raise PublishError('cleanup_timeout') from None


async def verify_and_prune(
    settings: PagesSettings,
    expected: bytes,
    result: PublishResult,
    *,
    purge: bool,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=10) as client:
        await verify(client, settings.project, expected)
        await prune(client, settings, result, purge=purge)


def main(argv: list[str], *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = PublishParser(prog='law-calendar publish', description='Export and publish the static calendar site')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--erp-daily-feed', type=Path)
    parser.add_argument('--frontend-dist', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--purge-history', action='store_true')
    result = PublishResult()
    # HTTPX's request logs include URLs, and the Pages API path contains the account ID.
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        args = parser.parse_args(argv)
        try:
            load_environment(ENV_LOG)
        except OSError, ValueError:
            raise PublishError('invalid_environment') from None
        database = args.database or os.environ.get(DB_KEY)
        daily_feed = args.erp_daily_feed or os.environ.get(DAILY_KEY)
        if not database or not daily_feed:
            raise PublishError('missing_inputs')
        try:
            database, daily_feed = Path(database).expanduser(), Path(daily_feed).expanduser()
            dist = Path(
                args.frontend_dist or os.environ.get('LAWBOT_CALENDAR_FRONTEND_DIST') or SERVICE / 'frontend/dist'
            ).expanduser()
            out = (args.out or site_path()).expanduser()
        except RuntimeError:
            raise PublishError('invalid_paths') from None
        with exported_site(
            snapshot_app(database, daily_feed),
            dist,
            out,
            protected=(database, daily_feed),
            lock_timeout=PUBLISH_LOCK_TIMEOUT,
        ) as (site, result.files):
            settings = PagesSettings.from_environment()
            if settings is None:
                result.report('skipped', reason='pages_not_configured')
                return 0
            if (
                not PROJECT.fullmatch(settings.project)
                or not re.fullmatch(r'[a-zA-Z0-9-]{1,32}', settings.account)
                or not re.fullmatch(r'[!-~]+', settings.token)
            ):
                raise PublishError('invalid_pages_config')
            with TemporaryDirectory(prefix='law-calendar-publish-', dir='/tmp') as directory:
                stage = Path(directory).resolve() / 'site'
                result.files = stage_site(site, stage)
                expected = (stage / STATUS_PATH).read_bytes()
                result.deployment_id = upload(settings, dist, stage)
                asyncio.run(verify_and_prune(settings, expected, result, purge=args.purge_history, transport=transport))
    except PublishError as exc:
        result.report('failed', error=str(exc))
        return 1
    except ValueError:
        result.report('failed', error='export_failed')
        return 1
    except OSError:
        result.report('failed', error='publish_io')
        return 1
    result.report('ok')
    return 0
