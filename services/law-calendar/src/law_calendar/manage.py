"""Local law import and review CLI."""

import argparse
import json
import sqlite3
from pathlib import Path

from law_calendar.db import read_database, write_database
from law_calendar.ingest import import_files, preview_changes, read_event
from law_calendar.review import inspect_event, review, review_template
from law_calendar.superseded import count_hidden_holds, find_superseded_changes, withdraw_superseded_changes


def main():
    parser = argparse.ArgumentParser(description='Local calendar law import and review')
    parser.add_argument('--database', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    importer = sub.add_parser('import')
    importer.add_argument('--monitor-home', type=Path, required=True)
    importer.add_argument('files', type=Path, nargs='+', help='Explicit saved monitor event JSON files')
    preview = sub.add_parser('preview', help='Preview automatic decisions without using a database')
    preview.add_argument('file', type=Path, help='Exported monitor event JSON')
    preview.add_argument('--monitor-home', type=Path, help='Base of saved source paths (default: event directory)')
    sub.add_parser('list')
    sub.add_parser('runs')
    supersede = sub.add_parser('supersede-stale', help='List changes outside the current source snapshot')
    supersede.add_argument('--apply', action='store_true', help='Withdraw the listed changes in one transaction')
    sub.add_parser('holds-check', help='Count pending changes missing from the current snapshot or holds list')
    show = sub.add_parser('show')
    show.add_argument('event_id')
    show.add_argument('--template', action='store_true')
    reviewer = sub.add_parser('review')
    reviewer.add_argument('manifest', type=Path)
    reviewer.add_argument('--reviewer', required=True)
    args = parser.parse_args()
    if args.command != 'preview' and args.database is None:
        parser.error('--database is required for this command')
    failed = False
    try:
        if args.command == 'preview':
            path = args.file.resolve()
            _, raw, texts = read_event(args.monitor_home or path.parent, path)
            result = preview_changes(json.loads(raw), texts)
        elif args.command == 'import':
            result = import_files(args.database, args.monitor_home, args.files)
            failed = result['failed'] > 0
        elif args.command in ('list', 'runs'):
            with read_database(args.database) as conn:
                query = (
                    'SELECT e.id,e.status,e.review_state,s.snapshot_id '
                    'FROM change_events e JOIN event_sources s ON s.event_id=e.id ORDER BY e.id'
                    if args.command == 'list'
                    else 'SELECT * FROM import_runs ORDER BY started_at DESC LIMIT 50'
                )
                result = [dict(r) for r in conn.execute(query)]
        elif args.command == 'supersede-stale':
            open_database = write_database if args.apply else read_database
            with open_database(args.database) as conn:
                result = find_superseded_changes(conn)
                if args.apply:
                    withdraw_superseded_changes(conn, result)
        elif args.command == 'holds-check':
            with read_database(args.database) as conn:
                result = count_hidden_holds(conn)
            failed = any(result.values())
        elif args.command == 'show':
            result = inspect_event(args.database, args.event_id)
            if args.template:
                result = review_template(result)
        else:
            review(args.database, json.loads(args.manifest.read_text(encoding='utf-8')), reviewer=args.reviewer)
            result = {'decision': 'recorded'}
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
