"""Build query vectors with explicit request and elapsed-time limits."""

import fcntl
import os
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from itertools import batched
from pathlib import Path
from zoneinfo import ZoneInfo

from lawbot.api_client import ModelServerClient, embedding_revision
from lawbot.cache_question_store import question_catalog, save_vectors, unit_vector

BATCH_SIZE = 16
KST = ZoneInfo('Asia/Seoul')


def request_count(questions):
    return (questions + BATCH_SIZE - 1) // BATCH_SIZE


def require_available_window():
    at = datetime.now(KST)
    if 380 <= at.hour * 60 + at.minute <= 410:
        raise ValueError('06:20–06:50 KST에는 추천 임베딩을 실행할 수 없습니다.')


def embedding_lock_path() -> Path:
    return Path('/tmp') / f'lawbot-reviewed-question-embedding-{os.getuid()}.lock'


@contextmanager
def embedding_budget(requests, approved_requests, max_runtime):
    if requests <= 0 or approved_requests != requests or not max_runtime or max_runtime <= 0:
        raise ValueError('--dry-run 요청 수와 일치하는 --approved-requests 및 --max-runtime-seconds가 필요합니다.')
    require_available_window()
    fd = os.open(embedding_lock_path(), os.O_CREAT | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('추천 임베딩 잠금 파일의 소유자 또는 유형이 올바르지 않습니다.')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('다른 추천 임베딩 작업이 실행 중입니다.') from exc
        yield time.monotonic() + max_runtime


def seconds_remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('추천 임베딩 작업의 제한 시간이 지났습니다.')
    return remaining


def current_revision(client, model, timeout=5):
    revision = embedding_revision(client.models(timeout=timeout), model)
    if not revision:
        raise ValueError('임베딩 모델 revision을 확인할 수 없습니다.')
    return revision


def encode_questions(client, cfg, texts, deadline):
    vectors = []
    for batch in batched(texts, BATCH_SIZE, strict=False):
        require_available_window()
        timeout = min(seconds_remaining(deadline), 300, cfg.read_timeout)
        result = client.embed(cfg.profile.emb_model, batch, 'query', cfg.profile.max_seq, timeout)
        normalized = [unit_vector(row) for row in result]
        if len(normalized) != len(batch) or any(row is None for row in normalized):
            raise ValueError('임베딩 서버가 유효하지 않은 질문 벡터를 반환했습니다.')
        vectors.extend(normalized)
    if len({row.size for row in vectors}) > 1:
        raise ValueError('질문 벡터 차원이 일치하지 않습니다.')
    return vectors


def generate_vectors(path, ctx, cfg, args):
    model = cfg.profile.emb_model
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    try:
        revision = current_revision(client, model)
        control, questions, stored = question_catalog(
            path, ctx.snapshot, model, revision, cfg.profile.max_seq, timeout=5
        )
        if (control.namespace_id, control.activation_event, control.protection_hash) != (
            ctx.snapshot.namespace.id,
            ctx.event,
            ctx.protection.fingerprint,
        ):
            raise ValueError('현행 코퍼스와 보호 지문에 맞는 캐시 승인이 필요합니다.')
        pending = [q for q in questions if q.id not in stored]
        requests = request_count(len(pending))
        summary = {
            'approved_questions': len(questions),
            'matching_vectors': len(stored),
            'missing_vectors': len(pending),
            'embedding_requests': requests,
            'embedding_model': model,
            'model_revision': revision,
            'batch_size': BATCH_SIZE,
        }
        if args.dry_run or not pending:
            return summary
        with embedding_budget(requests, args.approved_requests, args.max_runtime_seconds) as deadline:
            if current_revision(client, model, min(5, seconds_remaining(deadline))) != revision:
                raise ValueError('실행 전에 모델 revision이 바뀌었습니다.')
            vectors = encode_questions(client, cfg, [q.normalized for q in pending], deadline)
            if current_revision(client, model, min(5, seconds_remaining(deadline))) != revision:
                raise ValueError('벡터 생성 중 모델 revision이 바뀌었습니다.')
            save_vectors(
                path,
                control,
                model,
                revision,
                cfg.profile.max_seq,
                list(zip(pending, vectors, strict=True)),
                ctx.check_import,
            )
        return summary | {'generated_vectors': len(vectors)}
    finally:
        client.http.close()
