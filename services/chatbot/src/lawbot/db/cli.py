import argparse
import os
import sqlite3
from pathlib import Path

from lawbot import envfile
from lawbot.db import backup, migrate
from lawbot.db.connection import DEFAULT_DB, db_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Lawbot 데이터 DB 관리')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'migrate', 'status', 'backup'):
        command = commands.add_parser(name)
        command.add_argument('--db', help='DB 경로 (기본값은 LAWBOT_DB 또는 data/db/lawbot.db)')
        if name == 'backup':
            command.add_argument('--out', required=True, type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('--from', dest='source', required=True, type=Path)
    restore.add_argument('--to', required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == 'restore':
            backup.restore(args.source, args.to)
            print(f'복원 DB {args.to.expanduser().resolve()}')
            return

        if args.db is None:
            envfile.load()
        value = args.db if args.db is not None else os.environ.get('LAWBOT_DB', '').strip()
        path = db_path(value) if value else DEFAULT_DB
        if args.command == 'backup':
            backup.backup(path, args.out)
            print(f'백업 DB {args.out.expanduser().resolve()}')
        else:
            action = {'init': migrate.init, 'migrate': migrate.migrate, 'status': migrate.check_schema}[args.command]
            print(f'스키마 버전 {action(path)}')
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f'lawbot-db: {exc}\n')


if __name__ == '__main__':
    main()
