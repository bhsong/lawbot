"""Local import, review and official notice content CLI."""

import argparse
import json
import sqlite3
from pathlib import Path

from law_calendar.assembly_ingest import inspect_assembly, list_assembly, review_assembly
from law_calendar.db import read_database
from law_calendar.ingest import import_files
from law_calendar.notice_content import MAX_NOTICES, refresh_notice_content
from law_calendar.review import inspect_event, review, review_template


def main():
    parser = argparse.ArgumentParser(description='Local calendar import, review and notice content')
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
    sub.add_parser('assembly-list')
    assembly_show = sub.add_parser('assembly-show')
    assembly_show.add_argument('snapshot_id')
    assembly_review = sub.add_parser('assembly-review')
    assembly_review.add_argument('snapshot_id')
    assembly_review.add_argument('decision', choices=['approve', 'reject', 'withdraw'])
    assembly_review.add_argument('--reviewer', required=True)
    assembly_review.add_argument('--note', required=True)
    notice_content = sub.add_parser('notice-content', help='Collect official content for approved notices')
    notice_content.add_argument('--limit', type=int, default=MAX_NOTICES)
    notice_content.add_argument('--refresh', action='store_true')
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
        elif args.command == 'assembly-list':
            result = list_assembly(args.database)
        elif args.command == 'assembly-show':
            result = inspect_assembly(args.database, args.snapshot_id)
        elif args.command == 'assembly-review':
            review_assembly(args.database, args.snapshot_id, args.decision, reviewer=args.reviewer, note=args.note)
            result = {'decision': 'recorded'}
        elif args.command == 'notice-content':
            result = refresh_notice_content(args.database, limit=args.limit, refresh=args.refresh)
            failed = bool(result['failed'] or result['pdfErrors'])
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
