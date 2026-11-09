"""Verify the P07 handoff and build draft rows against the current corpus and splits."""

import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from zipfile import ZipFile

from lawbot.cache_store import Draft
from lawbot.dataset_store import protection_rows
from lawbot.db.connection import connect
from lawbot.exact_cache import NORMALIZER, Snapshot, canonical, digest, normalize, read_articles

P07_SHA256 = '69034f850b39769e0684d8e83b4a1b3653e2b3750bd74d43b37a2a8b43349f59'
BATCH_COUNTS = dict(zip((f'P{i:02}' for i in range(1, 8)), (21, 23, 31, 18, 43, 27, 14), strict=True))
DRAFT_FLAGS = {
    'seed_status': 'content_pass_pending_release',
    'approval_status': 'not_approved',
    'currency_status': 'unverified',
    'final_rendering_policy_status': 'unverified',
    'exact_enabled': False,
    'semantic_enabled': False,
}


def csv_rows(raw):
    return list(csv.DictReader(StringIO(raw.decode('utf-8-sig'), newline='')))


def json_lines(raw):
    return [json.loads(line) for line in raw.decode('utf-8').splitlines()]


@dataclass(frozen=True)
class Bundle:
    candidates: list[dict]
    protected: frozenset[str]


def load_bundle(path: Path) -> Bundle:
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != P07_SHA256:
            raise ValueError('승인된 P01~P07 인계 ZIP과 해시가 다릅니다.')
        stream.seek(0)
        with ZipFile(stream) as archive:
            return _verified_bundle(archive)


def _verified_bundle(archive: ZipFile) -> Bundle:
    checksums = json.loads(archive.read('FILE_SHA256.json'))
    names = archive.namelist()
    if len(names) != len(set(names)) or set(names) != set(checksums) | {'FILE_SHA256.json'}:
        raise ValueError('ZIP과 파일 매니페스트 목록이 다릅니다.')
    for name, expected in checksums.items():
        if hashlib.sha256(archive.read(name)).hexdigest() != expected:
            raise ValueError('ZIP 내부 파일 해시가 다릅니다.')

    corpus_path = 'corpus/law_articles_v5_decree.csv'
    snapshot = f'uploaded_sha256:{checksums[corpus_path]}'
    articles = read_articles(archive.read(corpus_path))
    candidates = json_lines(archive.read('data/pass_seed_candidates.jsonl'))
    answers = {a['answer_key']: a for a in json_lines(archive.read('data/pass_answers.jsonl'))}
    keys = {q['id']: q for q in csv_rows(archive.read('data/query_keys_normalizer_v1.csv'))}
    splits = {q['id']: q for q in csv_rows(archive.read('data/protection/split_v4.csv'))}
    protected = csv_rows(archive.read('data/protection/protected_query_hashes.csv'))
    normal = {
        q['id']
        for q in csv_rows(archive.read('provenance/training/sft_normal_pilot_232.csv'))
        if q['sample_type'] == 'normal'
    }
    reports = {
        batch: json.loads(archive.read(f'results/{batch}/review_{batch}.json'))['answers'] for batch in BATCH_COUNTS
    }
    reviews = {(batch, item['review_unit_id']): item for batch, items in reports.items() for item in items}
    protected_ids = {qid for qid, q in splits.items() if (q['split_emb'], q['split_sllm']) != ('train', 'train')}
    if (
        len(protected) != 3629
        or len(protected_ids) != len(protected)
        or {q['id'] for q in protected} != protected_ids
        or any(
            q['normalizer_version'] != NORMALIZER
            or any(q[k] != splits[q['id']][k] for k in ('split_emb', 'split_sllm'))
            for q in protected
        )
    ):
        raise ValueError('v4 보호 분할과 해시 목록이 다릅니다.')
    if (
        Counter(q['batch_id'] for q in candidates) != BATCH_COUNTS
        or len({q['id'] for q in candidates}) != 177
        or len(keys) != 177
        or len(answers) != 32
        or {q['answer_key'] for q in candidates} != answers.keys()
    ):
        raise ValueError('P01~P07 후보 범위 또는 개수가 다릅니다.')
    for q in candidates:
        key = keys[q['id']]
        answer = answers[q['answer_key']]
        report = f'results/{q["batch_id"]}/review_{q["batch_id"]}.json'
        review = reviews[(q['batch_id'], q['review_unit_id'])]
        normalized = normalize(q['question'])
        ids, hashes = q['display_citation_article_ids'], q['display_citation_hashes']
        if (
            any(q[k] != v or answer[k] != v for k, v in DRAFT_FLAGS.items())
            or q['answer_verdict'] != 'pass'
            or q['pair_verdict'] != 'pass'
            or review['answer_verdict'] != 'pass'
            or review['answer_sha256'] != q['answer_sha256']
            or review['answer_key'] != q['answer_key']
            or not any(pair['id'] == q['id'] and pair['pair_verdict'] == 'pass' for pair in review['pairs'])
            or q['id'] not in normal
            or q['id'] in protected_ids
            or q['source_snapshot_id'] != snapshot
            or any(q[k] != 'train' or splits[q['id']][k] != 'train' for k in ('split_emb', 'split_sllm'))
            or q['source_report'] != report
            or q['source_report_sha256'] != checksums[report]
            or digest(q['answer']) != q['answer_sha256']
            or digest(q['question']) != q['question_sha256']
            or any(
                q[k] != answer[k]
                for k in (
                    'batch_id',
                    'review_unit_id',
                    'answer',
                    'answer_sha256',
                    'display_citation_article_ids',
                    'display_citation_hashes',
                    'review_evidence_article_ids',
                    'source_snapshot_id',
                    'source_report',
                    'source_report_sha256',
                )
            )
            or key['answer_key'] != q['answer_key']
            or key['normalizer_version'] != NORMALIZER
            or key['question_normalized'] != normalized
            or key['normalized_question_sha256'] != digest(normalized)
            or key['question_raw_sha256'] != q['question_sha256']
            or not ids
            or len(ids) != len(hashes)
            or any(key not in articles or articles[key].text_hash != hashes.get(key) for key in ids)
            or any(key not in articles for key in q['review_evidence_article_ids'])
        ):
            raise ValueError('후보의 이중 pass, 원문 또는 근거 기록이 일치하지 않습니다.')
    return Bundle(candidates, frozenset(q['normalized_question_sha256'] for q in protected))


@dataclass(frozen=True)
class Protection:
    blocked: frozenset[str]
    v4_train: dict[str, str]
    fingerprint: str


def load_protection(db: Path, bundle: Bundle, extra: list[Path]) -> Protection:
    rows = protection_rows(db)
    blocked = set(bundle.protected)
    train = {}
    manifest = []
    for row in rows:
        checksum = digest(normalize(row.question))
        manifest.append((row.qa_uid, row.split_set, row.split, checksum))
        if row.split != 'train':
            blocked.add(checksum)
        elif row.split_set == 'v4':
            train[row.source_id] = digest(row.question)
    # Include assigned feedback and protected sets added after the original DB import.
    with connect(db) as conn:
        protected = conn.execute(
            'SELECT q.qa_uid,s.split_set,s.purpose,s.split,q.question FROM qa_splits s JOIN qa_items q USING (qa_uid) '
            "WHERE s.split IN ('val','test') ORDER BY q.qa_uid,s.split_set,s.purpose"
        ).fetchall()
    for uid, split_set, purpose, split, question in protected:
        checksum = digest(normalize(question))
        blocked.add(checksum)
        manifest.append((uid, split_set, purpose, split, checksum))
    for path in extra:
        raw = path.read_bytes()
        additions = csv_rows(raw)
        if not additions or any(not normalize(row.get('question') or '') for row in additions):
            raise ValueError('추가 보호셋은 question 열에 보호 질문을 포함해야 합니다.')
        blocked.update(digest(normalize(row['question'])) for row in additions)
        manifest.append(('extra', hashlib.sha256(raw).hexdigest(), len(additions)))
    fingerprint = digest(canonical([NORMALIZER, P07_SHA256, sorted(manifest)]))
    return Protection(frozenset(blocked), train, fingerprint)


@dataclass(frozen=True)
class Seed:
    drafts: list[Draft]
    summary: dict
    exclusions: list[dict]


def prepare(bundle: Bundle, protection: Protection, snapshot: Snapshot) -> Seed:
    drafts = []
    exclusions = []
    claims = defaultdict(set)
    accepted = set()
    for q in bundle.candidates:
        normalized = normalize(q['question'])
        claims[digest(normalized)].add((normalized, q['answer_sha256'], canonical(q['display_citation_hashes'])))
    for q in bundle.candidates:
        normalized = normalize(q['question'])
        key = digest(normalized)
        reasons = []
        if protection.v4_train.get(q['id']) != q['question_sha256'] or key in protection.blocked:
            reasons.append('excluded_split_rows')
        citations = [(key, q['display_citation_hashes'][key]) for key in q['display_citation_article_ids']]
        text = snapshot.render(q['answer'], citations)
        if text is None:
            reasons.append(
                'missing_citation_rows' if snapshot.resolve(citations) is None else 'incompatible_display_rows'
            )
        if len(claims[key]) > 1:
            reasons.append('conflicting_answer_rows')
        elif key in accepted:
            reasons.append('duplicate_same_answer_rows')
        if reasons or text is None:
            exclusions.append({'id': q['id'], 'primary_reason': reasons[0], 'reasons': reasons})
            continue
        source = {
            k: q[k]
            for k in (
                'batch_id',
                'review_unit_id',
                'source_report',
                'source_report_sha256',
                'source_snapshot_id',
                'split_emb',
                'split_sllm',
                'review_evidence_article_ids',
            )
        }
        source.update(bundle_sha256=P07_SHA256, protection_hash=protection.fingerprint)
        drafts.append(Draft(q['id'], q['question'], q['answer_key'], q['answer_sha256'], text, citations, source))
        accepted.add(key)
    counts = Counter(row['primary_reason'] for row in exclusions)
    summary: dict[str, int] = dict.fromkeys(
        (
            'excluded_split_rows',
            'excluded_context_rows',
            'excluded_time_sensitive_rows',
            'excluded_privacy_rows',
            'excluded_refusal_rows',
            'missing_citation_rows',
            'incompatible_display_rows',
            'duplicate_same_answer_rows',
            'conflicting_answer_rows',
            'approved_query_rows',
            'approved_answer_rows',
        ),
        0,
    )
    summary.update(counts)
    summary['input_rows'] = len(bundle.candidates)
    summary['allowed_train_rows'] = sum('excluded_split_rows' not in row['reasons'] for row in exclusions) + len(drafts)
    summary['draft_query_rows'] = len(drafts)
    summary['draft_answer_rows'] = len({draft.answer_key for draft in drafts})
    return Seed(drafts, summary, exclusions)
