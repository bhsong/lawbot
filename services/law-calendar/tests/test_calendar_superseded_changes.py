"""Keep replaced law changes out of the review queue without changing current holds."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from law_calendar import ingest
from law_calendar.ingest import import_event
from law_calendar.review import review
from law_calendar.server import create_app
from test_calendar_workflow import approval, import_one
from test_calendar_workflow import workflow as workflow


def rows(database, query):
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute(query).fetchall()


def manage(database, *args):
    return subprocess.run(
        [sys.executable, '-m', 'law_calendar.manage', '--database', str(database), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def numbered_change(workflow):
    db, home, path, event = workflow
    before = '##### 제110조\n\n공통 본문\n\n  1\\. 종전 규정\n'
    (home / event['old']['file']).write_text(before, encoding='utf-8')
    (home / event['new']['file']).write_text(before.replace('종전', '개정'), encoding='utf-8')
    event['diff']['chunks'] = [{'article': '제110조'}]
    event['context'].pop('lawgo')
    path.write_text(json.dumps(event), encoding='utf-8')
    return db, home, path, event


def scope_effective_date(path, event):
    event['context']['addenda_added'] = [
        '이 법은 공포한 날부터 시행한다. 다만, 제110조제1호의 개정규정은 2026년 11월 5일부터 시행한다.'
    ]
    path.write_text(json.dumps(event), encoding='utf-8')


def test_reimport_with_new_label_withdraws_old_hold_and_auto_publishes(numbered_change, monkeypatch):
    db, home, path, event = numbered_change
    event_id = import_one(numbered_change)
    [(stale_id, article)] = rows(db, 'SELECT id,article FROM article_changes')
    assert article == '제110조'
    assert [r[0] for r in rows(db, 'SELECT change_id FROM held_changes')] == [stale_id]

    scope_effective_date(path, event)
    assert import_event(db, home, path)[0]
    [(snapshot_id,)] = rows(db, 'SELECT snapshot_id FROM event_sources')
    assert rows(db, 'SELECT article,review_state FROM article_changes ORDER BY article') == [
        ('제110조', 'withdrawn'),
        ('제110조 제1호', 'approved'),
    ]
    assert rows(db, "SELECT verified_at FROM article_changes WHERE article='제110조'") == [(None,)]
    assert rows(db, 'SELECT change_id FROM held_changes') == []
    assert rows(db, 'SELECT article,effective_on FROM public_changes') == [('제110조 제1호', '2026-11-05')]
    assert rows(db, 'SELECT review_state FROM change_events') == [('approved',)]
    [(audited_event, reviewer, note, audited_snapshot, payload)] = rows(
        db,
        'SELECT a.event_id,a.reviewer,a.note,d.snapshot_id,d.payload_json '
        "FROM review_audit a JOIN review_details d ON d.audit_id=a.id WHERE a.decision='withdraw'",
    )
    assert (audited_event, reviewer, note, audited_snapshot) == (
        event_id,
        'import-adapter',
        f'Superseded by snapshot {snapshot_id}',
        snapshot_id,
    )
    assert json.loads(payload) == {'change_ids': [stale_id]}

    monkeypatch.setattr(ingest, 'PARSER_VERSION', 'fixture-rule-upgrade')
    assert import_event(db, home, path)[0]
    assert rows(db, "SELECT COUNT(*) FROM review_audit WHERE decision='withdraw'") == [(1,)]

    event['context'].pop('addenda_added')
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_event(db, home, path)[0]
    assert rows(db, "SELECT id,review_state FROM article_changes WHERE article='제110조'") == [(stale_id, 'pending')]
    assert rows(db, 'SELECT id FROM public_holds') == [(stale_id,)]


def test_same_label_reimport_keeps_change_id_and_refreshes_hold(numbered_change):
    db, home, path, event = numbered_change
    import_one(numbered_change)
    [(change_id, old_snapshot)] = rows(db, 'SELECT change_id,snapshot_id FROM held_changes')
    event['context']['amendment_reason'] = '수집본 정정'
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_event(db, home, path)[0]
    [(new_snapshot,)] = rows(db, 'SELECT snapshot_id FROM event_sources')
    assert new_snapshot != old_snapshot
    assert rows(db, 'SELECT id,review_state FROM article_changes') == [(change_id, 'pending')]
    assert rows(db, 'SELECT change_id,snapshot_id FROM held_changes') == [(change_id, new_snapshot)]
    assert rows(db, "SELECT id FROM review_audit WHERE decision='withdraw'") == []


@pytest.mark.parametrize(('decision', 'state'), [('reject', 'rejected'), ('withdraw', 'withdrawn')])
def test_reimport_respects_terminal_event_review(numbered_change, decision, state):
    db, home, path, event = numbered_change
    event_id = import_one(numbered_change)
    review(db, {**approval(db, event_id), 'decision': decision, 'changes': []}, reviewer='fixture')
    scope_effective_date(path, event)
    assert import_event(db, home, path)[0]
    assert rows(db, 'SELECT review_state FROM change_events') == [(state,)]
    assert rows(db, 'SELECT article,review_state FROM article_changes ORDER BY article') == [
        ('제110조', 'withdrawn'),
        ('제110조 제1호', state),
    ]
    assert rows(db, 'SELECT change_id FROM held_changes') == []


@pytest.fixture
def legacy_holds(workflow):
    db, home, path, event = workflow
    import_one(workflow)
    [(old_snapshot,)] = rows(db, 'SELECT snapshot_id FROM event_sources')
    event['context']['lawgo']['article_effective'] = '20261105:제1조'
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_event(db, home, path)[0]
    with closing(sqlite3.connect(db)) as conn, conn:
        stale = conn.execute("SELECT id FROM article_changes WHERE article='제2조'").fetchone()[0]
        conn.execute('UPDATE change_versions SET snapshot_id=? WHERE change_id=?', (old_snapshot, stale))
        conn.execute('UPDATE held_changes SET snapshot_id=? WHERE change_id=?', (old_snapshot, stale))
        conn.execute("UPDATE article_changes SET verified_at='2026-11-05' WHERE id=?", (stale,))
    return stale


def test_supersede_stale_preview_and_apply_preserve_approved_event(workflow, legacy_holds):
    db, home, path, _ = workflow
    [(event_id, snapshot_id)] = rows(db, 'SELECT event_id,snapshot_id FROM event_sources')
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            'INSERT INTO article_changes(id,event_id,article,summary,review_state,verified_at) '
            "VALUES ('unlinked',?,'제4조','예전 조문','approved','2026-11-05')",
            (event_id,),
        )
    current_changes = "SELECT * FROM article_changes WHERE article IN ('제1조','제3조') ORDER BY article"
    changes = rows(db, current_changes)
    events = rows(db, 'SELECT * FROM change_events')
    assert rows(db, 'SELECT review_state FROM change_events') == [('approved',)]
    original_db = db.read_bytes()
    preview = manage(db, 'supersede-stale')
    assert preview.returncode == 0, preview.stderr
    candidates = [
        {'change_id': legacy_holds, 'article': '제2조', 'event_id': event_id, 'snapshot_id': snapshot_id},
        {'change_id': 'unlinked', 'article': '제4조', 'event_id': event_id, 'snapshot_id': snapshot_id},
    ]
    assert json.loads(preview.stdout) == candidates
    assert db.read_bytes() == original_db

    applied = manage(db, 'supersede-stale', '--apply')
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout) == candidates
    assert rows(db, "SELECT review_state,verified_at FROM article_changes WHERE article IN ('제2조','제4조')") == [
        ('withdrawn', None),
        ('withdrawn', None),
    ]
    assert legacy_holds not in {r[0] for r in rows(db, 'SELECT change_id FROM held_changes')}
    [(payload,)] = rows(
        db,
        "SELECT d.payload_json FROM review_audit a JOIN review_details d ON d.audit_id=a.id WHERE a.decision='withdraw'",
    )
    assert json.loads(payload) == {'change_ids': [legacy_holds, 'unlinked']}
    assert not import_event(db, home, path)[0]
    assert rows(db, current_changes) == changes
    assert rows(db, 'SELECT * FROM change_events') == events

    applied_db = db.read_bytes()
    repeated = manage(db, 'supersede-stale', '--apply')
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout) == []
    assert db.read_bytes() == applied_db


def test_holds_check_reports_both_counts_and_fails_on_either(workflow, legacy_holds):
    db = workflow[0]

    def check(outside, hidden):
        result = manage(db, 'holds-check')
        assert result.returncode == int(bool(outside or hidden)), result.stderr
        assert json.loads(result.stdout) == {'pending_outside_snapshot': outside, 'hidden_pending_holds': hidden}

    check(1, 1)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('DELETE FROM held_changes WHERE change_id=?', (legacy_holds,))
    check(1, 0)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE article_changes SET review_state='withdrawn' WHERE id=?", (legacy_holds,))
        conn.execute("UPDATE change_events SET source_url=NULL WHERE review_state='approved'")
    check(0, 1)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('UPDATE change_events SET source_url=?', (workflow[3]['context']['source_url'],))
    check(0, 0)


@pytest.mark.integration
def test_stale_cleanup_preserves_public_hold_list_response(workflow, legacy_holds):
    db = workflow[0]
    with TestClient(create_app(db)) as client:
        before = client.get('/api/law-holds')
        assert before.status_code == 200
        assert [item['article'] for item in before.json()['items']] == ['제3조']
        assert all(item['id'] != legacy_holds for item in before.json()['items'])
        applied = manage(db, 'supersede-stale', '--apply')
        assert applied.returncode == 0, applied.stderr
        after = client.get('/api/law-holds')
        assert (after.status_code, after.json()) == (before.status_code, before.json())
