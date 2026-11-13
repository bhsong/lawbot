"""Record explicit ERP rule reviews and export a read-only public timeline."""

import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import Field

from erp_updates import feed, store
from erp_updates.sources import Source
from lawbot_common.erp_rules import (
    RULE_FEED_MAX_BYTES,
    Contract,
    ErpRuleProposal,
    ErpRuleVersion,
    Identifier,
    RuleScope,
    RuleSnapshot,
    official_link,
    target_date,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_editions (
    version_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_reviews (
    id INTEGER PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES rule_editions(version_id),
    decision TEXT NOT NULL CHECK(decision IN ('confirmed','proposed','recheck_required','withdrawn')),
    reviewed_at TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS rule_review_version ON rule_reviews(version_id, id);
"""


class Submission(Contract):
    sourceKey: Identifier
    scope: RuleScope
    version: ErpRuleVersion | ErpRuleProposal
    decision: Literal['confirmed', 'proposed', 'recheck_required', 'withdrawn']
    reason: str | None = Field(default=None, max_length=500)


def parse(path: Path) -> Submission:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Rule review must be a JSON object')
    scope = {field: data.pop(field) for field in RuleScope.model_fields if field in data}
    source, decision, reason = (data.pop(field, None) for field in ('sourceKey', 'decision', 'reason'))
    if data.get('evidenceKind') == 'proposal':
        for field in ('evidenceKind', 'effectiveFrom', 'appliesFrom', 'appliesUntil', 'sourceRevisionId', 'valueText'):
            if field != 'evidenceKind' and data.get(field) is not None:
                raise ValueError('Proposals may only publish an official link')
            data.pop(field, None)
    return Submission.model_validate(
        {'sourceKey': source, 'scope': scope, 'version': data, 'decision': decision, 'reason': reason}
    )


def editions(conn: sqlite3.Connection, source_key: str, scope_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT e.version_id,e.payload,r.decision,r.reviewed_at FROM rule_editions e
           JOIN rule_reviews r ON r.id=(SELECT MAX(id) FROM rule_reviews WHERE version_id=e.version_id)
           WHERE e.source_key=? AND e.scope_id=? ORDER BY e.version_id""",
        (source_key, scope_id),
    ).fetchall()


def check_confirmed(conn: sqlite3.Connection, review: Submission, rows: list[sqlite3.Row]) -> None:
    version = review.version
    assert isinstance(version, ErpRuleVersion)
    approved = conn.execute(
        """SELECT json_extract(v.data_json,'$.sourceUrl') FROM versions v JOIN items i ON i.id=v.item_id
           WHERE v.id=? AND i.source_key=? AND v.state='approved'""",
        (version.sourceRevisionId, review.sourceKey),
    ).fetchone()
    if not approved or not approved[0] or not official_link(approved[0]):
        raise store.StoreError('Confirm the exact approved source revision for this ERP item first')
    for day in (version.appliesFrom, version.appliesUntil):
        if day and target_date(day, review.scope.applicability) != day:
            raise store.StoreError('Application dates must start the selected month or year')
    active = []
    for row in rows:
        if row['version_id'] == version.ruleVersionId or row['decision'] in ('withdrawn', 'proposed'):
            continue
        payload = json.loads(row['payload'])
        if payload['scope'] != review.scope.model_dump():
            raise store.StoreError('Use a consistent scope and applicability for this timeline')
        active.append(ErpRuleVersion.model_validate(payload['version']))
    starts = {edition.appliesFrom: edition.ruleVersionId for edition in active}
    if version.appliesFrom in starts:
        raise store.StoreError('Withdraw or correct the conflicting edition before confirming this start date')
    earlier = [start for start in starts if start < version.appliesFrom]
    if version.predecessorId != (starts[max(earlier)] if earlier else None):
        raise store.StoreError('Specify the preceding edition in this application scope')
    if any(row['version_id'] == version.predecessorId and row['decision'] != 'confirmed' for row in rows):
        raise store.StoreError('Reconfirm the preceding edition before confirming its successor')
    if any(
        edition.appliesFrom > version.appliesFrom and edition.predecessorId == version.predecessorId
        for edition in active
    ):
        raise store.StoreError('Reconcile the later edition before inserting an earlier edition into its timeline')


def record_review(conn: sqlite3.Connection, review: Submission, sources: list[Source], reviewer: str) -> None:
    if review.sourceKey not in {source.key for source in sources} or not reviewer.strip():
        raise store.StoreError('Use a registered ERP source key and reviewer')
    proposal = isinstance(review.version, ErpRuleProposal)
    if (proposal and review.decision not in ('proposed', 'withdrawn')) or (
        not proposal and review.decision == 'proposed'
    ):
        raise store.StoreError('Proposal links and confirmed versions require distinct decisions')
    if review.decision == 'withdrawn' and not (review.reason or '').strip():
        raise store.StoreError('Provide a reason for withdrawal')
    payload = review.model_dump_json(exclude={'decision', 'reason'})
    version_id = review.version.ruleVersionId
    conn.executescript(SCHEMA)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        rows = editions(conn, review.sourceKey, review.scope.scopeId)
        prior = next((row for row in rows if row['version_id'] == version_id), None)
        if prior is None:
            if review.decision not in ('confirmed', 'proposed'):
                raise store.StoreError('Review an existing edition')
            if conn.execute('SELECT 1 FROM rule_editions WHERE version_id=?', (version_id,)).fetchone():
                raise store.StoreError('Use a unique ERP rule edition ID')
            conn.execute(
                'INSERT INTO rule_editions VALUES (?,?,?,?)',
                (version_id, review.sourceKey, review.scope.scopeId, payload),
            )
        elif prior['payload'] != payload or prior['decision'] == 'withdrawn':
            raise store.StoreError('An edition is immutable. Withdraw it and use a new edition ID for corrections')
        if review.decision == 'confirmed':
            check_confirmed(conn, review, rows)
        at = store.now()
        if prior and prior['decision'] == review.decision and prior['reviewed_at'] == at:
            return
        conn.execute(
            'INSERT INTO rule_reviews(version_id,decision,reviewed_at,reviewer,reason) VALUES (?,?,?,?,?)',
            (version_id, review.decision, at, reviewer.strip(), review.reason),
        )
        if review.decision in ('withdrawn', 'recheck_required'):
            # A changed predecessor makes each successor's reviewed comparison stale.
            conn.execute(
                """WITH RECURSIVE successors(version_id) AS (
                       SELECT ?
                       UNION
                       SELECT e.version_id FROM rule_editions e JOIN successors s
                       ON json_extract(e.payload,'$.version.predecessorId')=s.version_id
                   )
                   INSERT INTO rule_reviews(version_id,decision,reviewed_at,reviewer,reason)
                   SELECT s.version_id,'recheck_required',?,?,? FROM successors s
                   JOIN rule_reviews r ON r.id=(SELECT MAX(id) FROM rule_reviews WHERE version_id=s.version_id)
                   WHERE s.version_id!=? AND r.decision='confirmed'""",
                (version_id, at, reviewer.strip(), 'Predecessor needs review', version_id),
            )


def has_reviews(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_schema WHERE name='rule_editions'").fetchone() is not None


def history(conn: sqlite3.Connection, version_id: str) -> list[dict]:
    if not has_reviews(conn):
        return []
    rows = conn.execute(
        """SELECT e.payload,r.decision,r.reviewed_at,r.reviewer,r.reason FROM rule_editions e
           JOIN rule_reviews r USING(version_id) WHERE e.version_id=? ORDER BY r.id""",
        (version_id,),
    )
    return [{**json.loads(row['payload']), **{key: row[key] for key in row.keys() if key != 'payload'}} for row in rows]


def build(conn: sqlite3.Connection, sources: list[Source]) -> RuleSnapshot:
    # A plain export on an old DB has no rule reviews and preserves its legacy feed.
    results = {
        source.key: {
            'ruleId': source.key,
            'sourceKey': source.key,
            'title': source.title,
            'scopes': {},
            'proposalLinks': [],
        }
        for source in sources
    }
    if has_reviews(conn):
        with conn:
            conn.execute('BEGIN')
            records = conn.execute('SELECT * FROM rule_editions ORDER BY source_key,scope_id,version_id').fetchall()
            for record in records:
                item = results.get(record['source_key'])
                if item is None:
                    continue
                payload = json.loads(record['payload'])
                reviews = conn.execute(
                    'SELECT reviewed_at AS at,decision FROM rule_reviews WHERE version_id=? ORDER BY id',
                    (record['version_id'],),
                ).fetchall()
                if reviews[-1]['decision'] == 'withdrawn':
                    continue
                if reviews[-1]['decision'] == 'proposed':
                    item['proposalLinks'].append(payload['version'])
                else:
                    scope = item['scopes'].setdefault(record['scope_id'], payload['scope'] | {'editions': []})
                    scope['editions'].append({'version': payload['version'], 'reviews': [dict(row) for row in reviews]})
    for item in results.values():
        item['scopes'] = list(item['scopes'].values())
    return RuleSnapshot.model_validate(
        {'schemaVersion': 2, 'generatedAt': store.now(), 'rules': list(results.values())}
    )


def export(conn: sqlite3.Connection, sources: list[Source], out: Path, database: Path) -> None:
    if out.resolve() == database.resolve() or (out.exists() and out.samefile(database)):
        raise store.StoreError('The public rule file must be separate from the private ERP database')
    if out.exists():
        with out.open('rb') as stream:
            prior = stream.read(RULE_FEED_MAX_BYTES + 1)
        if len(prior) > RULE_FEED_MAX_BYTES:
            raise store.StoreError('Existing rule file exceeds the size limit')
        RuleSnapshot.model_validate_json(prior)
    snapshot = build(conn, sources).model_dump(mode='json')
    if len(json.dumps(snapshot, ensure_ascii=False, indent=2).encode()) + 1 > RULE_FEED_MAX_BYTES:
        raise store.StoreError('Public rule file exceeds the size limit')
    feed.write(snapshot, out)
