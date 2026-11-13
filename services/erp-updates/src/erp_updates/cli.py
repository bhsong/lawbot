"""erp-updates: collect from monitor clones, attach PDFs, review and export the public feed."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from erp_updates import feed, rules, sources, store

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
    attach.add_argument('--item', help='item ID when several ERP items list this URL')

    listing = commands.add_parser('list', help='latest version per item')
    listing.add_argument('--json', action='store_true')

    review = commands.add_parser('review', help='approve or reject the latest version')
    review.add_argument('decision', choices=['approve', 'reject'])
    review.add_argument('item')
    review.add_argument('--reviewer', required=True)
    review.add_argument('--reason')

    export = commands.add_parser('export', help='write the public feed atomically')
    export.add_argument('--out', type=Path, required=True)
    rule_review = commands.add_parser('review-rule', help='record a verified ERP rule or proposal')
    rule_review.add_argument('--file', type=Path, required=True)
    rule_review.add_argument('--reviewer', required=True)
    rule_export = commands.add_parser('export-rules', help='write the separate public ERP rule file')
    rule_export.add_argument('--out', type=Path, required=True)
    revisions = commands.add_parser('rule-sources', help='list exact source revisions for rule review')
    revisions.add_argument('source_key')
    history = commands.add_parser('rule-history', help='list private decisions for a rule edition')
    history.add_argument('version_id')
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
        found, errors = sources.collect(load_config(args.config), args.repos)
        created = store.save(conn, found)
        print(f'collected={len(found)} new_versions={created} errors={len(errors)}')
        for line in errors:
            print(f'erp-updates: {line}', file=sys.stderr)
        return 1 if errors else 0
    if args.command == 'attach':
        print(f'attached item={store.attach(conn, args.data, args.file, args.url, item_id=args.item)}')
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
    if args.command in ('review-rule', 'export-rules', 'rule-sources', 'rule-history'):
        return run_rules(args, conn)
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


def load_config(path: Path) -> list[sources.Source]:
    try:
        return sources.load_sources(path)
    except (OSError, ValueError) as exc:
        raise store.StoreError(f'Invalid ERP source configuration: {exc}') from exc


def run_rules(args: argparse.Namespace, conn) -> int:
    try:
        if args.command == 'rule-sources':
            rows = conn.execute(
                """SELECT v.id AS sourceRevisionId,i.source_key AS sourceKey,v.state,v.collected_at AS collectedAt,
                          v.content_sha256 AS contentSha256,json_extract(v.data_json,'$.title') AS title,
                          json_extract(v.data_json,'$.sourceUrl') AS sourceUrl
                   FROM versions v JOIN items i ON i.id=v.item_id WHERE i.source_key=? ORDER BY v.id DESC""",
                (args.source_key,),
            )
            print(json.dumps([dict(row) for row in rows], ensure_ascii=False))
        elif args.command == 'rule-history':
            print(json.dumps(rules.history(conn, args.version_id), ensure_ascii=False))
        elif args.command == 'review-rule':
            rules.record_review(conn, rules.parse(args.file), load_config(args.config), args.reviewer)
            print('ERP rule review recorded')
        else:
            rules.export(conn, load_config(args.config), args.out, args.data / 'erp.db')
            print('ERP rule feed exported')
    except ValidationError as exc:
        raise store.StoreError('Invalid ERP rule fields or official evidence') from exc
    except (OSError, ValueError) as exc:
        raise store.StoreError('Check the rule JSON and file paths') from exc
    return 0
