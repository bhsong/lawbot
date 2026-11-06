"""Offline monitor files through review and withdrawal, using synthetic text only."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from law_calendar.db import migrate
from law_calendar.ingest import import_files
from law_calendar.review import inspect_event, review
from law_calendar.server import create_app

NOW = '2026-11-05T10:00:00+09:00'
SOURCE = 'https://www.law.go.kr/법령/예시'


@pytest.fixture
def workflow(tmp_path):
    home = tmp_path / 'monitor'
    files = home / 'data' / 'files'
    files.mkdir(parents=True)
    (files / 'old.md').write_text(
        '---\n제목: 예시\n---\n# 예시\n\n##### 제1조 (예시)\n\n이전 원문  \n\n'
        '##### 제2조 (삭제)\n삭제 원문\n## 부칙\n부칙 <예시>\n경과조치 원문\n',
        encoding='utf-8',
    )
    (files / 'new.md').write_text(
        '---\n제목: 예시\n---\n# 예시\n\n##### 제1조 (예시)\n\n이후 원문  \n\n'
        '##### 제3조 (신설)\n신설 원문\n## 부칙\n부칙 <예시>\n새 경과조치 원문\n',
        encoding='utf-8',
    )
    event = {
        'event_id': 'upstream-id',
        'source': 'legalize_law',
        'change_type': 'law_amended',
        'detected_at': NOW,
        'target': {'law_id': 'synthetic', 'law_name': '[예시] 가상법'},
        'dates': {'promulgation': '2026-10-01', 'effective': '2026-10-01'},
        'old': {'file': 'data/files/old.md', 'version_key': 'old-blob'},
        'new': {'file': 'data/files/new.md', 'version_key': 'new-blob'},
        'diff': {'chunks': [{'article': a} for a in ('제1조', '제2조', '제3조', '부칙 <예시>')]},
        'context': {'source_url': SOURCE, 'lawgo': {'article_effective': '20261106:제3조제1항'}},
    }
    event_file = home / 'event.json'
    event_file.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
    db = tmp_path / 'calendar.db'
    migrate(db)
    return db, home, event_file, event


def import_one(workflow):
    db, home, event_file, _ = workflow
    result = import_files(db, home, [event_file])
    assert result['failed'] == 0
    with sqlite3.connect(db) as conn:
        return conn.execute('SELECT id FROM change_events').fetchone()[0]


def approval(db, event_id):
    inspected = inspect_event(db, event_id)
    changes = [
        {
            'id': c['id'],
            'effective_on': '2026-11-06' if c['article'] == '제3조' else '2026-11-05',
            'summary': f'[예시] {c["article"]} 검토된 요약',
            'evidence_url': SOURCE,
            'comparison_verified': True,
        }
        for c in inspected['changes']
    ]
    return {
        'event_id': event_id,
        'snapshot_id': inspected['snapshot_id'],
        'decision': 'approve',
        'note': '합성 원문·시행일·부칙 검토',
        'source_url': SOURCE,
        'dates_verified': True,
        'promulgated_on': '2026-10-01',
        'changes': changes,
    }


def test_import_is_pending_idempotent_and_preserves_original_versions(workflow):
    db, home, path, _ = workflow
    event_id = import_one(workflow)
    assert import_files(db, home, [path]) == {'imported': 0, 'unchanged': 1, 'failed': 0}
    inspected = inspect_event(db, event_id)
    assert len(inspected['changes']) == 3
    assert all(c['effective_on'] is None and not c['comparable'] for c in inspected['changes'])
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM law_versions').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM public_changes').fetchone()[0] == 0
        row = conn.execute("SELECT before_text,after_text FROM article_changes WHERE article='제1조'").fetchone()
        assert row == ('##### 제1조 (예시)\n\n이전 원문  \n\n', '##### 제1조 (예시)\n\n이후 원문  \n\n')
        assert conn.execute("SELECT before_text FROM article_changes WHERE article='제3조'").fetchone()[0] is None
        assert conn.execute("SELECT after_text FROM article_changes WHERE article='제2조'").fetchone()[0] is None
    assert '새 경과조치 원문' in json.dumps(inspected, ensure_ascii=False)


def test_review_dates_counts_and_withdrawal_are_consistent(workflow):
    db = workflow[0]
    event_id = import_one(workflow)
    payload = approval(db, event_id)
    review(db, payload, reviewer='test-reviewer')
    with TestClient(create_app(db)) as client:
        days = client.get('/api/law-calendar?month=2026-11').json()['days']
        assert [d['effective_count'] for d in days] == [2, 1]
        assert client.get('/api/law-calendar?month=2026-10').json()['days'] == []
        change = payload['changes'][0]['id']
        assert client.get(f'/api/law-changes/{change}').json()['comparable'] is True
        review(db, {**payload, 'decision': 'withdraw', 'note': '예시 철회'}, reviewer='test-reviewer')
        assert client.get(f'/api/law-changes/{change}').status_code == 404
        assert client.get('/api/law-events?month=2026-11').json()['total'] == 0
        assert client.get('/api/law-calendar?month=2026-11').json()['days'] == []
    with sqlite3.connect(db) as conn:
        assert set(r[0] for r in conn.execute('SELECT decision FROM review_audit')) == {'approve', 'withdraw'}


@pytest.mark.parametrize('edit', ['date', 'evidence', 'comparison', 'dates', 'snapshot', 'unknown', 'empty'])
def test_invalid_or_stale_approval_cannot_publish(workflow, edit):
    db = workflow[0]
    event_id = import_one(workflow)
    payload = approval(db, event_id)
    if edit == 'date':
        payload['changes'][0]['effective_on'] = '2026-02-30'
    elif edit == 'evidence':
        payload['changes'][0]['evidence_url'] = 'https://www.law.go.kr.evil.test/x'
    elif edit == 'comparison':
        payload['changes'][0]['comparison_verified'] = False
    elif edit == 'dates':
        payload['dates_verified'] = False
    elif edit == 'snapshot':
        payload['snapshot_id'] = 'stale'
    elif edit == 'unknown':
        payload['changes'][0]['id'] = 'other-event'
    else:
        payload['changes'] = []
    with pytest.raises(ValueError):
        review(db, payload, reviewer='test-reviewer')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_changes').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM review_audit').fetchone()[0] == 0


def test_correction_unpublishes_retains_history_and_refuses_old_review(workflow):
    db, home, path, event = workflow
    event_id = import_one(workflow)
    old_review = approval(db, event_id)
    review(db, old_review, reviewer='test-reviewer')
    event['context']['amendment_reason'] = '정정된 원천'
    path.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
    assert import_files(db, home, [path])['imported'] == 1
    with pytest.raises(ValueError, match='snapshot'):
        review(db, old_review, reviewer='test-reviewer')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_changes').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM review_audit WHERE decision='invalidate'").fetchone()[0] >= 1
    assert import_files(db, home, [path])['unchanged'] == 1


def test_upstream_event_cannot_be_reassigned_to_another_law(workflow):
    db, home, path, event = workflow
    event_id = import_one(workflow)
    original = inspect_event(db, event_id)
    event['target']['law_id'] = 'another-law'
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_files(db, home, [path])['failed'] == 1
    assert inspect_event(db, event_id)['snapshot_id'] == original['snapshot_id']


def test_partial_approval_leaves_unknown_effective_dates_private(workflow):
    db = workflow[0]
    event_id = import_one(workflow)
    payload = approval(db, event_id)
    payload['changes'] = payload['changes'][:1]
    review(db, payload, reviewer='test-reviewer')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_changes').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM article_changes WHERE effective_on IS NULL').fetchone()[0] == 2


def test_concurrent_imports_create_one_event_and_snapshot(workflow):
    db, home, path, _ = workflow
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_files(db, home, [path]), range(2)))
    assert sum(r['failed'] for r in results) == 0
    assert sum(r['imported'] for r in results) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 1


@pytest.mark.parametrize('absent,kind', [('old', 'law_new'), ('new', 'law_repealed')])
def test_whole_law_addition_and_removal_remain_unverified(workflow, absent, kind):
    db, home, path, event = workflow
    event[absent] = None
    event['change_type'] = kind
    event['diff']['chunks'] = [{'article': '제1조'}]
    path.write_text(json.dumps(event), encoding='utf-8')
    event_id = import_one(workflow)
    inspected = inspect_event(db, event_id)
    assert inspected['changes'][0]['before_text' if absent == 'old' else 'after_text'] is None
    assert inspected['changes'][0]['effective_on'] is None


def test_failed_files_are_isolated_and_retryable_without_false_freshness(workflow):
    db, home, path, _ = workflow
    bad = home / 'bad.json'
    bad.write_text('{broken', encoding='utf-8')
    assert import_files(db, home, [bad, path]) == {'imported': 1, 'unchanged': 0, 'failed': 1}
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT collected_at FROM collection_status').fetchone() is None
        assert 'bad.json' in conn.execute('SELECT errors_json FROM import_runs').fetchone()[0]
    assert import_files(db, home, [path])['unchanged'] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT collected_at FROM collection_status').fetchone()[0] == NOW


@pytest.mark.parametrize('problem', ['traversal', 'symlink', 'missing', 'unsupported', 'no_articles'])
def test_unsafe_or_unsupported_sources_do_not_create_events(workflow, tmp_path, problem):
    db, home, path, event = workflow
    if problem == 'traversal':
        event['old']['file'] = '../outside.md'
    elif problem == 'symlink':
        outside = tmp_path / 'outside.md'
        outside.write_text('outside', encoding='utf-8')
        (home / 'link.md').symlink_to(outside)
        event['old']['file'] = 'link.md'
    elif problem == 'missing':
        event['new']['file'] = 'missing.md'
    elif problem == 'unsupported':
        event['source'] = 'legalize_admrule'
    else:
        event['diff']['chunks'] = [{'article': '부칙'}]
    path.write_text(json.dumps(event), encoding='utf-8')
    assert import_files(db, home, [path])['failed'] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0


def test_cli_import_show_review_and_reject(workflow, tmp_path):
    db, home, path, _ = workflow
    base = [sys.executable, '-m', 'law_calendar.manage', '--database', str(db)]

    def run(*args):
        return subprocess.run([*base, *args], capture_output=True, text=True, check=True).stdout

    assert json.loads(run('import', '--monitor-home', str(home), str(path)))['imported'] == 1
    event_id = json.loads(run('list'))[0]['id']
    assert json.loads(run('show', event_id))['review_state'] == 'pending'
    manifest = tmp_path / 'review.json'
    payload = approval(db, event_id)
    payload['decision'] = 'reject'
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    run('review', str(manifest), '--reviewer', 'test-reviewer')
    assert json.loads(run('show', event_id))['review_state'] == 'rejected'


def test_v1_upgrade_backs_up_preserves_public_data_and_refuses_unknown(tmp_path):
    from law_calendar.db import SCHEMA

    db = tmp_path / 'v1.db'
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA + '\nPRAGMA user_version=1;')
        conn.execute("INSERT INTO laws VALUES ('existing','existing')")
    migrate(db)
    migrate(db)
    backup = db.with_suffix('.db.pre-v3.backup')
    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 6
        assert conn.execute('SELECT name FROM laws').fetchone()[0] == 'existing'
        conn.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):
        migrate(db)
