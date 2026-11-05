"""Local-only import and review CLI. No HTTP writes, secrets, collectors or LLM calls."""

import argparse
import json
import sqlite3
from pathlib import Path

from law_calendar.db import read_database
from law_calendar.ingest import import_files
from law_calendar.review import inspect_event, review, review_template


def main():
    parser = argparse.ArgumentParser(description='Offline calendar import and human review')
    parser.add_argument('--database', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    importer = sub.add_parser('import')
    importer.add_argument('--monitor-home', type=Path, required=True)
    importer.add_argument('files', type=Path, nargs='+', help='Explicit saved monitor event JSON files')
    sub.add_parser('list')
    sub.add_parser('runs')
    show = sub.add_parser('show')
    show.add_argument('event_id')
    show.add_argument('--template', action='store_true')
    reviewer = sub.add_parser('review')
    reviewer.add_argument('manifest', type=Path)
    reviewer.add_argument('--reviewer', required=True)
    args = parser.parse_args()
    failed = False
    try:
        if args.command == 'import':
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
