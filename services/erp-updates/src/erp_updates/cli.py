"""erp-updates: collect from monitor clones, attach PDFs, review and export the public feed."""

import argparse
import json
import sys
from pathlib import Path

from erp_updates import feed, sources, store

SERVICE = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='erp-updates', description=__doc__)
    p.add_argument('--data', type=Path, default=SERVICE / 'data', help='ERP-only data folder (SQLite and PDFs)')
    p.add_argument('--config', type=Path, default=SERVICE / 'config/sources.yaml')
    commands = p.add_subparsers(dest='command', required=True)

    collect = commands.add_parser('collect', help='read monitor clones (read-only, no network)')
    collect.add_argument('--repos', type=Path, required=True, help='folder holding admrule-kr and legalize-kr')

    attach = commands.add_parser('attach', help='register a downloaded official PDF')
    attach.add_argument('--file', type=Path, required=True)
    attach.add_argument('--url', required=True, help='PDF link listed by the source front matter')

    listing = commands.add_parser('list', help='latest version per item')
    listing.add_argument('--json', action='store_true')

    review = commands.add_parser('review', help='approve or reject the latest version')
    review.add_argument('decision', choices=['approve', 'reject'])
    review.add_argument('item')
    review.add_argument('--reviewer', required=True)
    review.add_argument('--reason')

    export = commands.add_parser('export', help='write the public feed atomically')
    export.add_argument('--out', type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    conn = store.connect(args.data)
    try:
        return run(args, conn)
    except store.StoreError as exc:
        print(f'erp-updates: {exc}', file=sys.stderr)
        return 1
    finally:
        conn.close()


def run(args: argparse.Namespace, conn) -> int:
    if args.command == 'collect':
        found, errors = sources.collect(sources.load_sources(args.config), args.repos)
        created = store.save(conn, found)
        print(f'collected={len(found)} new_versions={created} errors={len(errors)}')
        for line in errors:
            print(f'erp-updates: {line}', file=sys.stderr)
        return 1 if errors else 0
    if args.command == 'attach':
        print(f'attached item={store.attach(conn, args.data, args.file, args.url)}')
        return 0
    if args.command == 'review':
        decision = 'approved' if args.decision == 'approve' else 'rejected'
        store.review(conn, args.item, decision, args.reviewer, args.reason)
        print(f'{decision} item={args.item}')
        return 0
    if args.command == 'export':
        result = feed.build(conn)
        feed.write(result, args.out)
        print(f'exported items={len(result["items"])}')
        return 0
    summary = [
        {
            'id': row['id'],
            'sourceKey': row['data']['sourceKey'],
            'title': row['data']['title'],
            'state': row['version']['state'],
            'versions': row['versions'],
            'collectedAt': row['version']['collected_at'],
            'files': len(row['files']),
        }
        for row in store.rows(conn)
    ]
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        for row in summary:
            print('\t'.join(str(row[key]) for key in ('id', 'sourceKey', 'state', 'versions', 'files', 'title')))
    return 0
