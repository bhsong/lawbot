"""Export a versioned dataset snapshot. Run assign_splits first to include pending feedback."""

import argparse
import codecs
import csv
import io
import json
import re
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime

from lawbot import citation
from lawbot.db.connection import connect, repo_path, transaction
from lawbot.db.corpus_import import release_bytes
from lawbot.db.csv_import import sha256
from lawbot.db.dataset_import import DATASETS, PURPOSES, dataset_bytes
from lawbot.db.importers import DatabaseError
from lawbot.db.migrate import check_schema
from lawbot.feedback import check_private
from lawbot.llm import build_prompt
from lawbot.qa_rules import OK_TO_VERDICT
from lawbot_tools.feedback_export import SCOPE_VALUES, article_docs
from lawbot_tools.feedback_review import private_write

VERDICT_TO_OK = {verdict: ok for ok, verdict in OK_TO_VERDICT.items()}
SCOPES = dict(zip(('in', 'out'), SCOPE_VALUES, strict=True))
ASSIGNED = ('train', 'val')
COMPLETION = ('feedback_id', 'split', 'model', 'fmt', 'system', 'prompt', 'completion')
CSV_COLUMNS = {
    'fb_questions.csv': ('feedback_id', 'split', 'question', 'label', 'reason', 'h_ok', 'h_gold', 'h_scope', 'h_note'),
    'fb_emb_pairs.csv': ('feedback_id', 'split', 'question', 'positive', 'negative', 'gold'),
    'fb_sft.csv': COMPLETION,
    'fb_kto.csv': (*COMPLETION, 'label'),
}
PREFERENCES = 'fb_preferences.jsonl'
MANIFEST = 'manifest.json'


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def _csv_bytes(columns, rows):
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, columns)
    writer.writeheader()
    writer.writerows(rows)
    return codecs.BOM_UTF8 + text.getvalue().encode()


def _file_metadata(name, raw):
    if name.endswith('.csv'):
        with io.StringIO(raw.decode('utf-8-sig'), newline='') as text:
            rows = sum(1 for _ in csv.reader(text)) - 1
    elif name.endswith('.jsonl'):
        rows = raw.count(b'\n')
    else:
        rows = 1
    return dict(rows=rows, sha256=sha256(raw))


def _corpus(conn, release, version):
    source = release_bytes(conn, release)
    law_manifest, combined_manifest = conn.execute(
        'SELECT law_manifest_name, combined_manifest_name FROM corpus_releases WHERE release_id=?', (release,)
    ).fetchone()
    law = json.loads(source[law_manifest])
    combined = json.loads(source[combined_manifest])
    law_csv, combined_csv = source[law['articles_file']], source[combined['articles_file']]
    law['articles_file'] = f'articles_{version}.csv'
    combined['articles_file'] = f'law_articles_{version}_decree.csv'
    files = {
        law['articles_file']: law_csv,
        combined['articles_file']: combined_csv,
        f'manifest_law_articles_{version}_decree.json': _json(combined).encode(),
    }
    metadata = {
        'corpus_release_id': release,
        'corpus_sha256': {'law': law['articles_sha256'], 'combined': combined['articles_sha256']},
        'source_manifest_sha256': {name: sha256(source[name]) for name in (law_manifest, combined_manifest)},
    }
    return files, law, combined_csv, metadata


def _feedback_rows(conn):
    cursor = conn.execute(
        'SELECT q.question,emb.split AS emb,sllm.split AS sllm,emb.group_key AS emb_key,sllm.group_key AS sllm_key, '
        'f.feedback_id,f.label,f.reason,f.record,f.model,f.question AS original_question,f.answer_raw, '
        'r.verdict,r.scope,r.gold_articles,r.note, '
        'r.corrected_answer,a.answer '
        'FROM qa_items q '
        "LEFT JOIN qa_splits emb ON emb.qa_uid=q.qa_uid AND emb.split_set='feedback' AND emb.purpose='emb' "
        "LEFT JOIN qa_splits sllm ON sllm.qa_uid=q.qa_uid AND sllm.split_set='feedback' AND sllm.purpose='sllm' "
        "LEFT JOIN feedback_promotions p ON p.qa_uid=q.qa_uid AND p.state='active' "
        'LEFT JOIN feedbacks f ON f.feedback_id=p.feedback_id '
        'LEFT JOIN feedback_reviews r ON r.review_id=p.review_id '
        'LEFT JOIN answer_keys a ON a.key_set=q.key_set AND a.answer_key=q.answer_key '
        "WHERE q.dataset='feedback' AND q.status='active' ORDER BY q.qa_uid"
    )
    columns = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(columns, row, strict=True))


def _embedding_rows(row, docs, questions, pairs, warnings):
    gold = [(article['law'], article['article_no']) for article in json.loads(row['gold_articles'])]
    question = dict(feedback_id=row['feedback_id'], split=row['emb'], question=row['question'])
    questions.append(
        question
        | dict(
            label=row['label'],
            reason=row['reason'],
            h_ok=VERDICT_TO_OK[row['verdict']],
            h_gold='; '.join(citation.key(*article) for article in gold),
            h_scope=SCOPES.get(row['scope']),
            h_note=row['note'],
        )
    )
    for article in gold:
        if article not in docs:
            warnings['missing_positive'] += 1
            continue
        pairs.append(question | dict(positive=docs[article], negative='', gold=citation.key(*article)))


def _completion_rows(row, record, sft, kto, preferences, warnings):
    if row['verdict'] not in ('correct', 'incorrect'):
        return
    hits = record.get('hits')
    if hits is None:
        warnings['missing_hits'] += 1
        return
    try:
        prompt = build_prompt(hits, row['original_question'])
    except IndexError, KeyError, TypeError, AttributeError:
        warnings['missing_hits'] += 1
        return
    example = dict(
        feedback_id=row['feedback_id'],
        split=row['sllm'],
        model=row['model'],
        fmt=record.get('fmt'),
        system=record.get('system'),
        prompt=prompt,
    )
    for field in ('model', 'fmt', 'system'):
        if example[field] is None:
            warnings[f'missing_{field}'] += 1
    correct = row['verdict'] == 'correct'
    if correct and row['label'] == 'like':
        sft.append(example | dict(completion=row['answer']))
    if not row['answer_raw']:
        warnings['missing_answer_raw'] += 1
        return
    kto.append(example | dict(completion=row['answer_raw'], label=correct))
    if not correct and row['corrected_answer']:
        preferences.append(
            dict(
                feedback_id=row['feedback_id'],
                split=row['sllm'],
                prompt=prompt,
                chosen=row['corrected_answer'],
                rejected=row['answer_raw'],
            )
        )


def _feedback_files(conn, corpus):
    docs = article_docs(io.BytesIO(corpus))
    rows = {name: [] for name in CSV_COLUMNS}
    preferences = []
    warnings = Counter(unassigned=0)
    models = {name: set() for name in ('chat', 'embedding', 'rerank', 'system_sha256')}
    groups = {purpose: set() for purpose in PURPOSES}
    for row in _feedback_rows(conn):
        emb, sllm = row['emb'], row['sllm']
        if any(split in (None, 'unassigned') for split in (emb, sllm)):
            warnings['unassigned'] += 1
        if 'test' in (emb, sllm) or not any(split in ASSIGNED for split in (emb, sllm)):
            continue
        if row['record'] is None or row['verdict'] is None or row['answer'] is None:
            raise ValueError('배정된 피드백 문항에 승격·검수·정답 기록이 없습니다.')
        record = json.loads(row['record'])
        values = dict(
            chat=record.get('chat_model'), embedding=record.get('emb_model'), rerank=record.get('rerank_model')
        )
        values['system_sha256'] = record.get('system_sha256')
        if values['system_sha256'] is None and isinstance(system := record.get('system'), str):
            values['system_sha256'] = sha256(system.encode())
        for name, value in values.items():
            if value:
                models[name].add(value)
        if emb in ASSIGNED:
            groups['emb'].add(row['emb_key'])
            _embedding_rows(row, docs, rows['fb_questions.csv'], rows['fb_emb_pairs.csv'], warnings)
        if sllm in ASSIGNED:
            groups['sllm'].add(row['sllm_key'])
            _completion_rows(row, record, rows['fb_sft.csv'], rows['fb_kto.csv'], preferences, warnings)

    files = {name: _csv_bytes(columns, rows[name]) for name, columns in CSV_COLUMNS.items()}
    files[PREFERENCES] = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in preferences).encode()
    metadata = dict(
        warnings=dict(sorted(warnings.items())),
        group_counts={purpose: len(keys) for purpose, keys in groups.items()},
        models={name: sorted(values) for name, values in models.items()},
    )
    return files, metadata


def _read_snapshot(db, version, release):
    with connect(db) as conn:
        conn.execute('BEGIN')
        schema = conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
        files, manifest, combined_csv, metadata = _corpus(conn, release, version)
        sets = dict(conn.execute('SELECT split_set,policy FROM split_sets ORDER BY split_set'))
        for name in sorted(sets.keys() & DATASETS.keys()):
            restored = dataset_bytes(conn, name)
            if restored.keys() & (files.keys() | CSV_COLUMNS.keys() | {PREFERENCES, MANIFEST}):
                raise ValueError(f'{name} 세트의 원본 파일 이름이 스냅샷의 다른 파일과 겹칩니다.')
            files.update(restored)
        feedback, feedback_metadata = _feedback_files(conn, combined_csv)
        files.update(feedback)
    manifest['snapshot'] = dict(
        version=version,
        db=db.name,
        schema_version=schema,
        **metadata,
        **feedback_metadata,
        split_policy=json.loads(sets.get('feedback', '{}')),
        files={name: _file_metadata(name, raw) for name, raw in sorted(files.items())},
    )
    return files, manifest


def _matches(out, files):
    return (
        out.is_dir()
        and not out.is_symlink()
        and {path.name for path in out.iterdir()} == files.keys()
        and all(
            (file := out / name).is_file() and not file.is_symlink() and sha256(file.read_bytes()) == sha256(raw)
            for name, raw in files.items()
        )
    )


def _publish(db, out, version, release, files, manifest):
    # Register only the receipt here. All dataset reads came from the read snapshot.
    with ExitStack() as rollback:
        with connect(db, write=True) as conn, transaction(conn):
            saved = conn.execute(
                'SELECT created_at,corpus_release_id,manifest,manifest_sha256 FROM dataset_releases WHERE version=?',
                (version,),
            ).fetchone()
            at = saved[0] if saved else datetime.now(UTC).isoformat()
            manifest['snapshot']['created_at'] = at
            text = _json(manifest)
            raw = text.encode()
            receipt = (at, release, text, sha256(raw))
            if saved and saved != receipt:
                raise ValueError('등록된 버전의 내용이 달라졌습니다. 새 버전을 지정하세요.')
            files[MANIFEST] = raw
            if out.exists() or out.is_symlink():
                if saved and _matches(out, files):
                    return '같음'
                raise FileExistsError('출력 경로가 이미 있고 등록된 스냅샷과 일치하지 않습니다.')

            out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            out.mkdir(mode=0o700)
            rollback.callback(out.rmdir)
            for name, raw in files.items():
                file = out / name
                file.touch(mode=0o600, exist_ok=False)
                rollback.callback(file.unlink, missing_ok=True)
                if name in CSV_COLUMNS or name == PREFERENCES:
                    private_write(file, raw.decode())
                else:
                    file.write_bytes(raw)
            if not saved:
                conn.execute(
                    'INSERT INTO dataset_releases (version,created_at,corpus_release_id,manifest,manifest_sha256) '
                    'VALUES (?,?,?,?,?)',
                    (version, *receipt),
                )
        rollback.pop_all()
    return '완료'


def export(db, out, version, release):
    """Read a consistent snapshot, then publish it without overwriting existing data."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', version):
        raise ValueError('버전에는 영문자·숫자·마침표·하이픈·밑줄만 쓸 수 있습니다.')
    check_private(out, '--out')
    check_schema(db)
    files, manifest = _read_snapshot(db, version, release)
    for name in (*files, MANIFEST):
        check_private(out / name, '--out', folder=False)
    result = _publish(db, out, version, release, files, manifest)
    for warning, count in manifest['snapshot']['warnings'].items():
        if count:
            print(f'경고 · {warning} {count}건')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='피드백은 assign_splits로 먼저 분할한 뒤 스냅샷을 내보내세요.')
    parser.add_argument('--db', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--corpus-release', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        result = export(repo_path(args.db), repo_path(args.out), args.version, args.corpus_release)
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        print(f'FAIL {exc}')
        return 2
    print(f'스냅샷 {args.version} · {result}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
