"""Read the reviewed semantic catalog and fence lookups against its active release."""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lawbot.cache_store import READ_TIMEOUT, Control, now, read_control, translate_sqlite_errors, valid, verify_answer
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect
from lawbot.exact_cache import Snapshot, canonical, digest
from lawbot.semantic_gate import Candidate, Parent

ELIGIBILITY_COLUMNS = """
q.query_revision_id, q.status AS question_status, a.status AS atom_status,
       b.activation_event, b.semantic_enabled, b.carry_status, b.reviewed_at,
       b.valid_from AS binding_start, b.valid_until AS binding_end,
       r.status AS answer_status, r.valid_from, r.valid_until, r.expires_at, p.policy_id AS existing_policy,
       b.candidate_query_id, src.status AS source_status, src.exact_enabled AS source_enabled
"""
DETAIL_COLUMNS = """
q.question,q.question_sha256,a.atom_revision_id,a.parent_chunk_id,a.parent_sha256,a.source_end,
b.binding_id,b.policy_id,b.policy_version,r.answer_id,p.conditions
"""
CATALOG_SQL = """
FROM atomic_questions q JOIN atoms a USING (atom_revision_id)
LEFT JOIN atom_answer_bindings b USING (query_revision_id, atom_revision_id, namespace_id)
LEFT JOIN cache_answers r ON r.answer_id=b.answer_id AND r.namespace_id=b.namespace_id
LEFT JOIN semantic_reuse_policies p ON p.policy_id=b.policy_id AND p.version=b.policy_version
LEFT JOIN cache_queries src ON src.query_id=b.candidate_query_id AND src.namespace_id=b.namespace_id
WHERE a.namespace_id=? AND a.corpus_snapshot_id=?
"""
INDEXED_QUERIES = ' AND q.query_revision_id IN (SELECT value FROM json_each(?))'


class ShadowMiss(RuntimeError):
    """A controlled early exit with a reason suitable for the request log."""


def manifest_row(conn, revision):
    return conn.execute(
        'SELECT namespace_id,corpus_snapshot_id,embedding_fingerprint,query_revisions,row_count,dimension,vectors_sha256 '
        'FROM semantic_index_manifests WHERE index_revision=?',
        (revision,),
    ).fetchone()


def binding_reason(row, event, current):
    if 'revoked' in (row['atom_status'], row['question_status'], row['answer_status'], row['source_status']):
        return 'revoked'
    if (row['atom_status'], row['question_status'], row['answer_status']) != ('approved', 'approved', 'approved'):
        return 'not_approved'
    if row['activation_event'] != event:
        return 'not_released'
    if row['candidate_query_id'] is not None and row['source_enabled'] != 1:
        return 'semantic_disabled'
    if not row['semantic_enabled'] or row['carry_status'] != 'keep' or row['existing_policy'] is None:
        return 'semantic_disabled'
    if not valid(current, row['valid_from'], row['valid_until'], row['expires_at']) or not valid(
        current, row['binding_start'] or row['reviewed_at'], row['binding_end'], None
    ):
        return 'expired'
    return None


@dataclass(frozen=True)
class SemanticStore:
    path: Path
    snapshot: Snapshot
    index_revision: str

    def manifest(self) -> tuple | None:
        with translate_sqlite_errors(), connect(self.path, timeout=READ_TIMEOUT) as conn:
            return manifest_row(conn, self.index_revision)

    def eligible(self, manifest: tuple, query_ids: tuple[str, ...], event: int) -> tuple[Control, set[str]]:
        namespace = self.snapshot.namespace.id
        allowed, seen, rejected = set(), set(), set()
        with translate_sqlite_errors(), connect(self.path, timeout=READ_TIMEOUT) as conn:
            conn.execute('BEGIN')
            control = self.check_catalog(conn, namespace, event, manifest)
            conn.row_factory = sqlite3.Row
            current = now()
            for row in conn.execute(
                f'SELECT {ELIGIBILITY_COLUMNS} {CATALOG_SQL} {INDEXED_QUERIES}',
                (namespace, self.snapshot.release_id, manifest[3]),
            ):
                key = row['query_revision_id']
                seen.add(key)
                if reason := binding_reason(row, event, current):
                    rejected.add(reason)
                else:
                    allowed.add(key)
        if seen != set(query_ids):
            raise ShadowMiss('stale_index')
        if not allowed:
            reason = next(
                (
                    code
                    for code in ('revoked', 'not_released', 'semantic_disabled', 'expired', 'not_approved')
                    if code in rejected
                ),
                'no_match',
            )
            raise ShadowMiss(reason)
        return control, allowed

    def candidates(self, manifest: tuple, query_ids: list[str], event: int, before: Control) -> list[Candidate]:
        namespace = self.snapshot.namespace.id
        with translate_sqlite_errors(), connect(self.path, timeout=READ_TIMEOUT) as conn:
            conn.execute('BEGIN')
            if self.check_catalog(conn, namespace, event, manifest) != before:
                raise ShadowMiss('state_changed')
            return self.read_candidates(conn, namespace, event, query_ids)

    def recheck(self, manifest: tuple, event: int, before: Control, selected: Candidate) -> str:
        namespace = self.snapshot.namespace.id
        with translate_sqlite_errors(), connect(self.path, timeout=READ_TIMEOUT) as conn:
            conn.execute('BEGIN')
            if self.check_catalog(conn, namespace, event, manifest) != before:
                return 'state_changed'
            return self.verify_selection(conn, namespace, event, selected)

    def check_catalog(self, conn, ns, event, manifest):
        control = read_control(conn)
        if (control.namespace_id, control.activation_event) != (ns, event) or control.approval_id is None:
            raise ShadowMiss('not_released')
        if manifest_row(conn, self.index_revision) != manifest:
            raise ShadowMiss('stale_index')
        return control

    def read_candidates(self, conn, ns, event, query_ids):
        conn.row_factory = sqlite3.Row
        accepted: dict[str, list[Candidate]] = defaultdict(list)
        parents: dict[str, Parent] = {}
        current = now()
        for row in conn.execute(
            f'SELECT {ELIGIBILITY_COLUMNS}, {DETAIL_COLUMNS} {CATALOG_SQL} {INDEXED_QUERIES} ORDER BY b.binding_id',
            (ns, self.snapshot.release_id, canonical(query_ids)),
        ):
            key = row['query_revision_id']
            if binding_reason(row, event, current) is not None:
                continue
            question = row['question']
            if digest(question) != row['question_sha256'] or is_calculation_question(question):
                raise ShadowMiss('stale_index')
            parent_id = row['parent_chunk_id']
            article = self.snapshot.articles.get(parent_id)
            if article is None or article.text_hash != row['parent_sha256'] or row['source_end'] > len(article.text):
                raise ShadowMiss('corpus_mismatch')
            if parent_id not in parents:
                parents[parent_id] = Parent(
                    self.snapshot.release_id, parent_id, article.text_hash, article.hit()['doc']
                )
            accepted[key].append(
                Candidate(
                    row['binding_id'],
                    row['atom_revision_id'],
                    key,
                    question,
                    parents[parent_id],
                    row['answer_id'],
                    row['policy_id'],
                    row['policy_version'],
                    row['conditions'],
                )
            )
        if accepted.keys() != set(query_ids):
            raise ShadowMiss('state_changed')
        return [candidate for key in query_ids for candidate in accepted[key]]

    def verify_selection(self, conn, ns, event, selected):
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f'SELECT {ELIGIBILITY_COLUMNS} {CATALOG_SQL} AND b.binding_id=?',
            (ns, self.snapshot.release_id, selected.binding_id),
        ).fetchone()
        if row is None:
            return 'not_released'
        if reason := binding_reason(row, event, now()):
            return reason
        answer = conn.execute(
            'SELECT answer_id,answer_key,raw_sha256,answer_text,text_sha256,citation_count FROM cache_answers '
            'WHERE answer_id=? AND namespace_id=?',
            (selected.answer_id, ns),
        ).fetchone()
        citations, reason = verify_answer(conn, ns, answer)
        if citations is None:
            return reason
        if self.snapshot.resolve(citations) is None:
            return 'citation_missing'
        for article_id, checksum in conn.execute(
            'SELECT article_id,text_sha256 FROM semantic_answer_evidence WHERE answer_id=?',
            (selected.answer_id,),
        ):
            if self.snapshot.resolve([(article_id, checksum)]) is None:
                return 'citation_missing'
        return 'hit'
