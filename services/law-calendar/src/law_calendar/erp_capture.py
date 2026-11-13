"""Offline bridge that captures the same validated payloads as the public API."""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import get_args

from law_calendar import news
from law_calendar.db import read_database
from law_calendar.erp_rules import from_snapshot
from law_calendar.server import erp_updates
from lawbot_common.erp_daily import LIMIT, replace_file
from lawbot_common.erp_news import SNAPSHOT_MAX_BYTES, Agency, NewsSnapshot, official_url
from lawbot_common.erp_rules import RULE_FEED_MAX_BYTES, RuleSnapshot


def approved_law_changes(
    database: Path, summary_path: Path, run_id: str, cutoff: datetime, enabled_law_names: list[str]
) -> list[dict]:
    """Bind the morning reply's inputs to public rows from the exact imported revision."""
    try:
        with summary_path.open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            return []
        summary = json.loads(raw)
        if summary['runId'] != run_id or not isinstance(summary['entries'], list):
            return []
        event_ids = {e['eventId'] for row in summary['entries'] for e in row['events'] if isinstance(e['eventId'], str)}
        if not event_ids:
            return []
        with read_database(database) as conn:
            rows = conn.execute(
                """SELECT e.upstream_id,e.event_id,s.raw_event,p.law_name,p.source_url,p.promulgated_on,
                          p.summary,p.comparable,p.effective_on,
                          (SELECT COUNT(*) FROM article_changes WHERE event_id=e.event_id) AS expected
                   FROM event_sources e JOIN source_snapshots s ON s.id=e.snapshot_id
                   JOIN public_changes p ON p.event_id=e.event_id
                   JOIN change_versions v ON v.change_id=p.id AND v.snapshot_id=s.id
                   WHERE e.source='legalize_law' AND e.upstream_id IN (SELECT value FROM json_each(?))
                   AND p.law_name IN (SELECT value FROM json_each(?))
                   AND julianday(p.verified_at)<=julianday(?) AND julianday(p.event_verified_at)<=julianday(?)
                   ORDER BY e.event_id,p.id""",
                (json.dumps(sorted(event_ids)), json.dumps(enabled_law_names), cutoff.isoformat(), cutoff.isoformat()),
            ).fetchall()
    except OSError, ValueError, sqlite3.Error, KeyError, TypeError:
        return []
    changes: dict[str, dict] = {}
    for row in rows:
        if not official_url(row['source_url'], 'lawgo'):
            continue
        change = changes.setdefault(
            row['event_id'],
            {
                'id': 'law:' + row['event_id'],
                'kind': 'law',
                'change': 'new',
                'title': row['law_name'],
                'url': row['source_url'],
                'publishedOn': row['promulgated_on'],
                'eventId': row['upstream_id'],
                'revision': hashlib.sha256(row['raw_event'].encode()).hexdigest(),
                'expected': row['expected'],
                'publicSummaries': [],
                'dates': [],
            },
        )
        change['publicSummaries'].append(row['summary'] if row['comparable'] else None)
        change['dates'].append(row['effective_on'])
    return list(changes.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'erp', 'rules', 'news', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cutoff', required=True, type=datetime.fromisoformat)
    parser.add_argument('--without-law-news', action='store_true')
    parser.add_argument('--morning-summary', type=Path)
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if args.cutoff.utcoffset() is None:
        parser.error('Use a timezone-aware snapshot cutoff')
    with args.rules.open('rb') as stream:
        data = stream.read(RULE_FEED_MAX_BYTES + 1)
    if len(data) > RULE_FEED_MAX_BYTES:
        parser.error('ERP rule snapshot exceeds the size limit')
    rule_snapshot = RuleSnapshot.model_validate_json(data)
    rules = rule_snapshot.model_dump(mode='json')
    with args.news.open('rb') as stream:
        data = stream.read(SNAPSHOT_MAX_BYTES + 1)
    if len(data) > SNAPSHOT_MAX_BYTES:
        parser.error('ERP news snapshot exceeds the size limit')
    news_snapshot = NewsSnapshot.model_validate_json(data)
    payload = {
        'erp': erp_updates(args.erp),
        'rules': rules,
        'resolvedRules': from_snapshot(rule_snapshot, args.cutoff.date(), args.cutoff.date()).model_dump(mode='json')[
            'rules'
        ],
        'news': {
            agency: news.from_snapshot(
                args.database, news_snapshot, agency, 10, now=args.cutoff, use_law_feed=not args.without_law_news
            )
            for agency in get_args(Agency)
        },
    }
    if args.morning_summary and args.run_id and not args.without_law_news:
        payload['lawChanges'] = approved_law_changes(
            args.database, args.morning_summary, args.run_id, args.cutoff, news_snapshot.lawNames
        )
    if payload['erp']['state'] != 'ok' or any(
        entry['state'] not in ('ok', 'link_only', 'unavailable') for entry in payload['news'].values()
    ):
        parser.error('ERP public input is unavailable')
    replace_file(args.out, payload)


if __name__ == '__main__':
    main()
