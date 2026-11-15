"""Write the reviewed pass handoff into the exact-cache catalog."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from lawbot.answer_format import render
from lawbot.cache_seed import P07_SHA256, Protection
from lawbot.cache_store import Draft, Store, audit, insert_answer, now, read_control, valid, with_review_evidence
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import FORMAT, Snapshot, canonical, digest, normalize

IMPORT_REASON = '사용자 전수 검수 승인 2026-11-15'
ACTOR = 'import'
ACTION = 'pass_seed_import'
DB_ACTION = 'cache_db_import'


@dataclass(frozen=True)
class Reference:
    article_id: str
    sha256: str


@dataclass(frozen=True)
class Answer:
    key: str
    unit_id: str
    parent: str | None
    raw: str
    sha256: str
    display: tuple[Reference, ...]
    evidence: tuple[Reference, ...]
    metadata: dict
    parent_sha256: str | None
    revision_key: str | None = None


@dataclass(frozen=True)
class Question:
    id: str
    answer_key: str
    text: str
    metadata: dict
    valid_until: str | None = None
    expires_at: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class Handoff:
    sha256: str
    answers: tuple[Answer, ...]
    questions: tuple[Question, ...]


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
    source = with_review_evidence(conn, revision, json.loads(row[0]))
    if set(source.get('review_evidence_article_ids', ())) - evidence.keys():
        return False
    return all(evidence.get(key) == checksum for key, checksum in source.get('review_evidence_hashes', {}).items())


def review_hashes(answer: Answer) -> dict[str, str]:
    evidence = {ref.article_id: ref.sha256 for ref in answer.evidence}
    if answer.parent is not None and answer.parent_sha256 is not None:
        if answer.parent not in {ref.article_id for ref in answer.display}:
            evidence[answer.parent] = answer.parent_sha256
    return evidence


def evidence_result(answer: Answer, snapshot: Snapshot) -> dict[str, str]:
    expected = {ref.article_id: ref.sha256 for ref in answer.display} | review_hashes(answer)
    if answer.parent is not None and answer.parent_sha256 is not None:
        expected[answer.parent] = answer.parent_sha256
    result = {}
    for key, checksum in expected.items():
        article = snapshot.articles.get(key)
        result[key] = 'missing' if article is None else ('match' if article.text_hash == checksum else 'mismatch')
    return result


def answer_revision(conn, snapshot, answer: Answer, questions, prior, matched, at, sha, *, reuse_prior):
    ns = snapshot.namespace.id
    citations = [(ref.article_id, ref.sha256) for ref in answer.display]
    evidence = review_hashes(answer)
    reusable = sorted({row.answer_id for row in prior.values() if row.raw_hash == answer.sha256}) if reuse_prior else ()
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
            questions[0].id,
            questions[0].text,
            answer.revision_key or answer.key,
            answer.sha256,
            text,
            citations,
            source,
            answer.raw,
        )
        intended = draft.answer_id(ns)
        if conn.execute(
            'SELECT 1 FROM cache_answers WHERE answer_id=?', (intended,)
        ).fetchone() is not None and not compatible_revision(conn, intended, citations, evidence):
            # Old exact revision IDs omit review-only evidence. Keep its existing audit and rendering.
            draft = replace(draft, answer_key=f'{draft.answer_key}:evidence:{digest(canonical(evidence))}')
        revision = insert_answer(conn, ns, draft)

    def attachable(question):
        if not valid(at, at, question.valid_until, question.expires_at):
            return False
        row = prior.get(question.id)
        return row is None or (
            row.answer_id == revision
            and row.matches(question, answer.sha256)
            and (
                (row.status == 'draft' and valid(at, at, row.valid_until, row.expires_at))
                or (
                    row.status == 'approved'
                    and row.enabled
                    and valid(at, row.valid_from, row.valid_until, row.expires_at)
                )
            )
        )

    added = False
    if matched and any(attachable(question) for question in questions):
        added = bool(
            conn.execute(
                "UPDATE cache_answers SET status='approved',reviewed_by=?,reviewed_at=?,valid_from=? "
                "WHERE answer_id=? AND status='draft'",
                (ACTOR, at, at, revision),
            ).rowcount
        )
    status, *bounds = conn.execute(
        'SELECT status,valid_from,valid_until,expires_at FROM cache_answers WHERE answer_id=?', (revision,)
    ).fetchone()
    return revision, matched and status == 'approved' and valid(at, *bounds), added


def record_review_evidence(conn, revision, answer, bundle_hash):
    row = conn.execute('SELECT source FROM cache_answers WHERE answer_id=?', (revision,)).fetchone()
    source = with_review_evidence(conn, revision, json.loads(row[0]))
    known = source.get('review_evidence_hashes', {})
    added = {key: checksum for key, checksum in review_hashes(answer).items() if key not in known}
    if added:
        audit(
            conn,
            'review_evidence',
            revision,
            ACTOR,
            IMPORT_REASON,
            {
                'bundle_sha256': bundle_hash,
                'review_evidence_hashes': added,
            },
        )
    conn.execute('INSERT INTO cache_render_inputs VALUES (?,?) ON CONFLICT DO NOTHING', (revision, answer.raw))


def earliest(*values):
    return min((value for value in values if value is not None), default=None)


def attach_query(conn, snapshot, q: Question, revision, raw_hash, prior, ready, at, sha):
    normalized = normalize(q.text)
    ns = snapshot.namespace.id
    qhash = digest(normalized)
    query = digest(canonical([ns, qhash]))
    ready = ready and valid(at, at, q.valid_until, q.expires_at)
    if prior is not None:
        if not prior.matches(q, raw_hash) or prior.answer_id != revision:
            return None, False, 'existing_conflict'
        if prior.status == 'draft' and ready:
            end, expires = earliest(prior.valid_until, q.valid_until), earliest(prior.expires_at, q.expires_at)
        else:
            end, expires = prior.valid_until, prior.expires_at
        if prior.status == 'draft' and ready and valid(at, at, end, expires):
            conn.execute(
                "UPDATE cache_queries SET status='approved',exact_enabled=1,reviewed_by=?,reviewed_at=?,valid_from=?,"
                'valid_until=?,expires_at=? '
                'WHERE query_id=?',
                (ACTOR, at, at, end, expires, prior.query_id),
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
        'normalized_sha256,source,status,exact_enabled,reviewed_by,reviewed_at,valid_from,valid_until,expires_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (
            query,
            ns,
            revision,
            q.source_id or q.id,
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
            q.valid_until,
            q.expires_at,
        ),
    )
    return query, ready, 'inserted'


def seed_receipt(conn, receipt, action):
    row = conn.execute(
        'SELECT detail FROM cache_audit_events WHERE action=? AND target=? AND actor=? ORDER BY event_id DESC LIMIT 1',
        (action, receipt, ACTOR),
    ).fetchone()
    return json.loads(row[0]) if row else None


def check_seed_import(conn, namespace, event, receipt, protection, action):
    control = read_control(conn)
    if action == DB_ACTION and control.mode != 'off':
        raise ValueError('DB 적재 전에 대상 캐시 모드를 off로 전환하세요.')
    previous = seed_receipt(conn, receipt, action)
    if (
        action == ACTION
        and previous is None
        and conn.execute('SELECT 1 FROM cache_audit_events WHERE action=? LIMIT 1', (ACTION,)).fetchone()
    ):
        raise ValueError('다른 통과 ZIP 적재 또는 코퍼스 활성화 이력이 있습니다. 새 개발 사본을 사용하세요.')
    if previous is not None and previous['protection_hash'] != protection.fingerprint:
        raise ValueError('protected split changed since import')
    if control.protection_hash not in (None, protection.fingerprint):
        active = conn.execute(
            "SELECT 1 FROM cache_queries WHERE namespace_id=? AND status='approved' AND exact_enabled=1 LIMIT 1",
            (control.namespace_id,),
        ).fetchone()
        if action == ACTION or active is not None or previous is not None:
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
    db: Path,
    seed: Handoff,
    snapshot: Snapshot,
    event: int,
    protection: Protection,
    *,
    check_snapshot=lambda: None,
    source_db_sha256: str | None = None,
):
    """Approve a verified batch atomically under the destination corpus and split."""
    Store(db).check_schema()
    ns = snapshot.namespace.id
    action = ACTION if source_db_sha256 is None else DB_ACTION
    origin = (
        ['pass-seed', seed.sha256]
        if source_db_sha256 is None
        else [DB_ACTION, source_db_sha256, protection.fingerprint]
    )
    receipt = digest(canonical([*origin, ns, event]))
    with connect(db) as conn:
        conn.execute('BEGIN')
        _, previous = check_seed_import(conn, ns, event, receipt, protection, action)
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
        control, previous = check_seed_import(conn, ns, event, receipt, protection, action)
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
        approval_added = False
        for answer in seed.answers:
            statuses = evidence_result(answer, snapshot)
            checked.update(statuses)
            if not (questions := groups.get(answer.key)):
                continue
            matched = set(statuses.values()) == {'match'}
            prior = {q.id: existing_queries[q.id] for q in questions if q.id in existing_queries}
            revision, ready, added = answer_revision(
                conn, snapshot, answer, questions, prior, matched, at, seed.sha256, reuse_prior=action == ACTION
            )
            approval_added |= added
            approved_for_answer = False
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
                    approved_for_answer = True
                    approved_revisions.add(revision)
                    approval_added |= q.id not in prior or prior[q.id].status == 'draft'
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
                        'source_id': q.source_id or q.id,
                        'answer_id': revision,
                        'question_sha256': digest(q.text),
                        'exact_approved': approved,
                        'outcome': outcome,
                    },
                )
            if approved_for_answer:
                record_review_evidence(conn, revision, answer, seed.sha256)
        result['approved_answers'] = len(approved_revisions)
        result.update(f'hash_{value}' for value in checked.values())
        payload: dict[str, int | str] = dict(result)
        if approved_revisions:
            approval = digest(canonical([control.approval_id, receipt])) if approval_added else control.approval_id
            conn.execute(
                'UPDATE cache_control SET namespace_id=?,activation_event=?,protection_hash=?,approval_id=?,'
                'generation=generation+1 WHERE singleton=1',
                (ns, event, protection.fingerprint, approval),
            )
            if approval is not None:
                payload['approval_id'] = approval
            if approval_added:
                audit(
                    conn,
                    'approve',
                    approval,
                    ACTOR,
                    IMPORT_REASON,
                    {
                        'previous_approval_id': control.approval_id,
                        'import_receipt': receipt,
                    },
                )

        check_snapshot()
        audit(
            conn,
            action,
            receipt,
            ACTOR,
            IMPORT_REASON,
            {
                'bundle_sha256': seed.sha256,
                'counts': payload,
                'protection_hash': protection.fingerprint,
                'snapshot': snapshot.release_id,
                'namespace_id': ns,
                'previous_approval_id': control.approval_id,
            }
            | ({'source_db_sha256': source_db_sha256} if source_db_sha256 is not None else {}),
        )
    return payload
