import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from law_monitor import cli
from law_monitor.events import make_event_id
from law_monitor.state import State
from mon_helpers import LAW_PATH, WAGE_PATH, FakeLawGo, SourceRepo, git, law_md, make_config, make_monitor

WAGE_NAME = '최저임금법'
ADDENDUM = (
    '부칙(개정령) <제4호,2026.11.10>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다.\n\n'
    '제2조(다른 법률의 개정)\n① 최저임금법 일부를 다음과 같이 개정한다.\n제1조를 다음과 같이 한다.'
)


def calendar(*args):
    return json.loads(calendar_command('law_calendar.manage', *args).stdout)


def calendar_command(module, *args):
    return subprocess.run([sys.executable, '-m', module, *map(str, args)], capture_output=True, text=True, check=True)


@pytest.fixture(autouse=True)
def lawgo(monkeypatch):
    provider = FakeLawGo({'42': {'법령': {'기본정보': {}, '개정문': {'개정문내용': ['합성 개정문']}}}})
    monkeypatch.setattr(cli.secrets, 'law_oc', lambda: 'fixture')
    monkeypatch.setattr(cli, 'LawGoClient', provider.client)
    return provider


@pytest.fixture
def amendments(tmp_path):
    repo = SourceRepo(tmp_path / 'legalize')
    original = law_md(WAGE_NAME, {'제1조': '기존 최저임금'})
    repo.commit({WAGE_PATH: original, LAW_PATH: law_md('근로기준법', {'제2조': '기존 조문'})}, 'base')
    amended = law_md(WAGE_NAME, {'제1조': '개정 최저임금'}, promulgated='2026-11-10', addenda=[ADDENDUM], mst='42')
    amended = amended.replace("공포번호: '00001'", "공포번호: '4'")
    sha = repo.commit({WAGE_PATH: amended, LAW_PATH: law_md('근로기준법', {'제2조': '다른 변경'})}, 'amend')
    repo.commit({WAGE_PATH: law_md(WAGE_NAME, {'제1조': '다음 변경'})}, 'later amend')
    home = tmp_path / 'monitor'
    args = ['--home', str(home), 'replay-amendment', '--repo-path', str(repo.path), '--path', WAGE_PATH, '--commit']
    return home, args, original, amended, sha


def test_replay_amendment_dry_run_matches_borrowed_addendum_import(tmp_path, amendments, capsys):
    home, args, original, amended, sha = amendments
    live_source = home / 'data/files/existing.md'
    live_source.parent.mkdir(parents=True)
    live_source.write_text('existing source', encoding='utf-8')
    state = State(home / 'data/state.db')
    state.conn.close()
    existing_files = {path: path.read_bytes() for path in home.rglob('*') if path.is_file()}
    output_dir = tmp_path / 'preview'
    preview_args = [*args, sha, '--dry-run', '--output-dir', str(output_dir)]
    assert cli.main(preview_args) == 0
    output = capsys.readouterr()
    export = json.loads(output.out)
    event_path = Path(export['event_path'])
    assert export == {'event_path': str(event_path), 'lawgo_requests': 1}
    assert event_path.is_relative_to(output_dir / 'data/replay/amendments')
    event = json.loads(event_path.read_text(encoding='utf-8'))
    assert (event_path.parent / event['old']['file']).read_text(encoding='utf-8') == original
    assert (event_path.parent / event['new']['file']).read_text(encoding='utf-8') == amended
    assert {path: path.read_bytes() for path in home.rglob('*') if path.is_file()} == existing_files
    database = tmp_path / 'calendar.db'
    preview = calendar('preview', event_path)
    assert preview == [
        {'article': '제1조', 'effective_on': '2026-11-10', 'decision': 'auto_publish', 'basis': 'borrowed_addendum'}
    ]
    assert output.err == ''
    assert not database.exists()
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    calendar_command('law_calendar.db', database)
    assert calendar('--database', database, 'import', '--monitor-home', home, saved)['imported'] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT article,event_on FROM public_changes').fetchall() == [('제1조', '2026-11-10')]
    assert cli.main(preview_args) == 0
    assert json.loads(capsys.readouterr().out) == {**export, 'lawgo_requests': 0}


def test_replay_dry_run_reports_invalid_addendum_date(amendments, tmp_path, capsys):
    home, args, _, _, _ = amendments
    repo = SourceRepo(tmp_path / 'invalid-date')
    repo.commit({WAGE_PATH: law_md(WAGE_NAME, {'제1조': '이전 조문'})}, 'base')
    added = '부칙 <제00001호,2026.11.10>\n\n제1조(시행일) 이 법은 2026년 2월 30일부터 시행한다.'
    sha = repo.commit(
        {WAGE_PATH: law_md(WAGE_NAME, {'제1조': '이후 조문'}, promulgated='2026-11-10', addenda=[added])}, 'amend'
    )
    args[args.index('--repo-path') + 1] = str(repo.path)
    assert cli.main([*args, sha, '--dry-run', '--output-dir', str(tmp_path / 'preview')]) == 0
    event_path = json.loads(capsys.readouterr().out)['event_path']
    assert calendar('preview', event_path) == [
        {'article': '제1조', 'effective_on': None, 'decision': 'hold', 'basis': 'conflict'}
    ]
    assert not home.exists()
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    database = tmp_path / 'calendar.db'
    calendar_command('law_calendar.db', database)
    assert calendar('--database', database, 'import', '--monitor-home', home, saved)['imported'] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT reason FROM held_changes').fetchall() == [('conflict',)]
        assert not conn.execute('SELECT 1 FROM public_changes').fetchall()


def test_preview_counts_lawgo_retry_attempts(tmp_path, amendments, lawgo, capsys):
    _, args, _, _, sha = amendments
    lawgo.fail = 1
    assert cli.main([*args, sha, '--dry-run', '--output-dir', str(tmp_path / 'preview')]) == 0
    assert json.loads(capsys.readouterr().out)['lawgo_requests'] == len(lawgo.requests) == 2


def test_replay_amendment_writes_one_importable_event_without_registering_it(tmp_path, amendments, capsys):
    home, args, original, amended, sha = amendments
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    event = json.loads(saved.read_text(encoding='utf-8'))
    assert event['change_type'] == 'law_amended' and event['replay']
    assert event['target']['item_id'] == WAGE_PATH
    assert event['context']['commit']['sha'] == sha
    assert (home / event['old']['file']).read_text(encoding='utf-8') == original
    assert (home / event['new']['file']).read_text(encoding='utf-8') == amended
    assert not (home / 'data' / 'state.db').exists()
    assert not (home / 'data' / 'events').exists()
    database = tmp_path / 'calendar.db'
    calendar_command('law_calendar.db', database)
    assert calendar('--database', database, 'import', '--monitor-home', home, saved) == {
        'imported': 1,
        'unchanged': 0,
        'failed': 0,
    }


@pytest.mark.parametrize('has_state', [False, True], ids=['different-home-without-state', 'registered-scope-event'])
def test_replay_amendment_import_keeps_rejected_scope_event_separate(tmp_path, amendments, capsys, has_state):
    home, args, _, amended, sha = amendments
    blob = git(tmp_path / 'legalize', 'rev-parse', f'{sha}:{WAGE_PATH}')
    old_blob = git(tmp_path / 'legalize', 'rev-parse', f'{sha}^:{WAGE_PATH}')
    original_id = make_event_id('legalize_law', WAGE_PATH, blob)
    if has_state:
        state = State(home / 'data' / 'state.db')
        with state.conn:
            state.conn.execute(
                'INSERT INTO events(event_id,source,change_type,created_at) VALUES (?,?,?,?)',
                (original_id, 'legalize_law', 'law_new', '2026-11-15T06:30:00+09:00'),
            )
        state.conn.close()
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    replay = json.loads(saved.read_text(encoding='utf-8'))
    assert replay['event_id'] == make_event_id('legalize_law', WAGE_PATH, f'amendment:{old_blob}:{blob}')

    scope_event = {**replay, 'event_id': original_id, 'change_type': 'law_new', 'old': None}
    scope_path = home / 'scope.json'
    scope_path.write_text(json.dumps(scope_event), encoding='utf-8')
    assert (home / replay['new']['file']).read_text(encoding='utf-8') == amended
    database = tmp_path / 'calendar.db'
    calendar_command('law_calendar.db', database)
    assert calendar('--database', database, 'import', '--monitor-home', home, scope_path)['imported'] == 1
    rejected_id = hashlib.sha256(f'legalize_law:{original_id}'.encode()).hexdigest()
    snapshot_id = calendar('--database', database, 'show', rejected_id)['snapshot_id']
    manifest = tmp_path / 'reject.json'
    manifest.write_text(
        json.dumps(
            {'event_id': rejected_id, 'snapshot_id': snapshot_id, 'decision': 'reject', 'note': 'scope expansion'}
        ),
        encoding='utf-8',
    )
    calendar('--database', database, 'review', manifest, '--reviewer', 'fixture')
    assert calendar('--database', database, 'import', '--monitor-home', home, saved)['imported'] == 1
    assert calendar('--database', database, 'show', rejected_id)['review_state'] == 'rejected'
    assert cli.main([*args, sha]) == 0
    assert home / capsys.readouterr().out.strip() == saved
    assert calendar('--database', database, 'import', '--monitor-home', home, saved)['unchanged'] == 1


def test_replay_amendment_has_same_lawgo_context_as_daily(tmp_path, amendments, lawgo, capsys):
    home, args, original, amended, _ = amendments
    repo = SourceRepo(tmp_path / 'daily-input')
    repo.commit({WAGE_PATH: original}, 'before')
    monitor = make_monitor(tmp_path, make_config(repo), lawgo=lawgo.client())
    monitor.bootstrap(only='legalize_law')
    sha = repo.commit({WAGE_PATH: amended}, 'amend')
    [normal] = monitor.collect(only='legalize_law').events
    args[args.index('--repo-path') + 1] = str(repo.path)
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    replay = json.loads(saved.read_text(encoding='utf-8'))
    assert replay['context'] == normal['context']
    assert replay['context']['lawgo']['mst_new'] == '42'


def test_replay_amendment_refuses_a_creation_commit(tmp_path, amendments, capsys):
    home, args, _, _, _ = amendments
    repo = SourceRepo(tmp_path / 'another-legalize')
    repo.commit({LAW_PATH: law_md('근로기준법', {'제2조': '기존 조문'})}, 'base')
    sha = repo.commit({WAGE_PATH: law_md(WAGE_NAME, {'제1조': '새 법률'})}, 'enact')
    args[args.index('--repo-path') + 1] = str(repo.path)
    assert cli.main([*args, sha, '--dry-run', '--output-dir', str(tmp_path / 'preview')]) == 2
    output = capsys.readouterr()
    assert output.out == '' and '기존 파일의 개정' in output.err
    assert not home.exists()


def test_dry_run_refuses_monitor_home_as_output(amendments, capsys):
    home, args, _, _, sha = amendments
    assert cli.main([*args, sha, '--dry-run', '--output-dir', str(home)]) == 2
    assert '별도' in capsys.readouterr().err
    assert not home.exists()
