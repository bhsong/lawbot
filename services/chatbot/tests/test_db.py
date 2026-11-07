import hashlib
import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing

import pytest

from lawbot.db import backup as backups
from lawbot.db import connection
from lawbot.db import migrate as migrations


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / 'private' / 'lawbot.db'
    migrations.init(path)
    return path


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_init_creates_private_database(tmp_path):
    path = tmp_path / 'private' / 'lawbot.db'
    result = subprocess.run(
        [sys.executable, '-m', 'lawbot.db.cli', 'init', '--db', str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as conn:
        row = conn.execute('SELECT version, name, length(checksum), applied_at FROM schema_migrations').fetchone()
    assert row[:3] == (1, '0001_init.sql', 64)
    assert row[3]


def test_init_refuses_existing_file(tmp_path):
    path = tmp_path / 'existing.db'
    path.write_bytes(b'existing file')
    before = digest(path)
    with pytest.raises(FileExistsError):
        migrations.init(path)
    assert digest(path) == before


@pytest.mark.parametrize('write', [False, True])
def test_connect_configures_sqlite(db_path, write):
    with connection.connect(db_path, write=write) as conn:
        for pragma, expected in [
            ('foreign_keys', 1),
            ('journal_mode', 'wal'),
            ('busy_timeout', 5000),
            ('synchronous', 1),
        ]:
            assert conn.execute(f'PRAGMA {pragma}').fetchone()[0] == expected
        if not write:
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                conn.execute('CREATE TABLE forbidden (id INTEGER)')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        conn.execute('SELECT 1')


def test_foreign_key_violation_rolls_back_write(db_path):
    with connection.connect(db_path, write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'), connection.transaction(conn):
            conn.execute("INSERT INTO split_sets VALUES ('feedback', 0, '{}')")
            conn.execute("INSERT INTO qa_splits VALUES ('missing', 'feedback', 'emb', 'train', 'group', NULL, 'now')")
        assert conn.execute('SELECT count(*) FROM split_sets').fetchone() == (0,)


def test_migrate_rejects_modified_migration(db_path, tmp_path, monkeypatch):
    changed = (migrations.MIGRATIONS / '0001_init.sql').read_text(encoding='utf-8') + '\n-- Changed after applying\n'
    (tmp_path / '0001_init.sql').write_text(changed, encoding='utf-8')
    monkeypatch.setattr(migrations, 'MIGRATIONS', tmp_path)
    with pytest.raises(RuntimeError, match=r'checksum.*0001_init\.sql'):
        migrations.migrate(db_path)


def test_migrate_applies_each_version_atomically(db_path, tmp_path, monkeypatch):
    (tmp_path / '0001_init.sql').write_bytes((migrations.MIGRATIONS / '0001_init.sql').read_bytes())
    (tmp_path / '0002_add.sql').write_text('CREATE TABLE applied (id INTEGER);', encoding='utf-8')
    pending = tmp_path / '0003_invalid.sql'
    pending.write_text('CREATE TABLE partial (id INTEGER); INSERT INTO nonexistent VALUES (1);', encoding='utf-8')
    monkeypatch.setattr(migrations, 'MIGRATIONS', tmp_path)
    with pytest.raises(sqlite3.OperationalError, match='nonexistent'):
        migrations.migrate(db_path)
    with connection.connect(db_path) as conn:
        assert conn.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall() == [(1,), (2,)]
        assert conn.execute("SELECT name FROM sqlite_schema WHERE name IN ('applied', 'partial')").fetchall() == [
            ('applied',)
        ]
    pending.write_text('CREATE TABLE finished (id INTEGER);', encoding='utf-8')
    assert migrations.migrate(db_path) == 3
    assert migrations.migrate(db_path) == 3
    assert migrations.check_schema(db_path) == 3


def test_schema_version_errors(db_path, tmp_path):
    missing = tmp_path / 'missing.db'
    with pytest.raises(FileNotFoundError, match='lawbot-db init'):
        migrations.check_schema(missing)
    assert not missing.exists()
    assert migrations.check_schema(db_path) == 1
    with connection.connect(db_path, write=True) as conn:
        with connection.transaction(conn):
            conn.execute('UPDATE schema_migrations SET version = 2 WHERE version = 1')
        with pytest.raises(RuntimeError, match=r'스키마 버전.*높습니다'):
            migrations.check_schema(db_path)
        with connection.transaction(conn):
            conn.execute('DELETE FROM schema_migrations WHERE version = 2')
        with pytest.raises(RuntimeError, match=r'스키마 버전.*낮습니다.*lawbot-db migrate'):
            migrations.check_schema(db_path)


def add_feedback(conn, feedback_id, question):
    conn.execute(
        'INSERT INTO feedbacks (feedback_id, turn_id, ts, label, question, record, origin) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (feedback_id, 'turn', 'now', 'like', question, '{}', 'test'),
    )


def test_feedback_is_append_only(db_path):
    with connection.connect(db_path, write=True) as conn, connection.transaction(conn):
        add_feedback(conn, 'fb-1', 'Synthetic question')
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            conn.execute("UPDATE feedbacks SET question = 'edited' WHERE feedback_id = 'fb-1'")


def add_item(conn):
    conn.execute(
        'INSERT INTO qa_items (qa_uid, dataset, source_id, question, question_norm, row, origin, status, created_at) '
        "VALUES ('feedback:1', 'feedback', '1', 'Synthetic question', 'syntheticquestion', '{}', 'test', 'active', 'now')"
    )


def test_split_triggers(db_path):
    with connection.connect(db_path, write=True) as conn, connection.transaction(conn):
        add_item(conn)
        conn.execute("INSERT INTO split_sets VALUES ('feedback', 0, '{}'), ('v5', 0, '{}')")
        conn.execute(
            "INSERT INTO qa_splits VALUES ('feedback:1', 'feedback', 'emb', 'unassigned', 'q', NULL, 'now'), "
            "('feedback:1', 'v5', 'emb', 'unassigned', 'q', NULL, 'now')"
        )
        conn.execute("UPDATE split_sets SET frozen = 1 WHERE split_set = 'v5'")
        with pytest.raises(sqlite3.IntegrityError, match='split set is frozen'):
            conn.execute("INSERT INTO qa_splits VALUES ('feedback:1', 'v5', 'sllm', 'test', 'q', NULL, 'now')")
        with pytest.raises(sqlite3.IntegrityError, match='assigned or frozen'):
            conn.execute("UPDATE qa_splits SET split = 'train' WHERE split_set = 'v5'")
        with pytest.raises(sqlite3.IntegrityError, match='never deleted'):
            conn.execute("DELETE FROM qa_splits WHERE split_set = 'feedback'")
        conn.execute("UPDATE qa_splits SET split = 'train' WHERE split_set = 'feedback'")
        with pytest.raises(sqlite3.IntegrityError, match='assigned or frozen'):
            conn.execute("UPDATE qa_splits SET split = 'val' WHERE split_set = 'feedback'")
        with pytest.raises(sqlite3.IntegrityError, match='cannot unfreeze'):
            conn.execute("UPDATE split_sets SET frozen = 0 WHERE split_set = 'v5'")
        assert conn.execute("SELECT split FROM qa_splits WHERE split_set = 'feedback'").fetchone() == ('train',)


def test_only_one_active_promotion(db_path):
    with connection.connect(db_path, write=True) as conn, connection.transaction(conn):
        add_item(conn)
        add_feedback(conn, 'fb-1', 'Synthetic question')
        conn.execute(
            'INSERT INTO feedback_reviews (review_id, feedback_id, reviewer, verdict, decision, gold_articles, created_at) '
            "VALUES (1, 'fb-1', 'test', 'correct', 'approved', '[]', 'now')"
        )
        promote = (
            'INSERT INTO feedback_promotions (feedback_id, review_id, kind, qa_uid, approver, created_at, state) '
            "VALUES ('fb-1', 1, 'up', 'feedback:1', 'test', 'now', 'active')"
        )
        conn.execute(promote)
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE constraint failed'):
            conn.execute(promote)
        conn.execute("UPDATE feedback_promotions SET state = 'revoked' WHERE feedback_id = 'fb-1'")
        conn.execute(promote)


def feedback_snapshot(conn):
    rows = conn.execute('SELECT feedback_id, question FROM feedbacks ORDER BY feedback_id').fetchall()
    return len(rows), hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def test_online_backup_and_restore_with_open_writer(db_path, tmp_path):
    out, restored = tmp_path / 'backups' / 'copy.db', tmp_path / 'restore' / 'restored.db'
    with connection.connect(db_path, write=True) as writer:
        with connection.transaction(writer):
            add_feedback(writer, 'fb-1', 'Synthetic question')
        assert (db_path.parent / (db_path.name + '-wal')).stat().st_size > 32
        assert mode(db_path.parent / (db_path.name + '-wal')) == 0o600
        assert mode(db_path.parent / (db_path.name + '-shm')) == 0o600
        expected = feedback_snapshot(writer)
        with connection.transaction(writer):
            add_feedback(writer, 'fb-2', 'Uncommitted question')
            backups.backup(db_path, out)
    backups.restore(out, restored)
    assert mode(out) == mode(restored) == 0o600
    assert mode(out.parent) == mode(restored.parent) == 0o700
    assert migrations.check_schema(restored) == 1
    with connection.connect(restored) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert feedback_snapshot(conn) == expected
    before = digest(out)
    with pytest.raises(FileExistsError):
        backups.backup(db_path, out)
    assert digest(out) == before


def test_restore_refuses_existing_file(db_path, tmp_path):
    out, restored = tmp_path / 'backup.db', tmp_path / 'existing.db'
    backups.backup(db_path, out)
    restored.write_bytes(b'keep this file')
    before = digest(restored)
    with pytest.raises(FileExistsError):
        backups.restore(out, restored)
    assert digest(restored) == before


def test_restore_does_not_publish_invalid_database(tmp_path):
    invalid, out = tmp_path / 'invalid.db', tmp_path / 'restored.db'
    with closing(sqlite3.connect(invalid, autocommit=True)) as conn:
        conn.execute('CREATE TABLE unrelated (id INTEGER)')
    invalid.chmod(0o600)
    with pytest.raises(RuntimeError, match=r'스키마 버전.*낮습니다'):
        backups.restore(invalid, out)
    assert not out.exists()
