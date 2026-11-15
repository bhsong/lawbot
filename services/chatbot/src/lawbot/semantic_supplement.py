"""Approve reviewed search questions in a development cache without creating answer bindings."""

import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from lawbot.cache_seed import Protection
from lawbot.cache_store import audit, now, read_control
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import connect, transaction
from lawbot.exact_cache import Snapshot, canonical, digest, normalize
from lawbot.qa_rules import norm
from lawbot.semantic_index_store import SUPPLEMENT_EXPRESSION

ACTION = 'semantic_supplement_import'
REASON = '서브에이전트 검토 후 사용자 승인 2026-11-15'
SHA256 = '2d8e847cdcdc7b3528837d0bc283679ae17330e7e39a80f06aeead8b9d5fcd7e'
ACTOR = 'import'


@dataclass(frozen=True)
class Supplement:
    id: str
    article: str
    checksum: str
    question: str


def read_supplement(path: Path) -> list[Supplement]:
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SHA256:
        raise ValueError('승인한 보충 질문 파일의 SHA-256과 다릅니다.')
    rows = []
    try:
        for line in raw.splitlines():
            row = json.loads(line)
            rows.append(
                Supplement(*(row[key] for key in ('supplement_id', 'article_id', 'article_sha256', 'question')))
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('보충 질문 파일의 구조가 올바르지 않습니다.') from exc
    if any(not all(isinstance(value, str) and value.strip() for value in vars(row).values()) for row in rows):
        raise ValueError('보충 질문 파일에 빈 값 또는 잘못된 값이 있습니다.')
    if len({row.id for row in rows}) != len(rows):
        raise ValueError('보충 질문 ID가 중복됩니다.')
    return rows


def import_supplement(
    db,
    rows,
    checksum,
    snapshot: Snapshot,
    event,
    protection: Protection,
    *,
    version_hashes,
    check_snapshot=lambda: None,
):
    ns = snapshot.namespace.id
    receipt = digest(canonical([ns, snapshot.release_id, event, checksum]))
    with connect(db, write=True) as conn, transaction(conn):
        check_snapshot()
        control = read_control(conn)
        if (control.namespace_id, control.activation_event, control.protection_hash) != (
            ns,
            event,
            protection.fingerprint,
        ):
            raise ValueError('현재 코퍼스에서 통과 seed와 동일한 보호 지문이 필요합니다.')
        prior = conn.execute(
            'SELECT detail FROM cache_audit_events WHERE action=? AND actor=? AND target=?',
            (ACTION, ACTOR, receipt),
        ).fetchone()
        if prior is not None:
            return json.loads(prior[0])['counts']

        existing = {
            norm(normalize(question))
            for (question,) in conn.execute(
                'SELECT q.question FROM atomic_questions q JOIN atoms a USING (atom_revision_id) '
                "WHERE a.namespace_id=? AND a.corpus_snapshot_id=? AND q.status='approved'",
                (ns, snapshot.release_id),
            )
        }
        counts: Counter[str] = Counter(
            dict.fromkeys(
                (
                    'approved_questions',
                    'approved_articles',
                    'hash_mismatch',
                    'hash_missing',
                    'excluded_calculation',
                    'protected_exact',
                    'protected_near',
                    'duplicate',
                ),
                0,
            )
        )
        counts['input_rows'] = len(rows)
        articles = set()
        at = now()
        for row in sorted(rows, key=lambda item: item.id):
            parent = snapshot.articles.get(row.article)
            key = norm(normalize(row.question))
            if parent is None or row.article not in version_hashes or parent.kind != '법률':
                counts['hash_missing'] += 1
            elif version_hashes[row.article] != row.checksum:
                counts['hash_mismatch'] += 1
            elif is_calculation_question(row.question):
                counts['excluded_calculation'] += 1
            elif match := protection.match(row.question):
                counts[f'protected_{match}'] += 1
            elif not key or key in existing:
                counts['duplicate'] += 1
            else:
                # With no evidence quote the reviewed handoff rule uses the full article.
                group = digest(canonical([row.article, checksum]))
                atom = digest(canonical([ns, group, parent.text_hash, 0, digest(parent.text)]))
                conn.execute(
                    "INSERT INTO atoms VALUES (?,?,?,?,?,?,?,0,?,'approved',?,?,?,?) ON CONFLICT DO NOTHING",
                    (
                        atom,
                        group,
                        ns,
                        snapshot.release_id,
                        row.article,
                        parent.text_hash,
                        parent.text,
                        len(parent.text),
                        at,
                        ACTOR,
                        at,
                        ACTOR,
                    ),
                )
                query = f'supplement:{row.id}'
                text_hash = digest(row.question)
                revision = digest(canonical([atom, query, text_hash]))
                conn.execute(
                    "INSERT INTO atomic_questions VALUES (?,?,?,?,?,?,'approved',?,?,?,?)",
                    (revision, query, atom, row.question, text_hash, SUPPLEMENT_EXPRESSION, at, ACTOR, at, ACTOR),
                )
                articles.add(row.article)
                existing.add(key)
                counts['approved_questions'] += 1
        counts['approved_articles'] = len(articles)
        check_snapshot()
        audit(
            conn,
            ACTION,
            receipt,
            ACTOR,
            REASON,
            {
                'source_sha256': checksum,
                'snapshot': snapshot.release_id,
                'protection_hash': protection.fingerprint,
                'counts': dict(counts),
            },
        )
    return dict(counts)
