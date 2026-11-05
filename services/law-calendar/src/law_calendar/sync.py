"""Import committed monitor law events as a post-collection check in the daily job."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from law_calendar.assembly_ingest import ASSEMBLY_TYPES, import_assembly
from law_calendar.db import write_database
from law_calendar.ingest import ARTICLE, Diff, import_files, inside
from law_calendar.validation import timestamp

LAW_SOURCE = 'legalize_law'
SOURCE_TYPES = {LAW_SOURCE: ('law_new', 'law_amended', 'law_repealed'), **ASSEMBLY_TYPES}


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
            'SELECT event_id,source,change_type,json_path FROM events WHERE source IN (?,?,?) ORDER BY created_at,rowid',
            tuple(SOURCE_TYPES),
        ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {source: [] for source in SOURCE_TYPES}
    for row in rows:
        if row['change_type'] in SOURCE_TYPES[row['source']]:
            grouped[row['source']].append(row)
    return run, collectors, grouped


def source_snapshot(
    home: Path, source: str, run: sqlite3.Row | None, collectors: dict, rows: list[sqlite3.Row]
) -> tuple[str, list[tuple[Path, dict]], int]:
    if not run or not run['finished_at'] or run['status'] not in ('ok', 'partial'):
        raise ValueError(f'No completed successful {source} collection is available')
    if collectors.get(source, {}).get('status') != 'ok':
        raise ValueError(f'The latest {source} collector did not succeed')
    collected_at = timestamp(run['finished_at'])
    events = []
    skipped = 0
    for row in rows:
        if not row['json_path']:
            raise ValueError('Registered event has no saved JSON path')
        path = inside(home, home / row['json_path'])
        event = json.loads(path.read_text(encoding='utf-8'))
        if (
            not isinstance(event, dict)
            or event.get('event_id') != row['event_id']
            or event.get('source') != row['source']
            or event.get('change_type') != row['change_type']
        ):
            raise ValueError('Saved event does not match its monitor registration')
        if event.get('replay'):
            raise ValueError('Replay output cannot be used as a scheduled collection result')
        if source == LAW_SOURCE:
            diff = Diff.model_validate(event.get('diff'))
            if not any(ARTICLE.fullmatch('##### ' + chunk.article) for chunk in diff.chunks):
                skipped += 1
                continue
        events.append((path, event))
    return collected_at, events, skipped


def _synchronize_laws(
    database: Path, monitor_home: Path, snapshot: tuple[str, list[tuple[Path, dict]], int]
) -> dict[str, int]:
    collected_at, events, skipped = snapshot
    result = import_files(database, monitor_home, [path for path, _ in events])
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
    run, collectors, rows = registrations(monitor_home)
    assembly = [source for source in ASSEMBLY_TYPES if source in collectors]
    result = {'imported': 0, 'unchanged': 0, 'failed': 0, 'skipped': 0}
    try:
        snapshot = source_snapshot(monitor_home, LAW_SOURCE, run, collectors, rows[LAW_SOURCE])
        result = _synchronize_laws(database, monitor_home, snapshot)
    except (OSError, ValueError, sqlite3.Error) as exc:
        if not assembly:
            raise
        result['failed'] = 1
        print(f'calendar sync {LAW_SOURCE} failed: {type(exc).__name__}', file=sys.stderr)

    for source in assembly:
        try:
            stamp, events, _ = source_snapshot(monitor_home, source, run, collectors, rows[source])
            imported, unchanged = import_assembly(database, source, stamp, [event for _, event in events])
        except (OSError, ValueError, sqlite3.Error) as exc:
            result[f'{source}_failed'] = 1
            result['failed'] += 1
            print(f'calendar sync {source} failed: {type(exc).__name__}', file=sys.stderr)
        else:
            result.update({f'{source}_imported': imported, f'{source}_unchanged': unchanged, f'{source}_failed': 0})
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
        print(f'calendar sync failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    print('calendar sync ' + ' '.join(f'{key}={value}' for key, value in result.items()))
    return 2 if result['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
