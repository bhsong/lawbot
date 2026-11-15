"""Write the reviewed handoff as an atomic cache approval."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from lawbot.answer_format import render
from lawbot.cache_seed import P07_SHA256, Protection
from lawbot.cache_store import SCHEMA, Draft, Store, audit, insert_answer, now, read_control, set_mode_in, valid
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import FORMAT, Snapshot, canonical, digest, normalize
from lawbot.semantic_handoff import Answer, Handoff, Question, evidence_result
from lawbot.semantic_policy import INTENTS, ReusePolicy, classify_intent

IMPORT_REASON = '사용자 전수 검수 승인 2026-11-15'
ACTOR = 'import'
ACTION = 'semantic_seed_import'
UPGRADE = 'semantic_seed_policy_upgrade'


def make_policies(conn, at):
    policies = {
        intent: ReusePolicy(kind='intent', intent_id=intent).model_dump_json(exclude_none=True, exclude_defaults=True)
        for intent in [*INTENTS, None]
    }
    ids = {intent: digest(conditions) for intent, conditions in policies.items()}
    conn.executemany(
        "INSERT INTO semantic_reuse_policies VALUES (?,'1',?,?,?) ON CONFLICT DO NOTHING",
        ((ids[intent], value, at, ACTOR) for intent, value in policies.items()),
    )
    return ids


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
    audit(conn, 'semantic_seed_exclusion', prior.query_id, ACTOR, reason, {'bundle_sha256': bundle_hash})
    return True


def compatible_revision(conn, revision, citations, evidence):
    displayed = conn.execute(
        'SELECT article_id,text_sha256 FROM cache_citations WHERE answer_id=? ORDER BY position', (revision,)
    ).fetchall()
    if displayed != citations:
        return False
    source = conn.execute('SELECT source FROM cache_answers WHERE answer_id=?', (revision,)).fetchone()
    reviewed = json.loads(source[0]).get('review_evidence_article_ids')
    if reviewed is not None and set(reviewed) != evidence.keys():
        return False
    stored = dict(
        conn.execute(
            "SELECT article_id,text_sha256 FROM semantic_answer_evidence WHERE answer_id=? AND kind='dependency'",
            (revision,),
        )
    )
    return not stored or stored == evidence


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
            'group_id': answer.group_id,
            'review_evidence_article_ids': list(evidence),
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
    existing = dict(
        conn.execute(
            "SELECT article_id,text_sha256 FROM semantic_answer_evidence WHERE answer_id=? AND kind='dependency'",
            (revision,),
        )
    )
    if any(article in existing and existing[article] != checksum for article, checksum in evidence.items()):
        raise ValueError('existing answer revision has incompatible review evidence')
    position = conn.execute(
        "SELECT coalesce(max(position)+1,0) FROM semantic_answer_evidence WHERE answer_id=? AND kind='dependency'",
        (revision,),
    ).fetchone()[0]
    conn.executemany(
        "INSERT INTO semantic_answer_evidence VALUES (?,'dependency',?,?,?)",
        (
            (revision, position + i, article, checksum)
            for i, (article, checksum) in enumerate(evidence.items())
            if article not in existing
        ),
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


def attach_atom(conn, snapshot, answer, q, atom, revision, query, policy, matched, approved, outcome, event, at):
    ns = snapshot.namespace.id
    query_revision = digest(canonical([atom, q.id, digest(q.text)]))
    binding = digest(canonical([query_revision, revision, policy]))
    enabled = approved
    conn.execute(
        'INSERT INTO atomic_questions VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
        (
            query_revision,
            q.id,
            atom,
            q.text,
            digest(q.text),
            'reviewed_handoff',
            'approved' if approved else 'draft',
            at,
            ACTOR,
            at if approved else None,
            ACTOR if approved else None,
        ),
    )
    conn.execute(
        "INSERT INTO atom_answer_bindings VALUES (?,?,?,?,?,?,'1',?,?,?,?,?,?,NULL,?)",
        (
            binding,
            ns,
            atom,
            query_revision,
            revision,
            policy,
            query,
            event if approved else None,
            int(enabled),
            at if approved else None,
            ACTOR if approved else None,
            at if approved else None,
            'keep' if approved else 'off',
        ),
    )
    if not matched or outcome == 'existing_conflict':
        reason = 'source_evidence_mismatch' if not matched else 'existing_conflict'
        changes = (
            evidence_result(answer, snapshot)
            if not matched
            else {
                'answer_revision': revision,
                'reason': 'existing_exact_answer_revision_differs',
            }
        )
        conn.execute(
            "INSERT INTO semantic_binding_decisions VALUES (?,?,NULL,?,?,'off',?,?,?,?)",
            (
                binding,
                event,
                answer.metadata.get('source_snapshot_id', snapshot.release_id),
                snapshot.release_id,
                reason,
                canonical(changes),
                ACTOR,
                at,
            ),
        )
    return binding, enabled


def seed_receipt(conn, receipt):
    row = conn.execute(
        'SELECT detail FROM cache_audit_events WHERE action IN (?,?) AND target=? AND actor=? ORDER BY event_id DESC LIMIT 1',
        (ACTION, UPGRADE, receipt, ACTOR),
    ).fetchone()
    return json.loads(row[0]) if row else None


def previous_exact_approval(conn, receipt, namespace, event):
    # The legacy import overwrote both fields. Recover an audited, release-scoped exact approval if available.
    row = conn.execute(
        "SELECT target,json_extract(detail,'$.protection_hash') FROM cache_audit_events "
        "WHERE action='approve' AND json_extract(detail,'$.namespace_id')=? "
        "AND json_extract(detail,'$.activation_event')=? AND event_id < ("
        'SELECT min(event_id) FROM cache_audit_events WHERE action=? AND target=?) '
        'ORDER BY event_id DESC LIMIT 1',
        (namespace, event, ACTION, receipt),
    ).fetchone()
    return row if row is not None else (None, None)


def retire_legacy_bindings(conn, ns, sha):
    conn.execute(
        "UPDATE atom_answer_bindings SET semantic_enabled=0,carry_status='off' WHERE namespace_id=? "
        'AND binding_id IN (SELECT target FROM cache_audit_events WHERE action=? AND actor=? '
        "AND json_extract(detail,'$.bundle_sha256')=?)",
        (ns, 'semantic_seed_question', ACTOR, sha),
    )


def import_seed(
    db: Path, seed: Handoff, snapshot: Snapshot, event: int, protection: Protection, *, check_snapshot=lambda: None
):
    """Approve one verified pass bundle in a private 0003 cache copy, preserving exact mode and history."""
    if Store(db).check_schema() != SCHEMA:
        raise ValueError('semantic seed requires the current cache schema')
    ns = snapshot.namespace.id
    receipt = digest(canonical([seed.sha256, ns, event]))
    groups = defaultdict(list)
    rejected = []
    result: Counter[str] = Counter(
        dict.fromkeys(
            (
                'approved_questions',
                'approved_answers',
                'excluded_calculation',
                'disabled_policy',
                'blocked_evidence_questions',
                'existing_conflicts',
                'existing_exclusions',
                'p07_links',
                'existing_links',
                'inserted_questions',
                'atoms',
                'atomic_questions',
                'hash_match',
                'hash_mismatch',
                'hash_missing',
                'seed_protected_exact',
                'seed_protected_near',
                'semantic_enabled',
            ),
            0,
        )
    )
    result.update(dict.fromkeys((f'intent_{kind}' for kind in [*INTENTS, 'none']), 0))
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
        control = read_control(conn)
        previous = seed_receipt(conn, receipt)
        if previous is not None and previous.get('policy_format') == 'intent':
            if previous['protection_hash'] != protection.fingerprint:
                raise ValueError('protected split changed since import')
            return previous['counts']
        if control.mode != 'off' or (control.namespace_id, control.activation_event) not in ((None, None), (ns, event)):
            raise ValueError('seed import requires an inactive cache copy for the current snapshot')
        conn.execute(
            'INSERT INTO cache_namespaces VALUES (?,?,?) ON CONFLICT DO NOTHING',
            (ns, snapshot.namespace.serialize(), at),
        )
        if previous is not None:
            retire_legacy_bindings(conn, ns, seed.sha256)
        policies = make_policies(conn, at)
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
            atom = digest(
                canonical([ns, answer.group_id, answer.parent_sha256, answer.start, digest(answer.proposition)])
            )
            conn.execute(
                'INSERT INTO atoms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                (
                    atom,
                    answer.group_id,
                    ns,
                    snapshot.release_id,
                    answer.parent,
                    answer.parent_sha256,
                    answer.proposition,
                    answer.start,
                    answer.start + len(answer.proposition),
                    'approved' if ready else 'draft',
                    at,
                    ACTOR,
                    at if ready else None,
                    ACTOR if ready else None,
                ),
            )
            result['atoms'] += 1
            for q in questions:
                intent = classify_intent(q.text)
                if not matched:
                    result['existing_exclusions'] += exclude_existing(
                        conn, prior.get(q.id), q, answer.sha256, 'source_evidence_mismatch', seed.sha256
                    )
                query, approved, outcome = attach_query(
                    conn, snapshot, q, revision, answer.sha256, prior.get(q.id), ready, at, seed.sha256
                )
                binding, enabled = attach_atom(
                    conn,
                    snapshot,
                    answer,
                    q,
                    atom,
                    revision,
                    query,
                    policies[intent],
                    matched,
                    approved,
                    outcome,
                    event,
                    at,
                )
                result['approved_questions'] += approved
                if approved:
                    approved_revisions.add(revision)
                result['atomic_questions'] += 1
                result['semantic_enabled'] += enabled
                if approved:
                    result[f'intent_{intent or "none"}'] += 1
                result['disabled_policy'] += approved and not enabled
                result['blocked_evidence_questions'] += not matched
                result['existing_conflicts'] += outcome == 'existing_conflict'
                if outcome == 'existing_link':
                    bundle = json.loads(prior[q.id].source).get('bundle_sha256')
                    result['existing_links'] += bundle != seed.sha256
                    result['p07_links'] += bundle == P07_SHA256
                result['inserted_questions'] += outcome == 'inserted'
                if previous is None:
                    audit(
                        conn,
                        'semantic_seed_question',
                        binding,
                        ACTOR,
                        IMPORT_REASON,
                        q.metadata
                        | {
                            'bundle_sha256': seed.sha256,
                            'source_id': q.id,
                            'answer_id': revision,
                            'question_sha256': digest(q.text),
                            'exact_approved': approved,
                            'semantic_enabled': enabled,
                            'outcome': outcome,
                        },
                    )
        result['approved_answers'] = len(approved_revisions)
        result.update(f'hash_{value}' for value in checked.values())
        if previous is not None and control.approval_id == receipt:
            exact_approval, exact_protection = previous_exact_approval(conn, receipt, ns, event)
            conn.execute(
                'UPDATE cache_control SET protection_hash=?,approval_id=? WHERE singleton=1',
                (exact_protection, exact_approval),
            )
        if approved_revisions:
            # The exact controller keeps any existing exact approval and its protection identity.
            conn.execute(
                'UPDATE cache_control SET namespace_id=?,activation_event=?,protection_hash=coalesce(protection_hash,?),'
                'generation=generation+1 WHERE singleton=1',
                (ns, event, protection.fingerprint),
            )
        elif previous is not None:
            conn.execute('UPDATE cache_control SET generation=generation+1 WHERE singleton=1')
        payload: dict[str, int | str] = dict(result)
        if previous is not None:
            payload['inserted_questions'] += previous['counts']['inserted_questions']
            payload['existing_exclusions'] += previous['counts']['existing_exclusions']
        if approved_revisions:
            payload['approval_id'] = receipt

        check_snapshot()
        audit(
            conn,
            ACTION if previous is None else UPGRADE,
            receipt,
            ACTOR,
            IMPORT_REASON,
            {
                'bundle_sha256': seed.sha256,
                'counts': payload,
                'policy_format': 'intent',
                'protection_hash': protection.fingerprint,
                'snapshot': snapshot.release_id,
            },
        )
    return payload


def activate_seed(db, mode, actor, reason, approval, snapshot, event, protection, check_snapshot):
    """Select the imported exact approval explicitly through the existing mode interface."""
    with connect(db, write=True) as conn, transaction(conn):
        control = read_control(conn)
        receipt = seed_receipt(conn, approval)
        if receipt is None or receipt['counts'].get('approval_id') != approval:
            raise ValueError('semantic seed approval was not found')
        if (
            (control.namespace_id, control.activation_event) != (snapshot.namespace.id, event)
            or receipt['snapshot'] != snapshot.release_id
            or receipt['protection_hash'] != protection.fingerprint
            or approval != digest(canonical([receipt['bundle_sha256'], snapshot.namespace.id, event]))
        ):
            raise ValueError('semantic seed approval, corpus, or protected split changed')
        check_snapshot()
        if mode != 'off' and control.approval_id is None:
            # The exact controller requires an approval for a serving mode. Seed receipts stay separate.
            exact_approval = digest(canonical(['exact-from-semantic-seed', approval]))
            conn.execute('UPDATE cache_control SET approval_id=? WHERE singleton=1', (exact_approval,))
            audit(conn, 'semantic_seed_exact_activation', exact_approval, actor, reason, {'seed_approval_id': approval})
            control = read_control(conn)
        set_mode_in(conn, control, mode, actor, reason, control.approval_id)
