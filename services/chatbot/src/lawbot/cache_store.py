"""Read and mutate the separate SQLite catalog for reviewed exact answers."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from lawbot.db import migrate as migrations
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import Namespace, canonical, digest, normalize

MIGRATIONS = files('lawbot.db') / 'cache_migrations'
SCHEMA = 1
READ_TIMEOUT = 0.01


class CacheDatabaseError(RuntimeError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@contextmanager
def translate_sqlite_errors():
    """Expose storage failures without importing SQLite in the web or CLI layer."""
    try:
        yield
    except sqlite3.Error as exc:
        code = getattr(exc, 'sqlite_errorcode', 0) & 0xFF
        reason = 'db_timeout' if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else 'db_error'
        raise CacheDatabaseError(reason, str(exc)) from exc


def init(path: Path) -> int:
    return migrations.init(path, directory=MIGRATIONS)


def migrate(path: Path) -> int:
    return migrations.migrate(path, directory=MIGRATIONS)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec='microseconds')


def audit(conn, action, target, actor, reason, detail=None):
    conn.execute(
        'INSERT INTO cache_audit_events (action,target,actor,reason,created_at,detail) VALUES (?,?,?,?,?,?)',
        (action, target, actor, reason, now(), canonical(detail)),
    )


@dataclass(frozen=True)
class Draft:
    source_id: str
    question: str
    answer_key: str
    raw_sha256: str
    answer_text: str
    citations: list[tuple[str, str]]
    source: dict

    def query_id(self, namespace_id: str) -> str:
        return digest(canonical([namespace_id, digest(normalize(self.question))]))

    def answer_id(self, namespace_id: str) -> str:
        return revision_id(namespace_id, self.answer_key, self.raw_sha256, digest(self.answer_text), self.citations)


def revision_id(namespace_id, answer_key, raw_sha256, text_sha256, citations):
    return digest(canonical([namespace_id, answer_key, raw_sha256, text_sha256, citations]))


def insert_answer(conn, namespace_id: str, draft: Draft) -> str:
    answer_id = draft.answer_id(namespace_id)
    result = conn.execute(
        'INSERT INTO cache_answers '
        '(answer_id,namespace_id,answer_key,raw_sha256,answer_text,text_sha256,source,citation_count) '
        'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
        (
            answer_id,
            namespace_id,
            draft.answer_key,
            draft.raw_sha256,
            draft.answer_text,
            digest(draft.answer_text),
            canonical(draft.source),
            len(draft.citations),
        ),
    )
    if result.rowcount:
        conn.executemany(
            'INSERT INTO cache_citations VALUES (?,?,?,?)',
            ((answer_id, pos, *pair) for pos, pair in enumerate(draft.citations)),
        )
    return answer_id


@dataclass(frozen=True)
class Control:
    mode: str
    generation: int
    namespace_id: str | None
    activation_event: int | None
    protection_hash: str | None
    approval_id: str | None


def read_control(conn) -> Control:
    row = conn.execute('SELECT max(version) FROM schema_migrations').fetchone()
    if row != (SCHEMA,):
        raise sqlite3.DatabaseError('unsupported cache schema')
    row = conn.execute(
        'SELECT mode,generation,namespace_id,activation_event,protection_hash,approval_id '
        'FROM cache_control WHERE singleton=1'
    ).fetchone()
    if row is None:
        raise sqlite3.DatabaseError('missing cache control')
    return Control(*row)


@dataclass(frozen=True)
class CachedAnswer:
    answer_id: str
    text: str
    citations: list[tuple[str, str]]
    control: Control


class Store:
    def __init__(self, path: Path):
        self.path = path

    def control(self) -> Control:
        with connect(self.path, timeout=READ_TIMEOUT) as conn:
            return read_control(conn)

    def check_schema(self) -> int:
        return migrations.check_schema(self.path, directory=MIGRATIONS)

    def lookup(self, namespace_id: str, question: str) -> tuple[Control, CachedAnswer | None, str]:
        with connect(self.path, timeout=READ_TIMEOUT) as conn:
            conn.execute('BEGIN')
            control = read_control(conn)
            if control.mode == 'off':
                return control, None, 'off'
            if control.namespace_id != namespace_id:
                return control, None, 'namespace_mismatch'
            row = conn.execute(
                'SELECT q.normalized,q.status,q.exact_enabled,q.valid_from,q.valid_until,q.expires_at, '
                'a.status,a.valid_from,a.valid_until,a.expires_at, '
                'a.answer_id,a.answer_key,a.raw_sha256,a.answer_text,a.text_sha256,a.citation_count '
                'FROM cache_queries q JOIN cache_answers a USING (namespace_id,answer_id) '
                'WHERE q.namespace_id=? AND q.normalized_sha256=?',
                (namespace_id, digest(question)),
            ).fetchone()
            if row is None or row[0] != question:
                return control, None, 'no_match'
            if row[1:3] != ('approved', 1) or row[6] != 'approved':
                return control, None, 'inactive'
            current = now()
            if not all(valid(current, *bounds) for bounds in (row[3:6], row[7:10])):
                return control, None, 'expired'
            answer_id, key, raw_hash, text, text_hash, count = row[10:]
            citations = conn.execute(
                'SELECT position,article_id,text_sha256 FROM cache_citations WHERE answer_id=? ORDER BY position',
                (answer_id,),
            ).fetchall()
        pairs = [(article, checksum) for _, article, checksum in citations]
        if [pos for pos, _, _ in citations] != list(range(count)) or not pairs:
            return control, None, 'citation_mismatch'
        if digest(text) != text_hash or revision_id(namespace_id, key, raw_hash, text_hash, pairs) != answer_id:
            return control, None, 'revision_mismatch'
        return control, CachedAnswer(answer_id, text, pairs, control), 'hit'

    def stage(self, namespace: Namespace, drafts: list[Draft], actor: str, reason: str) -> int:
        namespace_id = namespace.id
        inserted = 0
        with connect(self.path, write=True) as conn, transaction(conn):
            read_control(conn)
            conn.execute(
                'INSERT INTO cache_namespaces VALUES (?,?,?) ON CONFLICT DO NOTHING',
                (namespace_id, namespace.serialize(), now()),
            )
            for draft in drafts:
                question = normalize(draft.question)
                if not question or not draft.answer_text.strip() or not draft.citations:
                    raise ValueError('빈 질문, 답변 또는 근거는 등록할 수 없습니다.')
                answer_id = draft.answer_id(namespace_id)
                query_id = draft.query_id(namespace_id)
                existing = conn.execute(
                    'SELECT normalized,answer_id,source_id FROM cache_queries WHERE namespace_id=? AND normalized_sha256=?',
                    (namespace_id, digest(question)),
                ).fetchone()
                if existing is not None:
                    normalized, current, source_id = existing
                    if (normalized, source_id) == (question, draft.source_id):
                        if (
                            current == answer_id
                            or conn.execute(
                                "SELECT 1 FROM cache_audit_events WHERE action='stage' AND target=? "
                                "AND json_extract(detail,'$.answer_id')=?",
                                (query_id, answer_id),
                            ).fetchone()
                        ):
                            continue
                    raise ValueError('질문 키 충돌로 이번 등록을 보류했습니다.')
                insert_answer(conn, namespace_id, draft)
                conn.execute(
                    'INSERT INTO cache_queries '
                    '(query_id,namespace_id,answer_id,source_id,original,raw_sha256,normalized,normalized_sha256,source) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    (
                        query_id,
                        namespace_id,
                        answer_id,
                        draft.source_id,
                        draft.question,
                        digest(draft.question),
                        question,
                        digest(question),
                        canonical(draft.source),
                    ),
                )
                inserted += 1
                audit(conn, 'stage', query_id, actor, reason, {'answer_id': answer_id, 'source_id': draft.source_id})
        return inserted

    def approve(
        self,
        namespace_id: str,
        activation_event: int,
        protection_hash: str,
        selection: dict[str, str],
        *,
        actor: str,
        reason: str,
        valid_from: str | None = None,
        valid_until: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        if not selection:
            raise ValueError('승인할 질문과 답변 리비전을 지정하세요.')
        at = now()
        bounds = (valid_from or at, valid_until, expires_at)
        if any(end is not None and end <= bounds[0] for end in bounds[1:]):
            raise ValueError('유효 종료와 만료는 시작보다 늦어야 합니다.')
        with connect(self.path, write=True) as conn, transaction(conn):
            control = read_control(conn)
            if control.mode != 'off':
                raise ValueError('승인 묶음을 바꾸기 전에 캐시를 off로 전환하세요.')
            approval = digest(
                canonical([namespace_id, activation_event, protection_hash, selection, bounds, control.generation])
            )
            conn.execute(
                'UPDATE cache_queries SET exact_enabled=0 WHERE namespace_id=? AND exact_enabled=1', (namespace_id,)
            )
            for query_id, answer_id in selection.items():
                result = conn.execute(
                    "UPDATE cache_queries SET status='approved',exact_enabled=1,reviewed_by=?,reviewed_at=?, "
                    'valid_from=?,valid_until=?,expires_at=? WHERE query_id=? AND namespace_id=? AND answer_id=? '
                    "AND status IN ('draft','approved')",
                    (actor, at, *bounds, query_id, namespace_id, answer_id),
                )
                if result.rowcount != 1:
                    raise ValueError('승인할 질문이 없거나 리비전이 변경·철회되었습니다.')
                result = conn.execute(
                    "UPDATE cache_answers SET status='approved',reviewed_by=?,reviewed_at=?, "
                    'valid_from=?,valid_until=?,expires_at=? WHERE answer_id=? AND namespace_id=? '
                    "AND status IN ('draft','approved')",
                    (actor, at, *bounds, answer_id, namespace_id),
                )
                if result.rowcount != 1:
                    raise ValueError('승인할 답변이 없거나 철회되었습니다.')
            conn.execute(
                'UPDATE cache_control SET namespace_id=?,activation_event=?,protection_hash=?,approval_id=?, '
                'generation=generation+1 WHERE singleton=1',
                (namespace_id, activation_event, protection_hash, approval),
            )
            audit(
                conn,
                'approve',
                approval,
                actor,
                reason,
                {
                    'selection': selection,
                    'bounds': bounds,
                    'namespace_id': namespace_id,
                    'activation_event': activation_event,
                    'protection_hash': protection_hash,
                },
            )
        return approval

    def replace_answer(self, expected: Control, draft: Draft, previous: str, actor: str, reason: str) -> str:
        """Approve one corrected revision and move only the explicitly reviewed question."""
        namespace_id = expected.namespace_id
        if expected.mode != 'off' or namespace_id is None or expected.approval_id is None:
            raise ValueError('기존 승인 묶음을 off로 전환한 후 답변을 교체하세요.')
        query_id = draft.query_id(namespace_id)
        answer_id = draft.answer_id(namespace_id)
        if answer_id == previous or not draft.answer_text.strip() or not draft.citations:
            raise ValueError('검토한 새 답변 리비전과 근거가 필요합니다.')
        at = now()
        with connect(self.path, write=True) as conn, transaction(conn):
            if read_control(conn) != expected:
                raise ValueError('캐시 상태가 바뀌었습니다. 최신 승인 묶음으로 다시 검토하세요.')
            row = conn.execute(
                'SELECT normalized,source_id,answer_id FROM cache_queries '
                'WHERE query_id=? AND namespace_id=? AND reviewed_at IS NOT NULL',
                (query_id, namespace_id),
            ).fetchone()
            if row != (normalize(draft.question), draft.source_id, previous):
                raise ValueError('기존 승인 질문 또는 교체할 답변 리비전이 일치하지 않습니다.')
            insert_answer(conn, namespace_id, draft)
            result = conn.execute(
                "UPDATE cache_answers SET status='approved',reviewed_by=?,reviewed_at=?, "
                "valid_from=COALESCE(valid_from,?) WHERE answer_id=? AND status IN ('draft','approved')",
                (actor, at, at, answer_id),
            )
            if result.rowcount != 1:
                raise ValueError('철회된 답변 리비전은 다시 사용할 수 없습니다.')
            conn.execute(
                "UPDATE cache_queries SET answer_id=?,status='approved',exact_enabled=1,reviewed_by=?,reviewed_at=? "
                'WHERE query_id=?',
                (answer_id, actor, at, query_id),
            )
            approval = digest(canonical([expected.approval_id, expected.generation, query_id, previous, answer_id]))
            conn.execute(
                'UPDATE cache_control SET approval_id=?,generation=generation+1 WHERE singleton=1', (approval,)
            )
            audit(
                conn,
                'replace',
                query_id,
                actor,
                reason,
                {
                    'previous_answer_id': previous,
                    'answer_id': answer_id,
                    'previous_approval_id': expected.approval_id,
                    'approval_id': approval,
                    'correction_review_sha256': draft.source.get('correction_review_sha256'),
                },
            )
        return approval

    def set_mode(self, mode: str, actor: str, reason: str, approval: str | None = None) -> None:
        with connect(self.path, write=True) as conn, transaction(conn):
            control = read_control(conn)
            if mode not in ('off', 'shadow', 'on'):
                raise ValueError('지원하지 않는 캐시 모드입니다.')
            if mode != 'off' and (approval is None or approval != control.approval_id):
                raise ValueError('현재 승인 묶음을 지정하세요.')
            if mode == 'on' and control.mode not in ('shadow', 'on'):
                raise ValueError('shadow 검증 후 on으로 전환하세요.')
            conn.execute('UPDATE cache_control SET mode=?,generation=generation+1 WHERE singleton=1', (mode,))
            audit(conn, 'mode', mode, actor, reason, {'previous': control.mode, 'approval': approval})

    def revoke(self, kind: str, target: str, actor: str, reason: str) -> None:
        with connect(self.path, write=True) as conn, transaction(conn):
            read_control(conn)
            if kind == 'answer':
                result = conn.execute("UPDATE cache_answers SET status='revoked' WHERE answer_id=?", (target,))
                conn.execute('UPDATE cache_queries SET exact_enabled=0 WHERE answer_id=?', (target,))
            elif kind == 'query':
                result = conn.execute(
                    "UPDATE cache_queries SET status='revoked',exact_enabled=0 WHERE query_id=?", (target,)
                )
            else:
                raise ValueError('철회 대상은 query 또는 answer여야 합니다.')
            if result.rowcount != 1:
                raise ValueError('철회 대상을 찾을 수 없습니다.')
            conn.execute('UPDATE cache_control SET generation=generation+1 WHERE singleton=1')
            audit(conn, f'revoke_{kind}', target, actor, reason)


def valid(current, start, end, expires) -> bool:
    return (
        start is not None
        and start <= current
        and (end is None or current < end)
        and (expires is None or current < expires)
    )
