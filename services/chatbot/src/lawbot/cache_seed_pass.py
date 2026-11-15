"""Import the reviewed pass handoff into an exact-cache development copy."""

import argparse
import json
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lawbot import deploy, envfile
from lawbot.cache_seed import csv_rows, json_lines, load_protection
from lawbot.cache_store import translate_sqlite_errors
from lawbot.corpus_store import active_release, active_token
from lawbot.db.connection import check_db_path, repo_path
from lawbot.exact_cache import PROFILE, Snapshot, canonical, digest, normalize, read_articles
from lawbot.paths import ROOT
from lawbot.service import cache_snapshot

PASS_SHA256 = '2da4c96ed8541c7c13fdc948db1f3c39e9e19d42c55972842851c0f77a75166f'
PROVENANCE = ('batch_id', 'source_report', 'source_report_sha256', 'source_snapshot_id')


@dataclass(frozen=True)
class Reference:
    article_id: str
    sha256: str


@dataclass(frozen=True)
class Answer:
    key: str
    unit_id: str
    parent: str
    raw: str
    sha256: str
    display: tuple[Reference, ...]
    evidence: tuple[Reference, ...]
    metadata: dict
    parent_sha256: str


@dataclass(frozen=True)
class Question:
    id: str
    answer_key: str
    text: str
    metadata: dict


@dataclass(frozen=True)
class Handoff:
    sha256: str
    answers: tuple[Answer, ...]
    questions: tuple[Question, ...]


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
def verified_zip(path: Path, expected: str):
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
                ('package_kind', 'approval_status', 'exact_enabled', 'question_pairs', 'answer_units_used_by_pairs'),
            )
            if (
                manifest['package_kind'] != 'pass'
                or manifest['approval_status'] != 'not_approved'
                or manifest['exact_enabled'] is not False
                or len(archive.namelist()) != len(set(archive.namelist()))
            ):
                raise ValueError('invalid handoff manifest')
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


def development_path(path: str) -> Path:
    resolved = repo_path(path).resolve()
    production = ROOT.with_name('lawbot-prod').resolve()
    if resolved.is_relative_to(production):
        raise ValueError('이 도구는 개발용 사본에서만 실행할 수 있습니다.')
    return resolved


@dataclass(frozen=True)
class CorpusContext:
    db: Path
    snapshot: Snapshot
    event: int

    def check(self):
        if active_token(self.db, PROFILE) != (self.snapshot.release_id, self.event):
            raise ValueError('검사 중 활성 코퍼스가 변경되었습니다.')


def corpus_context(db: Path, env=None) -> CorpusContext:
    profile = deploy.get_profile(env)
    token = active_token(db, PROFILE)
    release = active_release(db, PROFILE)
    snapshot = cache_snapshot(profile, release, deploy.decree(profile, env))
    if snapshot is None or token is None or snapshot.release_id != token[0]:
        raise ValueError('활성 v5 Pro 코퍼스와 답변 정책이 필요합니다.')
    ctx = CorpusContext(db, snapshot, token[1])
    ctx.check()
    return ctx


def run(args):
    from lawbot.cache_seed_store import import_seed

    corpus = check_db_path(development_path(args.corpus_db))
    cache = check_db_path(development_path(args.cache_db))
    if cache == corpus or (cache.exists() and corpus.exists() and cache.samefile(corpus)):
        raise ValueError('코퍼스와 캐시에는 서로 다른 개발 DB 사본을 지정하세요.')
    ctx = corpus_context(corpus)
    seed = read_pass(development_path(args.bundle))
    protection = load_protection(corpus, [development_path(path) for path in args.protected_csv])
    return import_seed(cache, seed, ctx.snapshot, ctx.event, protection, check_snapshot=ctx.check) | {
        'protected_unique': len(protection.exact)
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
