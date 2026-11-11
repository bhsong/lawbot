import csv
import hashlib
import io
import json
import subprocess
import sys
from collections import Counter

import pandas as pd
import pytest

from lawbot.db import migrate
from lawbot.db.connection import connect, transaction
from lawbot.db.migrate import init
from lawbot.paths import DATA
from lawbot.qa_rules import latest_rows
from lawbot.retriever import doc_text

QA_FILES = {
    'v4': ('v4/qa_full_v4.csv', 'v4/split_v4.csv', 'v4/answer_key_v4.csv'),
    'v5': ('v5/qa_tax_v5.csv', 'v5/split_tax_v5.csv', 'v5/answer_key_tax_v5.csv'),
    'decree_v4': ('decree_exp/qa_decree_v4.csv', 'decree_exp/split_decree_v4.csv'),
    'reasoning_v5': ('v5/qa_reasoning_val.csv',),
    'oos': ('oos_eval.csv',),
    'scope': ('scope_controls.csv',),
    'refusal_hard': ('refusal_hard_cases.csv',),
    'labor_eval': ('labor_eval_keywords.csv',),
}
QA_COUNTS = dict(zip(QA_FILES, (7618, 13680, 4096, 30, 102, 20, 86, 650)))
SPLIT_COUNTS = {
    'v4': ((4433, 1110, 2075), (4987, 556, 2075)),
    'v5': ((8208, 2052, 3420), (9234, 1026, 3420)),
    'decree_v4': ((1772, 444, 1880), (1994, 222, 1880)),
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / 'private' / 'migration.db'
    init(path)
    return path


def run(db_path, command, *args):
    from lawbot_tools.migrate_csv_to_sqlite import main

    return main([command, '--db', str(db_path), *map(str, args)])


def import_corpus(db_path, profile, *args):
    return run(db_path, 'corpus', '--dir', DATA / profile, '--profile', profile, '--release-id', profile, *args)


def csv_rows(raw):
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))


def assert_bytes(restored, original):
    assert hashlib.sha256(restored).digest() == hashlib.sha256(original.read_bytes()).digest(), original.name


def searchable_docs(raw):
    table = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding='utf-8-sig')
    live = table[table.deleted == '0'].copy()
    live['article_id'] = live['article_no']
    return doc_text(live).tolist()


@pytest.mark.parametrize(
    'profile,manifest', [('v5', 'manifest_law_articles_v5_decree.json'), ('v4', 'manifest_decree.json')]
)
def test_corpus_roundtrip_and_search_documents(db_path, profile, manifest):
    from lawbot.db.corpus_import import release_bytes

    assert import_corpus(db_path, profile) == 0
    with connect(db_path) as conn:
        restored = release_bytes(conn, profile)
    for name in ('manifest.json', manifest):
        assert restored[name] == (DATA / profile / name).read_bytes()
    for name in (f'articles_{profile}.csv', f'law_articles_{profile}_decree.csv'):
        original = DATA / profile / name
        assert_bytes(restored[name], original)
        assert searchable_docs(restored[name]) == searchable_docs(original.read_bytes())


def test_corpus_reuses_versions_and_activates_idempotently(db_path, capsys):
    assert import_corpus(db_path, 'v4') == 0
    assert import_corpus(db_path, 'v5', '--activate') == 0
    before = db_state(db_path)
    assert import_corpus(db_path, 'v5', '--activate') == 0
    assert '변경 0' in capsys.readouterr().out
    assert db_state(db_path) == before
    with connect(db_path) as conn:
        assert conn.execute('SELECT profile, release_id FROM active_corpus').fetchall() == [('v5', 'v5')]
        expected_rows = {
            profile: len(csv_rows((DATA / profile / f'law_articles_{profile}_decree.csv').read_bytes()))
            for profile in ('v4', 'v5')
        }
        release_rows = conn.execute('SELECT release_id, count(*) FROM corpus_release_articles GROUP BY release_id')
        assert dict(release_rows) == expected_rows
        assert conn.execute('SELECT count(*) FROM article_versions').fetchone()[0] < sum(expected_rows.values())
    assert run(db_path, 'corpus', '--dir', DATA / 'v4', '--profile', 'v4', '--release-id', 'v5') != 0
    assert db_state(db_path) == before


def test_release_bytes_and_verify_allow_a_human_note(db_path):
    from lawbot.db.corpus_import import release_bytes

    assert import_corpus(db_path, 'v5') == 0
    with connect(db_path, write=True) as conn:
        assert conn.execute("SELECT note FROM corpus_releases WHERE release_id='v5'").fetchone() == (None,)
        before = release_bytes(conn, 'v5')
        with transaction(conn):
            conn.execute("UPDATE corpus_releases SET note='Human release note' WHERE release_id='v5'")
        assert release_bytes(conn, 'v5') == before
    assert run(db_path, 'verify') == 0


def test_migrate_legacy_csv_format_and_preserve_human_notes(tmp_path, monkeypatch):
    migration_files = migrate.MIGRATIONS
    old_migrations = tmp_path / 'v1'
    old_migrations.mkdir()
    (old_migrations / '0001_init.sql').write_bytes((migration_files / '0001_init.sql').read_bytes())
    legacy = tmp_path / 'legacy.db'
    with monkeypatch.context() as patch:
        patch.setattr(migrate, 'MIGRATIONS', old_migrations)
        assert init(legacy) == 1
    format_metadata = {part: dict(bom=True, newline='\n', final_newline=True) for part in ('law', 'combined')}
    with connect(legacy, write=True) as conn, transaction(conn):
        conn.executemany(
            'INSERT INTO corpus_releases (release_id, profile, dataset_version, reference_date, law_csv_name, '
            'combined_csv_name, law_columns, combined_columns, law_csv_sha256, combined_csv_sha256, '
            'law_manifest, combined_manifest, law_manifest_name, combined_manifest_name, origin, status, created_at, note) '
            "VALUES (?,'v5','v5','2026-11-08','law.csv','combined.csv','[]','[]','','','{}','{}',"
            "'law.json','combined.json','data/v5','validated','now',?)",
            [
                ('legacy', json.dumps({'csv_format': format_metadata})),
                ('mixed', json.dumps({'csv_format': format_metadata, 'note': 'Keep me'})),
                ('plain', 'Human release note'),
            ],
        )
    assert migrate.migrate(legacy) == 2
    with connect(legacy) as conn:
        rows = {
            key: (json.loads(fmt) if fmt else None, note)
            for key, fmt, note in conn.execute('SELECT release_id, csv_format, note FROM corpus_releases')
        }
    assert rows == {
        'legacy': (format_metadata, None),
        'mixed': (format_metadata, '{"note":"Keep me"}'),
        'plain': (None, 'Human release note'),
    }


@pytest.mark.parametrize('dataset', QA_FILES)
def test_qa_roundtrip_and_frozen_splits(db_path, dataset):
    from lawbot.db.dataset_import import dataset_bytes

    assert run(db_path, 'qa', '--set', dataset) == 0
    paths = [DATA / p for p in QA_FILES[dataset]]
    source = csv_rows(paths[0].read_bytes())
    with connect(db_path) as conn:
        restored = dataset_bytes(conn, dataset)
        assert set(restored) == {p.name for p in paths}
        for path in paths:
            assert_bytes(restored[path.name], path)
        items = conn.execute(
            'SELECT qa_uid, source_id, key_set, answer_key, row FROM qa_items WHERE dataset=? ORDER BY row_no',
            (dataset,),
        ).fetchall()
        assert len(items) == QA_COUNTS[dataset]
        assert conn.execute('SELECT frozen FROM split_sets WHERE split_set=?', (dataset,)).fetchone() == (1,)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        for position, ((uid, sid, key_set, key, raw), row) in enumerate(zip(items, source, strict=True)):
            assert sid == row['id']
            assert uid == (f'{dataset}:#{position}' if dataset == 'refusal_hard' else f'{dataset}:{sid}')
            assert json.loads(raw) == row
            assert (key_set, key) == ((dataset, row['answer_key']) if dataset in ('v4', 'v5') else (None, None))
        splits = conn.execute(
            'SELECT purpose, split, source_id FROM qa_splits JOIN qa_items USING (qa_uid) WHERE split_set=?',
            (dataset,),
        ).fetchall()
    if dataset in SPLIT_COUNTS:
        originals = csv_rows(paths[1].read_bytes())
        for purpose, counts in zip(('emb', 'sllm'), SPLIT_COUNTS[dataset], strict=True):
            counts_by_name = Counter(split for p, split, _ in splits if p == purpose)
            assert tuple(counts_by_name[s] for s in ('train', 'val', 'test')) == counts
            assert {i for p, s, i in splits if p == purpose and s == 'test'} == {
                r['id'] for r in originals if r[f'split_{purpose}'] == 'test'
            }
    else:
        assert Counter((p, s) for p, s, _ in splits) == {('eval', 'test'): QA_COUNTS[dataset]}


def db_state(db_path):
    with connect(db_path) as conn:
        return hashlib.sha256('\n'.join(conn.iterdump()).encode()).hexdigest()


def test_qa_is_idempotent_and_rejects_changes_without_overwriting(db_path, tmp_path, capsys):
    assert run(db_path, 'qa', '--set', 'scope') == 0
    before = db_state(db_path)
    assert run(db_path, 'qa', '--set', 'scope') == 0
    assert '변경 0' in capsys.readouterr().out
    original = (DATA / 'scope_controls.csv').read_bytes()
    changed = tmp_path / 'scope.csv'
    changed.write_bytes(original.replace(csv_rows(original)[0]['question'].encode(), b'synthetic change', 1))
    assert run(db_path, 'qa', '--set', 'scope', '--file', changed) != 0
    assert db_state(db_path) == before


def test_qa_keeps_crlf_without_bom_and_column_order(db_path, tmp_path):
    from lawbot.db.dataset_import import dataset_bytes

    original = csv_rows((DATA / 'scope_controls.csv').read_bytes())
    path = tmp_path / 'scope.csv'
    columns = list(reversed(original[0]))
    with path.open('w', encoding='utf-8', newline='') as target:
        writer = csv.DictWriter(target, columns, lineterminator='\r\n')
        writer.writeheader()
        writer.writerows(original)
    assert run(db_path, 'qa', '--set', 'scope', '--file', path) == 0
    with connect(db_path) as conn:
        assert_bytes(dataset_bytes(conn, 'scope')[path.name], path)


def test_unknown_answer_key_rejects_entire_dataset(db_path, tmp_path):
    original = (DATA / QA_FILES['v4'][0]).read_bytes()
    altered = tmp_path / 'bad-qa.csv'
    altered.write_bytes(original.replace(csv_rows(original)[0]['answer_key'].encode(), b'no-answer-key', 1))
    before = db_state(db_path)
    assert run(db_path, 'qa', '--set', 'v4', '--file', altered) != 0
    assert db_state(db_path) == before


def test_feedback_preserves_records_and_only_reports_line_locations(db_path, tmp_path, capsys):
    question = 'Synthetic private question 93'
    labels = [
        ('like', None),
        *[('flag', r) for r in ('retrieval', 'answer', 'false_refusal', 'other')],
        ('cleared', None),
    ]
    rows = [
        dict(
            feedback_id=f'fb-{i}',
            turn_id='same' if i in (0, 5) else f'turn-{i}',
            ts=f'2026-11-01T10:00:0{i}+0900',
            label=label,
            reason=reason,
            question=question,
            answer_raw='Synthetic answer',
            hits=[{'kind': 'law', 'meta': [1, None]}],
        )
        for i, (label, reason) in enumerate(labels)
    ]
    source = tmp_path / 'feedback_synthetic.jsonl'
    source.write_text(
        ''.join(json.dumps(r) + '\n' for r in rows)
        + '{broken\n'
        + json.dumps(rows[0] | {'label': 'unknown'})
        + '\n'
        + json.dumps(rows[1] | {'reason': 'unknown'})
        + '\n'
    )
    report = tmp_path / 'report.md'
    assert run(db_path, 'feedback', source, '--report', report) == 1
    summary = capsys.readouterr().out + report.read_text()
    assert question not in summary and 'Synthetic answer' not in summary
    assert all(f'{source.name}:{line}' in summary for line in (7, 8, 9))
    with connect(db_path) as conn:
        stored = [json.loads(raw) for (raw,) in conn.execute('SELECT record FROM feedbacks ORDER BY rowid')]
    assert stored == rows and [list(r) for r in stored] == [list(r) for r in rows]
    assert latest_rows(stored) == latest_rows(rows)
    before = db_state(db_path)
    assert run(db_path, 'feedback', source) == 1
    assert db_state(db_path) == before
    source.write_text(
        json.dumps(rows[0] | {'feedback_id': 'new'})
        + '\n'
        + json.dumps(rows[0] | {'question': 'Conflicting synthetic question'})
        + '\n'
    )
    assert run(db_path, 'feedback', source) != 0
    assert db_state(db_path) == before


@pytest.mark.parametrize('bad_kind', ['malformed', 'invalid'])
def test_feedback_partial_status_in_dry_run_and_import(db_path, tmp_path, bad_kind):
    valid = dict(feedback_id='ok', turn_id='turn', ts='now', label='like', question='Synthetic question')
    bad = '{broken' if bad_kind == 'malformed' else json.dumps(valid | {'label': 'flag', 'reason': 'bad'})
    source = tmp_path / 'feedback.jsonl'
    source.write_text(json.dumps(valid) + '\n' + bad + '\n')
    assert run(db_path, 'feedback', source, '--dry-run') == 1
    with connect(db_path) as conn:
        assert conn.execute('SELECT count(*) FROM feedbacks').fetchone() == (0,)
    assert run(db_path, 'feedback', source) == 1
    with connect(db_path) as conn:
        assert conn.execute('SELECT feedback_id FROM feedbacks').fetchall() == [('ok',)]


def test_cli_dry_run_validates_inputs_without_creating_database(tmp_path):
    db = tmp_path / 'unopened.db'
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'lawbot_tools.migrate_csv_to_sqlite',
            'qa',
            '--db',
            str(db),
            '--set',
            'reasoning_v5',
            '--dry-run',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert '30' in result.stdout and not db.exists()


def test_verify_all_imports_and_detects_changed_source(db_path, tmp_path, capsys):
    source = tmp_path / 'scope.csv'
    source.write_bytes((DATA / 'scope_controls.csv').read_bytes())
    for profile in ('v4', 'v5'):
        assert import_corpus(db_path, profile) == 0
    for dataset in QA_FILES:
        extra = ('--file', source) if dataset == 'scope' else ()
        assert run(db_path, 'qa', '--set', dataset, *extra) == 0
    capsys.readouterr()
    result = subprocess.run(
        [sys.executable, '-m', 'lawbot_tools.migrate_csv_to_sqlite', 'verify', '--db', str(db_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name in QA_FILES:
        assert f'OK qa {name}' in result.stdout
    for profile in ('v4', 'v5'):
        assert f'OK corpus {profile}' in result.stdout
    original = source.read_bytes()
    first_question = csv_rows(original)[0]['question'].encode()
    source.write_bytes(original.replace(first_question, b'synthetic changed question', 1))
    assert run(db_path, 'verify') == 1
    changed = capsys.readouterr().out
    assert 'OK qa scope' in changed and 'SOURCE CHANGED scope.csv' in changed and 'FAIL qa scope' not in changed
    source.unlink()
    assert run(db_path, 'verify') == 1
    missing = capsys.readouterr().out
    assert 'OK qa scope' in missing and 'SOURCE MISSING scope.csv' in missing and 'FAIL qa scope' not in missing


def test_verify_distinguishes_a_changed_corpus_source_from_corrupt_db(db_path, tmp_path, capsys):
    source = tmp_path / 'source'
    source.mkdir()
    for name in (
        'articles_v5.csv',
        'law_articles_v5_decree.csv',
        'manifest.json',
        'manifest_law_articles_v5_decree.json',
    ):
        (source / name).write_bytes((DATA / 'v5' / name).read_bytes())
    assert run(db_path, 'corpus', '--dir', source, '--profile', 'v5', '--release-id', 'local') == 0
    law = source / 'articles_v5.csv'
    original = law.read_bytes()
    title = csv_rows(original)[0]['title'].encode()
    law.write_bytes(original.replace(title, b'changed synthetic title', 1))
    capsys.readouterr()
    assert run(db_path, 'verify') == 1
    changed = capsys.readouterr().out
    assert 'OK corpus local' in changed and 'SOURCE CHANGED articles_v5.csv' in changed
    with connect(db_path, write=True) as conn, transaction(conn):
        conn.execute("UPDATE corpus_releases SET law_manifest=law_manifest || ' ' WHERE release_id='local'")
    assert run(db_path, 'verify') == 2
    assert 'FAIL corpus local' in capsys.readouterr().out
