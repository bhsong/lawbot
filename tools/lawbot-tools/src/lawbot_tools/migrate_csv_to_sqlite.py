"""Import or verify the existing CSV and feedback files in a Lawbot database."""

import argparse
from pathlib import Path

from lawbot.db.connection import check_db_path, repo_path
from lawbot.db.corpus_import import prepare_corpus, write_corpus
from lawbot.db.dataset_import import DATASETS, prepare_dataset, write_dataset
from lawbot.db.feedback_import import prepare_feedback, write_feedback
from lawbot.db.importers import DatabaseError, apply_import, verify_imports
from lawbot_tools.feedback_review import files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='CSV·피드백 JSONL을 Lawbot DB로 이관하거나 대조합니다.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('corpus', 'qa', 'feedback', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('--db', required=True)
        command.add_argument('--report')
        command.add_argument('--dry-run', action='store_true')
        if name == 'corpus':
            command.add_argument('--dir', required=True)
            command.add_argument('--profile', required=True, choices=('v4', 'v5'))
            command.add_argument('--release-id', required=True)
            command.add_argument('--activate', action='store_true')
        elif name == 'qa':
            command.add_argument('--set', dest='dataset', required=True, choices=DATASETS)
            command.add_argument('--file')
        elif name == 'feedback':
            command.add_argument('paths', nargs='+')
    return parser


def _import(args, db: Path) -> tuple[int, list[str]]:
    lines = []
    status = 0
    if args.command == 'corpus':
        corpus = prepare_corpus(repo_path(args.dir), args.profile, args.release_id)
        count = corpus.row_count
        lines.extend(
            f'{part.csv.path.name} · {len(part.csv.rows)}행 · sha256 {part.csv.checksum}'
            for part in (corpus.law, corpus.combined)
        )
        write = lambda conn: write_corpus(conn, corpus, args.activate)
    elif args.command == 'qa':
        dataset = prepare_dataset(args.dataset, repo_path(args.file) if args.file else None)
        count = dataset.row_count
        lines.extend(
            f'{file.path.name} · {len(file.rows)}행 · sha256 {file.checksum}' for file in dataset.files().values()
        )
        write = lambda conn: write_dataset(conn, dataset)
    else:
        feedback = prepare_feedback(files([repo_path(path) for path in args.paths]))
        count = feedback.row_count
        status = int(bool(feedback.malformed or feedback.invalid))
        lines.extend(
            (
                f'깨진 JSON {len(feedback.malformed)}행',
                *feedback.malformed,
                f'유효하지 않은 피드백 {len(feedback.invalid)}행',
                *feedback.invalid,
            )
        )
        write = lambda conn: write_feedback(conn, feedback)

    print(f'대상 DB {db} · 입력 {count}행', flush=True)
    if args.dry_run:
        return status, [*lines, '입력 검사 완료 · DB 미접근']
    changed = apply_import(db, write)
    return status, [*lines, f'변경 {changed}건']


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        db = check_db_path(repo_path(args.db))
        report = repo_path(args.report) if args.report else None
        if report is not None:
            if report.exists() or report.is_symlink():
                raise FileExistsError(f'보고서 경로가 이미 있습니다: {report}')
            if not report.parent.is_dir():
                raise FileNotFoundError(f'보고서 폴더가 없습니다: {report.parent}')
        status = 0
        if args.command != 'verify':
            status, lines = _import(args, db)
        elif args.dry_run:
            lines = ['대조 경로 검사 완료 · DB 미접근']
        else:
            status, lines = verify_imports(db)
        print('\n'.join(lines))
        if report is not None:
            with report.open('x', encoding='utf-8') as target:
                target.write('# SQLite 이관 결과\n\n' + '\n'.join(f'- {line.strip()}' for line in lines) + '\n')
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        print(f'FAIL {exc}')
        return 2
    return status


if __name__ == '__main__':
    raise SystemExit(main())
