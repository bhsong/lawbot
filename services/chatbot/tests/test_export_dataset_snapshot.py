import codecs
import csv
import json
import stat
import subprocess
import sys

import pytest

from lawbot import feedback_store, manifest
from lawbot.dataset_store import insert_feedback_qa, retire_feedback_qa
from lawbot.db.connection import connect, transaction
from lawbot.db.csv_import import sha256
from lawbot.llm import build_prompt
from lawbot.paths import DATA
from lawbot_tools import eval_v4, score_answers
from lawbot_tools.feedback_export import article_docs
from test_migrate_csv_to_sqlite import QA_FILES, csv_rows, import_corpus
from test_migrate_csv_to_sqlite import db_path as db_path
from test_migrate_csv_to_sqlite import run as migrate

AT = '2026-11-08T00:00:00+00:00'
GOLD = [{'law': '근로기준법', 'article_no': '제26조'}]
HITS = [dict(law='최저임금법', article_id='제6조', title='합성 제목', doc='합성 제목\n검색 조문 본문')]


@pytest.fixture
def corpus_db(db_path):
    assert import_corpus(db_path, 'v5') == 0
    return db_path


def args(db, out, version):
    return ['--db', str(db), '--version', version, '--corpus-release', 'v5', '--out', str(out)]


def export(db, out, version='v6'):
    from lawbot_tools.export_dataset_snapshot import main

    return main(args(db, out, version))


def add_promotion(db, name, *, verdict='correct', label='like', emb='train', sllm='train', revoked=False, omit=()):
    answer_raw = f'원래 답변 {name}'
    question = f'교정 질문 {name}'
    answer = f'교정 답변 {name}'
    reason = None if label == 'like' else 'retrieval'
    system = '합성 시스템 프롬프트'
    record = dict(
        feedback_id=name,
        turn_id=name,
        ts=AT,
        label=label,
        reason=reason,
        question=f'원 질문 {name}',
        answer_raw=answer_raw,
        model='fast',
        chat_model='synthetic-model',
        fmt='chat',
        system=system,
        system_sha256=sha256(system.encode()),
        hits=HITS,
    )
    for field in omit:
        del record[field]
    with connect(db, write=True) as conn, transaction(conn):
        feedback_store.insert(conn, feedback_store.record_values(record), json.dumps(record), 'web')
        review_id = conn.execute(
            'INSERT INTO feedback_reviews (feedback_id, reviewer, verdict, decision, scope, gold_articles, '
            'corrected_question, corrected_answer, note, created_at) '
            "VALUES (?,'reviewer',?,'approved','in',?,?,?,'검수 메모',?)",
            (name, verdict, json.dumps(GOLD), question, answer, AT),
        ).lastrowid
        uid, preference = insert_feedback_qa(
            conn,
            key=f'FB-{name}-r1',
            feedback_id=name,
            question=question,
            answer=answer,
            gold=GOLD,
            rejected=answer_raw if verdict == 'incorrect' else None,
            at=AT,
        )
        conn.execute(
            'INSERT INTO feedback_promotions (feedback_id,review_id,kind,qa_uid,preference_id,approver,created_at,state) '
            "VALUES (?,?,?,?,?,'reviewer',?,?)",
            (
                name,
                review_id,
                'up' if label == 'like' else 'down',
                uid,
                preference,
                AT,
                'revoked' if revoked else 'active',
            ),
        )
        for purpose, split in [('emb', emb), ('sllm', sllm)]:
            conn.execute(
                'UPDATE qa_splits SET split=?,group_key=? WHERE qa_uid=? AND purpose=?', (split, uid, uid, purpose)
            )
        if revoked:
            retire_feedback_qa(conn, uid)


def test_v5_roundtrip_and_evaluation_consumers(corpus_db, tmp_path, monkeypatch):
    assert migrate(corpus_db, 'qa', '--set', 'v5') == 0
    assert migrate(corpus_db, 'qa', '--set', 'labor_eval') == 0
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out, 'v5') == 0
    for source in QA_FILES['v5']:
        original = DATA / source
        assert (out / original.name).read_bytes() == original.read_bytes()
    expected = eval_v4.load_tax('test')
    for var, source in zip(('TAX_QA', 'TAX_SPLIT', 'TAX_KEYS'), QA_FILES['v5'], strict=True):
        monkeypatch.setattr(eval_v4, var, out / source.split('/')[-1])
    monkeypatch.setattr(eval_v4, 'EVAL', out / 'labor_eval_keywords.csv')
    actual = eval_v4.load_tax('test')
    assert len(actual) == 3420 and actual.equals(expected)
    row = actual.iloc[0]
    keywords = eval_v4.keywords('v5')
    assert score_answers.score_one(keywords.loc[row.id], row.article_id)['cite_ok'] == 1


def test_renamed_corpus_manifests_and_cli(corpus_db, tmp_path):
    out = tmp_path / 'snapshot'
    result = subprocess.run(
        [sys.executable, '-m', 'lawbot_tools.export_dataset_snapshot', *args(corpus_db, out, 'v6')],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for output, source, manifest_name in (
        ('articles_v6.csv', 'articles_v5.csv', 'manifest.json'),
        ('law_articles_v6_decree.csv', 'law_articles_v5_decree.csv', 'manifest_law_articles_v5_decree.json'),
    ):
        original = json.loads((DATA / 'v5' / manifest_name).read_bytes())
        expected = original | {'articles_file': output}
        path = out / output
        assert path.read_bytes() == (DATA / 'v5' / source).read_bytes()
        assert manifest.load(path) == manifest.check(path)
        assert {k: manifest.load(path)[k] for k in expected} == expected
    raw = (out / 'manifest.json').read_bytes()
    snapshot = json.loads(raw)['snapshot']
    assert snapshot['db'] == corpus_db.name and snapshot['schema_version'] >= 2
    assert snapshot['corpus_release_id'] == 'v5'
    for name, metadata in snapshot['files'].items():
        assert sha256((out / name).read_bytes()) == metadata['sha256']
    assert snapshot['files']['articles_v6.csv']['rows'] == 713
    assert snapshot['source_manifest_sha256'] == {
        name: sha256((DATA / 'v5' / name).read_bytes())
        for name in ('manifest.json', 'manifest_law_articles_v5_decree.json')
    }
    with connect(corpus_db) as conn:
        assert conn.execute(
            'SELECT version,corpus_release_id,manifest,manifest_sha256 FROM dataset_releases'
        ).fetchall() == [('v6', 'v5', raw.decode(), sha256(raw))]


def test_same_version_is_repeatable_and_refuses_changed_input(corpus_db, tmp_path, capsys):
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out) == 0
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    assert export(corpus_db, out) == 0
    assert '같음' in capsys.readouterr().out
    copy = tmp_path / 'copy'
    assert export(corpus_db, copy) == 0
    assert {path.name: path.read_bytes() for path in copy.iterdir()} == before
    add_promotion(corpus_db, 'new')
    assert export(corpus_db, out) != 0
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    changed = tmp_path / 'changed'
    assert export(corpus_db, changed) != 0
    assert not changed.exists()


def test_feedback_uses_review_and_purpose_splits(corpus_db, tmp_path, capsys):
    add_promotion(corpus_db, 'good', emb='val')
    add_promotion(corpus_db, 'wrong', verdict='incorrect', label='flag', sllm='val')
    add_promotion(corpus_db, 'flag-correct', label='flag')
    add_promotion(corpus_db, 'emb-only', sllm='unassigned')
    add_promotion(corpus_db, 'sllm-only', emb='unassigned')
    add_promotion(corpus_db, 'waiting', emb='unassigned', sllm='unassigned')
    add_promotion(corpus_db, 'retired', verdict='incorrect', label='flag', revoked=True)
    add_promotion(corpus_db, 'test-only', emb='test', sllm='test')
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out) == 0
    assert 'unassigned' in capsys.readouterr().out
    meta = json.loads((out / 'manifest.json').read_bytes())['snapshot']
    assert meta['warnings']['unassigned'] == 3
    assert meta['models']['chat'] == ['synthetic-model']

    completion = 'feedback_id split model fmt system prompt completion'.split()
    columns = {
        'fb_questions': 'feedback_id split question label reason h_ok h_gold h_scope h_note'.split(),
        'fb_emb_pairs': 'feedback_id split question positive negative gold'.split(),
        'fb_sft': completion,
        'fb_kto': [*completion, 'label'],
    }
    rows = {}
    for name, expected in columns.items():
        path = out / f'{name}.csv'
        raw = path.read_bytes()
        assert raw.startswith(codecs.BOM_UTF8)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with path.open(encoding='utf-8-sig', newline='') as file:
            assert csv.DictReader(file).fieldnames == expected
        rows[name] = {row['feedback_id']: row for row in csv_rows(raw)}
    assert rows['fb_questions']['wrong'] == dict(
        feedback_id='wrong',
        split='train',
        question='교정 질문 wrong',
        label='flag',
        reason='retrieval',
        h_ok='틀림',
        h_gold='근로기준법 제26조',
        h_scope='범위 안',
        h_note='검수 메모',
    )
    positive = article_docs(out / 'law_articles_v6_decree.csv')['근로기준법', '제26조']
    assert rows['fb_emb_pairs']['wrong'] == dict(
        feedback_id='wrong',
        split='train',
        question='교정 질문 wrong',
        positive=positive,
        negative='',
        gold='근로기준법 제26조',
    )
    assert rows['fb_questions']['good']['split'] == rows['fb_emb_pairs']['good']['split'] == 'val'
    assert rows['fb_sft']['good']['split'] == 'train'
    assert rows['fb_sft']['good']['completion'] == '교정 답변 good'
    assert rows['fb_sft']['good']['prompt'] == build_prompt(HITS, '원 질문 good')
    assert rows['fb_kto']['wrong']['split'] == 'val'
    assert rows['fb_kto']['wrong']['label'] == 'False'
    assert rows['fb_kto']['wrong']['completion'] == '원래 답변 wrong'
    assert rows['fb_kto']['wrong']['prompt'] == build_prompt(HITS, '원 질문 wrong')
    assert rows['fb_kto']['good']['label'] == 'True'
    assert set(rows['fb_questions']) == set(rows['fb_emb_pairs']) == {'good', 'wrong', 'flag-correct', 'emb-only'}
    assert set(rows['fb_sft']) == {'good', 'sllm-only'}
    assert set(rows['fb_kto']) == {'good', 'wrong', 'flag-correct', 'sllm-only'}
    assert [json.loads(line) for line in (out / 'fb_preferences.jsonl').read_text().splitlines()] == [
        dict(
            feedback_id='wrong',
            split='val',
            prompt=build_prompt(HITS, '원 질문 wrong'),
            chosen='교정 답변 wrong',
            rejected='원래 답변 wrong',
        )
    ]


def test_preference_rows_include_sllm_split(corpus_db, tmp_path):
    add_promotion(corpus_db, 'wrong', verdict='incorrect', label='flag', emb='train', sllm='val')
    add_promotion(corpus_db, 'wrong-train', verdict='incorrect', label='flag', emb='val', sllm='train')
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out) == 0
    rows = [json.loads(line) for line in (out / 'fb_preferences.jsonl').read_text().splitlines()]
    assert {row.get('feedback_id'): row.get('split') for row in rows} == {'wrong': 'val', 'wrong-train': 'train'}


def test_unregistered_directory_and_tampered_snapshot_are_not_overwritten(corpus_db, tmp_path):
    unregistered = tmp_path / 'existing'
    unregistered.mkdir()
    assert export(corpus_db, unregistered) != 0
    assert list(unregistered.iterdir()) == []
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out) == 0
    file = out / 'fb_sft.csv'
    file.write_bytes(b'tampered')
    assert export(corpus_db, out) != 0
    assert file.read_bytes() == b'tampered'


def test_export_keeps_a_single_read_snapshot(corpus_db, tmp_path, monkeypatch):
    from lawbot_tools import export_dataset_snapshot

    assert migrate(corpus_db, 'qa', '--set', 'v5') == 0
    original = export_dataset_snapshot.dataset_bytes

    def restore(conn, name):
        add_promotion(corpus_db, 'concurrent')
        return original(conn, name)

    out = tmp_path / 'snapshot'
    with monkeypatch.context() as patch:
        patch.setattr(export_dataset_snapshot, 'dataset_bytes', restore)
        assert export(corpus_db, out) == 0
    assert csv_rows((out / 'fb_questions.csv').read_bytes()) == []
    assert export(corpus_db, out) == 2


def test_registration_error_cleans_up_new_snapshot(corpus_db, tmp_path):
    with connect(corpus_db, write=True) as conn, transaction(conn):
        conn.execute(
            'CREATE TRIGGER reject_snapshot BEFORE INSERT ON dataset_releases '
            "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out) == 2
    assert not out.exists()


def test_legacy_feedback_without_prompt_fields_is_reported(corpus_db, tmp_path, capsys):
    add_promotion(corpus_db, 'no-hits', verdict='incorrect', label='flag', omit=('hits',))
    add_promotion(corpus_db, 'no-system', omit=('fmt', 'system', 'system_sha256'))
    out = tmp_path / 'snapshot'
    assert export(corpus_db, out) == 0
    assert 'missing_hits 1건' in capsys.readouterr().out
    assert (out / 'fb_preferences.jsonl').read_bytes() == b''
    assert [row['feedback_id'] for row in csv_rows((out / 'fb_questions.csv').read_bytes())] == ['no-hits', 'no-system']
    [sft] = csv_rows((out / 'fb_sft.csv').read_bytes())
    assert sft['fmt'] == sft['system'] == ''
    warnings = json.loads((out / 'manifest.json').read_bytes())['snapshot']['warnings']
    assert warnings == dict(unassigned=0, missing_fmt=1, missing_hits=1, missing_system=1)
