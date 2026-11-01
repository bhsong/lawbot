"""실행기(실패 격리·요약·collector_failing)와 CLI·replay."""

import json

import pytest

from law_monitor import cli
from law_monitor.app import AlreadyBootstrapped, BootstrapRequired
from law_monitor.collectors.base import CollectResult
from mon_helpers import LAW_PATH, SourceRepo, law_md, make_config, make_monitor


class OkCollector:
    name = 'ok_source'

    def has_baseline(self, state):
        return True

    def collect(self, ctx):
        return CollectResult()


class BrokenCollector(OkCollector):
    name = 'broken_source'

    def collect(self, ctx):
        raise RuntimeError('접속 실패')


def test_one_failure_does_not_stop_others(tmp_path):
    mon = make_monitor(tmp_path, make_config(), collectors=[BrokenCollector(), OkCollector()])
    summary = mon.collect()
    assert summary.ok == ['ok_source'] and list(summary.failed) == ['broken_source']
    assert summary.line == 'collectors=2 ok=1 failed=1(broken_source) events=0'
    row = mon.state.last_run()
    assert row['status'] == 'partial'
    assert 'RuntimeError' in json.loads(row['summary_json'])['collectors']['broken_source']['error']
    log = next(mon.paths.logs.glob('collector-*.log')).read_text(encoding='utf-8')
    assert log.rstrip().splitlines()[-1].endswith(summary.line)


def test_collector_failing_event_after_three_failures(tmp_path):
    mon = make_monitor(tmp_path, make_config(), collectors=[BrokenCollector(), OkCollector()])
    counts = [len(mon.collect().events) for _ in range(4)]
    assert counts == [0, 0, 1, 0]
    ev = next(mon.paths.events.glob('**/*.json'))
    data = json.loads(ev.read_text(encoding='utf-8'))
    assert data['change_type'] == 'collector_failing' and data['source'] == 'broken_source'
    assert data['context']['consecutive_failures'] == 3


def test_only_runs_one_collector_and_skips_streak(tmp_path):
    mon = make_monitor(tmp_path, make_config(), collectors=[BrokenCollector(), OkCollector()])
    mon.collect()
    mon.collect()
    assert mon.collect(only='ok_source').ok == ['ok_source']  # 실행 안 한 수집기는 연속 실패를 끊지도 늘리지도 않음
    assert len(mon.collect().events) == 1
    with pytest.raises(ValueError, match='없는 수집기'):
        mon.collect(only='nope')


@pytest.fixture
def legalize(tmp_path):
    repo = SourceRepo(tmp_path / 'src-legalize')
    repo.commit({LAW_PATH: law_md('근로기준법', {'제1조 (목적)': 'v1'})}, '초기')
    return repo


def test_collect_requires_bootstrap_and_bootstrap_once(tmp_path, legalize):
    mon = make_monitor(tmp_path, make_config(legalize, laws=[{'name': '근로기준법', 'category': 1}]))
    with pytest.raises(BootstrapRequired):
        mon.collect(only='legalize_law')
    mon.bootstrap(only='legalize_law')
    with pytest.raises(AlreadyBootstrapped):
        mon.bootstrap(only='legalize_law')
    mon.bootstrap(only='legalize_law', force=True)


def test_replay_writes_separately_without_state(tmp_path, legalize):
    first = legalize.head()
    legalize.commit({LAW_PATH: law_md('근로기준법', {'제1조 (목적)': 'v2'})}, '개정 1')
    second = legalize.commit({LAW_PATH: law_md('근로기준법', {'제1조 (목적)': 'v3'})}, '개정 2')
    mon = make_monitor(tmp_path, make_config(legalize, laws=[{'name': '근로기준법', 'category': 1}]))
    events = mon.replay('legalize-kr', first, second)
    assert [e['context']['commit']['subject'] for e in events] == ['개정 1', '개정 2']
    assert all(e['replay'] for e in events)
    out = mon.paths.replay / f'{first[:8]}_{second[:8]}'
    assert len(list(out.glob('*.json'))) == 2 and len(list(out.glob('*.diff'))) == 2
    assert (mon.paths.home / events[0]['diff']['path']).exists()
    assert mon.state.cursor('legalize-kr') is None and not mon.state.event_exists(events[0]['event_id'])
    assert list(mon.paths.events.glob('**/*.json')) == []


def test_replay_folder_uses_resolved_commits(tmp_path, legalize):
    """--from 'abc^'처럼 이름으로 줘도 폴더 이름은 실제 커밋 해시 앞 8자리."""
    first = legalize.head()
    second = legalize.commit({LAW_PATH: law_md('근로기준법', {'제1조 (목적)': 'v2'})}, '개정 1')
    mon = make_monitor(tmp_path, make_config(legalize, laws=[{'name': '근로기준법', 'category': 1}]))
    assert len(mon.replay('legalize-kr', f'{second}^', 'HEAD')) == 1
    assert [p.name for p in mon.paths.replay.iterdir()] == [f'{first[:8]}_{second[:8]}']


def test_cli_end_to_end(tmp_path, legalize, capsys, monkeypatch):
    cfg = tmp_path / 'targets.yaml'
    cfg.write_text(
        f'repos: {{legalize-kr: "{legalize.url}", admrule-kr: "file:///none"}}\n'
        'laws: [{name: 근로기준법, category: 1}]\nadmrules: {names: [], keyword_rules: []}\n',
        encoding='utf-8',
    )
    base = ['--home', str(tmp_path / 'h'), '--config', str(cfg)]
    assert cli.main([*base, 'collect', '--only', 'legalize_law']) == 2
    assert 'bootstrap' in capsys.readouterr().err
    assert cli.main([*base, 'bootstrap', '--only', 'legalize_law']) == 0
    legalize.commit({LAW_PATH: law_md('근로기준법', {'제1조 (목적)': 'v2'})}, '개정')
    assert cli.main([*base, 'collect', '--only', 'legalize_law']) == 0
    assert 'collectors=1 ok=1 failed=0 events=1' in capsys.readouterr().out
    # 모든 수집기 실행: legalize_admrule은 기준점이 없어 실행 전에 거부 (종료 코드 2)
    assert cli.main([*base, 'collect']) == 2
