"""Write the reviewed handoff as an atomic cache approval."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from lawbot.answer_format import render
from lawbot.cache_seed import P07_SHA256
from lawbot.cache_store import SCHEMA, Draft, Store, audit, insert_answer, now, read_control, set_mode_in, valid
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import FORMAT, Snapshot, canonical, digest, normalize
from lawbot.semantic_handoff import Answer, Handoff, Question, evidence_result
from lawbot.semantic_policy import ReusePolicy
from lawbot.semantic_val import Protection

IMPORT_REASON = '사용자 전수 검수 승인 2026-11-15'
ACTOR = 'import'
ACTION = 'semantic_seed_import'
INTENTS = {
    'deadline': re.compile(
        r'언제(?:까지|부터)?(?:인가|입니까|인가요|까지인가요|까지예요|까지인지|까지 해야 하나요|까지 해야 합니까|까지인가)?\s*[?？.]?$'
    ),
    'requirements': re.compile(
        r'(?:요건|조건)(?:은|이|을)\s*(?:무엇인가요|무엇입니까|뭔가요|알려주세요|알려 주세요)\s*[?？.]?$'
    ),
    'procedure': re.compile(
        r'(?:절차|방법)(?:는|이|을)\s*(?:무엇인가요|무엇입니까|뭔가요|알려주세요|알려 주세요)\s*[?？.]?$'
    ),
    'permission': re.compile(r'(?:해도|하여도|가능한데 해도)\s*(?:되나요|됩니까|되나|되는가)\s*[?？.]?$'),
    'obligation': re.compile(r'(?:해야|하여야)\s*(?:하나요|합니까|하나|하는가)\s*[?？.]?$'),
}


def intent(text: str) -> str | None:
    if text.count('?') + text.count('？') > 1:
        return None
    return next((key for key, ending in INTENTS.items() if ending.search(text.rstrip())), None)


def make_policies(conn, questions, at):
    variants = defaultdict(set)
    for q in questions:
        if kind := intent(q.text):
            variants[kind].add(q.text.replace('{', '{{').replace('}', '}}'))
    policies = {}
    for kind, patterns in variants.items():
        policy = ReusePolicy(
            intent_id=kind,
            query_must_include=[],
            query_must_not_include=[],
            required_slots={},
            allowed_optional_slots={},
            slot_terms={},
            supported_query_patterns=sorted(patterns),
        )
        policies[kind] = policy.model_dump_json()
    policies[None] = canonical({'unsupported_condition_policy': 'bypass', 'intent_id': None})
    ids = {kind: digest(conditions) for kind, conditions in policies.items()}
    conn.executemany(
        "INSERT INTO semantic_reuse_policies VALUES (?,'1',?,?,?) ON CONFLICT DO NOTHING",
        ((ids[kind], value, at, ACTOR) for kind, value in policies.items()),
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


def answer_revision(conn, snapshot, answer: Answer, questions, prior, matched, at, sha):
    ns = snapshot.namespace.id
    citations = [(ref.article_id, ref.sha256) for ref in answer.display]
    reusable = sorted({row.answer_id for row in prior.values() if row.raw_hash == answer.sha256})
    same_display = next(
        (
            key
            for key in reusable
            if conn.execute(
                'SELECT article_id,text_sha256 FROM cache_citations WHERE answer_id=? ORDER BY position',
                (key,),
            ).fetchall()
            == citations
        ),
        None,
    )
    if same_display and matched:
        revision = same_display
    else:
        hits = snapshot.resolve(citations)
        text = render(answer.raw, [article.hit() for article in hits], FORMAT, done=True) if hits else answer.raw
        draft = Draft(
            questions[0].id,
            questions[0].text,
            answer.key,
            answer.sha256,
            text,
            citations,
            answer.metadata | {'bundle_sha256': sha, 'review_unit_id': answer.unit_id, 'group_id': answer.group_id},
            answer.raw,
        )
        revision = insert_answer(conn, ns, draft)
    if matched:
        conn.execute(
            "UPDATE cache_answers SET status='approved',reviewed_by=?,reviewed_at=?,valid_from=? "
            "WHERE answer_id=? AND status='draft'",
            (ACTOR, at, at, revision),
        )
    evidence = {ref.article_id: ref.sha256 for ref in answer.evidence}
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
        if not prior.matches(q, raw_hash):
            return None, False, 'existing_conflict'
        if prior.status == 'draft' and ready and prior.answer_id == revision:
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


def attach_atom(conn, snapshot, answer, q, atom, revision, query, policy, matched, approved, event, at):
    ns = snapshot.namespace.id
    query_revision = digest(canonical([atom, q.id, digest(q.text)]))
    binding = digest(canonical([query_revision, revision, policy]))
    enabled = approved and intent(q.text) is not None
    conn.execute(
        'INSERT INTO atomic_questions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
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
    if not matched:
        conn.execute(
            "INSERT INTO semantic_binding_decisions VALUES (?,?,NULL,?,?,'off',?,?,?,?)",
            (
                binding,
                event,
                answer.metadata.get('source_snapshot_id', snapshot.release_id),
                snapshot.release_id,
                'source_evidence_mismatch',
                canonical(evidence_result(answer, snapshot)),
                ACTOR,
                at,
            ),
        )
    return binding, enabled


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
        previous = conn.execute(
            'SELECT detail FROM cache_audit_events WHERE action=? AND target=?', (ACTION, receipt)
        ).fetchone()
        if previous:
            details = json.loads(previous[0])
            if details['protection_hash'] != protection.fingerprint:
                raise ValueError('protected split changed since import')
            return details['counts']
        if control.mode != 'off' or (control.namespace_id, control.activation_event) not in ((None, None), (ns, event)):
            raise ValueError('seed import requires an inactive cache copy for the current snapshot')
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
            policies = make_policies(conn, questions, at)
            atom = digest(
                canonical([ns, answer.group_id, answer.parent_sha256, answer.start, digest(answer.proposition)])
            )
            conn.execute(
                'INSERT INTO atoms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
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
                    policies[intent(q.text)],
                    matched,
                    approved,
                    event,
                    at,
                )
                result['approved_questions'] += approved
                if approved:
                    approved_revisions.add(revision)
                result['atomic_questions'] += 1
                result['semantic_enabled'] += enabled
                result['disabled_policy'] += approved and not enabled
                result['blocked_evidence_questions'] += not matched
                result['existing_conflicts'] += outcome == 'existing_conflict'
                result['existing_links'] += outcome == 'existing_link'
                if outcome == 'existing_link':
                    result['p07_links'] += json.loads(prior[q.id].source).get('bundle_sha256') == P07_SHA256
                result['inserted_questions'] += outcome == 'inserted'
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
        if approved_revisions:
            conn.execute(
                'UPDATE cache_control SET namespace_id=?,activation_event=?,protection_hash=?,approval_id=?,'
                'generation=generation+1 WHERE singleton=1',
                (ns, event, protection.fingerprint, receipt),
            )
        check_snapshot()
        audit(
            conn,
            ACTION,
            receipt,
            ACTOR,
            IMPORT_REASON,
            {
                'bundle_sha256': seed.sha256,
                'counts': dict(result),
                'protection_hash': protection.fingerprint,
                'snapshot': snapshot.release_id,
            },
        )
    return dict(result)


def activate_seed(db, mode, actor, reason, approval, snapshot, event, protection, check_snapshot):
    """Select the imported exact approval explicitly through the existing mode interface."""
    with connect(db, write=True) as conn, transaction(conn):
        control = read_control(conn)
        if (control.namespace_id, control.activation_event, control.protection_hash, control.approval_id) != (
            snapshot.namespace.id,
            event,
            protection.fingerprint,
            approval,
        ):
            raise ValueError('semantic seed approval, corpus, or protected split changed')
        receipt = conn.execute(
            'SELECT 1 FROM cache_audit_events WHERE action=? AND target=? AND actor=?', (ACTION, approval, ACTOR)
        ).fetchone()
        if receipt is None:
            raise ValueError('semantic seed approval was not found')
        exact_questions = conn.execute(
            "SELECT original FROM cache_queries WHERE namespace_id=? AND status='approved' AND exact_enabled=1",
            (snapshot.namespace.id,),
        )
        if any(protection.match(question) for (question,) in exact_questions):
            raise ValueError('an existing exact question overlaps the protected split')
        check_snapshot()
        set_mode_in(conn, control, mode, actor, reason, approval)
