import json
import sqlite3

import pytest

from law_calendar.db import migrate
from law_calendar.ingest import digest, import_files
from law_calendar.review import inspect_event, review
from law_monitor import cli
from law_monitor.events import make_event_id
from law_monitor.state import State
from mon_helpers import LAW_PATH, WAGE_PATH, FakeLawGo, SourceRepo, git, law_md, make_config, make_monitor

WAGE_NAME = '최저임금법'
ADDENDUM = (
    '부칙(개정령) <제4호,2026.11.10>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다.\n\n'
    '제2조(다른 법률의 개정)\n① 최저임금법 일부를 다음과 같이 개정한다.\n제1조를 다음과 같이 한다.'
)


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
    home, args, _, _, sha = amendments
    assert cli.main([*args, sha, '--dry-run']) == 0
    output = capsys.readouterr()
    preview = json.loads(output.out)
    assert preview['lawgo_requests'] == 1
    assert preview['articles'] == [
        {'article': '제1조', 'effective_on': '2026-11-10', 'decision': 'auto_publish', 'basis': 'borrowed_addendum'}
    ]
    assert output.err == ''
    assert not home.exists()
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    database = tmp_path / 'calendar.db'
    migrate(database)
    assert import_files(database, home, [saved])['imported'] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT article,event_on FROM public_changes').fetchall() == [('제1조', '2026-11-10')]
    assert cli.main([*args, sha, '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out) == {**preview, 'lawgo_requests': 0}


def test_replay_dry_run_reports_invalid_addendum_date(amendments, tmp_path, capsys):
    home, args, _, _, _ = amendments
    repo = SourceRepo(tmp_path / 'invalid-date')
    repo.commit({WAGE_PATH: law_md(WAGE_NAME, {'제1조': '이전 조문'})}, 'base')
    added = '부칙 <제00001호,2026.11.10>\n\n제1조(시행일) 이 법은 2026년 2월 30일부터 시행한다.'
    sha = repo.commit(
        {WAGE_PATH: law_md(WAGE_NAME, {'제1조': '이후 조문'}, promulgated='2026-11-10', addenda=[added])}, 'amend'
    )
    args[args.index('--repo-path') + 1] = str(repo.path)
    assert cli.main([*args, sha, '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['articles'] == [
        {'article': '제1조', 'effective_on': None, 'decision': 'hold', 'basis': 'conflict'}
    ]
    assert not home.exists()
    assert cli.main([*args, sha]) == 0
    saved = home / capsys.readouterr().out.strip()
    database = tmp_path / 'calendar.db'
    migrate(database)
    assert import_files(database, home, [saved])['imported'] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT reason FROM held_changes').fetchall() == [('conflict',)]
        assert not conn.execute('SELECT 1 FROM public_changes').fetchall()


def test_preview_counts_lawgo_retry_attempts(amendments, lawgo, capsys):
    _, args, _, _, sha = amendments
    lawgo.fail = 1
    assert cli.main([*args, sha, '--dry-run']) == 0
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
    migrate(database)
    assert import_files(database, home, [saved]) == {'imported': 1, 'unchanged': 0, 'failed': 0}


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
    migrate(database)
    assert import_files(database, home, [scope_path])['imported'] == 1
    rejected_id = digest(f'legalize_law:{original_id}')
    snapshot_id = inspect_event(database, rejected_id)['snapshot_id']
    review(
        database,
        {'event_id': rejected_id, 'snapshot_id': snapshot_id, 'decision': 'reject', 'note': 'scope expansion'},
        reviewer='fixture',
    )
    assert import_files(database, home, [saved])['imported'] == 1
    assert inspect_event(database, rejected_id)['review_state'] == 'rejected'
    assert cli.main([*args, sha]) == 0
    assert home / capsys.readouterr().out.strip() == saved
    assert import_files(database, home, [saved])['unchanged'] == 1


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
    assert cli.main([*args, sha, '--dry-run']) == 2
    output = capsys.readouterr()
    assert output.out == '' and '기존 파일의 개정' in output.err
    assert not home.exists()
