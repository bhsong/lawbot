"""Run the real export and a disposable Wrangler stand-in; intercept all Pages HTTP."""

import json
import subprocess
import sys
from uuid import UUID

import httpx
import pytest

from calendar_publish_fixtures import offline_calendar as offline_calendar
from law_calendar import server
from test_calendar_api import dataset as dataset
from test_calendar_export import export_args
from test_calendar_export import inputs as inputs

PROJECT = 'calendar-fixture'
ACCOUNT = 'fixture-account-value'
TOKEN = 'fixture-token-value'
PUBLIC_STATUS = 'data/law-calendar/status.json'
CURRENT, PRIOR, FAILED, OLDER, PREVIEW = (str(UUID(int=n)) for n in range(1, 6))
PAGES_KEYS = ('LAWBOT_CALENDAR_PAGES_PROJECT', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN')


@pytest.fixture(autouse=True)
def isolated_publish_env(monkeypatch):
    for key in ('LAWBOT_CALENDAR_NODE', 'LAWBOT_CALENDAR_SITE', 'LAWBOT_CALENDAR_FRONTEND_DIST'):
        monkeypatch.delenv(key, raising=False)


def args(inputs, *extra):
    return ['publish', *export_args(inputs)[1:], *extra]


def test_unconfigured_cli_exports_and_reports_one_line(inputs):
    proc = subprocess.run(
        [sys.executable, '-m', 'law_calendar.server', *args(inputs)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == 1
    assert proc.stderr == ''
    out = inputs[-1]
    assert (out / PUBLIC_STATUS).is_file()
    count = sum(p.is_file() and not p.name.startswith('.') for p in out.rglob('*'))
    assert json.loads(proc.stdout) == {
        'status': 'skipped',
        'reason': 'pages_not_configured',
        'deploymentId': None,
        'deleted': 0,
        'files': count,
    }


def test_publish_loads_service_env_and_keeps_process_priority(inputs, tmp_path, monkeypatch, capsys):
    _, dist, feed, _, out = inputs
    env = tmp_path / 'calendar.env'
    env.write_text(
        f'LAWBOT_CALENDAR_ERP_DAILY_FEED={feed}\nLAWBOT_CALENDAR_FRONTEND_DIST={dist}\n'
        f'LAWBOT_CALENDAR_SITE={out}\nCLOUDFLARE_ACCOUNT_ID={ACCOUNT}\nCLOUDFLARE_API_TOKEN={TOKEN}\n'
    )
    env.chmod(0o600)
    monkeypatch.setenv('LAWBOT_ENV_FILE', str(env))
    monkeypatch.setenv('LAW_CALENDAR_DB', str(inputs[0]))
    monkeypatch.setenv('LAWBOT_CALENDAR_SITE', str(tmp_path / 'process-site'))
    for key in ('LAWBOT_CALENDAR_ERP_DAILY_FEED', 'LAWBOT_CALENDAR_FRONTEND_DIST', *PAGES_KEYS):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, 'argv', ['law-calendar', 'publish'])
    assert server.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)['reason'] == 'pages_not_configured'
    assert (tmp_path / 'process-site' / PUBLIC_STATUS).is_file()
    assert not out.exists()


def fake_wrangler(inputs, monkeypatch, *, fail=False):
    dist = inputs[1]
    wrangler = dist.parent / 'node_modules/wrangler/bin/wrangler.js'
    wrangler.parent.mkdir(parents=True)
    wrangler.write_text(f"""
import fcntl, json, os, sys
from pathlib import Path
stage = Path(sys.argv[3])
assert sys.argv[1:] == ['pages', 'deploy', str(stage), '--project-name', {PROJECT!r}, '--branch', 'main']
assert Path.cwd() == stage and not stage.is_relative_to(Path(__file__).parent)
assert Path({PUBLIC_STATUS!r}).is_file() and Path('_headers').is_file()
assert not any(p.name.startswith('.') or p.name in {{'_worker.js', '_routes.json', 'functions'}} for p in stage.rglob('*'))
with Path({str(inputs[-1].with_name('.site.lock'))!r}).open() as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise AssertionError('publish lost the export lock')
assert os.environ['WRANGLER_SEND_METRICS'] == 'false'
assert os.environ['CLOUDFLARE_API_TOKEN'] == {TOKEN!r}
assert os.environ['CLOUDFLARE_ACCOUNT_ID'] == {ACCOUNT!r}
Path(os.environ['WRANGLER_OUTPUT_FILE_PATH']).write_text(json.dumps({{
    'type': 'pages-deploy-detailed', 'version': 1, 'pages_project': {PROJECT!r},
    'deployment_id': {CURRENT!r}, 'environment': 'production',
}}) + '\\n')
print(f'{{os.environ["CLOUDFLARE_ACCOUNT_ID"]}} {{os.environ["CLOUDFLARE_API_TOKEN"]}}')
sys.exit({int(fail)})
""")
    for key, value in zip(PAGES_KEYS, (PROJECT, ACCOUNT, TOKEN), strict=True):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('LAWBOT_CALENDAR_NODE', sys.executable)


def deployment(identifier, day, *, environment='production', success=True, aliases=None):
    return {
        'id': identifier,
        'environment': environment,
        'created_on': f'2026-11-{day:02}T12:00:00Z',
        'latest_stage': {'name': 'deploy', 'status': 'success' if success else 'failure'},
        'aliases': aliases,
    }


@pytest.mark.parametrize('purge', [False, True])
def test_publish_verifies_production_and_prunes_every_history_page(inputs, monkeypatch, capsys, purge):
    from law_calendar import publish

    fake_wrangler(inputs, monkeypatch)
    out = inputs[-1]
    calls = []
    project_path = f'/client/v4/accounts/{ACCOUNT}/pages/projects/{PROJECT}'
    deployments = [
        deployment(OLDER, 15),
        deployment(CURRENT, 24),
        deployment(PREVIEW, 23, environment='preview', aliases=['preview']),
        deployment(FAILED, 22, success=False),
        deployment(PRIOR, 20),
    ]

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == '/' + PUBLIC_STATUS:
            assert request.url.host == PROJECT + '.pages.dev'
            assert request.headers.get('authorization') is None
            return httpx.Response(200, content=(out / PUBLIC_STATUS).read_bytes())
        assert request.headers['authorization'] == f'Bearer {TOKEN}'
        if request.url.path == project_path:
            return httpx.Response(200, json={'success': True, 'result': {'canonical_deployment': {'id': CURRENT}}})
        if request.method == 'GET':
            assert request.url.path == project_path + '/deployments'
            page = int(request.url.params['page'])
            return httpx.Response(
                200,
                json={
                    'success': True,
                    'result': deployments[:2] if page == 1 else deployments[2:],
                    'result_info': {'total_count': len(deployments)},
                },
            )
        return httpx.Response(200, json={'success': True, 'result': None})

    assert (
        publish.main(args(inputs, *(['--purge-history'] if purge else []))[1:], transport=httpx.MockTransport(handler))
        == 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {
        'status': 'ok',
        'deploymentId': CURRENT,
        'deleted': 3 + purge,
        'files': sum(p.is_file() and not p.name.startswith('.') for p in out.rglob('*')),
    }
    assert captured.err == '' and TOKEN not in captured.out and ACCOUNT not in captured.out
    assert calls[0][:2] == ('GET', '/' + PUBLIC_STATUS)
    deleted = {path.rsplit('/', 1)[1]: params for method, path, params in calls if method == 'DELETE'}
    assert deleted == {OLDER: {}, PREVIEW: {'force': 'true'}, FAILED: {}} | ({PRIOR: {}} if purge else {})


@pytest.mark.parametrize('failure', ['upload_failed', 'verify_failed', 'production_mismatch'])
def test_publish_failure_never_deletes_or_reports_sensitive_values(inputs, monkeypatch, capsys, failure):
    from law_calendar import publish

    fake_wrangler(inputs, monkeypatch, fail=failure == 'upload_failed')
    monkeypatch.setattr(publish, 'VERIFY_TIMEOUT', 0.02)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == '/' + PUBLIC_STATUS:
            return httpx.Response(
                200, content=(inputs[-1] / PUBLIC_STATUS).read_bytes() + (b' ' if failure == 'verify_failed' else b'')
            )
        if request.url.path.endswith('/deployments'):
            return httpx.Response(200, json={'success': True, 'result': []})
        return httpx.Response(200, json={'success': True, 'result': {'canonical_deployment': {'id': PRIOR}}})

    assert publish.main(args(inputs, '--purge-history')[1:], transport=httpx.MockTransport(handler)) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result['status'] == 'failed' and result['error'] == failure and result['deleted'] == 0
    assert result['deploymentId'] == (None if failure == 'upload_failed' else CURRENT)
    assert not any(req.method == 'DELETE' for req in calls)
    if failure == 'upload_failed':
        assert not calls
    elif failure == 'verify_failed':
        assert all(req.url.path == '/' + PUBLIC_STATUS for req in calls)
    assert captured.err == '' and all(value not in captured.out for value in (ACCOUNT, TOKEN, 'https://'))


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
