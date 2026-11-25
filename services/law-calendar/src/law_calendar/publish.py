"""Deploy the locked public export and prune Pages history only after verifying production."""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import FrameType

import httpx

from law_calendar import settings
from law_calendar.export import BUILD_MARKER, FORBIDDEN, exported_site, snapshot_app
from law_calendar.publish_state import checked_file, private_directory, trusted_directory
from lawbot_common import envfile
from lawbot_common.calendar_publish import DEPLOYMENT_ID, PUBLISH_DEADLINE
from lawbot_common.process import SYSTEM_PATH, signal_group

API = 'https://api.cloudflare.com/client/v4'
PROJECT = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
UPLOAD_TIMEOUT = 180
VERIFY_TIMEOUT = 120
CLEANUP_TIMEOUT = 120
PUBLISH_LOCK_TIMEOUT = 450
PER_PAGE = 25
STAGES = {'queued', 'initialize', 'clone_repo', 'build', 'deploy'}


class PublishError(ValueError):
    """A fixed failure code safe to report without the underlying exception."""


class PublishInterrupted(BaseException):
    """The command deadline expired or its caller asked it to terminate."""


class Deadline:
    interrupted = False
    starting = False
    killed = False
    process: subprocess.Popen | None = None

    def interrupt(self, _signum: int, _frame: FrameType | None) -> None:
        if self.interrupted:
            return
        self.interrupted = True
        if not self.starting:
            self.kill_child()
            raise PublishInterrupted

    def kill_child(self) -> None:
        if self.process is not None and self.process.returncode is None and not self.killed:
            signal_group(self.process.pid, signal.SIGKILL)
            self.killed = True

    @contextmanager
    def child(self, args: list[str], work: Path, env: dict[str, str]) -> Iterator[subprocess.Popen]:
        # A signal during Popen must not arrive before the child PID can be collected.
        self.starting = True
        try:
            self.process = subprocess.Popen(
                args,
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.starting = False
            if self.interrupted:
                raise PublishInterrupted
            yield self.process
        finally:
            self.starting = True
            try:
                if self.process is not None and self.process.returncode is None:
                    self.kill_child()
                    self.process.wait()
                self.process = None
            finally:
                self.starting = False
            if self.interrupted:
                raise PublishInterrupted


@contextmanager
def time_limit() -> Iterator[Deadline]:
    deadline = Deadline()
    previous_alarm = signal.signal(signal.SIGALRM, deadline.interrupt)
    previous_term = signal.signal(signal.SIGTERM, deadline.interrupt)
    signal.setitimer(signal.ITIMER_REAL, PUBLISH_DEADLINE)
    try:
        yield deadline
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGALRM, previous_alarm)


@dataclass(frozen=True, repr=False)
class PagesSettings:
    project: str
    account: str
    token: str

    @classmethod
    def read(cls, project: str) -> PagesSettings:
        try:
            with checked_file(settings.SERVICE / 'pages.env', os.O_RDONLY) as fd:
                with open(fd, encoding='utf-8', closefd=False) as stream:
                    values = envfile.parse(stream.read(), 'pages.env', settings.PAGES_KEYS.__contains__)
            account, token = (values.get(key, '') for key in settings.PAGES_KEYS)
            if not re.fullmatch(r'[a-zA-Z0-9-]{1,32}', account) or not re.fullmatch(r'[!-~]+', token):
                raise ValueError
        except OSError, ValueError:
            raise PublishError('invalid_pages_credentials') from None
        if not PROJECT.fullmatch(project):
            raise PublishError('invalid_pages_config')
        return cls(project, account, token)

    @property
    def url(self) -> str:
        return f'{API}/accounts/{self.account}/pages/projects/{self.project}'

    @property
    def headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.token}'}


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


def wrangler_command(config: dict[str, str]) -> tuple[Path, Path]:
    node = Path(config.get('LAWBOT_CALENDAR_NODE', ''))
    wrangler = settings.SERVICE / 'frontend/node_modules/wrangler/bin/wrangler.js'
    try:
        if not node.is_absolute():
            raise OSError
        node = node.resolve(strict=True)
        trusted_directory(node.parent, require_owner=False)
        trusted_directory(wrangler.parent)
        info = node.stat()
        if (
            ':' in str(node.parent)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid not in (0, os.getuid())
            or info.st_mode & 0o022
            or not os.access(node, os.X_OK)
        ):
            raise OSError
        with checked_file(wrangler, os.O_RDONLY, private=False):
            pass
    except OSError, RuntimeError, ValueError:
        raise PublishError('wrangler_unavailable') from None
    return node, wrangler


def upload(pages: PagesSettings, command: tuple[Path, Path], stage: Path, work: Path, deadline: Deadline) -> str:
    node, wrangler = command
    receipt = work / 'deploy.jsonl'
    env = {
        'PATH': f'{node.parent}:{SYSTEM_PATH}',
        'HOME': str(work),
        'TMPDIR': str(work),
        'XDG_CONFIG_HOME': str(work),
        'XDG_CACHE_HOME': str(work / 'cache'),
        'CI': 'true',
        settings.ACCOUNT_KEY: pages.account,
        settings.TOKEN_KEY: pages.token,
        'WRANGLER_SEND_METRICS': 'false',
        'WRANGLER_WRITE_LOGS': 'false',
        'WRANGLER_CACHE_DIR': str(work / 'cache'),
        'WRANGLER_OUTPUT_FILE_PATH': str(receipt),
    }
    args = [
        str(node),
        str(wrangler),
        'pages',
        'deploy',
        str(stage),
        '--project-name',
        pages.project,
        '--branch',
        'main',
    ]
    try:
        with deadline.child(args, work, env) as process:
            try:
                code = process.wait(timeout=UPLOAD_TIMEOUT)
            except subprocess.TimeoutExpired:
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
                    and entry.get('pages_project') == pages.project
                    and entry.get('environment') == 'production'
                ):
                    identifier = entry.get('deployment_id')
                    if isinstance(identifier, str) and DEPLOYMENT_ID.fullmatch(identifier):
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
                        f'https://{project}.pages.dev/{BUILD_MARKER}', headers={'Cache-Control': 'no-cache'}
                    )
                except httpx.RequestError:
                    await asyncio.sleep(2)
                    continue
                if response.status_code == 200 and response.content == expected:
                    return
                await asyncio.sleep(2)
    except TimeoutError:
        raise PublishError('verify_failed') from None


async def api_request(client: httpx.AsyncClient, method: str, url: str, *, error='cleanup_failed', **kwargs) -> dict:
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError, ValueError:
        raise PublishError(error) from None
    if not isinstance(payload, dict) or payload.get('success') is not True or 'result' not in payload:
        raise PublishError(error)
    return payload


async def project_metadata(client: httpx.AsyncClient, pages: PagesSettings) -> dict:
    metadata = (await api_request(client, 'GET', pages.url, headers=pages.headers, error='project_mismatch'))['result']
    if (
        not isinstance(metadata, dict)
        or metadata.get('name') != pages.project
        or metadata.get('production_branch') != 'main'
    ):
        raise PublishError('project_mismatch')
    return metadata


async def check_canonical(client: httpx.AsyncClient, pages: PagesSettings, identifier: str) -> None:
    canonical = (await project_metadata(client, pages)).get('canonical_deployment')
    if not isinstance(canonical, dict) or canonical.get('id') != identifier:
        raise PublishError('production_mismatch')


@dataclass(frozen=True)
class Deployment:
    id: str
    created: datetime
    production: bool
    successful: bool
    aliased: bool

    @classmethod
    def parse(cls, row: object) -> Deployment:
        try:
            if not isinstance(row, dict):
                raise ValueError
            created = datetime.fromisoformat(row['created_on'])
            stage = row['latest_stage']
            identifier, environment = row['id'], row['environment']
            name, status = stage['name'], stage['status']
            aliases = row['aliases']
            if (
                not DEPLOYMENT_ID.fullmatch(identifier)
                or not created.tzinfo
                or environment not in ('production', 'preview')
                or name not in STAGES
                or not (status in ('failure', 'canceled') or (name == 'deploy' and status == 'success'))
                or (
                    aliases is not None
                    and (
                        not isinstance(aliases, list)
                        or any(not isinstance(alias, str) or not alias for alias in aliases)
                    )
                )
            ):
                raise ValueError
        except KeyError, TypeError, ValueError:
            raise PublishError('cleanup_failed') from None
        return cls(identifier, created, environment == 'production', status == 'success', bool(aliases))


async def deployments(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> dict[str, Deployment]:
    found: dict[str, Deployment] = {}
    total = None
    page = 1
    while True:
        envelope = await api_request(client, 'GET', url, headers=headers, params={'page': page, 'per_page': PER_PAGE})
        rows = envelope['result']
        info = envelope.get('result_info')
        if not isinstance(rows, list) or not isinstance(info, dict):
            raise PublishError('cleanup_failed')
        page_number, reported_total = info.get('page'), info.get('total_count')
        if (
            type(page_number) is not int
            or page_number != page
            or type(reported_total) is not int
            or reported_total < 0
            or (total is not None and total != reported_total)
        ):
            raise PublishError('cleanup_failed')
        total = reported_total
        pages = (total + PER_PAGE - 1) // PER_PAGE
        for key, expected in (('count', len(rows)), ('per_page', PER_PAGE), ('total_pages', pages)):
            if key in info and (type(info[key]) is not int or info[key] != expected):
                raise PublishError('cleanup_failed')
        if len(rows) != min(PER_PAGE, total - len(found)):
            raise PublishError('cleanup_failed')
        for row in rows:
            item = Deployment.parse(row)
            if item.id in found:
                raise PublishError('cleanup_failed')
            found[item.id] = item
        if len(found) == total:
            return found
        page += 1


async def prune(client: httpx.AsyncClient, pages: PagesSettings, identifier: str, result: PublishResult) -> None:
    url = pages.url + '/deployments'
    headers = pages.headers
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT):
            history = await deployments(client, url, headers)
            current = history.get(identifier)
            if current is None or not current.production or not current.successful:
                raise PublishError('production_mismatch')
            others = [item for item in history.values() if item.id != current.id]
            if any(item.production and item.created >= current.created for item in others):
                raise PublishError('production_mismatch')
            for item in others:
                await check_canonical(client, pages, identifier)
                params = {'force': 'true'} if not item.production and item.aliased else None
                await api_request(client, 'DELETE', f'{url}/{item.id}', headers=headers, params=params)
                result.deleted += 1
            remaining = await deployments(client, url, headers)
            current = remaining.get(identifier)
            if len(remaining) != 1 or current is None or not current.production or not current.successful:
                raise PublishError('cleanup_failed')
            await check_canonical(client, pages, identifier)
    except TimeoutError:
        raise PublishError('cleanup_timeout') from None


async def deploy(
    pages: PagesSettings,
    command: tuple[Path, Path],
    stage: Path,
    work: Path,
    deadline: Deadline,
    result: PublishResult,
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=10) as client:
        await project_metadata(client, pages)
        expected = (stage / BUILD_MARKER).read_bytes()
        identifier = upload(pages, command, stage, work, deadline)
        result.deployment_id = identifier
        await verify(client, pages.project, expected)
        await prune(client, pages, identifier, result)


def fixed_inputs(config: dict[str, str], args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    if not config.get(settings.DB_KEY) or not config.get(settings.DAILY_KEY):
        raise PublishError('missing_inputs')
    try:
        database = settings.SERVICE / Path(config[settings.DB_KEY]).expanduser()
        feed = settings.SERVICE / Path(config[settings.DAILY_KEY]).expanduser()
        dist = settings.SERVICE / Path(config.get('LAWBOT_CALENDAR_FRONTEND_DIST') or 'frontend/dist').expanduser()
        out = settings.SERVICE / Path(config.get('LAWBOT_CALENDAR_SITE') or 'data/site').expanduser()
        for given, expected in (
            (args.database, database),
            (args.erp_daily_feed, feed),
            (args.frontend_dist, dist),
            (args.out, out),
        ):
            if given is not None and given.expanduser().resolve() != expected.resolve():
                raise PublishError('input_mismatch')
    except OSError, RuntimeError:
        raise PublishError('invalid_paths') from None
    return database, feed, dist, out


def publish_site(
    args: argparse.Namespace, deadline: Deadline, result: PublishResult, transport: httpx.AsyncBaseTransport | None
) -> str:
    try:
        config = settings.publish_config()
    except OSError, ValueError:
        raise PublishError('invalid_environment') from None
    project = config.get(settings.PROJECT_KEY, '').strip()
    if not project and not config.get(settings.DB_KEY) and not config.get(settings.DAILY_KEY):
        return 'skipped'
    database, feed, dist, out = fixed_inputs(config, args)
    state = private_directory()
    pages = PagesSettings.read(project) if project else None
    command = wrangler_command(config) if pages else None

    with exported_site(
        snapshot_app(database, feed), dist, out, protected=(database, feed), lock_timeout=PUBLISH_LOCK_TIMEOUT
    ) as (site, result.files):
        if pages is None or command is None:
            return 'skipped'
        with TemporaryDirectory(prefix='build-', dir=state) as directory:
            stage = Path(directory) / 'upload'
            work = Path(directory) / 'work'
            work.mkdir(mode=0o700)
            result.files = stage_site(site, stage)
            asyncio.run(deploy(pages, command, stage, work, deadline, result, transport=transport))
    return 'ok'


def main(argv: list[str], *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = PublishParser(prog='law-calendar publish', description='Export and publish the static calendar site')
    parser.add_argument('--database', type=Path, help='Assert the configured database path')
    parser.add_argument('--erp-daily-feed', type=Path, help='Assert the configured ERP snapshot path')
    parser.add_argument('--frontend-dist', type=Path, help='Assert the configured frontend build path')
    parser.add_argument('--out', type=Path, help='Assert the configured site path')
    result = PublishResult()
    # HTTPX's request logs include URLs, and the Pages API path contains the account ID.
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        args = parser.parse_args(argv)
        with time_limit() as deadline:
            status = publish_site(args, deadline, result, transport)
    except PublishInterrupted:
        result.report('failed', error='publish_timeout')
        return 1
    except PublishError as exc:
        result.report('failed', error=str(exc))
        return 1
    except ValueError:
        result.report('failed', error='export_failed')
        return 1
    except OSError:
        result.report('failed', error='publish_io')
        return 1
    if status == 'skipped':
        result.report(status, reason='pages_not_configured')
    else:
        result.report(status)
    return 0
