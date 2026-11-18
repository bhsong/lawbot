"""Import committed monitor law events as a post-collection check in the daily job."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from law_calendar.db import read_database, write_database
from law_calendar.import_failures import ImportInputError, error_message, record_import_failure
from law_calendar.ingest import ARTICLE, Diff, import_files, inside
from law_calendar.validation import timestamp
from lawbot_common.law_scope import LawScope, load_law_scope, normalize_law_text

LAW_SOURCE = 'legalize_law'
SOURCE_TYPES = {LAW_SOURCE: ('law_new', 'law_amended', 'law_repealed')}


def registrations(home: Path) -> tuple[sqlite3.Row | None, dict, dict[str, list[sqlite3.Row]]]:
    """Read a consistent monitor DB snapshot; model processed_at is not a calendar cursor."""
    uri = (home / 'data/state.db').resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        run = conn.execute(
            "SELECT * FROM runs WHERE mode='normal' ORDER BY started_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        collectors = json.loads(run['summary_json'] or '{}').get('collectors', {}) if run else {}
        rows = conn.execute(
            'SELECT event_id,source,change_type,json_path FROM events WHERE source=? ORDER BY created_at,rowid',
            (LAW_SOURCE,),
        ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {source: [] for source in SOURCE_TYPES}
    for row in rows:
        if row['change_type'] in SOURCE_TYPES[row['source']]:
            grouped[row['source']].append(row)
    return run, collectors, grouped


def _recorded_ids(database: Path, source: str) -> set[str]:
    """Read upstream law IDs already committed by the importer."""
    with read_database(database) as conn:
        return {row[0] for row in conn.execute('SELECT upstream_id FROM event_sources WHERE source=?', (source,))}


def source_snapshot(
    database: Path,
    home: Path,
    source: str,
    run: sqlite3.Row | None,
    collectors: dict,
    rows: list[sqlite3.Row],
    scope: tuple[LawScope, ...],
) -> tuple[str, list[tuple[Path, dict]], int, int]:
    event_id = None
    try:
        if not run or not run['finished_at'] or run['status'] not in ('ok', 'partial'):
            raise ImportInputError(f'No completed successful {source} collection is available')
        if collectors.get(source, {}).get('status') != 'ok':
            raise ImportInputError(f'The latest {source} collector did not succeed')
        collected_at = timestamp(run['finished_at'])
        recorded = _recorded_ids(database, source)
        folders = {law.folder for law in scope}
        law_titles = {normalize_law_text(law.name + kind) for law in scope for kind in ('', ' 시행령', ' 시행규칙')}
        events = []
        skipped = unchanged = 0
        for row in rows:
            event_id = row['event_id']
            if event_id in recorded:
                unchanged += 1
                continue
            path, event = _read_registration(home, row)
            title = normalize_law_text(event['target']['law_name'])
            item_id = event['target'].get('item_id', '')
            parts = item_id.split('/') if isinstance(item_id, str) else []
            in_scope = parts[1] in folders if len(parts) == 3 and parts[0] == 'kr' else title in law_titles
            if not in_scope:
                skipped += 1
                continue
            diff = Diff.model_validate(event.get('diff'))
            if not any(ARTICLE.fullmatch('##### ' + chunk.article) for chunk in diff.chunks):
                skipped += 1
                continue
            events.append((path, event))
    except (OSError, ValueError) as exc:
        record_import_failure(database, source, event_id, exc)
        raise
    return collected_at, events, skipped, unchanged


def _read_registration(home: Path, row: sqlite3.Row) -> tuple[Path, dict]:
    if not row['json_path']:
        raise ImportInputError('Registered event has no saved JSON path')
    path = inside(home, home / row['json_path'])
    event = json.loads(path.read_text(encoding='utf-8'))
    if (
        not isinstance(event, dict)
        or event.get('event_id') != row['event_id']
        or event.get('source') != row['source']
        or event.get('change_type') != row['change_type']
    ):
        raise ImportInputError('Saved event does not match its monitor registration')
    if event.get('replay'):
        raise ImportInputError('Replay output cannot be used as a scheduled collection result')
    target = event.get('target')
    if not isinstance(target, dict) or not isinstance(title := target.get('law_name'), str) or not title.strip():
        raise ImportInputError('Registered event has no law name')
    return path, event


def _synchronize_laws(
    database: Path, monitor_home: Path, snapshot: tuple[str, list[tuple[Path, dict]], int, int]
) -> dict[str, int]:
    collected_at, events, skipped, unchanged = snapshot
    event_ids = {path: event['event_id'] for path, event in events}
    result = import_files(database, monitor_home, list(event_ids), event_ids=event_ids)
    result['unchanged'] += unchanged
    result['skipped'] = skipped
    if not result['failed']:
        with write_database(database) as conn:
            previous = conn.execute('SELECT collected_at FROM collection_status WHERE id=1').fetchone()
            if (
                not previous
                or not previous['collected_at']
                or datetime.fromisoformat(collected_at) > datetime.fromisoformat(previous['collected_at'])
            ):
                conn.execute(
                    'INSERT INTO collection_status(id,collected_at) VALUES (1,?) '
                    'ON CONFLICT(id) DO UPDATE SET collected_at=excluded.collected_at',
                    (collected_at,),
                )
    return result


def synchronize(database: Path, monitor_home: Path) -> dict[str, int]:
    scope = load_law_scope()
    run, collectors, rows = registrations(monitor_home)
    result = {'imported': 0, 'unchanged': 0, 'failed': 0, 'skipped': 0}
    managed = run is not None and json.loads(run['summary_json'] or '{}').get('managed') is True
    if not managed or LAW_SOURCE in collectors:
        snapshot = source_snapshot(database, monitor_home, LAW_SOURCE, run, collectors, rows[LAW_SOURCE], scope)
        result = _synchronize_laws(database, monitor_home, snapshot)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Scheduled calendar import after the monitor collector')
    parser.add_argument('--monitor-home', type=Path, required=True)
    parser.add_argument('--database', type=Path, default=os.environ.get('LAW_CALENDAR_DB'))
    args = parser.parse_args()
    if args.database is None:
        parser.error('An explicit --database or LAW_CALENDAR_DB is required')
    try:
        result = synchronize(args.database, args.monitor_home)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'calendar sync failed: {type(exc).__name__}: {error_message(exc)}', file=sys.stderr)
        return 2
    print('calendar sync ' + ' '.join(f'{key}={value}' for key, value in result.items()))
    if result['failed']:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
