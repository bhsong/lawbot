"""Import the reviewed semantic handoff, generate ID-only val rows, or build an index."""

import argparse
import fcntl
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tokenizers import Tokenizer

from lawbot import deploy, envfile, security
from lawbot.api_client import ModelServerClient, ModelServerError, request_timeout
from lawbot.cache_seed import load_protection
from lawbot.cache_store import translate_sqlite_errors
from lawbot.corpus_store import active_release, active_token, article_version_hashes
from lawbot.db.connection import check_db_path, repo_path
from lawbot.exact_cache import PROFILE, Snapshot, canonical
from lawbot.feedback import check_private
from lawbot.paths import DATA, ROOT
from lawbot.semantic_handoff import read_deferred, read_pass
from lawbot.semantic_import_store import import_seed
from lawbot.semantic_index_store import read_questions
from lawbot.semantic_index_tools import IndexContract, build_index, select_questions
from lawbot.semantic_supplement import SHA256, import_supplement, read_supplement
from lawbot.semantic_val import build_val, read_holdout, read_v6_training
from lawbot.service import cache_snapshot
from lawbot_common.model_revision import fingerprint


def development_path(path: str) -> Path:
    resolved = repo_path(path).resolve()
    production = ROOT.with_name('lawbot-prod').resolve()
    if resolved.is_relative_to(production):
        raise ValueError('이 도구는 개발용 사본에서만 실행할 수 있습니다.')
    return resolved


@dataclass(frozen=True)
class CorpusContext:
    db: Path
    snapshot: Snapshot
    event: int

    def check(self):
        if active_token(self.db, PROFILE) != (self.snapshot.release_id, self.event):
            raise ValueError('검사 중 활성 코퍼스가 변경되었습니다.')


def corpus_context(db: Path, env=None) -> CorpusContext:
    profile = deploy.get_profile(env)
    token = active_token(db, PROFILE)
    release = active_release(db, PROFILE)
    snapshot = cache_snapshot(profile, release, deploy.decree(profile, env))
    if snapshot is None or token is None or snapshot.release_id != token[0]:
        raise ValueError('활성 v5 Pro 코퍼스와 답변 정책이 필요합니다.')
    ctx = CorpusContext(db, snapshot, token[1])
    ctx.check()
    return ctx


class ServerEmbedding:
    def __init__(self, client, model_dir, contract, deadline):
        self.client = client
        self.contract = contract
        self.deadline = deadline
        if fingerprint(model_dir) != contract.revision:
            raise ValueError('로컬 tokenizer·prompt가 포함된 모델 revision이 일치하지 않습니다.')
        settings = json.loads((model_dir / 'config_sentence_transformers.json').read_bytes())
        self.prompt = settings.get('prompts', {}).get('query')
        if not isinstance(self.prompt, str) or not self.prompt:
            raise ValueError('로컬 모델에 query prompt가 없습니다.')
        self.tokenizer = Tokenizer.from_file(str(model_dir / 'tokenizer.json'))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

    def fits(self, question):
        return len(self.tokenizer.encode((self.prompt + question).strip()).ids) <= self.contract.max_seq

    def timeout(self):
        local = datetime.now(ZoneInfo('Asia/Seoul'))
        remaining = self.deadline - time.monotonic()
        minute = local.hour * 60 + local.minute
        if remaining <= 0 or 380 <= minute < 410:
            raise ValueError('임베딩 허용 시간이 지났습니다.')
        if minute < 380:
            remaining = min(remaining, (380 - minute) * 60 - local.second - local.microsecond / 1_000_000)
        return request_timeout(min(remaining, 300))

    def revision(self):
        timeout = self.timeout()
        try:
            models = self.client.models(timeout=timeout)
        except (OSError, ModelServerError, ValueError) as exc:
            raise ValueError('공유 서버의 임베딩 모델 목록을 읽을 수 없습니다.') from exc
        revisions = {
            row['revision']
            for row in models
            if row['type'] == 'embedding' and row['loaded'] and row['id'] == self.contract.model
        }
        if len(revisions) != 1 or None in revisions:
            raise ValueError('이미 상주 중인 임베딩 모델 revision을 확인할 수 없습니다.')
        return next(iter(revisions))

    def encode(self, questions):
        timeout = self.timeout()
        try:
            return self.client.embed(self.contract.model, questions, 'query', self.contract.max_seq, timeout=timeout)
        except (OSError, ModelServerError, ValueError) as exc:
            raise ValueError('임베딩 요청이 실패해 인덱스 생성을 중단했습니다.') from exc


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    for command in ('import', 'supplement', 'val', 'index'):
        item = sub.add_parser(command)
        item.add_argument('--corpus-db', required=True)
        if command != 'val':
            item.add_argument('--cache-db', required=True)
        if command != 'index':
            item.add_argument('--bundle', required=True)
            item.add_argument('--protected-csv', action='append', default=[])
        if command == 'val':
            item.add_argument('--deferred-bundle', required=True)
            item.add_argument('--v6-training-dir', help='verified Colab QA and split CSV files')
            item.add_argument('--output', required=True)
        if command == 'index':
            item.add_argument('--output')
            item.add_argument('--exclude-val', help='ID-only val manifest for an evaluation generation')
            item.add_argument('--batch-size', type=int, default=16)
            item.add_argument('--embed-live', action='store_true')
            item.add_argument('--approved-requests', type=int)
            item.add_argument('--max-runtime-seconds', type=int)
            item.add_argument('--model')
            item.add_argument('--revision')
            item.add_argument('--max-seq', type=int)
            item.add_argument('--dimension', type=int)
            item.add_argument('--model-dir')
    return cli


def run_index(args, db, ctx):
    if not 1 <= args.batch_size <= 16:
        raise ValueError('batch-size는 1~16이어야 합니다.')
    held = read_holdout(development_path(args.exclude_val)) if args.exclude_val else set()
    _, questions = read_questions(db, ctx.snapshot, ctx.event)
    rows = len(select_questions(questions, held))
    requests = (rows + args.batch_size - 1) // args.batch_size
    ctx.check()
    if not args.embed_live:
        return {'rows': rows, 'embedding_requests_planned': requests}
    if (
        args.approved_requests != requests
        or not all(
            (
                args.output,
                args.max_runtime_seconds,
                args.model,
                args.revision,
                args.max_seq,
                args.dimension,
                args.model_dir,
            )
        )
        or args.max_runtime_seconds < 1
    ):
        raise ValueError('승인 요청 수, 실행 시간 상한, 출력 경로 및 임베딩 계약이 필요합니다.')
    output = development_path(args.output)
    check_private(output, 'semantic index')
    model_dir = development_path(args.model_dir)
    try:
        url = security.model_server_url()
    except ValueError as exc:
        raise ValueError('개발 설정에서 공유 모델 서버 주소를 확인할 수 없습니다.') from exc
    if url is None:
        raise ValueError('개발 설정에 공유 모델 서버가 없습니다.')
    lock_path = development_path(str(DATA / 'db' / 'semantic' / '.embedding.lock'))
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = ModelServerClient(url, security.model_token())
        contract = IndexContract(args.model, args.revision, args.max_seq, args.dimension)
        try:
            encoder = ServerEmbedding(client, model_dir, contract, time.monotonic() + args.max_runtime_seconds)
            return build_index(
                db,
                output,
                ctx.snapshot,
                ctx.event,
                contract,
                encoder,
                batch_size=args.batch_size,
                held_out=held,
                check_snapshot=ctx.check,
                approved_requests=args.approved_requests,
            )
        finally:
            client.http.close()


def development_protection(corpus: Path, extra: Sequence[str | Path]):
    """Exact-cache protection from the corpus DB split and reviewer CSVs; no ZIP is read."""
    protected_csv = [development_path(str(filename)) for filename in extra]
    return load_protection(corpus, protected_csv)


def run(args):
    corpus = check_db_path(development_path(args.corpus_db))
    cache = check_db_path(development_path(args.cache_db)) if args.command != 'val' else None
    if cache == corpus or (cache is not None and cache.exists() and cache.samefile(corpus)):
        raise ValueError('코퍼스와 캐시에는 서로 다른 개발 DB 사본을 지정하세요.')
    ctx = corpus_context(corpus)
    if args.command == 'index':
        return run_index(args, cache, ctx)
    if args.command == 'supplement':
        rows = read_supplement(development_path(args.bundle))
        protection = development_protection(corpus, args.protected_csv)
        hashes = article_version_hashes(corpus, ctx.snapshot.release_id)
        return import_supplement(
            cache, rows, SHA256, ctx.snapshot, ctx.event, protection, version_hashes=hashes, check_snapshot=ctx.check
        )
    seed = read_pass(development_path(args.bundle))
    protection = development_protection(corpus, args.protected_csv)
    if args.command == 'val':
        deferred = read_deferred(development_path(args.deferred_bundle))
        training = read_v6_training(development_path(args.v6_training_dir)) if args.v6_training_dir else None
        rows, held, counts = build_val(seed, deferred, protection, ctx.snapshot, training=training)
        if training is not None:
            counts['v6_train_questions'] = len(training.by_id)
        ctx.check()
        with development_path(args.output).open('x', encoding='utf-8') as output:
            output.writelines(canonical(row) + '\n' for row in rows)
        return counts | {'rows': len(rows), 'held_out': len(held), 'protected_unique': len(protection.exact)}
    if cache is None:
        raise ValueError('개발 캐시 DB 사본이 필요합니다.')
    result = import_seed(cache, seed, ctx.snapshot, ctx.event, protection, check_snapshot=ctx.check)
    return result | {'protected_unique': len(protection.exact)}


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        envfile.load()
        with translate_sqlite_errors():
            result = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        cli.exit(2, f'시맨틱 도구 실행 실패: {exc}\n')
    print(canonical(result))


if __name__ == '__main__':
    main()
