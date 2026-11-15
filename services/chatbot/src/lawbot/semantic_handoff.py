"""Read the verified semantic handoffs without extracting them or logging their contents."""

import json
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lawbot.cache_seed import csv_rows, json_lines
from lawbot.exact_cache import canonical, digest, normalize, read_articles

PASS_SHA256 = '2da4c96ed8541c7c13fdc948db1f3c39e9e19d42c55972842851c0f77a75166f'
DEFERRED_SHA256 = '41dd33d4c1e7eae01bdfc95bb18793eeb1f11a24d0fc7b5b917d799955183862'
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
    proposition: str
    start: int
    parent_sha256: str

    @property
    def group_id(self):
        return digest(canonical(['semantic-answer-group-v1', self.parent, self.unit_id]))


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
def verified_zip(path: Path, expected: str, kind: str):
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
                ('package_kind', 'approval_status', 'semantic_enabled', 'exact_enabled'),
            )
            if (
                manifest['package_kind'] != kind
                or manifest['approval_status'] != 'not_approved'
                or manifest['semantic_enabled'] is not False
                or manifest['exact_enabled'] is not False
                or len(archive.namelist()) != len(set(archive.namelist()))
            ):
                raise ValueError('invalid handoff manifest')
            required_value(manifest, 'question_pairs')
            if kind == 'pass':
                required_value(manifest, 'answer_units_used_by_pairs')
            yield archive, manifest


def read_pass(path: Path) -> Handoff:
    with verified_zip(path, PASS_SHA256, 'pass') as (archive, manifest):
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
    quotes: dict[str, set[tuple[int, str]]] = {}
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
        primary = row['primary_article_id']
        raw_article = required_value(corpus, primary).text
        found = quotes.setdefault(row['answer_key'], set())
        for evidence in review['evidence']:
            require_record(evidence, ('article_id', 'quote'))
            quote = evidence['quote']
            if evidence['article_id'] == primary and quote and (start := raw_article.find(quote)) >= 0:
                found.add((start, quote))
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
        start, proposition = min(
            quotes[key], key=lambda entry: (entry[0], -len(entry[1]), entry[1]), default=(0, parent.text)
        )
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
                proposition,
                start,
                parent.text_hash,
            )
        )
    if len({answer.group_id for answer in verified}) != len(verified):
        raise ValueError('duplicate atom groups')
    return Handoff(PASS_SHA256, tuple(verified), tuple(questions))


def read_deferred(path: Path) -> tuple[Question, ...]:
    """Load only deferred evaluation questions and their pair verdicts."""
    with verified_zip(path, DEFERRED_SHA256, 'deferred') as (archive, manifest):
        rows = read_records(
            archive,
            'data/deferred_pairs_full.jsonl',
            ('id', 'answer_key', 'question', 'question_sha256', 'pair_verdict', 'split_emb', 'split_sllm'),
        )
        if len(rows) != manifest['question_pairs'] or len({r['id'] for r in rows}) != len(rows):
            raise ValueError('inconsistent deferred record counts')
        if any(
            row['pair_verdict'] not in ('review', 'revise') or digest(row['question']) != row['question_sha256']
            for row in rows
        ):
            raise ValueError('invalid deferred verdict or question hash')
        return tuple(
            Question(
                row['id'],
                row['answer_key'],
                row['question'],
                {k: row[k] for k in ('pair_verdict', 'split_emb', 'split_sllm')},
            )
            for row in rows
        )


def evidence_result(answer: Answer, snapshot) -> dict[str, str]:
    expected = {ref.article_id: ref.sha256 for ref in (*answer.display, *answer.evidence)}
    expected[answer.parent] = answer.parent_sha256
    result = {}
    for key, checksum in expected.items():
        article = snapshot.articles.get(key)
        result[key] = 'missing' if article is None else ('match' if article.text_hash == checksum else 'mismatch')
    return result
