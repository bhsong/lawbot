import hashlib
import json

import pytest

from lawbot.dataset_store import insert_feedback_qa, retire_feedback_qa
from lawbot.db.connection import connect, transaction
from lawbot.qa_rules import Overlap
from test_migrate_csv_to_sqlite import db_path as db_path

GOLD = [{'law': '근로기준법', 'article_no': '제26조'}]
QUESTION = 'abcdefghijklmnopqrstuvwxyz0123456789ABCD'
LEFT = 'xxx' + QUESTION[3:]
RIGHT = QUESTION[:-3] + 'yyy'
AT = '2026-11-08T00:00:00+00:00'


def run(db, *args):
    from lawbot_tools.assign_splits import main

    return main(['--db', str(db), '--split-set', 'feedback', '--val-frac', '0.5', '--seed', '42', *args])


def add_question(db, name, question):
    with connect(db, write=True) as conn, transaction(conn):
        uid, _ = insert_feedback_qa(
            conn,
            key=f'FB-{name}-r1',
            feedback_id=name,
            question=question,
            answer='합성 답변',
            gold=GOLD,
            rejected=None,
            at=AT,
        )
    return uid


def splits(db):
    with connect(db) as conn:
        return {
            (uid, purpose): (split, key, at)
            for uid, purpose, split, key, at in conn.execute(
                'SELECT qa_uid,purpose,split,group_key,assigned_at FROM qa_splits'
            )
        }


def test_transitive_group_never_crosses_train_and_val(db_path):
    uids = [
        add_question(db_path, name, question) for name, question in zip(('01', '02', '03'), (LEFT, RIGHT, QUESTION))
    ]
    assert Overlap([('feedback', 'left', LEFT)]).match(RIGHT) is None
    assert run(db_path, '--sllm-val-frac', '1') == 0
    rows = splits(db_path)
    assert {rows[uid, 'emb'][:2] for uid in uids} == {('train', min(uids))}
    assert {rows[uid, 'sllm'][:2] for uid in uids} == {('val', min(uids))}
    with connect(db_path) as conn:
        policy = json.loads(conn.execute("SELECT policy FROM split_sets WHERE split_set='feedback'").fetchone()[0])
    assert policy['seed'] == 42
    assert policy['results']['emb'] == {'assigned': 3, 'inherited': 0, 'conflicts': 0}


def test_incremental_assignment_inherits_retired_group_and_its_key(db_path):
    old = add_question(db_path, '01', QUESTION)
    assert run(db_path) == 0
    original = splits(db_path)
    with connect(db_path, write=True) as conn, transaction(conn):
        retire_feedback_qa(conn, old)
    new = add_question(db_path, '00', LEFT)
    assert new < old and hashlib.sha256(f'42:{new}'.encode()).digest()[0] < 128
    assert run(db_path) == 0
    current = splits(db_path)
    for purpose in ('emb', 'sllm'):
        assert original[old, purpose][:2] == ('train', old)
        assert current[old, purpose] == original[old, purpose]
        assert current[new, purpose][:2] == original[old, purpose][:2]
    assert run(db_path) == 0
    assert splits(db_path) == current


def test_incremental_bridge_reports_conflict_without_reassigning(db_path, capsys):
    train = add_question(db_path, '01', LEFT)
    val = add_question(db_path, '02', RIGHT)
    assert run(db_path) == 0
    original = splits(db_path)
    assert original[train, 'emb'][0] == 'train'
    assert original[val, 'emb'][0] == 'val'
    bridge = add_question(db_path, '03', QUESTION)
    independent = add_question(db_path, '04', '서로 닮지 않은 새 질문')
    capsys.readouterr()
    assert run(db_path) == 1
    report = capsys.readouterr().out
    assert '충돌' in report and bridge in report
    current = splits(db_path)
    assert all(current[key] == value for key, value in original.items())
    assert all(current[bridge, purpose][0] == 'unassigned' for purpose in ('emb', 'sllm'))
    assert current[independent, 'emb'][0] == 'train'


def test_assignment_aborts_when_state_changes_during_similarity(db_path, monkeypatch):
    uid = add_question(db_path, '01', LEFT)
    other = add_question(db_path, '02', QUESTION)
    original = Overlap.match
    changed = False

    def match(index, question):
        nonlocal changed
        if not changed:
            with connect(db_path, write=True) as conn, transaction(conn):
                conn.execute("UPDATE qa_splits SET split='val' WHERE qa_uid=? AND purpose='emb'", (uid,))
            changed = True
        return original(index, question)

    monkeypatch.setattr(Overlap, 'match', match)
    assert run(db_path) == 2
    rows = splits(db_path)
    assert rows[uid, 'emb'][0] == 'val'
    assert rows[other, 'emb'][0] == rows[other, 'sllm'][0] == 'unassigned'


def test_assignment_transaction_rolls_back_on_write_error(db_path):
    add_question(db_path, '01', QUESTION)
    before = splits(db_path)
    with connect(db_path, write=True) as conn, transaction(conn):
        conn.execute(
            "CREATE TRIGGER reject_assignment BEFORE UPDATE ON qa_splits WHEN NEW.purpose='sllm' "
            "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    assert run(db_path) == 2
    assert splits(db_path) == before
    with connect(db_path) as conn:
        assert conn.execute("SELECT policy FROM split_sets WHERE split_set='feedback'").fetchone() == ('{}',)


@pytest.mark.parametrize('args', [('--seed', '7'), ('--val-frac', '0.25'), ('--sllm-val-frac', '0.25')])
def test_existing_assignments_lock_the_recorded_policy(db_path, args, capsys):
    old = add_question(db_path, '01', LEFT)
    assert run(db_path) == 0
    with connect(db_path, write=True) as conn, transaction(conn):
        retire_feedback_qa(conn, old)
    new = add_question(db_path, '02', '서로 닮지 않은 새 질문')
    before = splits(db_path)
    with connect(db_path) as conn:
        policy = conn.execute("SELECT policy FROM split_sets WHERE split_set='feedback'").fetchone()[0]
    capsys.readouterr()

    assert run(db_path, *args) == 2
    assert '정책' in capsys.readouterr().out
    assert splits(db_path) == before
    with connect(db_path) as conn:
        assert conn.execute("SELECT policy FROM split_sets WHERE split_set='feedback'").fetchone() == (policy,)
    assert run(db_path) == 0
    assert splits(db_path)[new, 'emb'][0] == 'val'


def test_assignment_empty_db_and_cli_errors(db_path):
    assert run(db_path) == 0
    missing = db_path.with_name('missing.db')
    assert run(missing) == 2
    assert not missing.exists()
    with pytest.raises(SystemExit) as exc:
        run(db_path, '--val-frac', 'nan')
    assert exc.value.code == 2
