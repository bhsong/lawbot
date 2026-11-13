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

from source_hub import moel
from source_hub.config import CONFIG_PATH, load
from source_hub.server import create_app
from source_hub.store import Registry

TARGETS = ('group', 'agency', 'source', 'law', 'law-part')


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description='Lawbot 수집 설정')
    root.add_argument('--config', type=Path, default=CONFIG_PATH)
    root.add_argument('--database', type=Path, required=True)
    commands = root.add_subparsers(dest='command', required=True)
    for command in ('validate', 'list', 'dry-run', 'audit'):
        commands.add_parser(command)
    commands.add_parser('runs').add_argument('source_id')
    for command in ('collect', 'documents'):
        rss = commands.add_parser(command)
        rss.add_argument('source_id', choices=moel.CHANNELS)
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
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    status = 0
    try:
        registry = Registry(load(args.config), args.database)
        if args.command == 'serve':
            user = os.environ.get('SOURCE_HUB_ADMIN_USER', '')
            password = os.environ.get('SOURCE_HUB_ADMIN_PASSWORD', '')
            uvicorn.run(create_app(registry, user, password), host='127.0.0.1', port=args.port, access_log=False)
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
            result = moel.collect(registry, args.materials, args.source_id, reconcile=args.reconcile)
            if result['outcome'] in {'blocked', 'error', 'unverified', 'busy', 'partial'}:
                status = 2
        elif args.command == 'documents':
            result = moel.documents(args.materials, args.source_id)
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
