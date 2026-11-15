"""Write the reviewed pass handoff into the exact-cache catalog."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from lawbot.answer_format import render
from lawbot.cache_seed import P07_SHA256, Protection
from lawbot.cache_store import Draft, Store, audit, insert_answer, now, read_control, valid
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import FORMAT, Snapshot, canonical, digest, normalize

if TYPE_CHECKING:
    from lawbot.cache_seed_pass import Answer, Handoff, Question

IMPORT_REASON = '사용자 전수 검수 승인 2026-11-15'
ACTOR = 'import'
ACTION = 'pass_seed_import'


@dataclass(frozen=True)
class ExistingQuery:
    query_id: str
    normalized: str
    answer_id: str
    raw_hash: str
    status: str
    enabled: bool
    source: str
    valid_from: str | None
    valid_until: str | None
    expires_at: str | None

    def matches(self, question: Question, raw_hash: str) -> bool:
        return self.normalized == normalize(question.text) and self.raw_hash == raw_hash


def prior_queries(conn, namespace, questions):
    rows = {}
    for q in questions:
        key = digest(normalize(q.text))
        row = conn.execute(
            'SELECT q.query_id,q.normalized,q.answer_id,a.raw_sha256,q.status,q.exact_enabled,q.source,'
            'q.valid_from,q.valid_until,q.expires_at FROM cache_queries q JOIN cache_answers a USING (namespace_id,answer_id) '
            'WHERE q.namespace_id=? AND q.normalized_sha256=?',
            (namespace, key),
        ).fetchone()
        if row is not None:
            rows[q.id] = ExistingQuery(*row)
    return rows


def exclude_existing(conn, prior, question, raw_hash, reason, bundle_hash):
    if prior is None or not prior.enabled or not prior.matches(question, raw_hash):
        return False
    conn.execute('UPDATE cache_queries SET exact_enabled=0 WHERE query_id=?', (prior.query_id,))
    audit(conn, 'pass_seed_exclusion', prior.query_id, ACTOR, reason, {'bundle_sha256': bundle_hash})
    return True


def compatible_revision(conn, revision, citations, evidence):
    displayed = conn.execute(
        'SELECT article_id,text_sha256 FROM cache_citations WHERE answer_id=? ORDER BY position', (revision,)
    ).fetchall()
    if displayed != citations:
        return False
    row = conn.execute('SELECT source FROM cache_answers WHERE answer_id=?', (revision,)).fetchone()
    source = json.loads(row[0])
    reviewed = source.get('review_evidence_article_ids')
    if reviewed is not None and set(reviewed) != evidence.keys():
        return False
    hashes = source.get('review_evidence_hashes')
    return hashes is None or hashes == evidence


def evidence_result(answer: Answer, snapshot: Snapshot) -> dict[str, str]:
    expected = {ref.article_id: ref.sha256 for ref in (*answer.display, *answer.evidence)}
    expected[answer.parent] = answer.parent_sha256
    result = {}
    for key, checksum in expected.items():
        article = snapshot.articles.get(key)
        result[key] = 'missing' if article is None else ('match' if article.text_hash == checksum else 'mismatch')
    return result


def answer_revision(conn, snapshot, answer: Answer, questions, prior, matched, at, sha):
    ns = snapshot.namespace.id
    citations = [(ref.article_id, ref.sha256) for ref in answer.display]
    evidence = {ref.article_id: ref.sha256 for ref in answer.evidence}
    reusable = sorted({row.answer_id for row in prior.values() if row.raw_hash == answer.sha256})
    revision = next((key for key in reusable if compatible_revision(conn, key, citations, evidence)), None)
    if revision is None:
        hits = snapshot.resolve(citations)
        text = render(answer.raw, [article.hit() for article in hits], FORMAT, done=True) if hits else answer.raw
        source = answer.metadata | {
            'bundle_sha256': sha,
            'review_unit_id': answer.unit_id,
            'review_evidence_article_ids': list(evidence),
            'review_evidence_hashes': evidence,
        }
        draft = Draft(
            questions[0].id, questions[0].text, answer.key, answer.sha256, text, citations, source, answer.raw
        )
        intended = draft.answer_id(ns)
        if conn.execute(
            'SELECT 1 FROM cache_answers WHERE answer_id=?', (intended,)
        ).fetchone() is not None and not compatible_revision(conn, intended, citations, evidence):
            # Old exact revision IDs omit review-only evidence. Keep its existing audit and rendering.
            draft = replace(draft, answer_key=f'{answer.key}:evidence:{digest(canonical(evidence))}')
        revision = insert_answer(conn, ns, draft)

    def attachable(question):
        row = prior.get(question.id)
        return row is None or (
            row.answer_id == revision
            and row.matches(question, answer.sha256)
            and (
                row.status == 'draft'
                or (
                    row.status == 'approved'
                    and row.enabled
                    and valid(at, row.valid_from, row.valid_until, row.expires_at)
                )
            )
        )

    if matched and any(attachable(question) for question in questions):
        conn.execute(
            "UPDATE cache_answers SET status='approved',reviewed_by=?,reviewed_at=?,valid_from=? "
            "WHERE answer_id=? AND status='draft'",
            (ACTOR, at, at, revision),
        )
    status, *bounds = conn.execute(
        'SELECT status,valid_from,valid_until,expires_at FROM cache_answers WHERE answer_id=?', (revision,)
    ).fetchone()
    return revision, matched and status == 'approved' and valid(at, *bounds)


def attach_query(conn, snapshot, q: Question, revision, raw_hash, prior, ready, at, sha):
    normalized = normalize(q.text)
    ns = snapshot.namespace.id
    qhash = digest(normalized)
    query = digest(canonical([ns, qhash]))
    if prior is not None:
        if not prior.matches(q, raw_hash) or prior.answer_id != revision:
            return None, False, 'existing_conflict'
        if prior.status == 'draft' and ready:
            conn.execute(
                "UPDATE cache_queries SET status='approved',exact_enabled=1,reviewed_by=?,reviewed_at=?,valid_from=? "
                'WHERE query_id=?',
                (ACTOR, at, at, prior.query_id),
            )
            return prior.query_id, True, 'existing_link'
        approved = (
            ready
            and prior.status == 'approved'
            and prior.enabled
            and valid(at, prior.valid_from, prior.valid_until, prior.expires_at)
        )
        return prior.query_id, approved, 'existing_link'
    conn.execute(
        'INSERT INTO cache_queries (query_id,namespace_id,answer_id,source_id,original,raw_sha256,normalized,'
        'normalized_sha256,source,status,exact_enabled,reviewed_by,reviewed_at,valid_from) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (
            query,
            ns,
            revision,
            q.id,
            q.text,
            digest(q.text),
            normalized,
            qhash,
            canonical(q.metadata | {'bundle_sha256': sha}),
            'approved' if ready else 'draft',
            int(ready),
            ACTOR if ready else None,
            at if ready else None,
            at if ready else None,
        ),
    )
    return query, ready, 'inserted'


def seed_receipt(conn, receipt):
    row = conn.execute(
        'SELECT detail FROM cache_audit_events WHERE action=? AND target=? AND actor=? ORDER BY event_id DESC LIMIT 1',
        (ACTION, receipt, ACTOR),
    ).fetchone()
    return json.loads(row[0]) if row else None


def check_seed_import(conn, namespace, event, receipt, protection):
    control = read_control(conn)
    previous = seed_receipt(conn, receipt)
    if (
        previous is None
        and conn.execute('SELECT 1 FROM cache_audit_events WHERE action=? LIMIT 1', (ACTION,)).fetchone()
    ):
        raise ValueError('다른 통과 ZIP 적재 또는 코퍼스 활성화 이력이 있습니다. 새 개발 사본을 사용하세요.')
    if previous is not None and previous['protection_hash'] != protection.fingerprint:
        raise ValueError('protected split changed since import')
    if control.protection_hash not in (None, protection.fingerprint):
        raise ValueError('기존 exact 보호 지문과 seed 보호 지문이 다릅니다.')
    if previous is not None:
        return control, previous['counts']
    if control.mode != 'off' or (control.namespace_id, control.activation_event) not in (
        (None, None),
        (namespace, event),
    ):
        raise ValueError('seed import requires an inactive cache copy for the current snapshot')
    return control, None


def import_seed(
    db: Path, seed: Handoff, snapshot: Snapshot, event: int, protection: Protection, *, check_snapshot=lambda: None
):
    """Approve one verified pass bundle atomically, preserving mode and prior exact approval."""
    Store(db).check_schema()
    ns = snapshot.namespace.id
    receipt = digest(canonical(['pass-seed', seed.sha256, ns, event]))
    with connect(db) as conn:
        conn.execute('BEGIN')
        _, previous = check_seed_import(conn, ns, event, receipt, protection)
    if previous is not None:
        return previous
    groups = defaultdict(list)
    rejected = []
    result: Counter[str] = Counter(
        dict.fromkeys(
            (
                'approved_questions',
                'approved_answers',
                'excluded_calculation',
                'blocked_evidence_questions',
                'existing_conflicts',
                'existing_exclusions',
                'p07_links',
                'existing_links',
                'inserted_questions',
                'hash_match',
                'hash_mismatch',
                'hash_missing',
                'seed_protected_exact',
                'seed_protected_near',
            ),
            0,
        )
    )
    result.update(input_questions=len(seed.questions), input_answers=len(seed.answers))
    for q in seed.questions:
        if is_calculation_question(q.text):
            result['excluded_calculation'] += 1
            rejected.append((q, 'excluded_calculation'))
        elif overlap := protection.match(q.text):
            result[f'seed_protected_{overlap}'] += 1
            rejected.append((q, f'protected_{overlap}'))
        else:
            groups[q.answer_key].append(q)
    at = now()
    approved_revisions = set()
    with connect(db, write=True) as conn, transaction(conn):
        # Recheck after the near-duplicate scan, which runs outside the write transaction.
        control, previous = check_seed_import(conn, ns, event, receipt, protection)
        if previous is not None:
            return previous
        conn.execute(
            'INSERT INTO cache_namespaces VALUES (?,?,?) ON CONFLICT DO NOTHING',
            (ns, snapshot.namespace.serialize(), at),
        )
        checked = {}
        existing_queries = prior_queries(conn, ns, seed.questions)
        answer_hashes = {answer.key: answer.sha256 for answer in seed.answers}
        for q, reason in rejected:
            result['existing_exclusions'] += exclude_existing(
                conn, existing_queries.get(q.id), q, answer_hashes[q.answer_key], reason, seed.sha256
            )
        for answer in seed.answers:
            statuses = evidence_result(answer, snapshot)
            checked.update(statuses)
            if not (questions := groups.get(answer.key)):
                continue
            matched = set(statuses.values()) == {'match'}
            prior = {q.id: existing_queries[q.id] for q in questions if q.id in existing_queries}
            revision, ready = answer_revision(conn, snapshot, answer, questions, prior, matched, at, seed.sha256)
            for q in questions:
                if not matched:
                    result['existing_exclusions'] += exclude_existing(
                        conn, prior.get(q.id), q, answer.sha256, 'source_evidence_mismatch', seed.sha256
                    )
                query, approved, outcome = attach_query(
                    conn, snapshot, q, revision, answer.sha256, prior.get(q.id), ready, at, seed.sha256
                )
                result['approved_questions'] += approved
                if approved:
                    approved_revisions.add(revision)
                result['blocked_evidence_questions'] += not matched
                result['existing_conflicts'] += outcome == 'existing_conflict'
                if outcome == 'existing_link':
                    bundle = json.loads(prior[q.id].source).get('bundle_sha256')
                    result['existing_links'] += bundle != seed.sha256
                    result['p07_links'] += bundle == P07_SHA256
                result['inserted_questions'] += outcome == 'inserted'
                audit(
                    conn,
                    'pass_seed_question',
                    query or q.id,
                    ACTOR,
                    IMPORT_REASON,
                    q.metadata
                    | {
                        'bundle_sha256': seed.sha256,
                        'source_id': q.id,
                        'answer_id': revision,
                        'question_sha256': digest(q.text),
                        'exact_approved': approved,
                        'outcome': outcome,
                    },
                )
        result['approved_answers'] = len(approved_revisions)
        result.update(f'hash_{value}' for value in checked.values())
        payload: dict[str, int | str] = dict(result)
        if approved_revisions:
            # Give a fresh cache an exact approval while preserving any existing approval.
            approval = control.approval_id or receipt
            conn.execute(
                'UPDATE cache_control SET namespace_id=?,activation_event=?,protection_hash=?,approval_id=?,'
                'generation=generation+1 WHERE singleton=1',
                (ns, event, protection.fingerprint, approval),
            )
            payload['approval_id'] = approval

        check_snapshot()
        audit(
            conn,
            ACTION,
            receipt,
            ACTOR,
            IMPORT_REASON,
            {
                'bundle_sha256': seed.sha256,
                'counts': payload,
                'protection_hash': protection.fingerprint,
                'snapshot': snapshot.release_id,
            },
        )
    return payload
