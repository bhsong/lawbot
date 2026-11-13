import json
from types import SimpleNamespace

import pytest

from law_monitor import checks, cli
from law_monitor.app import Monitor
from law_monitor.collectors.base import CollectResult
from law_monitor.config import load
from law_monitor.paths import Paths
from mon_helpers import FakeLawGo, SourceRepo, law_md, make_config, make_monitor


@pytest.mark.parametrize('articles_enabled', [True, False], ids=['law-not-due', 'law-disabled'])
def test_managed_daily_uses_one_normal_run_and_respects_law_switch(tmp_path, monkeypatch, articles_enabled):
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

    def run(command, **_):
        if 'law_calendar.sync' in command:
            calls.append('calendar')
        else:
            assert command[0].endswith('/lawbot-articles-update')
            calls.append('articles')
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    monkeypatch.setattr(checks.subprocess, 'run', run)
    monkeypatch.setattr(cli, '_summarize', lambda *args, **kwargs: calls.append('summary') or 0)
    plan_file = tmp_path / 'plan.json'
    plan_file.write_text(
        json.dumps(
            {
                'run_id': 'daily-abc',
                'collectors': ['assembly_bill'],
                'law_paths': None,
                'articles_enabled': articles_enabled,
            }
        )
    )
    selection = cli._daily_selection(plan_file)
    assert cli._daily(monitor, None, selection=selection) == 0
    expected = (
        ['article_input', 'assembly_bill', 'calendar', 'articles', 'summary']
        if articles_enabled
        else [
            'assembly_bill',
            'calendar',
            'summary',
        ]
    )
    assert calls == expected
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
    first = repo.commit(
        {
            enabled: law_md('근로기준법', {'제1조 (목적)': '변경'}),
            disabled: law_md('최저임금법', {'제1조 (목적)': '첫 변경'}),
        },
        'changes',
    )
    summary = monitor.collect_managed('daily-01', ['legalize_law'], [enabled], disabled_law_paths=[disabled])
    assert [event['target']['item_id'] for event in summary.events] == [enabled]
    second = repo.commit({disabled: law_md('최저임금법', {'제1조 (목적)': '둘째 변경'})}, 'more changes')
    assert not monitor.collect_managed('daily-02', ['legalize_law'], [enabled], disabled_law_paths=[disabled]).events
    third = repo.commit({disabled: law_md('최저임금법', {'제1조 (목적)': '셋째 변경'})}, 'new changes')
    summary = monitor.collect_managed('daily-03', ['legalize_law'], [enabled, disabled])
    assert [event['target']['item_id'] for event in summary.events] == [disabled, disabled, disabled]
    assert [event['context']['commit']['sha'] for event in summary.events] == [first, second, third]
    assert not monitor.collect_managed('daily-04', ['legalize_law'], [enabled, disabled]).events


def test_managed_plan_cannot_select_the_git_collector_with_article_input_off(tmp_path):
    plan = tmp_path / 'plan.json'
    plan.write_text(
        json.dumps(
            {
                'run_id': 'daily-inconsistent',
                'collectors': ['legalize_law'],
                'law_paths': None,
                'articles_enabled': False,
            }
        )
    )
    with pytest.raises(ValueError, match='조문 갱신 설정'):
        cli._daily_selection(plan)


def test_cli_managed_assembly_selection_initializes_the_client(tmp_path, monkeypatch):
    assert not load().assembly_enabled
    monkeypatch.setattr(cli.secrets, 'get', lambda key: 'fixture' if key == 'LAW_ASSEMBLY_KEY' else None)
    monkeypatch.setattr(cli.secrets, 'law_oc', lambda: None)
    monkeypatch.setattr(cli, 'load_config', lambda: None)
    plan = tmp_path / 'plan.json'
    plan.write_text(
        json.dumps(
            {
                'run_id': 'daily-assembly',
                'collectors': ['assembly_notice'],
                'law_paths': None,
                'articles_enabled': False,
            }
        )
    )

    def inspect(monitor, only, *, selection):
        assert selection.collectors == ['assembly_notice']
        assert 'assembly_notice' in {collector.name for collector in monitor.collectors}
        return 0

    monkeypatch.setattr(cli, '_daily', inspect)
    assert cli.main(['--home', str(tmp_path), 'daily', '--source-plan', str(plan)]) == 0
