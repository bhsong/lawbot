import json
from types import SimpleNamespace

import pytest

from law_monitor import checks, cli
from law_monitor.app import Monitor
from law_monitor.collectors.base import CollectResult
from law_monitor.config import load
from law_monitor.paths import Paths
from mon_helpers import LAW_PATH, FakeLawGo, SourceRepo, git, law_md, make_config, make_monitor


@pytest.mark.parametrize('command', ['bootstrap', 'collect', 'check', 'replay'])
def test_non_model_commands_do_not_load_generation_config(tmp_path, monkeypatch, command):
    calls = []

    def invalid_config():
        pytest.fail('Non-model commands must not validate generation credentials')

    class FakeMonitor:
        def __init__(self, *args, **kwargs):
            pass

        def bootstrap(self, **kwargs):
            calls.append('bootstrap')
            return SimpleNamespace(line='ok', failed={})

        def collect(self, **kwargs):
            calls.append('collect')
            return SimpleNamespace(line='ok', failed={})

        def replay(self, *args):
            calls.append('replay')
            self.paths = SimpleNamespace(replay=tmp_path)
            return []

    monkeypatch.setattr(cli, 'load_config', invalid_config)
    monkeypatch.setattr(cli.secrets, 'law_oc', lambda: None)
    monkeypatch.setattr(cli, 'Monitor', FakeMonitor)
    monkeypatch.setattr(cli, '_check', lambda monitor: (calls.append('check'), 0))
    argv = [command]
    if command == 'replay':
        argv += ['--repo', 'legalize-kr', '--from', 'old', '--to', 'new']
    args = cli.build_parser().parse_args(argv)
    assert cli._run(args, Paths(tmp_path)) == 0
    assert calls == [command]


@pytest.mark.parametrize('credentials', ['no-url', 'no-key', 'short-key'])
def test_daily_bad_generation_config_preserves_collection_and_checks(tmp_path, monkeypatch, capsys, credentials):
    calls = []

    class Collector:
        name = 'legalize_admrule'

        def has_baseline(self, state):
            return True

        def collect(self, ctx):
            calls.append('collect')
            return CollectResult()

    monitor = Monitor(Paths(tmp_path), load(), collectors=[Collector()])
    monkeypatch.setattr(monitor, 'refresh_article_repo', lambda: calls.append('article_input'))
    monkeypatch.setattr(cli, 'Monitor', lambda *args, **kwargs: monitor)
    monkeypatch.setattr(cli.secrets, 'law_oc', lambda: None)
    values = {
        'LAWBOT_LLM_BASE_URL': '' if credentials == 'no-url' else 'http://127.0.0.1:9999/v1',
        'LAWBOT_LLM_API_KEY': '' if credentials == 'no-key' else 'short',
    }
    monkeypatch.setattr(cli.secrets, 'get', lambda key, **kwargs: values.get(key, ''))

    def run(command, **kwargs):
        calls.append('calendar' if 'law_calendar.sync' in command else 'articles')
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    monkeypatch.setattr(checks.subprocess, 'run', run)
    plan = tmp_path / 'plan.json'
    plan.write_text(
        json.dumps(
            {
                'run_id': 'daily-bad-generation-config',
                'collectors': ['legalize_admrule'],
                'law_paths': None,
                'articles_enabled': True,
            }
        )
    )
    args = cli.build_parser().parse_args(['daily', '--source-plan', str(plan)])
    assert cli._run(args, monitor.paths) == 2
    assert calls == ['article_input', 'collect', 'calendar', 'articles']
    [row] = monitor.state.conn.execute("SELECT * FROM runs WHERE mode='normal'").fetchall()
    assert row['run_id'] == 'daily-bad-generation-config'
    assert json.loads(row['summary_json'])['collectors']['legalize_admrule']['status'] == 'ok'
    assert '요약 모델 설정이 없습니다' in capsys.readouterr().err


@pytest.mark.parametrize('articles_enabled', [True, False], ids=['law-not-due', 'law-disabled'])
def test_managed_daily_uses_one_normal_run_and_respects_law_switch(tmp_path, monkeypatch, articles_enabled):
    calls = []

    class Collector:
        name = 'legalize_admrule'

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
                'collectors': ['legalize_admrule'],
                'law_paths': None,
                'articles_enabled': articles_enabled,
            }
        )
    )
    selection = cli._daily_selection(plan_file)
    assert cli._daily(monitor, None, selection=selection) == 0
    expected = (
        ['article_input', 'legalize_admrule', 'calendar', 'articles', 'summary']
        if articles_enabled
        else [
            'legalize_admrule',
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
        'collectors': {'legalize_admrule': {'status': 'ok', 'events': 0}},
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


def test_managed_scope_expansion_records_baseline_then_collects_real_amendment(tmp_path):
    repo = SourceRepo(tmp_path / 'source')
    decree = 'kr/근로기준법/시행령.md'
    creation = 'kr/근로기준법/시행규칙.md'
    original = law_md('근로기준법 시행령', {'제1조': '기존 조문'})
    repo.commit({LAW_PATH: law_md('근로기준법', {'제1조': '법률'}), decree: original}, 'baseline')
    monitor = make_monitor(tmp_path, make_config(repo), lawgo=FakeLawGo({}).client())
    collector = monitor.collectors[0]
    collector.matcher.selected_paths = frozenset({LAW_PATH})
    monitor.bootstrap(only='legalize_law')
    collector.enricher = None
    monitor.collect_managed('daily-initial', ['legalize_law'], [LAW_PATH])
    assert set(monitor.state.items('legalize_law')) == {LAW_PATH}

    expanded = [LAW_PATH, decree]
    result = monitor.collect_managed('daily-expanded', ['legalize_law'], expanded)
    assert result.ok == ['legalize_law'] and result.events == []
    assert '기준점 추가 1개' in result.line
    resumed = monitor.collect_managed('daily-expanded', ['legalize_law'], expanded)
    assert '기준점 추가 1개' in resumed.line
    assert not list(monitor.paths.events.glob('**/*.json'))
    baseline = monitor.state.items('legalize_law')[decree]
    assert baseline.version_key == git(repo.path, 'rev-parse', f'HEAD:{decree}')
    assert (monitor.paths.files / f'{baseline.version_key}.md').read_text(encoding='utf-8') == original

    sha = repo.commit({decree: law_md('근로기준법 시행령', {'제1조': '개정 조문'})}, 'amend')
    new_sha = repo.commit({creation: law_md('근로기준법 시행규칙', {'제1조': '새 규칙'})}, 'enact')
    result = monitor.collect_managed('daily-next', ['legalize_law'], [*expanded, creation])
    amended, new = result.events
    assert (amended['change_type'], amended['context']['commit']['sha']) == ('law_amended', sha)
    assert amended['old']['version_key'] == baseline.version_key
    assert (new['change_type'], new['context']['commit']['sha'], new['old']) == ('law_new', new_sha, None)


def test_managed_scope_expansion_seeds_only_new_paths_after_force_push(tmp_path):
    repo = SourceRepo(tmp_path / 'source')
    decree = 'kr/근로기준법/시행령.md'
    tracked_rule = 'kr/근로기준법/시행규칙.md'
    original = law_md('근로기준법', {'제1조': '기존 조문'})
    repo.commit({LAW_PATH: original, decree: law_md('근로기준법 시행령', {'제1조': '기존 시행령'})}, 'base')
    monitor = make_monitor(tmp_path, make_config(repo))
    previous = [LAW_PATH, tracked_rule]
    monitor.collectors[0].matcher.selected_paths = frozenset(previous)
    monitor.bootstrap(only='legalize_law')
    monitor.collect_managed('daily-before', ['legalize_law'], previous)
    repo.commit(
        {LAW_PATH: law_md('근로기준법', {'제1조': '개정'}), tracked_rule: law_md('시행규칙', {'제1조': '제정'})},
        'change',
    )
    repo.rewrite_history()

    result = monitor.collect_managed('daily-rewritten', ['legalize_law'], [*previous, decree])
    assert '기준점 추가 1개' in result.line
    assert {event['target']['item_id']: event['change_type'] for event in result.events} == {
        LAW_PATH: 'law_amended',
        tracked_rule: 'law_new',
    }
    assert all(event['context']['fallback'] == 'content_compare' for event in result.events)
    assert monitor.state.items('legalize_law')[decree].version_key == git(repo.path, 'rev-parse', f'HEAD:{decree}')


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
