"""Manage collection settings and run a reviewed source on demand."""

import argparse
import getpass
import json
import os
import sqlite3
import sys
from pathlib import Path

import uvicorn
import yaml
from pydantic import ValidationError

from source_hub import daily, materials, news
from source_hub.config import CONFIG_PATH, load
from source_hub.server import create_app
from source_hub.store import Registry

TARGETS = ('group', 'agency', 'source', 'law', 'law-part')
COLLECTORS = daily.COLLECTORS


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description='Lawbot 수집 설정')
    root.add_argument('--config', type=Path, default=CONFIG_PATH)
    root.add_argument('--database', type=Path, required=True)
    commands = root.add_subparsers(dest='command', required=True)
    for command in ('validate', 'list', 'dry-run', 'audit'):
        commands.add_parser(command)
    commands.add_parser('runs').add_argument('source_id')
    review = commands.add_parser('review-news')
    review.add_argument('source_id', choices=COLLECTORS)
    review.add_argument('upstream_id')
    review.add_argument('revision', type=int)
    review.add_argument('decision', choices=('approve', 'withdraw'))
    review.add_argument('--materials', type=Path, required=True)
    review.add_argument('--reason', default='', help='철회할 때 필수')
    export = commands.add_parser('export-news')
    export.add_argument('--materials', type=Path, required=True)
    export.add_argument('--out', type=Path, required=True)
    run_day = commands.add_parser('daily', help='run one KST collection day and publish its ERP snapshot')
    for name in ('root', 'monitor-home', 'erp-data', 'calendar-db', 'materials', 'out'):
        run_day.add_argument('--' + name, type=Path, required=True)
    run_day.add_argument('--uv', default='uv')
    run_day.add_argument('--erp-config', type=Path)
    run_day.add_argument('--monitor-config', type=Path)
    run_day.add_argument(
        '--republish', action='store_true', help='refresh today’s approved publication without collecting'
    )
    for command in ('collect', 'documents'):
        rss = commands.add_parser(command)
        rss.add_argument('source_id', choices=COLLECTORS)
        rss.add_argument('--materials', type=Path, required=True)
        if command == 'collect':
            rss.add_argument('--reconcile', action='store_true')
    for command in ('set', 'add'):
        edit = commands.add_parser(command)
        if command == 'set':
            edit.add_argument('kind', choices=TARGETS)
        edit.add_argument('target')
        edit.add_argument('changes', help='JSON object')
        edit.add_argument('--version', type=int, required=True)
        edit.add_argument('--reason', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, required=True)
    serve.add_argument('--materials', type=Path)
    serve.add_argument('--news-feed', type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    status = 0
    try:
        registry = Registry(load(args.config), args.database)
        if args.command == 'serve':
            user = os.environ.get('SOURCE_HUB_ADMIN_USER', '')
            password = os.environ.get('SOURCE_HUB_ADMIN_PASSWORD', '')
            app = create_app(registry, user, password, materials_db=args.materials, news_feed=args.news_feed)
            uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)
            return 0
        if args.command in ('set', 'add'):
            create = args.command == 'add'
            changes = json.loads(args.changes)
            if not isinstance(changes, dict):
                raise ValueError('변경값은 JSON 객체여야 합니다')
            result = {
                'config_version': registry.patch(
                    'source' if create else args.kind,
                    args.target,
                    changes,
                    actor=getpass.getuser(),
                    reason=args.reason,
                    version=args.version,
                    create=create,
                )
            }
        elif args.command == 'audit':
            result = registry.history()
        elif args.command == 'runs':
            result = registry.runs(args.source_id)
        elif args.command == 'collect':
            result = COLLECTORS[args.source_id](registry, args.materials, args.source_id, reconcile=args.reconcile)
            if result['outcome'] in {'blocked', 'error', 'unverified', 'busy', 'partial'}:
                status = 2
        elif args.command == 'documents':
            result = materials.documents(args.materials, args.source_id)
        elif args.command == 'review-news':
            news.review(
                args.materials,
                args.source_id,
                args.upstream_id,
                args.revision,
                args.decision,
                getpass.getuser(),
                args.reason,
            )
            result = {'decision': args.decision, 'revision': args.revision}
        elif args.command == 'export-news':
            snapshot = news.export(registry, args.materials, args.out)
            result = {'published': sum(len(agency['items']) for agency in snapshot['agencies'].values())}
        elif args.command == 'daily':
            result = daily.run(
                registry,
                daily.DailyConfig(
                    root=args.root.resolve(),
                    monitor_home=args.monitor_home.resolve(),
                    erp_data=args.erp_data.resolve(),
                    calendar_db=args.calendar_db.resolve(),
                    materials_db=args.materials.resolve(),
                    public=args.out.absolute(),
                    uv=args.uv,
                    erp_config=args.erp_config.resolve() if args.erp_config else None,
                    monitor_config=args.monitor_config.resolve() if args.monitor_config else None,
                ),
                republish=args.republish,
            )
            status = 0 if result['status'] == 'ok' else 2
        else:
            snapshot = registry.snapshot()
            result = (
                {'valid': True, 'config_version': snapshot['config_version']}
                if args.command == 'validate'
                else snapshot
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValidationError, yaml.YAMLError:
        print('source-hub: 등록부 설정 값과 공식 URL을 확인하세요', file=sys.stderr)
        return 2
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'source-hub: {exc}', file=sys.stderr)
        return 2
    return status


if __name__ == '__main__':
    sys.exit(main())
