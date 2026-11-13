import json
from types import SimpleNamespace

from law_monitor import cli
from law_monitor.app import Monitor
from law_monitor.collectors.base import CollectResult
from law_monitor.config import load
from law_monitor.paths import Paths
from mon_helpers import FakeLawGo, SourceRepo, law_md, make_config, make_monitor


def test_managed_daily_uses_one_normal_run_and_preserves_checks(tmp_path, monkeypatch):
    calls = []

    class Collector:
        name = 'assembly_bill'

        def has_baseline(self, state):
            return True

        def collect(self, ctx):
            calls.append(self.name)
            return CollectResult()

    monitor = Monitor(Paths(tmp_path), load(), collectors=[Collector()])
    monkeypatch.setattr(monitor, 'refresh_article_repo', lambda: calls.append('article_input'), raising=False)
    monkeypatch.setattr(monitor, 'check', lambda: calls.extend(['calendar', 'articles']) or [])
    monkeypatch.setattr(cli, '_summarize', lambda *args, **kwargs: calls.append('summary') or 0)
    selection = SimpleNamespace(run_id='daily-abc', collectors=['assembly_bill'], law_paths=None)
    assert cli._daily(monitor, None, selection=selection) == 0
    assert calls == ['article_input', 'assembly_bill', 'calendar', 'articles', 'summary']
    rows = monitor.state.conn.execute("SELECT * FROM runs WHERE mode='normal'").fetchall()
    [row] = rows
    assert row['run_id'] == selection.run_id
    assert json.loads(row['summary_json']) == {
        'managed': True,
        'collectors': {'assembly_bill': {'status': 'ok', 'events': 0}},
        'events': 0,
    }


def test_managed_scope_excludes_disabled_law_and_recovers_it_on_reenable(tmp_path):
    repo = SourceRepo(tmp_path / 'source')
    enabled = 'kr/근로기준법/법률.md'
    disabled = 'kr/최저임금법/법률.md'
    repo.commit(
        {
            enabled: law_md('근로기준법', {'제1조 (목적)': '기존'}),
            disabled: law_md('최저임금법', {'제1조 (목적)': '기존'}),
        },
        'baseline',
    )
    config = make_config(repo, laws=[{'name': '근로기준법', 'category': 1}, {'name': '최저임금법', 'category': 1}])
    monitor = make_monitor(tmp_path, config, lawgo=FakeLawGo({}).client())
    monitor.bootstrap(only='legalize_law')
    monitor.collectors[0].enricher = None
    repo.commit(
        {
            enabled: law_md('근로기준법', {'제1조 (목적)': '변경'}),
            disabled: law_md('최저임금법', {'제1조 (목적)': '변경'}),
        },
        'changes',
    )
    summary = monitor.collect_managed('daily-01', ['legalize_law'], [enabled])
    assert [event['target']['item_id'] for event in summary.events] == [enabled]
    summary = monitor.collect_managed('daily-02', ['legalize_law'], [enabled, disabled])
    assert [event['target']['item_id'] for event in summary.events] == [disabled]
