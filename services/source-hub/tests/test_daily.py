import json
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
        'source', 'assembly_notice', {'enabled': False}, actor='fixture', reason='fixture', version=0
    )
    registry.patch('source', 'moel-rss-notice', {'enabled': True}, actor='fixture', reason='fixture', version=version)
    calls = []

    def legacy(config, plan, previous):
        calls.append(('legacy', plan))
        assert 'assembly_notice' not in plan['collectors']
        return {source: {'outcome': 'ok', 'run_id': plan['run_id'], 'new_count': 0} for source in plan['collectors']}

    def local(config, *, collect):
        calls.append(('erp', collect))
        return {'outcome': 'ok', 'new_count': 0}

    def agency(registry, database, source_id, *, plan, reconcile):
        calls.append(('agency', source_id, plan['config_version']))
        return {'outcome': 'blocked', 'error': 'robots_disallow'}

    def capture(config, paths, cutoff):
        calls.append(('capture', cutoff))
        return {
            'erp': {'state': 'ok', 'items': [], 'history': [], 'relatedLaws': []},
            'rules': None,
            'news': {},
        }

    cfg = daily.DailyConfig(
        root=tmp_path,
        monitor_home=tmp_path / 'monitor',
        erp_data=tmp_path / 'erp',
        calendar_db=tmp_path / 'calendar.db',
        materials_db=tmp_path / 'materials.db',
        public=tmp_path / 'daily.json',
    )
    monkeypatch.setattr(daily, 'run_legacy', legacy)
    monkeypatch.setattr(daily, 'run_erp', local)
    monkeypatch.setattr(daily, 'capture', capture)
    monkeypatch.setattr(daily, 'COLLECTORS', {'moel-rss-notice': agency})
    first = daily.run(registry, cfg, now=lambda: STAMP)
    assert first['status'] == 'partial'
    assert [entry[0] for entry in calls] == ['legacy', 'erp', 'agency', 'capture']
    first_bytes = cfg.public.read_bytes()
    saved = read_snapshot(cfg.public)
    assert saved['snapshotHash'] == first['snapshot_hash']
    sources = {source['sourceId']: source for source in saved['sources']}
    assert sources['moel-rss-notice']['outcome'] == 'blocked'
    assert sources['assembly_notice']['outcome'] == 'disabled'
    assert sources['legalize_law']['newCount'] == 0
    again = daily.run(registry, cfg, now=lambda: STAMP + timedelta(hours=2))
    assert again['snapshot_hash'] == first['snapshot_hash'] and cfg.public.read_bytes() == first_bytes
    assert len(calls) == 4
    with TestClient(create_app(cfg.calendar_db, daily_feed=cfg.public)) as client:
        response = client.get('/api/erp-updates')
        assert response.json()['state'] == 'ok'
        assert response.headers['x-erp-snapshot'] == first['snapshot_hash']
        assert client.get('/api/erp-updates/status').json()['sources'] == saved['sources']
    assert json.loads(first_bytes)['dayKst'] == '2026-11-13'

    version = registry.snapshot()['config_version']
    registry.patch('source', 'moel-rss-notice', {'enabled': False}, actor='fixture', reason='fixture', version=version)
    refreshed = daily.run(registry, cfg, now=lambda: STAMP + timedelta(hours=3), republish=True)
    assert refreshed['snapshot_hash'] != first['snapshot_hash']
    assert [entry[0] for entry in calls] == ['legacy', 'erp', 'agency', 'capture', 'erp', 'capture']
    assert calls[-2] == ('erp', False)
    assert (
        daily.run(registry, cfg, now=lambda: STAMP + timedelta(hours=4))['snapshot_hash'] == refreshed['snapshot_hash']
    )

    # A conflicting target or damaged envelope cannot replace the existing public data.
    with pytest.raises(ValueError, match='distinct'):
        daily.run(registry, replace(cfg, public=registry.database), now=lambda: STAMP)
    modified = json.loads(cfg.public.read_bytes()) | {'dayKst': '2026-11-12'}
    cfg.public.write_text(json.dumps(modified))
    with TestClient(create_app(cfg.calendar_db, daily_feed=cfg.public)) as client:
        assert client.get('/api/erp-updates').json()['state'] == 'invalid'


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
        assert daily.run(registry, cfg, now=lambda: STAMP)['status'] == 'busy'
    assert daily.run(registry, cfg, now=lambda: STAMP)['snapshot_hash'] == read_snapshot(cfg.public)['snapshotHash']
    assert calls == ['legacy', 'erp', 'capture', 'capture']


def test_changes_follow_public_revisions_beyond_latest_ten(registry):
    registry.patch('source', 'moel-rss-notice', {'enabled': True}, actor='fixture', reason='fixture', version=0)
    plan = registry.snapshot()
    initial = [
        (
            str(i),
            {
                'title': '가상 글',
                'canonical_url': f'https://www.moel.go.kr/news/notice?id={i}',
                'revision': 1,
                'event': 'new_post' if i == 10 else 'baseline',
            },
        )
        for i in range(11)
    ]
    feeds = {'erp': {'items': []}, 'rules': None, 'news': {}}
    changes, ledger = daily.change_records(plan, {'moel-rss-notice': initial}, feeds, '2026-11-13', {})
    assert len(ledger) == 11 and len(changes) == 1 and changes[0]['url'].endswith('=10')
    updated = [*initial[:-1], ('10', initial[-1][1] | {'revision': 2, 'event': 'post_modified'})]
    changes, ledger = daily.change_records(plan, {'moel-rss-notice': updated}, feeds, '2026-11-14', ledger)
    assert len(changes) == 1 and changes[0]['change'] == 'modified'
    assert daily.change_records(plan, {'moel-rss-notice': updated}, feeds, '2026-11-15', ledger)[0] == []


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
