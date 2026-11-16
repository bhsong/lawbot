"""Import the reviewed pass handoff into an exact-cache development copy."""

import argparse
import json
import zlib
from contextlib import contextmanager
from hashlib import file_digest
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lawbot import envfile
from lawbot.cache_admin import corpus_context, development_path, same_file
from lawbot.cache_seed import csv_rows, json_lines
from lawbot.cache_seed_store import Answer, Handoff, Question, Reference, import_seed
from lawbot.cache_store import translate_sqlite_errors
from lawbot.db.connection import check_db_path
from lawbot.exact_cache import canonical, digest, normalize, read_articles

PASS_SHA256 = '2da4c96ed8541c7c13fdc948db1f3c39e9e19d42c55972842851c0f77a75166f'
PROVENANCE = ('batch_id', 'source_report', 'source_report_sha256', 'source_snapshot_id')


def archive_member(archive: ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:
        raise ValueError('handoff is missing a required field or reference') from exc
    except (BadZipFile, EOFError, NotImplementedError, zlib.error) as exc:
        raise ValueError('invalid handoff ZIP entry') from exc


def required_value(record, key):
    try:
        return record[key]
    except KeyError as exc:
        raise ValueError('handoff is missing a required field or reference') from exc


def require_record(value, fields):
    if not isinstance(value, dict):
        raise ValueError('handoff record must be an object')
    for key in fields:
        required_value(value, key)
    return value


def read_records(archive: ZipFile, name: str, fields):
    return [require_record(row, fields) for row in json_lines(archive_member(archive, name))]


@contextmanager
def verified_zip(path: Path, expected: str, kind='pass'):
    with path.open('rb') as stream:
        if file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('handoff ZIP SHA-256 mismatch')
        stream.seek(0)
        try:
            archive = ZipFile(stream)
        except BadZipFile as exc:
            raise ValueError('invalid handoff ZIP') from exc
        with archive:
            manifest = require_record(
                json.loads(archive_member(archive, 'manifest.json')),
                ('package_kind', 'approval_status', 'exact_enabled', 'question_pairs'),
            )
            if (
                manifest['package_kind'] != kind
                or manifest['approval_status'] != 'not_approved'
                or manifest['exact_enabled'] is not False
                or len(archive.namelist()) != len(set(archive.namelist()))
            ):
                raise ValueError('invalid handoff manifest')
            if kind == 'pass':
                required_value(manifest, 'answer_units_used_by_pairs')
            yield archive, manifest


def read_pass(path: Path) -> Handoff:
    with verified_zip(path, PASS_SHA256) as (archive, manifest):
        return parse_pass(archive, manifest)


def parse_pass(archive, manifest):
    corpus = read_articles(archive_member(archive, 'corpus/law_articles_v5_decree.csv'))
    answer_fields = (
        *PROVENANCE,
        'answer_key',
        'answer',
        'answer_sha256',
        'review_unit_id',
        'primary_article_id',
        'display_citation_article_ids',
        'display_citation_hashes',
        'review_evidence_article_ids',
    )
    answer_rows = read_records(archive, 'data/pass_answers.jsonl', answer_fields)
    pair_rows = read_records(
        archive,
        'data/pass_seed_candidates.jsonl',
        (
            *answer_fields,
            'id',
            'question',
            'question_sha256',
            'answer_verdict',
            'pair_verdict',
            'split_emb',
            'split_sllm',
        ),
    )
    from_keys = [
        require_record(row, ('id', 'answer_key', 'normalized_question_sha256'))
        for row in csv_rows(archive_member(archive, 'data/cache_key_inputs.csv'))
    ]
    review_rows = read_records(
        archive,
        'reviews/pass_review_evidence.jsonl',
        ('id', 'answer_verdict', 'pair_verdict', 'evidence'),
    )
    keys = {row['id']: row for row in from_keys}
    reviews = {row['id']: row for row in review_rows}
    answers = {row['answer_key']: row for row in answer_rows}
    if (
        len(keys) != len(from_keys)
        or len(reviews) != len(review_rows)
        or len(answers) != len(answer_rows)
        or {row['id'] for row in pair_rows} != keys.keys()
        or keys.keys() != reviews.keys()
        or len(keys) != len(pair_rows)
        or {row['answer_key'] for row in pair_rows} != answers.keys()
        or len(keys) != manifest['question_pairs']
        or len(answers) != manifest['answer_units_used_by_pairs']
    ):
        raise ValueError('inconsistent handoff record counts')
    questions = []
    for row in pair_rows:
        answer = answers[row['answer_key']]
        key, review = keys[row['id']], reviews[row['id']]
        if (
            any(row[k] != answer[k] for k in answer_fields)
            or row['answer_verdict'] != 'pass'
            or row['pair_verdict'] != 'pass'
            or review['answer_verdict'] != 'pass'
            or review['pair_verdict'] != 'pass'
            or (row['split_emb'], row['split_sllm']) != ('train', 'train')
            or digest(row['question']) != row['question_sha256']
            or key['answer_key'] != row['answer_key']
            or key['normalized_question_sha256'] != digest(normalize(row['question']))
        ):
            raise ValueError('invalid reviewed handoff linkage')
        for evidence in review['evidence']:
            require_record(evidence, ('article_id', 'quote'))
        provenance = {k: row[k] for k in (*PROVENANCE, 'split_emb', 'split_sllm', 'answer_verdict', 'pair_verdict')}
        questions.append(Question(row['id'], row['answer_key'], row['question'], provenance))
    if len({normalize(q.text) for q in questions}) != len(questions):
        raise ValueError('duplicate normalized seed questions')
    verified = []
    for key, row in answers.items():
        parent = required_value(corpus, row['primary_article_id'])
        display = tuple(
            Reference(a, required_value(row['display_citation_hashes'], a)) for a in row['display_citation_article_ids']
        )
        evidence = tuple(Reference(a, required_value(corpus, a).text_hash) for a in row['review_evidence_article_ids'])
        if (
            not display
            or len({ref.article_id for ref in display}) != len(display)
            or digest(row['answer']) != row['answer_sha256']
            or any(required_value(corpus, r.article_id).text_hash != r.sha256 for r in display)
        ):
            raise ValueError('invalid handoff answer or citation hash')
        verified.append(
            Answer(
                key,
                row['review_unit_id'],
                row['primary_article_id'],
                row['answer'],
                row['answer_sha256'],
                display,
                evidence,
                {k: row[k] for k in PROVENANCE},
                parent.text_hash,
            )
        )
    if len({(answer.parent, answer.unit_id) for answer in verified}) != len(verified):
        raise ValueError('duplicate reviewed answer units')
    return Handoff(PASS_SHA256, tuple(verified), tuple(questions))


def run(args):
    bundle = development_path(args.bundle)
    corpus = check_db_path(development_path(args.corpus_db))
    cache = check_db_path(development_path(args.cache_db))
    protected_csv = [development_path(path) for path in args.protected_csv]
    if same_file(cache, corpus):
        raise ValueError('코퍼스와 캐시에는 서로 다른 개발 DB 사본을 지정하세요.')
    seed = read_pass(bundle)
    ctx = corpus_context(corpus, protected_csv)
    return import_seed(cache, seed, ctx.snapshot, ctx.event, ctx.protection, check_snapshot=ctx.check_import) | {
        'protected_unique': len(ctx.protection.exact)
    }


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--corpus-db', required=True)
    cli.add_argument('--cache-db', required=True)
    cli.add_argument('--bundle', required=True)
    cli.add_argument('--protected-csv', action='append', default=[])
    args = cli.parse_args(argv)
    try:
        envfile.load()
        with translate_sqlite_errors():
            result = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        cli.exit(2, f'통과 ZIP 적재 실패: {exc}\n')
    print(canonical(result))


if __name__ == '__main__':
    main()
