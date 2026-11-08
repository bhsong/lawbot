import argparse
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from lawbot import envfile
from lawbot.db import backup, migrate
from lawbot.db.connection import DEFAULT_DB, connect, repo_path
from lawbot.db.corpus_import import release_bytes
from lawbot.db.csv_import import sha256


def _export_release(db: Path, release_id: str, out: Path, overwrite: bool) -> None:
    migrate.check_schema(db)
    with connect(db) as conn:
        conn.execute('BEGIN')
        files = release_bytes(conn, release_id)

    changes = []
    for name, raw in files.items():
        if Path(name).name != name or name in ('', '.', '..'):
            raise ValueError('조문 릴리스에 잘못된 파일 이름이 있습니다.')
        target = out / name
        if target.is_symlink():
            raise ValueError(f'출력 파일이 심볼릭 링크입니다: {name}')
        old = target.read_bytes() if target.exists() else None
        if old == raw:
            continue
        if old is not None and not overwrite:
            raise FileExistsError(f'기존 파일 내용이 다릅니다: {name}. 새 폴더 또는 --overwrite를 지정하세요.')
        changes.append((target, old, raw))

    if not changes:
        print(f'릴리스 {release_id} · 같음')
        return
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out, prefix='.lawbot-export-') as staged:
        for target, _, raw in changes:
            (Path(staged) / target.name).write_bytes(raw)
        for target, old, raw in changes:
            source = Path(staged) / target.name
            if overwrite:
                os.replace(source, target)
            else:
                os.link(source, target)
            before = sha256(old)[:12] if old is not None else '없음'
            print(f'{target.name} · sha256 {before} → {sha256(raw)[:12]}')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Lawbot 데이터 DB 관리', epilog='상대 경로는 레포 최상위 기준입니다.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'migrate', 'status', 'backup', 'export-release'):
        command = commands.add_parser(name)
        command.add_argument('--db', help='DB 경로 (기본값은 LAWBOT_DB 또는 data/db/lawbot.db)')
        if name in ('backup', 'export-release'):
            command.add_argument('--out', required=True)
        if name == 'export-release':
            command.add_argument('--release', required=True)
            command.add_argument('--overwrite', action='store_true', help='내용이 다른 기존 파일을 교체')
    restore = commands.add_parser('restore')
    restore.add_argument('--from', dest='source', required=True)
    restore.add_argument('--to', required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == 'restore':
            target = repo_path(args.to)
            backup.restore(repo_path(args.source), target)
            print(f'복원 DB {target.resolve()}')
            return

        if args.db is None:
            envfile.load()
        value = args.db if args.db is not None else os.environ.get('LAWBOT_DB', '').strip()
        path = repo_path(value) if value else DEFAULT_DB
        if args.command == 'backup':
            out = repo_path(args.out)
            backup.backup(path, out)
            print(f'백업 DB {out.resolve()}')
        elif args.command == 'export-release':
            _export_release(path, args.release, repo_path(args.out), args.overwrite)
        else:
            action = {'init': migrate.init, 'migrate': migrate.migrate, 'status': migrate.check_schema}[args.command]
            print(f'스키마 버전 {action(path)}')
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f'lawbot-db: {exc}\n')


if __name__ == '__main__':
    main()
