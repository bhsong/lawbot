"""Persist a release assessment and its cache projection in one transaction."""

import json

from lawbot import corpus_store
from lawbot.cache_carry import allowed_question, article_rows, classify, corpus_changes, verified_inputs
from lawbot.cache_store import Draft, audit, insert_answer, now, read_control
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import PROFILE, canonical, corpus_version, digest


def citations_for(conn, answer_id):
    return conn.execute(
        'SELECT article_id,text_sha256 FROM cache_citations WHERE answer_id=? ORDER BY position', (answer_id,)
    ).fetchall()


def query_rows(conn, answer_id):
    return conn.execute(
        'SELECT query_id,source_id,original,raw_sha256,normalized,normalized_sha256,source,status,reviewed_by,'
        'reviewed_at,valid_from,valid_until,expires_at,exact_enabled FROM cache_queries WHERE answer_id=? ORDER BY query_id',
        (answer_id,),
    ).fetchall()


def raw_for(raw_hash, stored, originals):
    raw = stored if stored is not None else originals.get(raw_hash)
    return raw if raw is not None and digest(raw) == raw_hash else None


def preview(snapshot, raw, prior_citations):
    if raw is None or any(key not in snapshot.articles for key, _ in prior_citations):
        return None, prior_citations
    citations = [(key, snapshot.articles[key].text_hash) for key, _ in prior_citations]
    shown = snapshot.render(raw, citations)
    return (shown, citations) if shown is not None else (None, prior_citations)


def _put_query(conn, namespace, answer_id, row, keep):
    (
        _old_id,
        source_id,
        original,
        raw_hash,
        normalized,
        key,
        source,
        status,
        actor,
        reviewed,
        start,
        end,
        expires,
        enabled,
    ) = row
    query_id = digest(canonical([namespace, key]))
    exact = int(keep and status == 'approved' and enabled)
    next_status = 'revoked' if status == 'revoked' else ('approved' if exact else 'draft')
    result = conn.execute(
        'INSERT INTO cache_queries (query_id,namespace_id,answer_id,source_id,original,raw_sha256,normalized,'
        'normalized_sha256,source,status,reviewed_by,reviewed_at,valid_from,valid_until,expires_at,exact_enabled) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET '
        "answer_id=excluded.answer_id, status=CASE WHEN cache_queries.status='revoked' THEN 'revoked' "
        "ELSE excluded.status END, exact_enabled=CASE WHEN cache_queries.status='revoked' THEN 0 ELSE excluded.exact_enabled END, "
        'reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at,valid_from=excluded.valid_from, '
        'valid_until=excluded.valid_until,expires_at=excluded.expires_at '
        'WHERE cache_queries.source_id=excluded.source_id AND cache_queries.original=excluded.original',
        (
            query_id,
            namespace,
            answer_id,
            source_id,
            original,
            raw_hash,
            normalized,
            key,
            source,
            next_status,
            actor,
            reviewed,
            start,
            end,
            expires,
            exact,
        ),
    )
    if result.rowcount != 1:
        raise ValueError('릴리스 질문 연결의 출처가 기존 행과 다릅니다.')


def apply_release(path, db, token, release, snapshot, bundle_file):
    counts = dict.fromkeys(('keep', 'pending', 'off'), 0)
    namespace = snapshot.namespace.id
    with connect(path, write=True) as conn, transaction(conn):
        control = read_control(conn)
        if control.namespace_id is None or control.activation_event is None:
            return counts
        if (control.namespace_id, control.activation_event) == (namespace, token[1]):
            counts.update(
                {
                    verdict: count
                    for verdict, count in conn.execute(
                        "SELECT verdict,count(*) FROM cache_release_decisions WHERE activation_event=? AND verdict!='revoked' GROUP BY verdict",
                        (token[1],),
                    )
                }
            )
            return counts
        old = corpus_store.release_at_event(db, PROFILE, control.activation_event)
        spec_row = conn.execute(
            'SELECT spec FROM cache_namespaces WHERE namespace_id=?', (control.namespace_id,)
        ).fetchone()
        spec = json.loads(spec_row[0])
        if spec['corpus_version'] != corpus_version(old):
            raise ValueError('캐시 승인 범위와 이전 코퍼스가 일치하지 않습니다.')
        current_rows = article_rows(release)
        before = article_rows(old)
        comparisons = {old.release_id: (before, corpus_changes(before, current_rows))}
        protection, originals = verified_inputs(db, bundle_file)
        guard = None
        if spec['answer_policy_version'] != snapshot.namespace.answer_policy_version:
            guard = 'policy_changed'
        elif protection is None:
            guard = 'bundle_missing'
        elif control.protection_hash != protection.fingerprint:
            guard = 'protection_changed'
        rows = conn.execute(
            'SELECT a.answer_id,answer_key,raw_sha256,answer_text,source,status,reviewed_by,reviewed_at,valid_from,'
            'valid_until,expires_at,i.raw_answer,d.previous_release_id '
            'FROM cache_answers a LEFT JOIN cache_render_inputs i USING(answer_id) '
            'LEFT JOIN cache_release_decisions d ON d.answer_id=a.answer_id AND d.activation_event=? '
            'WHERE a.namespace_id=? AND EXISTS(SELECT 1 FROM cache_queries q WHERE q.answer_id=a.answer_id '
            "AND q.reviewed_at IS NOT NULL AND (q.exact_enabled=1 OR q.status='revoked' OR a.status='revoked' "
            'OR EXISTS(SELECT 1 FROM cache_release_decisions d WHERE d.answer_id=a.answer_id AND d.activation_event=?))) '
            'ORDER BY a.answer_id',
            (control.activation_event, control.namespace_id, control.activation_event),
        ).fetchall()
        # Snapshot source rows first. The destination can be a previously served namespace.
        source_rows = [(row, query_rows(conn, row[0])) for row in rows]
        conn.execute(
            'INSERT INTO cache_namespaces VALUES (?,?,?) ON CONFLICT DO NOTHING',
            (namespace, snapshot.namespace.serialize(), now()),
        )
        conn.execute('UPDATE cache_queries SET exact_enabled=0 WHERE namespace_id=? AND exact_enabled=1', (namespace,))
        for row, queries in source_rows:
            old_id, key, raw_hash, old_text, encoded, status, actor, reviewed, start, end, expires, stored, baseline = (
                row
            )
            baseline = baseline if status == 'draft' and baseline else old.release_id
            if baseline not in comparisons:
                prior_rows = article_rows(corpus_store.release_by_id(db, PROFILE, baseline))
                comparisons[baseline] = (prior_rows, corpus_changes(prior_rows, current_rows))
            before, changed = comparisons[baseline]
            source = json.loads(encoded)
            prior = citations_for(conn, old_id)
            dependencies = {key for key, _ in prior} | set(source.get('review_evidence_article_ids', []))
            raw = raw_for(raw_hash, stored, originals)
            shown, citations = preview(snapshot, raw, prior)
            eligible = [q for q in queries if not is_calculation_question(q[2])]
            excluded_ids = {q[0] for q in queries} - {q[0] for q in eligible}
            answer_guard = guard
            if protection is not None and any(q[-1] and not allowed_question(protection, q[1], q[2]) for q in eligible):
                answer_guard = answer_guard or 'protected_question'
            verdict, reason, changes = classify(dependencies, before, changed, answer_guard, old_text, shown)
            if status == 'revoked':
                verdict, reason = 'revoked', 'withdrawn'
            elif excluded_ids and not any(q[7] != 'revoked' and q[9] is not None for q in eligible):
                verdict, reason = 'off', 'calculation_question'
            elif status != 'approved' and verdict == 'keep':
                verdict, reason = 'pending', 'awaiting_approval'
            draft = Draft(
                '',
                '',
                key,
                raw_hash,
                shown if shown is not None else old_text,
                citations,
                source | {'previous_answer_id': old_id},
                raw,
            )
            new_id = insert_answer(conn, namespace, draft)
            historical = conn.execute('SELECT status FROM cache_answers WHERE answer_id=?', (new_id,)).fetchone()[0]
            if historical == 'revoked':
                verdict, reason = 'revoked', 'withdrawn'
            keep = verdict == 'keep'
            next_status = 'approved' if keep else ('revoked' if verdict == 'revoked' else 'draft')
            conn.execute(
                'UPDATE cache_answers SET status=?,reviewed_by=?,reviewed_at=?,valid_from=?,valid_until=?,expires_at=? '
                'WHERE answer_id=?',
                (next_status, actor, reviewed, start, end, expires, new_id),
            )
            for query in queries:
                if query[9] is not None:
                    _put_query(conn, namespace, new_id, query, keep and query[0] not in excluded_ids)
            conn.execute(
                'INSERT INTO cache_release_decisions VALUES (?,?,?,?,?,?,?,?)',
                (token[1], new_id, old_id, token[0], baseline, verdict, reason, canonical(changes)),
            )
            audit(
                conn,
                'carry',
                new_id,
                'articles-update',
                reason,
                {
                    'verdict': verdict,
                    'release_id': token[0],
                    'activation_event': token[1],
                    'previous_answer_id': old_id,
                },
            )
            if verdict in counts:
                counts[verdict] += 1
        if corpus_store.active_token(db, PROFILE) != token:
            raise ValueError('판정 중 활성 코퍼스가 변경되었습니다.')
        approval_id = digest(canonical([control.approval_id, namespace, token, control.generation]))
        conn.execute(
            'UPDATE cache_control SET namespace_id=?,activation_event=?,approval_id=?, '
            'generation=generation+1 WHERE singleton=1',
            (namespace, token[1], approval_id),
        )
    return counts
