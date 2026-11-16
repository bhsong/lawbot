import json

import pytest

from law_calendar.db import migrate
from law_calendar.ingest import digest, import_files
from law_calendar.review import inspect_event, review
from law_monitor import cli
from law_monitor.events import make_event_id
from law_monitor.state import State
from mon_helpers import LAW_PATH, WAGE_PATH, SourceRepo, git, law_md

WAGE_NAME = '최저임금법'
ADDENDUM = '부칙(개정령) <제4호,2026.11.10>\n\n제1조(시행일) 이 법은 공포한 날부터 시행한다.'


@pytest.fixture
def amendments(tmp_path):
    repo = SourceRepo(tmp_path / 'legalize')
    original = law_md(WAGE_NAME, {'제1조': '기존 최저임금'})
    repo.commit({WAGE_PATH: original, LAW_PATH: law_md('근로기준법', {'제2조': '기존 조문'})}, 'base')
    amended = law_md(WAGE_NAME, {'제1조': '개정 최저임금'}, promulgated='2026-11-10', addenda=[ADDENDUM])
    sha = repo.commit({WAGE_PATH: amended, LAW_PATH: law_md('근로기준법', {'제2조': '다른 변경'})}, 'amend')
    repo.commit({WAGE_PATH: law_md(WAGE_NAME, {'제1조': '다음 변경'})}, 'later amend')
    home = tmp_path / 'monitor'
    args = ['--home', str(home), 'replay-amendment', '--repo-path', str(repo.path), '--path', WAGE_PATH, '--commit']
    return home, args, original, amended, sha


def test_replay_amendment_dry_run_only_prints_articles_and_addenda_dates(amendments, capsys):
    home, args, _, _, sha = amendments
    assert cli.main([*args, sha, '--dry-run']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {'articles': ['제1조'], 'addenda_effective_dates': ['2026-11-10']}
    assert output.err == ''
    assert not home.exists()


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


def test_replay_amendment_import_keeps_rejected_scope_event_separate(tmp_path, amendments, capsys):
    home, args, _, amended, sha = amendments
    blob = git(tmp_path / 'legalize', 'rev-parse', f'{sha}:{WAGE_PATH}')
    original_id = make_event_id('legalize_law', WAGE_PATH, blob)
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
    assert replay['event_id'] != original_id

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
