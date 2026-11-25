import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from law_calendar.server import create_app
from lawbot_common.erp_daily import read_snapshot
from source_hub import daily
from source_hub.store import KST
from test_source_hub import registry as registry

STAMP = datetime(2026, 11, 13, 6, 30, tzinfo=KST)


def skipped_publish():
    payload = {'status': 'skipped', 'reason': 'pages_not_configured', 'deploymentId': None, 'files': 20, 'deleted': 0}
    return subprocess.CompletedProcess(['law-calendar', 'publish'], 0, json.dumps(payload), '')


@pytest.fixture(autouse=True)
def fake_publisher(monkeypatch):
    command = daily.command

    def dispatch(config, project, *args, **kwargs):
        if project == 'law-calendar' and args[:2] == ('law-calendar', 'publish'):
            return skipped_publish()
        return command(config, project, *args, **kwargs)

    monkeypatch.setattr(daily, 'command', dispatch)
    return command


def test_daily_preserves_collector_environment_but_isolates_publisher(tmp_path, monkeypatch, fake_publisher):
    for key in (
        'CLOUDFLARE_ACCOUNT_ID',
        'CLOUDFLARE_API_TOKEN',
        'LAWBOT_CALENDAR_ADMIN_TOKEN',
        'UV_ENV_FILE',
        'LAW_OC',
        'LAW_CALENDAR_DB',
        'LAW_MONITOR_UV',
        'USER',
        'PYTHONUNBUFFERED',
    ):
        monkeypatch.setenv(key, 'inherited-fixture')
    monkeypatch.setenv('PATH', '/usr/bin:/usr/sbin')
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    cfg = daily.DailyConfig(tmp_path, tmp_path, tmp_path, tmp_path / 'db', tmp_path / 'material', tmp_path / 'feed')
    children = []

    def run(args, *, env, **kwargs):
        children.append((args, env))
        return skipped_publish()

    monkeypatch.setattr(daily, 'run_group', run)
    fake_publisher(cfg, '', 'law-monitor', 'daily')
    fake_publisher(cfg, 'erp-updates', 'erp-updates', 'collect')
    fake_publisher(cfg, 'law-calendar', 'law-calendar', 'publish')
    assert len(children) == 3
    assert all('--no-env-file' in args for args, _ in children)
    inherited = {
        key: value for key, value in os.environ.items() if key not in {'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN'}
    }
    inherited.update(LAW_CALENDAR_DB=str(cfg.calendar_db), LAW_MONITOR_UV=cfg.uv)
    if any(env != inherited for _, env in children[:2]):
        pytest.fail('Collection child did not receive the filtered parent environment', pytrace=False)
    assert set(children[2][1]) == {'PATH', 'HOME'}


def test_daily_publish_command_reaches_installed_cli_help(tmp_path, monkeypatch, fake_publisher):
    root = Path(__file__).resolve().parents[3]
    uv = shutil.which('uv')
    assert uv is not None
    cfg = daily.DailyConfig(root, tmp_path, tmp_path, tmp_path / 'db', tmp_path / 'material', tmp_path / 'feed', uv=uv)
    run_group = daily.run_group
    completed = []

    def show_help(args, *, env, **kwargs):
        assert '--no-env-file' in args
        proc = run_group([*args, '--help'], env=env | {'UV_OFFLINE': '1'}, **kwargs)
        completed.append(proc)
        return proc

    monkeypatch.setattr(daily, 'command', fake_publisher)
    monkeypatch.setattr(daily, 'run_group', show_help)
    daily._publish_calendar(cfg)
    [proc] = completed
    assert proc.returncode == 0, proc.stderr
    assert 'usage: law-calendar publish' in proc.stdout


def test_daily_reports_invalid_pages_credentials_without_rewriting_collection_outcome(registry, tmp_path, monkeypatch):
    cfg = daily.DailyConfig(tmp_path, tmp_path, tmp_path, tmp_path / 'db', tmp_path / 'material', tmp_path / 'feed')
    failed = {'status': 'failed', 'error': 'invalid_pages_credentials', 'deploymentId': None, 'files': 0, 'deleted': 0}
    monkeypatch.setattr(daily, '_run', lambda *_: {'status': 'ok'})
    monkeypatch.setattr(
        daily, 'command', lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, json.dumps(failed))
    )
    result = daily.run(registry, cfg, now=lambda: STAMP)
    assert result == {'status': 'ok', 'calendarPublish': failed}


def test_daily_finishes_collection_calendar_articles_summary_and_erp_without_removed_sources(
    registry, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from law_monitor import checks
    from law_monitor import cli as monitor_cli
    from law_monitor.app import Monitor
    from law_monitor.collectors.base import CollectResult
    from law_monitor.config import load as monitor_config
    from law_monitor.paths import Paths

    assert {row['agency'] for row in registry.snapshot()['sources']} == {
        'lawgo',
        'moel',
        'mofe',
        'nts',
        'hometax',
    }
    calls = []

    class Collector:
        def __init__(self, name):
            self.name = name

        def has_baseline(self, state):
            return True

        def collect(self, ctx):
            calls.append(self.name)
            return CollectResult()

    cfg = daily.DailyConfig(
        root=tmp_path,
        monitor_home=tmp_path / 'monitor',
        erp_data=tmp_path / 'erp',
        calendar_db=tmp_path / 'calendar.db',
        materials_db=tmp_path / 'materials.db',
        public=tmp_path / 'daily.json',
    )
    monitor = Monitor(
        Paths(cfg.monitor_home),
        monitor_config(),
        collectors=[Collector(name) for name in ('legalize_law', 'legalize_admrule')],
    )
    monkeypatch.setattr(monitor, 'refresh_article_repo', lambda: None)

    def check_command(command, **kwargs):
        calls.append('calendar' if 'law_calendar.sync' in command else 'articles')
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    def managed_command(config, project, *args, **kwargs):
        if project == 'law-calendar' and args[:2] == ('law-calendar', 'publish'):
            calls.append('publish')
            assert read_snapshot(cfg.public)['status'] == 'ok'
            return skipped_publish()
        selection = monitor_cli._daily_selection(Path(args[args.index('--source-plan') + 1]))
        assert selection.collectors == ['legalize_law', 'legalize_admrule']
        code = monitor_cli._daily(monitor, None, selection=selection)
        return SimpleNamespace(returncode=code)

    def erp(config, *, collect):
        assert collect
        calls.append('erp')
        return {'outcome': 'ok', 'new_count': 0}

    monkeypatch.setattr(checks.subprocess, 'run', check_command)
    monkeypatch.setattr(monitor_cli, '_summarize', lambda *args, **kwargs: calls.append('summary') or 0)
    monkeypatch.setattr(daily, 'command', managed_command)
    monkeypatch.setattr(daily, 'run_erp', erp)
    monkeypatch.setattr(daily, 'COLLECTORS', {})
    monkeypatch.setattr(
        daily, 'capture', lambda *args: {'erp': {'items': [], 'history': []}, 'rules': None, 'news': {}}
    )
    try:
        result = daily.run(registry, cfg, now=lambda: STAMP)
        assert result['status'] == 'ok'
        assert calls == ['legalize_law', 'legalize_admrule', 'calendar', 'articles', 'summary', 'erp', 'publish']
        assert result['calendarPublish']['reason'] == 'pages_not_configured'
        assert {entry['sourceId'] for entry in read_snapshot(cfg.public)['sources']} >= {
            'legalize_law',
            'legalize_admrule',
            'moel-rss-lawinfo',
            'mofe-tax-reform',
        }
    finally:
        monitor.state.conn.close()


@pytest.mark.parametrize(
    ('setting', 'enabled'),
    [(None, True), (('group', 'law'), False), (('agency', 'lawgo'), False), (('source', 'legalize_law'), False)],
)
def test_daily_plan_distinguishes_law_source_off_from_not_due(registry, setting, enabled):
    if setting:
        registry.patch(*setting, {'enabled': False}, actor='fixture', reason='fixture', version=0)
    rows = {row['source_id']: row for row in registry.snapshot()['sources']}
    plan = daily.legacy_selection(
        rows,
        {'legalize_law': {'lastAttemptAt': STAMP.isoformat()}},
        {'run_id': 'daily-fixture'},
        STAMP,
    )
    assert 'legalize_law' not in plan['collectors']
    assert plan['articles_enabled'] is enabled


def test_daily_plan_pauses_articles_when_all_individual_laws_are_off(registry):
    law_source = next(source for source in registry.snapshot()['sources'] if source['source_id'] == 'legalize_law')
    version = 0
    for law in law_source['laws']:
        version = registry.patch(
            'law', law['law_id'], {'enabled': False}, actor='fixture', reason='fixture', version=version
        )
    rows = {row['source_id']: row for row in registry.snapshot()['sources']}
    plan = daily.legacy_selection(rows, {}, {'run_id': 'daily-fixture'}, STAMP)
    assert plan['articles_enabled'] is False
    assert 'legalize_law' not in plan['collectors']
    assert 'legalize_admrule' in plan['collectors']


def test_daily_reuses_one_snapshot_and_exposes_same_public_data(registry, tmp_path, monkeypatch):
    version = registry.patch(
        'source', 'legalize_admrule', {'enabled': False}, actor='fixture', reason='fixture', version=0
    )
    registry.patch('source', 'moel-rss-lawinfo', {'enabled': True}, actor='fixture', reason='fixture', version=version)
    calls = []

    def legacy(config, plan, previous):
        calls.append(('legacy', plan))
        assert 'legalize_admrule' not in plan['collectors']
        return {source: {'outcome': 'ok', 'run_id': plan['run_id'], 'new_count': 0} for source in plan['collectors']}

    def local(config, *, collect):
        calls.append(('erp', collect))
        return {'outcome': 'ok', 'new_count': 0}

    def agency(registry, database, source_id, *, plan, reconcile):
        calls.append(('agency', source_id, plan['config_version']))
        return {'outcome': 'blocked', 'error': 'robots_disallow'}

    def capture(config, paths, cutoff):
        calls.append(('capture', cutoff))
        assert paths.law_since == STAMP.replace(hour=0, minute=0)
        items = [] if len(calls) > 4 else [{'id': 'approved-before-withdrawal', 'title': '이전 공개 자료'}]
        return {
            'erp': {'state': 'ok', 'items': [], 'history': [], 'relatedLaws': []},
            'rules': None,
            'news': {'moel': {'state': 'ok', 'items': items}},
        }

    cfg = daily.DailyConfig(
        root=tmp_path,
        monitor_home=tmp_path / 'monitor',
        erp_data=tmp_path / 'erp',
        calendar_db=tmp_path / 'calendar.db',
        materials_db=tmp_path / 'materials.db',
        public=tmp_path / 'daily.json',
    )
    publish_calls = []

    def fail_publish(config, project, *args, timeout):
        assert project == 'law-calendar'
        assert args == (
            'law-calendar',
            'publish',
            '--database',
            str(config.calendar_db),
            '--erp-daily-feed',
            str(config.public),
        )
        assert timeout > 0 and read_snapshot(config.public)['snapshotHash']
        publish_calls.append(config.public)
        return subprocess.CompletedProcess(args, 1, 'untrusted child output', 'untrusted error')

    monkeypatch.setattr(daily, 'command', fail_publish)
    monkeypatch.setattr(daily, 'run_legacy', legacy)
    monkeypatch.setattr(daily, 'run_erp', local)
    monkeypatch.setattr(daily, 'capture', capture)
    monkeypatch.setattr(daily, 'COLLECTORS', {'moel-rss-lawinfo': agency})
    first = daily.run(registry, cfg, now=lambda: STAMP)
    assert first['status'] == 'partial'
    assert first['calendarPublish'] == {'status': 'failed', 'error': 'publish_failed'}
    assert [entry[0] for entry in calls] == ['legacy', 'erp', 'agency', 'capture']
    first_bytes = cfg.public.read_bytes()
    saved = read_snapshot(cfg.public)
    assert saved['snapshotHash'] == first['snapshot_hash']
    assert saved['feeds']['news']['moel']['items']
    sources = {source['sourceId']: source for source in saved['sources']}
    assert sources['moel-rss-lawinfo']['outcome'] == 'blocked'
    assert sources['legalize_admrule']['outcome'] == 'disabled'
    assert sources['legalize_law']['newCount'] == 0
    again = daily.run(registry, cfg, now=lambda: STAMP + timedelta(hours=2))
    assert again['snapshot_hash'] == first['snapshot_hash'] and cfg.public.read_bytes() == first_bytes
    assert again['calendarPublish'] == first['calendarPublish']
    assert publish_calls == [cfg.public, cfg.public]
    assert len(calls) == 4
    with TestClient(create_app(cfg.calendar_db, daily_feed=cfg.public)) as client:
        response = client.get('/api/erp-updates')
        assert response.json()['state'] == 'ok'
        assert response.headers['x-erp-snapshot'] == first['snapshot_hash']
        assert client.get('/api/erp-updates/status').json()['sources'] == saved['sources']
    assert json.loads(first_bytes)['dayKst'] == '2026-11-13'

    version = registry.snapshot()['config_version']
    registry.patch('source', 'moel-rss-lawinfo', {'enabled': False}, actor='fixture', reason='fixture', version=version)
    refreshed = daily.run(registry, cfg, now=lambda: STAMP + timedelta(hours=3), republish=True)
    assert refreshed['snapshot_hash'] != first['snapshot_hash']
    assert [entry[0] for entry in calls] == ['legacy', 'erp', 'agency', 'capture', 'erp', 'capture']
    assert calls[-2] == ('erp', False)
    assert (
        daily.run(registry, cfg, now=lambda: STAMP + timedelta(hours=4))['snapshot_hash'] == refreshed['snapshot_hash']
    )
    restored = replace(cfg, public=tmp_path / 'restored.json')
    assert daily.run(registry, restored, now=lambda: STAMP)['snapshot_hash'] == refreshed['snapshot_hash']
    assert restored.public.read_bytes() == cfg.public.read_bytes()
    assert read_snapshot(restored.public)['feeds']['news']['moel']['items'] == []
    assert len(calls) == 6

    # A conflicting target or damaged envelope cannot replace the existing public data.
    with pytest.raises(ValueError, match='distinct'):
        daily.run(registry, replace(cfg, public=registry.database), now=lambda: STAMP)
    modified = json.loads(cfg.public.read_bytes()) | {'dayKst': '2026-11-12'}
    cfg.public.write_text(json.dumps(modified))
    with TestClient(create_app(cfg.calendar_db, daily_feed=cfg.public)) as client:
        assert client.get('/api/erp-updates').json()['state'] == 'invalid'


def test_law_change_window_reuses_previous_public_cutoff_on_next_day_and_republish(registry, tmp_path, monkeypatch):
    cutoffs = []
    cfg = daily.DailyConfig(
        tmp_path,
        tmp_path / 'monitor',
        tmp_path / 'erp',
        tmp_path / 'calendar.db',
        tmp_path / 'materials.db',
        tmp_path / 'daily.json',
    )
    monkeypatch.setattr(daily, 'run_legacy', lambda *_: {})
    monkeypatch.setattr(daily, 'run_erp', lambda *_, **__: {'outcome': 'ok'})

    def capture(config, paths, cutoff):
        cutoffs.append(paths.law_since)
        return {'erp': {'state': 'ok'}, 'rules': None, 'news': {}}

    monkeypatch.setattr(daily, 'capture', capture)
    tomorrow = STAMP + timedelta(days=1)
    daily.run(registry, cfg, now=lambda: STAMP)
    daily.run(registry, cfg, now=lambda: tomorrow)
    daily.run(registry, cfg, now=lambda: tomorrow + timedelta(hours=2), republish=True)
    assert cutoffs == [STAMP.replace(hour=0, minute=0), STAMP, STAMP]


def test_republish_shows_a_newly_enabled_sources_manual_collection(registry, tmp_path, monkeypatch):
    from source_hub.store import Run

    cfg = daily.DailyConfig(
        tmp_path,
        tmp_path / 'monitor',
        tmp_path / 'erp',
        tmp_path / 'calendar.db',
        tmp_path / 'materials.db',
        tmp_path / 'daily.json',
    )
    monkeypatch.setattr(daily, 'run_legacy', lambda *_: {})
    monkeypatch.setattr(daily, 'run_erp', lambda *_, **__: {'outcome': 'ok'})
    monkeypatch.setattr(daily, 'capture', lambda *_: {'erp': {'state': 'ok'}, 'rules': None, 'news': {}})
    daily.run(registry, cfg, now=lambda: STAMP)
    source = 'nts-calendar'
    version = registry.patch('source', source, {'enabled': True}, actor='fixture', reason='verified', version=0)
    observed = STAMP + timedelta(hours=1)
    registry.record_run(
        Run(
            run_id='manual',
            source_id=source,
            config_version=version,
            started_at=observed,
            finished_at=observed,
            outcome='ok',
            new_count=0,
            modified_count=0,
        )
    )
    daily.run(registry, cfg, now=lambda: observed + timedelta(minutes=1), republish=True)
    statuses = {row['sourceId']: row for row in read_snapshot(cfg.public)['sources']}
    assert statuses[source]['outcome'] == 'ok'
    assert statuses[source]['runId'] == 'manual'


def test_collection_resumes_after_failed_capture_without_repeating_completed_stages(registry, tmp_path, monkeypatch):
    calls = []
    cfg = daily.DailyConfig(
        tmp_path,
        tmp_path / 'monitor',
        tmp_path / 'erp',
        tmp_path / 'calendar.db',
        tmp_path / 'materials.db',
        tmp_path / 'daily.json',
    )

    def capture(*_):
        calls.append('capture')
        if calls.count('capture') == 1:
            raise ValueError('simulated failure')
        return {'erp': {'state': 'ok'}, 'rules': None, 'news': {}}

    monkeypatch.setattr(daily, 'run_legacy', lambda *args: calls.append('legacy') or {})
    monkeypatch.setattr(daily, 'run_erp', lambda *args, **kwargs: calls.append('erp') or {'outcome': 'ok'})
    monkeypatch.setattr(daily, 'capture', capture)
    with pytest.raises(ValueError, match='simulated failure'):
        daily.run(registry, cfg, now=lambda: STAMP)
    assert not cfg.public.exists()
    with daily.file_lock(registry.database.with_name(registry.database.name + '.daily') / '.lock'):
        busy = daily.run(registry, cfg, now=lambda: STAMP)
        assert busy == {'status': 'busy', 'day_kst': STAMP.date().isoformat()}
    result = daily.run(registry, cfg, now=lambda: STAMP)
    assert result['snapshot_hash'] == read_snapshot(cfg.public)['snapshotHash']
    assert result['calendarPublish']['status'] == 'skipped'
    assert calls == ['legacy', 'erp', 'capture', 'capture']


def test_changes_follow_public_revisions_beyond_latest_ten(registry):
    registry.patch('source', 'moel-rss-lawinfo', {'enabled': True}, actor='fixture', reason='fixture', version=0)
    plan = registry.snapshot()
    initial = [
        (
            str(i),
            {
                'title': '가상 글',
                'canonical_url': f'https://www.moel.go.kr/info/lawinfo/lawmaking/view.do?bbs_seq={i}',
                'revision': 1,
                'event': 'new_post' if i == 10 else 'baseline',
            },
        )
        for i in range(11)
    ]
    feeds = {'erp': {'items': []}, 'rules': None, 'news': {}}
    changes, ledger = daily.change_records(plan, {'moel-rss-lawinfo': initial}, feeds, '2026-11-13', {})
    assert len(ledger) == 11 and len(changes) == 1 and changes[0]['url'].endswith('=10')
    updated = [*initial[:-1], ('10', initial[-1][1] | {'revision': 2, 'event': 'post_modified'})]
    changes, ledger = daily.change_records(plan, {'moel-rss-lawinfo': updated}, feeds, '2026-11-14', ledger)
    assert len(changes) == 1 and changes[0]['change'] == 'modified'
    assert daily.change_records(plan, {'moel-rss-lawinfo': updated}, feeds, '2026-11-15', ledger)[0] == []


def test_attachment_replacement_and_new_presentation_fields_preserve_previous_ledger(registry):
    approved = {
        'id': 'form',
        'title': '가상 서식',
        'sourceUrl': 'https://www.law.go.kr/example',
        'contentSha256': 'unchanged',
        'attachments': [{'title': '서식 PDF', 'pdfUrl': 'https://www.law.go.kr/file', 'sha256': 'old'}],
        'excerpt': '공개 근거',
    }
    feeds = {'erp': {'items': [approved]}, 'rules': None, 'news': {}}
    plan = registry.snapshot()
    baseline = {
        'kind': 'erp',
        'title': approved['title'],
        'url': approved['sourceUrl'],
        'content': approved['contentSha256'],
        'attachments': approved['attachments'],
    }
    changes, ledger = daily.change_records(plan, {}, feeds, '2026-11-13', {'erp:form': daily.snapshot_hash(baseline)})
    assert changes == []
    approved['attachments'] = [approved['attachments'][0] | {'sha256': 'new'}]
    changes, _ = daily.change_records(plan, {}, feeds, '2026-11-14', ledger)
    assert len(changes) == 1 and changes[0]['attachmentChanged'] and changes[0]['excerpt'] == '공개 근거'


def test_offline_capture_bridge_runs_the_independent_exporters(registry, tmp_path, monkeypatch):
    cfg = daily.DailyConfig(
        tmp_path,
        tmp_path / 'monitor',
        tmp_path / 'erp',
        tmp_path / 'calendar.db',
        tmp_path / 'materials.db',
        tmp_path / 'daily.json',
    )
    registry.patch('group', 'law', {'enabled': False}, actor='fixture', reason='fixture', version=0)
    registry.patch('group', 'erp', {'enabled': False}, actor='fixture', reason='fixture', version=1)
    monkeypatch.setattr(daily, 'run_legacy', lambda *args: {})

    def command(config, project, *args, **kwargs):
        if project == 'law-calendar' and args[:2] == ('law-calendar', 'publish'):
            return skipped_publish()
        if project == 'erp-updates':
            args = (str(Path(sys.executable).with_name('erp-updates')), *args[1:])
        else:
            assert project == 'law-calendar' and args[0] == 'python'
            args = (sys.executable, *args[1:])
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        assert proc.returncode == 0, proc.stderr
        return proc

    monkeypatch.setattr(daily, 'command', command)
    result = daily.run(registry, cfg, now=lambda: STAMP)
    assert result['status'] == 'ok'
    feeds = read_snapshot(cfg.public)['feeds']
    assert feeds['erp']['state'] == 'ok' and not feeds['erp']['items']
    assert feeds['rules']['schemaVersion'] == 2 and len(feeds['rules']['rules']) == 6
    assert feeds['news']['hometax']['state'] == 'link_only'
