"""Import committed monitor law events as a post-collection check in the daily job."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from law_calendar.db import write_database
from law_calendar.ingest import ARTICLE, Diff, import_files, inside
from law_calendar.validation import timestamp


def source_snapshot(home: Path) -> tuple[str, list[Path], int]:
    """Read a consistent monitor DB snapshot; model processed_at is not a calendar cursor."""
    uri = (home / 'data/state.db').resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        run = conn.execute(
            "SELECT * FROM runs WHERE mode='normal' ORDER BY started_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        if not run or not run['finished_at'] or run['status'] not in ('ok', 'partial'):
            raise ValueError('No completed successful law collection is available')
        collectors = json.loads(run['summary_json'] or '{}').get('collectors', {})
        if collectors.get('legalize_law', {}).get('status') != 'ok':
            raise ValueError('The latest law collector did not succeed')
        collected_at = timestamp(run['finished_at'])
        rows = conn.execute(
            "SELECT event_id,source,json_path FROM events WHERE source='legalize_law' "
            "AND change_type IN ('law_new','law_amended','law_repealed') ORDER BY created_at,rowid"
        ).fetchall()
    files = []
    skipped = 0
    for row in rows:
        if not row['json_path']:
            raise ValueError('Registered law event has no saved JSON path')
        path = inside(home, home / row['json_path'])
        event = json.loads(path.read_text(encoding='utf-8'))
        if (
            not isinstance(event, dict)
            or event.get('event_id') != row['event_id']
            or event.get('source') != row['source']
        ):
            raise ValueError('Saved law event does not match its monitor registration')
        if event.get('replay'):
            raise ValueError('Replay output cannot be used as a scheduled collection result')
        diff = Diff.model_validate(event.get('diff'))
        if not any(ARTICLE.fullmatch('##### ' + chunk.article) for chunk in diff.chunks):
            skipped += 1
            continue
        files.append(path)
    return collected_at, files, skipped


def synchronize(database: Path, monitor_home: Path) -> dict[str, int]:
    collected_at, files, skipped = source_snapshot(monitor_home)
    result = import_files(database, monitor_home, files)
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
