"""Read and review cache revisions under the administrator's generation fence."""

import json
from dataclasses import dataclass
from pathlib import Path

from lawbot import corpus_store
from lawbot.cache_carry import FIELDS, allowed_question, article_rows, verified_inputs
from lawbot.cache_release_store import citations_for, preview, query_rows, raw_for
from lawbot.cache_store import (
    Draft,
    audit,
    insert_answer,
    now,
    read_control,
    revoke_in,
    set_mode_in,
    translate_sqlite_errors,
)
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import PROFILE, Snapshot, canonical, digest
from lawbot.feedback_store import StoreError

PROTECTION_CHANGED = '기존 승인과 보호 분할이 다릅니다. 추가 보호 CSV를 사용했다면 포함하여 CLI에서 다시 승인하세요.'


def disposition(status, verdict):
    if status == 'revoked':
        return 'revoked'
    if status == 'approved':
        return 'keep' if verdict == 'keep' else 'approved'
    return verdict if verdict in ('pending', 'off') else 'draft'


def reviewable_query(query, protection, mode, state):
    """Pending answers in serving modes can only recover previously reviewed links."""
    return (
        query[7] != 'revoked'
        and (state == 'draft' or mode == 'off' or query[9] is not None)
        and allowed_question(protection, query[1], query[2])
    )


class ApprovalError(StoreError):
    def __init__(self, answer_id, error: StoreError):
        super().__init__(error.code, error.detail, error.status)
        self.answer_id = answer_id


@dataclass(frozen=True)
class ReviewCatalog:
    path: Path
    corpus_db: Path
    snapshot: Snapshot
    bundle: Path | None

    def current(self, control, token):
        return (
            token is not None
            and token[0] == self.snapshot.release_id
            and (
                control.namespace_id is None
                or (control.namespace_id, control.activation_event) == (self.snapshot.namespace.id, token[1])
            )
        )

    def index(self, status):
        with translate_sqlite_errors(), connect(self.path) as conn:
            conn.execute('BEGIN')
            control = read_control(conn)
            token = corpus_store.active_token(self.corpus_db, PROFILE)
            namespace = control.namespace_id or self.snapshot.namespace.id
            rows = conn.execute(
                'SELECT a.answer_id,a.answer_key,a.status,d.verdict,d.reason,d.release_id,count(q.query_id) '
                'FROM cache_answers a JOIN cache_queries q USING(answer_id) '
                'LEFT JOIN cache_release_decisions d ON d.answer_id=a.answer_id AND d.activation_event=? '
                'WHERE a.namespace_id=? GROUP BY a.answer_id ORDER BY a.answer_key,a.answer_id',
                (control.activation_event, namespace),
            ).fetchall()
        items = [
            dict(
                answer_id=answer,
                answer_key=key,
                status=disposition(state, verdict),
                reason=reason,
                release_id=release_id,
                question_count=count,
            )
            for answer, key, state, verdict, reason, release_id, count in rows
        ]
        disabled = self.disabled(control, token)
        return dict(
            mode=control.mode,
            generation=control.generation,
            namespace=namespace[:12],
            active_release=token[0] if token else None,
            approval_id=control.approval_id,
            disabled_reason=disabled,
            items=[row for row in items if status in ('all', row['status'])],
        )

    def disabled(self, control, token):
        if self.bundle is None:
            return 'LAWBOT_EXACT_CACHE_BUNDLE 설정이 필요합니다.'
        if not self.bundle.is_file():
            return '설정된 P07 번들 파일을 찾을 수 없습니다.'
        if not self.current(control, token):
            return '활성 릴리스의 캐시 재판정과 웹앱 갱신이 필요합니다.'
        return None

    def answer(self, conn, control, answer_id):
        row = conn.execute(
            'SELECT a.answer_key,a.raw_sha256,a.answer_text,a.source,a.status,i.raw_answer,d.verdict,d.reason,'
            'd.previous_answer_id,d.previous_release_id,d.release_id,d.changes '
            'FROM cache_answers a LEFT JOIN cache_render_inputs i USING(answer_id) '
            'LEFT JOIN cache_release_decisions d ON d.answer_id=a.answer_id AND d.activation_event=? '
            'WHERE a.namespace_id=? AND a.answer_id=?',
            (control.activation_event, control.namespace_id or self.snapshot.namespace.id, answer_id),
        ).fetchone()
        if row is None:
            raise StoreError('not_found', '캐시 답변을 찾을 수 없습니다.', 404)
        return row

    def detail(self, answer_id):
        with translate_sqlite_errors(), connect(self.path) as conn:
            conn.execute('BEGIN')
            control = read_control(conn)
            token = corpus_store.active_token(self.corpus_db, PROFILE)
            row = self.answer(conn, control, answer_id)
            key, raw_hash, text, _, status, stored, verdict, reason, _previous, old_release, release_id, changed = row
            queries = query_rows(conn, answer_id)
            citations = citations_for(conn, answer_id)
            history = conn.execute(
                'WITH RECURSIVE revisions(id) AS (SELECT ? UNION SELECT d.previous_answer_id '
                'FROM cache_release_decisions d JOIN revisions r ON d.answer_id=r.id) '
                'SELECT action,actor,reason,created_at FROM cache_audit_events WHERE target IN revisions '
                'OR target IN (SELECT query_id FROM cache_queries WHERE answer_id IN revisions) ORDER BY event_id DESC',
                (answer_id,),
            ).fetchall()
        cards = self.snapshot.resolve(citations) or []
        disabled = self.disabled(control, token)
        shown = None
        raw = raw_for(raw_hash, stored, {})
        state = disposition(status, verdict)
        if disabled is None and state in ('pending', 'draft'):
            try:
                protection, originals = verified_inputs(self.corpus_db, self.bundle)
                if protection is None:
                    disabled = 'P07 번들 또는 보호 분할을 확인할 수 없습니다.'
                elif control.protection_hash not in (None, protection.fingerprint):
                    disabled = PROTECTION_CHANGED
                elif not any(reviewable_query(q, protection, control.mode, state) for q in queries):
                    disabled = '현재 모드에서 승인할 수 있는 질문이 없습니다.'
                else:
                    raw = raw_for(raw_hash, stored, originals)
                    shown, new_citations = preview(self.snapshot, raw, citations)
                    if shown is not None:
                        cards = self.snapshot.resolve(new_citations) or []
                    else:
                        disabled = '현재 조문으로 표시본을 확인할 수 없습니다. CLI replace로 정정하세요.'
            except ValueError, OSError, RuntimeError:
                disabled = 'P07 번들 또는 보호 분할을 확인할 수 없습니다.'
        changes = []
        if changed and old_release is not None and release_id is not None:
            before = article_rows(corpus_store.release_by_id(self.corpus_db, PROFILE, old_release))
            after = article_rows(corpus_store.release_by_id(self.corpus_db, PROFILE, release_id))
            for article_id in json.loads(changed):
                changes.append(
                    dict(
                        article_id=article_id,
                        before=article_view(before.get(article_id)),
                        after=article_view(after.get(article_id)),
                    )
                )
        return dict(
            answer_id=answer_id,
            answer_key=key,
            status=state,
            reason=reason,
            generation=control.generation,
            mode=control.mode,
            text=text,
            raw_answer=raw,
            preview=shown,
            preview_sha256=digest(shown) if shown and disabled is None else None,
            disabled_reason=disabled,
            questions=[dict(query_id=q[0], question=q[2], status=q[7]) for q in queries],
            cards=[
                dict(title=f'{a.law} {a.number} {a.title}', body=a.text, scores='', decree=a.kind != '법률')
                for a in cards
            ],
            changes=changes,
            audit=[dict(zip(('action', 'actor', 'reason', 'at'), item, strict=True)) for item in history],
        )

    def mutate(self, mutation, generation, action, target, reason, items):
        with translate_sqlite_errors(), connect(self.path, write=True) as conn, transaction(conn):
            receipt = mutation.receipt(conn)
            if receipt is not None:
                return receipt
            control = read_control(conn)
            batch = action == 'approve_batch'
            if batch:
                conn.execute('SAVEPOINT cache_approvals')
            try:
                self.apply_action(conn, control, generation, action, target, f'admin:{mutation.actor}', reason, items)
            except StoreError as exc:
                if not batch:
                    raise
                conn.execute('ROLLBACK TO cache_approvals')
                status, response = exc.status, {'code': exc.code, 'detail': exc.detail}
                if isinstance(exc, ApprovalError):
                    response['answer_id'] = exc.answer_id
            else:
                status, response = 200, {'generation': read_control(conn).generation}
            if batch:
                conn.execute('RELEASE cache_approvals')
            mutation.save(conn, status, response, now())
            return status, response

    def apply_action(self, conn, control, generation, action, target, actor, reason, items):
        if generation != control.generation:
            raise StoreError('stale_generation', '캐시가 변경되었습니다. 새로고침 후 다시 확인하세요.', 409)
        if action == 'mode' and target == 'off':
            set_mode_in(conn, control, target, actor, reason, None)
            return
        if action in ('revoke_answer', 'revoke_query'):
            kind = action.removeprefix('revoke_')
            table = 'cache_answers' if kind == 'answer' else 'cache_queries'
            found = conn.execute(
                f'SELECT 1 FROM {table} WHERE {kind}_id=? AND namespace_id=?',
                (target, control.namespace_id or self.snapshot.namespace.id),
            ).fetchone()
            if found is None:
                raise StoreError('not_found', '철회 대상을 찾을 수 없습니다.', 404)
            revoke_in(conn, kind, target, actor, reason)
            return
        token = corpus_store.active_token(self.corpus_db, PROFILE)
        if disabled := self.disabled(control, token):
            raise StoreError('cache_unavailable', disabled, 409)
        protection, originals = verified_inputs(self.corpus_db, self.bundle)
        if protection is None or token is None:
            raise StoreError('cache_unavailable', '캐시 승인 범위를 확인할 수 없습니다.', 409)
        if action == 'mode':
            if protection.fingerprint != control.protection_hash:
                raise StoreError('protection_changed', PROTECTION_CHANGED, 409)
            try:
                set_mode_in(conn, control, target, actor, reason, control.approval_id)
            except ValueError as exc:
                raise StoreError('invalid_mode', str(exc), 409) from exc
        else:
            if control.protection_hash not in (None, protection.fingerprint):
                raise StoreError('protection_changed', PROTECTION_CHANGED, 409)
            self.approve(conn, control, token, protection, originals, items, actor, reason)
        if corpus_store.active_token(self.corpus_db, PROFILE) != token:
            raise StoreError('stale_release', '활성 릴리스가 변경되었습니다.', 409)

    def approve(self, conn, control, token, protection, originals, items, actor, reason):
        namespace = self.snapshot.namespace.id
        current = article_rows(corpus_store.release_by_id(self.corpus_db, PROFILE, token[0]))
        prepared = {}
        for target, preview_hash in items:
            try:
                if target in prepared:
                    raise StoreError('duplicate_answer', '같은 답변을 두 번 선택할 수 없습니다.', 422)
                prepared[target] = self.prepare_approval(
                    conn, control, protection, originals, current, target, preview_hash
                )
            except StoreError as exc:
                raise ApprovalError(target, exc) from exc

        approved = []
        at = now()
        for target, (draft, query_ids) in prepared.items():
            answer_id = insert_answer(conn, namespace, draft)
            changed = conn.execute(
                "UPDATE cache_answers SET status='approved',reviewed_by=?,reviewed_at=?,valid_from=coalesce(valid_from,?) "
                "WHERE answer_id=? AND status!='revoked'",
                (actor, at, at, answer_id),
            )
            if not changed.rowcount:
                error = StoreError('invalid_state', '철회한 답변은 재승인할 수 없습니다.', 409)
                raise ApprovalError(target, error)
            conn.executemany(
                "UPDATE cache_queries SET answer_id=?,status='approved',exact_enabled=1,reviewed_by=?,reviewed_at=?, "
                "valid_from=coalesce(valid_from,?) WHERE query_id=? AND status!='revoked'",
                ((answer_id, actor, at, at, query_id) for query_id in query_ids),
            )
            if answer_id != target:
                conn.execute(
                    'INSERT INTO cache_release_decisions SELECT activation_event,?,previous_answer_id,release_id,'
                    'previous_release_id,verdict,reason,changes FROM cache_release_decisions WHERE activation_event=? '
                    'AND answer_id=? ON CONFLICT DO NOTHING',
                    (answer_id, token[1], target),
                )
            approved.append((answer_id, query_ids))
        # Preserve the existing digest input when approving a single answer.
        members = list(approved[0]) if len(approved) == 1 else [approved]
        approval = digest(canonical([control.approval_id, token, control.generation, *members]))
        conn.execute(
            'UPDATE cache_control SET namespace_id=?,activation_event=?,protection_hash=?,approval_id=?, '
            'generation=generation+1 WHERE singleton=1',
            (namespace, token[1], protection.fingerprint, approval),
        )
        for answer_id, query_ids in approved:
            audit(conn, 'approve', answer_id, actor, reason, {'approval_id': approval, 'query_ids': query_ids})

    def prepare_approval(self, conn, control, protection, originals, current, target, preview_hash):
        key, raw_hash, _, source, status, stored, verdict, *_ = self.answer(conn, control, target)
        state = disposition(status, verdict)
        if state == 'revoked':
            raise StoreError('invalid_state', '철회한 답변은 승인할 수 없습니다.', 409)
        if state not in ('draft', 'pending'):
            raise StoreError('invalid_state', '검토 대기 또는 초안 답변만 승인할 수 있습니다.', 409)
        queries = query_rows(conn, target)
        query_ids = [q[0] for q in queries if reviewable_query(q, protection, control.mode, state)]
        if not query_ids:
            if queries and all(q[7] == 'revoked' for q in queries):
                raise StoreError('review_changed', '연결 질문이 모두 철회되었습니다.', 409)
            raise StoreError('review_changed', '보호 분할과 검토 조건을 통과한 연결 질문이 없습니다.', 409)
        raw = raw_for(raw_hash, stored, originals)
        shown, citations = preview(self.snapshot, raw, citations_for(conn, target))
        if shown is None or digest(shown) != preview_hash:
            raise StoreError('review_changed', '확인한 표시본이 현재 표시본과 다릅니다.', 409)
        lineage = json.loads(source)
        if any(key not in current or current[key][-1] != '0' for key in lineage.get('review_evidence_article_ids', [])):
            raise StoreError('review_changed', '검토 참고 조문을 현재 릴리스에서 확인할 수 없습니다.', 409)
        return Draft('', '', key, raw_hash, shown, citations, lineage, raw), query_ids


def article_view(row):
    return dict(zip(FIELDS, row, strict=True)) if row else None
