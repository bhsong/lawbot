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
from law_calendar.notice_content import synchronize_notice_content
from law_calendar.validation import timestamp
from lawbot_common.law_scope import LawScope, load_law_scope, normalize_law_text, scope_keywords

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
    home: Path,
    source: str,
    run: sqlite3.Row | None,
    collectors: dict,
    rows: list[sqlite3.Row],
    scope: tuple[LawScope, ...],
) -> tuple[str, list[tuple[Path, dict]], int]:
    if not run or not run['finished_at'] or run['status'] not in ('ok', 'partial'):
        raise ValueError(f'No completed successful {source} collection is available')
    if collectors.get(source, {}).get('status') != 'ok':
        raise ValueError(f'The latest {source} collector did not succeed')
    collected_at = timestamp(run['finished_at'])
    folders = {law.folder for law in scope}
    law_titles = {normalize_law_text(law.name + kind) for law in scope for kind in ('', ' 시행령', ' 시행규칙')}
    keywords = tuple(map(normalize_law_text, scope_keywords(scope)))
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
        target = event.get('target')
        if not isinstance(target, dict) or not isinstance(title := target.get('law_name'), str) or not title.strip():
            raise ValueError('Registered event has no law name')
        normalized_title = normalize_law_text(title)
        if source == LAW_SOURCE:
            item_id = target.get('item_id', '')
            parts = item_id.split('/') if isinstance(item_id, str) else []
            in_scope = parts[1] in folders if len(parts) == 3 and parts[0] == 'kr' else normalized_title in law_titles
            if not in_scope:
                skipped += 1
                continue
            diff = Diff.model_validate(event.get('diff'))
            if not any(ARTICLE.fullmatch('##### ' + chunk.article) for chunk in diff.chunks):
                skipped += 1
                continue
        elif not any(word in normalized_title for word in keywords):
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
    scope = load_law_scope()
    run, collectors, rows = registrations(monitor_home)
    assembly = [source for source in ASSEMBLY_TYPES if source in collectors]
    result = {'imported': 0, 'unchanged': 0, 'failed': 0, 'skipped': 0}
    try:
        snapshot = source_snapshot(monitor_home, LAW_SOURCE, run, collectors, rows[LAW_SOURCE], scope)
        result = _synchronize_laws(database, monitor_home, snapshot)
    except (OSError, ValueError, sqlite3.Error) as exc:
        if not assembly:
            raise
        result['failed'] = 1
        print(f'calendar sync {LAW_SOURCE} failed: {type(exc).__name__}', file=sys.stderr)

    for source in assembly:
        try:
            stamp, events, _ = source_snapshot(monitor_home, source, run, collectors, rows[source], scope)
            imported, unchanged = import_assembly(database, source, stamp, [event for _, event in events])
        except (OSError, ValueError, sqlite3.Error) as exc:
            result[f'{source}_failed'] = 1
            result['failed'] += 1
            print(f'calendar sync {source} failed: {type(exc).__name__}', file=sys.stderr)
        else:
            result.update({f'{source}_imported': imported, f'{source}_unchanged': unchanged, f'{source}_failed': 0})
    if result.get('assembly_notice_failed') == 0:
        try:
            result.update(synchronize_notice_content(database))
        except (OSError, ValueError, sqlite3.Error) as exc:
            result['failed'] += 1
            print(f'calendar sync notice content failed: {type(exc).__name__}', file=sys.stderr)
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
    if result['failed']:
        return 2
    return int(
        any(
            result.get(key)
            for key in (
                'notice_pending',
                'notice_pdf_pending',
                'notice_content_errors',
                'notice_migration_required',
            )
        )
    )


if __name__ == '__main__':
    sys.exit(main())
