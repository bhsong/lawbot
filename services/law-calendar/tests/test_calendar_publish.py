"""Run the real export and a disposable Wrangler stand-in; intercept all Pages HTTP."""

import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import httpx
import pytest

from calendar_publish_fixtures import offline_calendar as offline_calendar
from law_calendar import publish, server, settings
from law_calendar.export import BUILD_MARKER
from lawbot_common.process import child_environment, run_group
from test_calendar_api import dataset as dataset
from test_calendar_export import export_args
from test_calendar_export import inputs as inputs

PROJECT = 'calendar-fixture'
ACCOUNT = 'fixture-account-value'
TOKEN = 'fixture-token-value'
PUBLIC_STATUS = 'data/law-calendar/status.json'
CURRENT, PRIOR, FAILED, OLDER, PREVIEW = (str(UUID(int=n)) for n in range(1, 6))


@pytest.fixture
def service(monkeypatch):
    # CI's system temp directory can have a world-writable ancestor.
    with TemporaryDirectory(prefix='law-calendar-test-', dir=Path.home().resolve()) as directory:
        path = Path(directory)
        monkeypatch.setattr(settings, 'SERVICE', path)
        yield path


def config(service, inputs, *, project=PROJECT, node=''):
    database, dist, feed, _, out = inputs
    (service / '.env').write_text(
        f'LAW_CALENDAR_DB={database}\nLAWBOT_CALENDAR_ERP_DAILY_FEED={feed}\n'
        f'LAWBOT_CALENDAR_FRONTEND_DIST={dist}\nLAWBOT_CALENDAR_SITE={out}\n'
        f'LAWBOT_CALENDAR_PAGES_PROJECT={project}\n'
        f'LAWBOT_CALENDAR_NODE={node}\n'
    )


def credentials(service):
    path = service / 'pages.env'
    path.touch(mode=0o600)
    path.write_text(f'CLOUDFLARE_ACCOUNT_ID={ACCOUNT}\nCLOUDFLARE_API_TOKEN={TOKEN}\n')
    return path


def test_unconfigured_cli_exports_and_reports_one_line(inputs, service, monkeypatch, capsys):
    config(service, inputs, project='')
    monkeypatch.setenv('LAWBOT_CALENDAR_PAGES_PROJECT', PROJECT)
    monkeypatch.setenv('LAWBOT_CALENDAR_SITE', str(service / 'wrong-output'))
    monkeypatch.setattr(sys, 'argv', ['law-calendar', 'publish', *export_args(inputs)[1:]])
    assert server.main() == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and not captured.err
    out = inputs[-1]
    assert (out / PUBLIC_STATUS).is_file()
    count = sum(p.is_file() and not p.name.startswith('.') for p in out.rglob('*'))
    assert json.loads(captured.out) == {
        'status': 'skipped',
        'reason': 'pages_not_configured',
        'deploymentId': None,
        'deleted': 0,
        'files': count,
    }


@pytest.mark.parametrize('option', ['--database', '--erp-daily-feed', '--out', '--frontend-dist'])
def test_fixed_inputs_and_pages_credentials_are_not_supplied_by_environment(
    inputs, service, monkeypatch, capsys, option
):
    config(service, inputs)
    foreign = service / 'foreign'
    monkeypatch.setenv('LAW_CALENDAR_DB', str(foreign))
    monkeypatch.setenv('CLOUDFLARE_ACCOUNT_ID', ACCOUNT)
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', TOKEN)
    requests = []
    transport = httpx.MockTransport(requests.append)
    assert publish.main([option, str(foreign)], transport=transport) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'input_mismatch'
    assert publish.main([], transport=transport) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'invalid_pages_credentials'
    assert not requests


@pytest.mark.parametrize('bad', ['partial', 'wide-mode', 'symlink', 'hardlink', 'directory'])
def test_publisher_refuses_invalid_credentials_without_contacting_pages(inputs, service, capsys, bad):
    config(service, inputs)
    if bad in ('symlink', 'directory'):
        (service / 'decoy').touch(mode=0o600)
        path = service / 'pages.env'
        if bad == 'symlink':
            path.symlink_to(service / 'decoy')
        else:
            path.mkdir(mode=0o700)
    else:
        path = credentials(service)
        if bad == 'hardlink':
            os.link(path, service / 'decoy')
        elif bad == 'wide-mode':
            path.chmod(0o640)
        else:
            path.write_text(f'CLOUDFLARE_ACCOUNT_ID={ACCOUNT}\n')
    assert publish.main([], transport=httpx.MockTransport(lambda _: pytest.fail('unexpected Pages request'))) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'invalid_pages_credentials'


def fake_wrangler(inputs, service, *, fail=False, before_receipt=''):
    node = service / 'node'
    node.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    node.chmod(0o700)
    config(service, inputs, node=node)
    credentials(service)
    wrangler = service / 'frontend/node_modules/wrangler/bin/wrangler.js'
    wrangler.parent.mkdir(parents=True, exist_ok=True)
    started = service / 'wrangler-started'
    wrangler.write_text(f"""
import fcntl, json, os, sys
from pathlib import Path
stage = Path(sys.argv[3])
assert sys.argv[1:] == ['pages', 'deploy', str(stage), '--project-name', {PROJECT!r}, '--branch', 'main']
work = Path.cwd()
assert stage.parent == work.parent and stage != work
assert Path({str(service / '.pages-publish')!r}) == work.parent.parent
assert (stage / {BUILD_MARKER!r}).is_file() and (stage / '_headers').is_file()
assert not any(p.name.startswith('.') or p.name in {{'_worker.js', '_routes.json', 'functions'}} for p in stage.rglob('*'))
with Path({str(service / '.pages-publish/export.lock')!r}).open() as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise AssertionError('publish lost the export lock')
assert os.environ['WRANGLER_SEND_METRICS'] == 'false'
assert os.environ['WRANGLER_WRITE_LOGS'] == 'false'
assert os.environ['WRANGLER_CACHE_DIR'] == str(work / 'cache')
assert os.environ['HOME'] == str(work)
assert os.environ['PATH'] == {str(node.parent) + ':/usr/bin:/bin'!r}
assert 'LAWBOT_CALENDAR_ADMIN_TOKEN' not in os.environ and 'NODE_OPTIONS' not in os.environ
assert os.environ['CLOUDFLARE_API_TOKEN'] == {TOKEN!r}
assert os.environ['CLOUDFLARE_ACCOUNT_ID'] == {ACCOUNT!r}
Path({str(started)!r}).touch()
{before_receipt}
Path(os.environ['WRANGLER_OUTPUT_FILE_PATH']).write_text(json.dumps({{
    'type': 'pages-deploy-detailed', 'version': 1, 'pages_project': {PROJECT!r},
    'deployment_id': {CURRENT!r}, 'environment': 'production',
}}) + '\\n')
print(f'{{os.environ["CLOUDFLARE_ACCOUNT_ID"]}} {{os.environ["CLOUDFLARE_API_TOKEN"]}}')
sys.exit({int(fail)})
""")
    return started


def deployment(identifier, day, *, environment='production', success=True, aliases=None):
    return {
        'id': identifier,
        'environment': environment,
        'created_on': f'2026-11-{day:02}T12:00:00Z',
        'latest_stage': {'name': 'deploy', 'status': 'success' if success else 'failure'},
        'aliases': aliases,
    }


class Pages:
    def __init__(self, out):
        self.out = out
        self.calls = []
        self.project_path = f'/client/v4/accounts/{ACCOUNT}/pages/projects/{PROJECT}'
        self.metadata = {
            'name': PROJECT,
            'production_branch': 'main',
            'canonical_deployment': {'id': CURRENT},
        }
        self.history = [
            deployment(OLDER, 15),
            deployment(CURRENT, 24),
            deployment(PREVIEW, 23, environment='preview', aliases=['preview']),
            deployment(FAILED, 22, success=False),
            deployment(PRIOR, 20),
        ]
        self.delete_works = True
        self.serve_stale = False
        self.corrupt = lambda rows, info: (rows, info)
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        path, method = request.url.path, request.method
        params = dict(request.url.params)
        self.calls.append((method, path, params))
        if path == '/' + BUILD_MARKER:
            assert request.url.host == PROJECT + '.pages.dev'
            assert 'authorization' not in request.headers
            name = PUBLIC_STATUS if self.serve_stale else BUILD_MARKER
            return httpx.Response(200, content=(self.out / name).read_bytes())
        assert request.headers['authorization'] == f'Bearer {TOKEN}'
        if path == self.project_path:
            return httpx.Response(200, json={'success': True, 'result': self.metadata})
        if method == 'GET':
            assert path == self.project_path + '/deployments'
            page, size = int(params['page']), int(params['per_page'])
            items = self.history[(page - 1) * size : page * size]
            info = {'page': page, 'per_page': size, 'count': len(items), 'total_count': len(self.history)}
            items, info = self.corrupt(items, info)
            return httpx.Response(200, json={'success': True, 'result': items, 'result_info': info})
        assert method == 'DELETE' and path.startswith(self.project_path + '/deployments/')
        if self.delete_works:
            self.history = [row for row in self.history if row['id'] != path.rsplit('/', 1)[1]]
        return httpx.Response(200, json={'success': True, 'result': None})

    @property
    def deletes(self):
        return {path.rsplit('/', 1)[1]: params for method, path, params in self.calls if method == 'DELETE'}


def test_publish_verifies_production_and_leaves_only_current(inputs, service, monkeypatch, capsys):
    fake_wrangler(inputs, service)
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'old-value')
    monkeypatch.setenv('LAWBOT_CALENDAR_ADMIN_TOKEN', 'admin-fixture')
    monkeypatch.setenv('NODE_OPTIONS', 'untrusted-fixture')
    monkeypatch.setattr(publish, 'PER_PAGE', 2)
    out = inputs[-1]
    pages = Pages(out)

    assert publish.main([], transport=pages.transport) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        'status': 'ok',
        'deploymentId': CURRENT,
        'deleted': 4,
        'files': sum(p.is_file() and not p.name.startswith('.') for p in out.rglob('*')),
    }
    assert not captured.err and TOKEN not in captured.out and ACCOUNT not in captured.out
    assert [call[:2] for call in pages.calls[:2]] == [('GET', pages.project_path), ('GET', '/' + BUILD_MARKER)]
    assert pages.deletes == {OLDER: {}, PREVIEW: {'force': 'true'}, FAILED: {}, PRIOR: {}}
    assert [row['id'] for row in pages.history] == [CURRENT]
    for index, (method, _, _) in enumerate(pages.calls):
        if method == 'DELETE':
            assert pages.calls[index - 1] == ('GET', pages.project_path, {})


@pytest.mark.parametrize('key,value', [('name', 'other'), ('production_branch', 'preview')])
def test_preflight_rejects_the_wrong_project_before_running_wrangler(inputs, service, capsys, key, value):
    started = fake_wrangler(inputs, service)
    pages = Pages(inputs[-1])
    pages.metadata[key] = value
    assert publish.main([], transport=pages.transport) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'project_mismatch'
    assert not started.exists()
    assert pages.calls == [('GET', pages.project_path, {})]


@pytest.mark.parametrize('node', ['', 'node'])
def test_node_must_be_absolute(inputs, service, capsys, node):
    fake_wrangler(inputs, service)
    config(service, inputs, node=node)
    assert publish.main([], transport=httpx.MockTransport(lambda _: pytest.fail('unexpected Pages request'))) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'wrangler_unavailable'


@pytest.mark.parametrize('failure', ['upload_failed', 'verify_failed', 'production_mismatch'])
def test_publish_failure_never_deletes_or_reports_sensitive_values(inputs, service, monkeypatch, capsys, failure):
    fake_wrangler(inputs, service, fail=failure == 'upload_failed')
    monkeypatch.setattr(publish, 'VERIFY_TIMEOUT', 0.02)
    pages = Pages(inputs[-1])
    pages.serve_stale = failure == 'verify_failed'
    if failure == 'production_mismatch':
        pages.metadata['canonical_deployment'] = {'id': PRIOR}
    assert publish.main([], transport=pages.transport) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result['status'] == 'failed' and result['error'] == failure and result['deleted'] == 0
    assert result['deploymentId'] == (None if failure == 'upload_failed' else CURRENT)
    assert not pages.deletes and not captured.err
    assert all(value not in captured.out for value in (ACCOUNT, TOKEN, 'https://'))


@pytest.mark.parametrize('bad', ['page', 'total-change', 'missing-total', 'stage', 'alias', 'row', 'duplicate'])
def test_inconsistent_history_fails_before_first_delete(inputs, service, monkeypatch, capsys, bad):
    fake_wrangler(inputs, service)
    pages = Pages(inputs[-1])
    monkeypatch.setattr(publish, 'PER_PAGE', 2)

    def corrupt(rows, info):
        if info['page'] == 2:
            if bad == 'page':
                info['page'] = 1
            elif bad == 'total-change':
                info['total_count'] += 1
            elif bad == 'missing-total':
                info.pop('total_count')
            elif bad == 'stage':
                rows[0] = rows[0] | {'latest_stage': {'name': 'deploy', 'status': 'unknown'}}
            elif bad == 'alias':
                rows[0] = rows[0] | {'aliases': 'alias'}
            elif bad == 'row':
                rows[0] = []
            else:
                rows[0] = pages.history[0]
        return rows, info

    pages.corrupt = corrupt
    assert publish.main([], transport=pages.transport) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'cleanup_failed'
    assert not pages.deletes


def test_external_rollback_stops_the_next_delete(inputs, service, capsys):
    fake_wrangler(inputs, service)
    pages = Pages(inputs[-1])

    def rollback_after_delete(request):
        response = pages.handle(request)
        if request.method == 'DELETE':
            pages.metadata['canonical_deployment'] = {'id': PRIOR}
        return response

    assert publish.main([], transport=httpx.MockTransport(rollback_after_delete)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['error'] == 'production_mismatch' and result['deleted'] == 1
    assert PRIOR not in pages.deletes


def test_failed_cleanup_is_retried_on_the_next_publish(inputs, service, capsys):
    fake_wrangler(inputs, service)
    out = inputs[-1]
    pages = Pages(out)
    pages.delete_works = False
    assert publish.main([], transport=pages.transport) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'cleanup_failed'
    old_build = (out / BUILD_MARKER).read_bytes()
    pages.delete_works = True

    assert publish.main([], transport=pages.transport) == 0
    assert json.loads(capsys.readouterr().out)['deleted'] == 4
    assert [row['id'] for row in pages.history] == [CURRENT]
    assert (out / BUILD_MARKER).read_bytes() != old_build


@pytest.mark.parametrize('caller_timeout', [False, True])
def test_publish_deadlines_kill_wrangler_group_and_clean_work(inputs, service, monkeypatch, capsys, caller_timeout):
    held = service / 'descendant.lock'
    ready = service / 'descendant-ready'
    child = f"""
import fcntl, time
from pathlib import Path
with open({str(held)!r}, 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    Path({str(ready)!r}).touch()
    time.sleep(30)
"""
    fake_wrangler(
        inputs,
        service,
        before_receipt=f"""
import subprocess
subprocess.Popen([sys.executable, '-c', {child!r}]).wait()
""",
    )
    pages = Pages(inputs[-1])
    if caller_timeout:
        bootstrap = f"""
import httpx
from pathlib import Path
from law_calendar import publish, settings
settings.SERVICE = Path({str(service)!r})
metadata = {{'success': True, 'result': {pages.metadata!r}}}
transport = httpx.MockTransport(lambda _: httpx.Response(200, json=metadata))
raise SystemExit(publish.main([], transport=transport))
"""
        with pytest.raises(subprocess.TimeoutExpired):
            run_group([sys.executable, '-I', '-c', bootstrap], env=child_environment(), timeout=2)
    else:
        monkeypatch.setattr(publish, 'PUBLISH_DEADLINE', 2)
        assert publish.main([], transport=pages.transport) == 1
        assert json.loads(capsys.readouterr().out)['error'] == 'publish_timeout'
    assert ready.exists() and not list((service / '.pages-publish').glob('build-*'))
    deadline = time.monotonic() + 2
    with held.open() as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                assert time.monotonic() < deadline, 'Wrangler child still holds its lock'
                time.sleep(0.01)
            else:
                break


def test_staging_excludes_dot_entries_and_rejects_forbidden_files_recursively(tmp_path):
    from law_calendar.publish import stage_site

    out = tmp_path / 'site'
    assets = out / 'assets'
    assets.mkdir(parents=True)
    (out / '.law-calendar-site').touch()
    (assets / '.hidden').mkdir()
    (assets / '.hidden/_worker.js').touch()
    (assets / 'bundle.js').touch()
    stage = tmp_path / 'stage'
    stage_site(out, stage)
    assert sorted(p.name for p in stage.rglob('*')) == ['assets', 'bundle.js']
    for forbidden in ('_worker.js', '_routes.json', 'functions'):
        path = assets / forbidden
        path.touch()
        with pytest.raises(ValueError, match='stage_failed'):
            stage_site(out, tmp_path / forbidden)
        path.unlink()
