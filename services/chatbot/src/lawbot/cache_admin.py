"""Inspect, stage, approve and disable exact-cache rows from a trusted host account."""

import argparse
import csv
import hashlib
import json
import os
import pwd
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from lawbot import deploy, envfile, settings
from lawbot.cache_seed import Protection, load_bundle, load_protection, prepare, require_db_protection
from lawbot.cache_seed_db_store import read_source
from lawbot.cache_seed_store import import_seed
from lawbot.cache_store import Store, init, migrate, translate_sqlite_errors
from lawbot.corpus_store import active_release, active_token
from lawbot.db.backup import backup
from lawbot.db.connection import db_path, repo_path
from lawbot.exact_cache import PROFILE, Snapshot, digest
from lawbot.feedback import check_private
from lawbot.paths import ROOT
from lawbot.question_vectors import generate_vectors
from lawbot.service import cache_snapshot


def reviewed_time(value):
    when = datetime.fromisoformat(value)
    if when.utcoffset() is None:
        raise ValueError('승인 시각에는 시간대를 지정하세요.')
    return when.astimezone(UTC).isoformat(timespec='microseconds')


def development_path(path: str | Path) -> Path:
    """Resolve a ZIP command path without permitting production worktrees or files."""
    resolved = repo_path(str(path)).resolve()
    production = ROOT.with_name('lawbot-prod').resolve()
    if ROOT.resolve() == production or resolved.is_relative_to(production):
        raise ValueError('이 도구는 개발용 사본에서만 실행할 수 있습니다.')
    return resolved


def same_file(first: Path, second: Path) -> bool:
    return first == second or (first.exists() and second.exists() and first.samefile(second))


@dataclass(frozen=True)
class CorpusContext:
    db: Path
    snapshot: Snapshot
    event: int
    protection: Protection
    protected_csv: tuple[Path, ...] = ()

    def check(self):
        if active_token(self.db, PROFILE) != (self.snapshot.release_id, self.event):
            raise ValueError('검사 중 활성 코퍼스가 변경되었습니다.')

    def check_import(self):
        if load_protection(self.db, list(self.protected_csv)).fingerprint != self.protection.fingerprint:
            raise ValueError('적재 중 보호 분할이 변경되었습니다.')
        self.check()

    def identity(self):
        return {
            'namespace_id': self.snapshot.namespace.id,
            'release_id': self.snapshot.release_id,
            'activation_event': self.event,
            'protection_hash': self.protection.fingerprint,
        }


def corpus_context(db: Path, protected_csv: list[Path], *, config: settings.Settings | None = None) -> CorpusContext:
    """Resolve one active v5 Pro answer scope and its protection from the corpus DB."""
    profile = deploy.get_profile() if config is None else config.profile
    decree = deploy.decree(profile) if config is None else config.decree
    token = active_token(db, PROFILE)
    release = active_release(db, PROFILE)
    snapshot = cache_snapshot(profile, release, decree)
    if token is None or snapshot is None or snapshot.release_id != token[0]:
        raise ValueError('활성 코퍼스 또는 승인할 답변 범위를 확인할 수 없습니다.')
    context = CorpusContext(db, snapshot, token[1], load_protection(db, protected_csv), tuple(protected_csv))
    context.check()
    return context


def scope(args):
    cfg = settings.load()
    if cfg.db is None or cfg.corpus != 'db' or cfg.profile.name != PROFILE:
        raise ValueError('현행 v5 DB 코퍼스 설정이 필요합니다.')
    if args.db is not None and same_file(db_path(args.db), cfg.db):
        raise ValueError('정확 일치 캐시는 별도 SQLite 파일을 사용해야 합니다.')
    return corpus_context(cfg.db, args.protected_csv, config=cfg)


def context(args):
    """Prepare reviewed P07 drafts from a development-only handoff ZIP."""
    bundle_path = development_path(args.bundle)
    args.protected_csv = [development_path(path) for path in args.protected_csv]
    for name in ('report_dir', 'review_file'):
        if path := getattr(args, name, None):
            development_path(path)
    for path in getattr(args, 'correction_file', ()):
        development_path(path)
    if corpus := os.environ.get('LAWBOT_DB', '').strip():
        development_path(corpus)
    ctx = scope(args)
    development_path(ctx.db)
    bundle = load_bundle(bundle_path)
    require_db_protection(bundle, ctx.protection)
    seed = prepare(bundle, ctx.protection, ctx.snapshot)
    ctx.check()
    return ctx.snapshot, seed, ctx.identity()


def reports(folder, seed, identity):
    folder = folder.resolve()
    for name in ('seed_summary.json', 'seed_exclusions.csv', 'review.json'):
        check_private(folder / name, folder=False)
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    (folder / 'seed_summary.json').write_text(json.dumps(seed.summary | identity, ensure_ascii=False), encoding='utf-8')
    with (folder / 'seed_exclusions.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, ['id', 'primary_reason', 'reasons'])
        writer.writeheader()
        writer.writerows(seed.exclusions)
    # Keep question and answer text in the private review file only.
    namespace = identity['namespace_id']
    review = [review_row(d, namespace) for d in seed.drafts]
    (folder / 'review.json').write_text(
        json.dumps(identity | {'selection': {}, 'candidates': review}, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def review_row(draft, namespace):
    return dict(
        query_id=draft.query_id(namespace),
        answer_id=draft.answer_id(namespace),
        source_id=draft.source_id,
        question=draft.question,
        text=draft.answer_text,
        citations=[list(pair) for pair in draft.citations],
    )


def read_review(path, identity):
    raw = path.read_bytes()
    reviewed = json.loads(raw)
    if not isinstance(reviewed, dict):
        raise ValueError('검토 파일은 JSON 객체여야 합니다.')
    if any(reviewed.get(k) != v for k, v in identity.items()):
        raise ValueError('검토 파일의 코퍼스·정책 또는 보호 분할이 바뀌었습니다.')
    return reviewed, hashlib.sha256(raw).hexdigest()


def selection(path, identity, seed, corrections=()):
    reviewed, _ = read_review(path, identity)
    namespace = identity['namespace_id']
    allowed = {d.query_id(namespace): review_row(d, namespace) for d in seed.drafts}
    candidate_rows = reviewed.get('candidates')
    if not isinstance(candidate_rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get('query_id'), str) for row in candidate_rows
    ):
        raise ValueError('검토 파일에 후보별 최종 표시본이 필요합니다.')
    candidates = {row.get('query_id'): row for row in candidate_rows}
    corrected = {draft.query_id(namespace): draft.answer_id(namespace) for draft in corrections}
    if len(corrected) != len(corrections):
        raise ValueError('같은 질문의 정정 검토 파일은 하나만 지정하세요.')
    selected = reviewed.get('selection')
    if not isinstance(selected, dict) or not selected:
        raise ValueError('검토 파일에 승인할 질문과 답변 리비전을 지정하세요.')
    for key, answer in selected.items():
        if key in corrected and corrected[key] == answer:
            continue
        row = allowed.get(key)
        if key not in corrected and row is not None and row['answer_id'] == answer and row == candidates.get(key):
            continue
        raise ValueError('검토 파일의 질문과 답변 리비전을 현재 등록 후보와 대조하세요.')
    return selected


def correction(path, identity, seed, snapshot):
    check_private(path, folder=False)
    reviewed, review_hash = read_review(path, identity)
    namespace_id = identity['namespace_id']
    original = next((d for d in seed.drafts if d.query_id(namespace_id) == reviewed.get('query_id')), None)
    previous = reviewed.get('previous_answer_id')
    raw = reviewed.get('answer_raw')
    article_ids = reviewed.get('citation_article_ids')
    if original is None or not isinstance(previous, str) or not isinstance(raw, str) or not raw.strip():
        raise ValueError('검토 파일에 P07 허용 질문, 이전 답변 리비전, 새 답변 원문이 필요합니다.')
    if (
        not isinstance(article_ids, list)
        or not article_ids
        or any(not isinstance(key, str) or key not in snapshot.articles for key in article_ids)
    ):
        raise ValueError('정정 답변의 표시 근거가 활성 법령에 있어야 합니다.')
    citations = [(key, snapshot.articles[key].text_hash) for key in article_ids]
    shown = snapshot.render(raw, citations)
    if not shown or shown != reviewed.get('answer_shown'):
        raise ValueError('정정 답변의 근거 또는 간결형 표시본이 검토 파일과 다릅니다.')
    return previous, replace(
        original,
        raw_sha256=digest(raw),
        raw_answer=raw,
        answer_text=shown,
        citations=citations,
        source=original.source | {'correction_review_sha256': review_hash, 'previous_answer_id': previous},
    )


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    for name in (
        'init',
        'migrate',
        'inspect',
        'seed',
        'approve',
        'replace',
        'set-mode',
        'revoke',
        'backup',
        'exclude-calculations',
        'import-db',
        'rebaseline-protection',
        'generate-question-vectors',
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument('--db', required=name != 'inspect')
        if name in ('inspect', 'seed', 'approve', 'replace'):
            cmd.add_argument('--bundle', type=Path, required=True)
        if name in (
            'inspect',
            'seed',
            'approve',
            'replace',
            'set-mode',
            'import-db',
            'rebaseline-protection',
            'generate-question-vectors',
        ):
            cmd.add_argument('--protected-csv', type=Path, action='append', default=[])
        if name in ('import-db', 'rebaseline-protection', 'generate-question-vectors'):
            cmd.add_argument('--corpus-db', required=True)
        if name == 'generate-question-vectors':
            cmd.add_argument('--dry-run', action='store_true')
            cmd.add_argument('--approved-requests', type=int)
            cmd.add_argument('--max-runtime-seconds', type=int)
        if name == 'import-db':
            cmd.add_argument('--from', dest='source_db', required=True)
        if name in ('seed', 'approve', 'replace', 'set-mode', 'revoke', 'rebaseline-protection'):
            cmd.add_argument('--reason', required=name != 'seed')
        if name in ('inspect', 'seed'):
            cmd.add_argument('--report-dir', type=Path)
        if name == 'seed':
            cmd.add_argument('--dry-run', action='store_true')
        if name in ('approve', 'replace'):
            cmd.add_argument('--review-file', type=Path, required=True)
            cmd.add_argument(
                '--reviewed',
                action='store_true',
                required=True,
                help='현행성, 간결형 표시, 공개 범위 및 모든 질문 연결을 검토했음을 기록',
            )
        if name == 'approve':
            cmd.add_argument('--correction-file', type=Path, action='append', default=[])
            for flag in ('--valid-from', '--valid-until', '--expires-at'):
                cmd.add_argument(flag)
        if name == 'replace':
            cmd.add_argument('--approval', required=True)
        if name == 'set-mode':
            cmd.add_argument('mode', choices=('off', 'shadow', 'on'))
            cmd.add_argument('--approval')
        if name == 'revoke':
            cmd.add_argument('kind', choices=('query', 'answer'))
            cmd.add_argument('target')
        if name == 'backup':
            cmd.add_argument('--out', required=True)
        if name == 'exclude-calculations':
            cmd.add_argument('--apply', action='store_true', help='제외 항목의 활성 플래그를 끕니다.')
            cmd.add_argument('--reason', help='--apply에 필요한 변경 사유')
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        envfile.load()
        with translate_sqlite_errors():
            result = run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        cli.exit(2, f'캐시 작업 실패: {exc}\n')
    print(json.dumps(result, ensure_ascii=False))


def run(args):
    command = args.command
    if command in ('inspect', 'seed', 'approve', 'replace') and args.db:
        development_path(args.db)
    path = db_path(args.db) if args.db else None
    actor = pwd.getpwuid(os.geteuid()).pw_name
    reason = getattr(args, 'reason', None) or ('P01~P07 draft import' if command == 'seed' else '')
    if command == 'inspect':
        _, seed, identity = context(args)
        if args.report_dir:
            reports(args.report_dir, seed, identity)
        return seed.summary | identity
    if path is None:
        raise ValueError('캐시 DB 경로가 필요합니다.')
    if command == 'init':
        return {'schema_version': init(path)}
    if command == 'migrate':
        return {'schema_version': migrate(path)}
    if command == 'backup':
        backup(path, db_path(args.out))
        return {'backup': 'ok'}
    if command == 'import-db':
        source, corpus = db_path(args.source_db), db_path(args.corpus_db)
        if same_file(source, path) or same_file(corpus, path) or same_file(source, corpus):
            raise ValueError('원천·대상 캐시와 대상 코퍼스에는 서로 다른 DB 파일을 지정하세요.')
        ctx = corpus_context(corpus, args.protected_csv)
        seed, checksum = read_source(source)
        return import_seed(
            path,
            seed,
            ctx.snapshot,
            ctx.event,
            ctx.protection,
            check_snapshot=ctx.check_import,
            source_db_sha256=checksum,
        )
    store = Store(path)
    if command == 'generate-question-vectors':
        corpus = db_path(args.corpus_db)
        if same_file(corpus, path):
            raise ValueError('코퍼스와 캐시에는 서로 다른 DB 파일을 지정하세요.')
        cfg = settings.load()
        return generate_vectors(path, corpus_context(corpus, args.protected_csv, config=cfg), cfg, args)
    if command == 'rebaseline-protection':
        if not reason.strip():
            raise ValueError('변경 사유가 필요합니다.')
        corpus = db_path(args.corpus_db)
        if same_file(corpus, path):
            raise ValueError('코퍼스와 캐시에는 서로 다른 DB 파일을 지정하세요.')
        ctx = corpus_context(corpus, args.protected_csv)
        count = store.rebaseline_protection(
            ctx.snapshot,
            ctx.event,
            ctx.protection.fingerprint,
            actor,
            reason,
            ctx.check_import,
        )
        return {'pending_questions': count, 'protection_hash': ctx.protection.fingerprint}
    if command == 'exclude-calculations':
        if args.apply and not reason.strip():
            raise ValueError('--apply에는 변경 사유가 필요합니다.')
        rows, disabled = (
            store.disable_calculations(actor, reason) if args.apply else (store.calculation_exclusions(), 0)
        )
        return {
            'counts': {status: sum(row.status == status for row in rows) for status in ('draft', 'approved')},
            'disabled': disabled,
            'items': [
                asdict(row) | {'effective_status': 'off', 'exact_enabled': row.exact_enabled and not args.apply}
                for row in rows
            ],
        }
    if not reason.strip():
        raise ValueError('변경 사유가 필요합니다.')
    if command == 'revoke':
        store.revoke(args.kind, args.target, actor, reason)
        return {'revoked': args.target}
    if command == 'set-mode' and args.mode == 'off':
        store.set_mode('off', actor, reason)
        return {'mode': 'off'}
    if command == 'set-mode':
        require_identity(store.control(), scope(args).identity())
        store.set_mode(args.mode, actor, reason, args.approval)
        return {'mode': args.mode}
    snapshot, seed, identity = context(args)
    if command == 'seed':
        if args.report_dir:
            reports(args.report_dir, seed, identity)
        count = 0 if args.dry_run else store.stage(snapshot.namespace, seed.drafts, actor, reason)
        return seed.summary | identity | {'inserted_query_rows': count, 'dry_run': args.dry_run}
    if command == 'approve':
        corrected = [correction(path, identity, seed, snapshot)[1] for path in args.correction_file]
        selected = selection(args.review_file, identity, seed, corrected)
        times = {
            key: reviewed_time(value)
            for key in ('valid_from', 'valid_until', 'expires_at')
            if (value := getattr(args, key)) is not None
        }
        approval = store.approve(
            identity['namespace_id'],
            identity['activation_event'],
            identity['protection_hash'],
            selected,
            actor=actor,
            reason=reason,
            **times,
        )
        return {
            'approval_id': approval,
            'approved_query_rows': len(selected),
            'approved_answer_rows': len(set(selected.values())),
        }
    control = store.control()
    require_identity(control, identity)
    if control.approval_id != args.approval:
        raise ValueError('현재 승인 묶음을 지정하세요.')
    previous, draft = correction(args.review_file, identity, seed, snapshot)
    approval = store.replace_answer(control, draft, previous, actor, reason)
    return {'approval_id': approval, 'answer_id': draft.answer_id(identity['namespace_id'])}


def require_identity(control, identity):
    if (control.namespace_id, control.activation_event, control.protection_hash) != (
        identity['namespace_id'],
        identity['activation_event'],
        identity['protection_hash'],
    ):
        raise ValueError('코퍼스·정책 또는 보호 분할이 변경되었습니다. off에서 재검토하세요.')


if __name__ == '__main__':
    main()
