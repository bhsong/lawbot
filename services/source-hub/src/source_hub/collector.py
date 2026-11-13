"""Run one registered source and commit its observation independently."""

import fcntl
import os
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from source_hub import materials
from source_hub.materials import Article
from source_hub.store import KST, Registry, Run


class FetchError(Exception):
    """A bounded fetch failure safe to persist in the run log."""


def is_blocked(error: str) -> bool:
    return error.startswith('robots_') or error in {'http_401', 'http_403', 'http_429'}


def source_plan(registry: Registry, source_id: str, adapter: str, mode: str, url: str, *, plan: dict | None = None):
    plan = registry.snapshot() if plan is None else plan
    source = next((row for row in plan['sources'] if row['source_id'] == source_id), None)
    if source is None or not source['effective_enabled']:
        return None, plan['config_version'], 'disabled'
    actual = (source['group'], source['adapter'], source['mode'], source['source_url'], source['parser_version'])
    if not source['runnable'] or actual != ('erp', adapter, mode, url, '1'):
        return None, plan['config_version'], 'unverified'
    return source, plan['config_version'], None


@contextmanager
def file_lock(lock: Path):
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def collect_lock(registry: Registry):
    db = registry.database.resolve()
    return file_lock(db.with_name(db.name + '.source-hub.lock'))


def interval_due(started: datetime, previous: datetime, minutes: int, *, kst_days: bool) -> bool:
    interval = timedelta(minutes=minutes)
    if kst_days and minutes % 1440 == 0:
        return started.astimezone(KST).date() >= previous.astimezone(KST).date() + interval
    return started >= previous + interval


def collect(
    registry: Registry,
    database: Path,
    source: dict,
    config_version: int,
    *,
    read_rules: Callable[[], list[str]],
    read_articles: Callable[[list[str]], list[Article]],
    read_board: Callable[[list[str], list[Article]], tuple[list[Article] | None, dict]] | None = None,
    invalid_source: str = 'invalid_feed',
    now: datetime | None = None,
    kst_days: bool = False,
) -> dict:
    source_id = source['source_id']
    path = database.resolve()
    registry_db = registry.database.resolve()
    if path == registry_db or (path.exists() and registry_db.exists() and path.samefile(registry_db)):
        raise ValueError('등록부와 수집 자료 DB에는 별도 경로를 사용하세요')
    started = now or datetime.now(KST)
    with collect_lock(registry) as locked:
        if not locked:
            return {'outcome': 'busy'}
        materials.initialize(path)
        if previous := materials.latest_success(path, source_id):
            registry.record_run(previous)
        latest = registry.runs(source_id)
        if latest and not interval_due(
            started,
            datetime.fromisoformat(latest[0]['started_at']),
            source['interval_minutes'],
            kst_days=kst_days,
        ):
            return {'outcome': 'not_due'}
        outcome, error = 'ok', None
        counts = {}
        articles: list[Article] = []
        board: list[Article] | None = None
        reconciliation = {}
        board_attempted = False
        try:
            rules = read_rules()
            articles = read_articles(rules)
        except FetchError as exc:
            error = str(exc)
            outcome = 'blocked' if is_blocked(error) else 'error'
        except ElementTree.ParseError, ValueError:
            error, outcome = invalid_source, 'error'
        else:
            if read_board is not None:
                last = materials.last_board_attempt(path, source_id)
                board_attempted = last is None or interval_due(
                    started,
                    datetime.fromisoformat(last),
                    source['reconcile_interval_minutes'],
                    kst_days=kst_days,
                )
                if board_attempted:
                    board, reconciliation = read_board(rules, articles)
                    if board is None:
                        outcome, error = 'partial', 'board_' + reconciliation['error']
                else:
                    reconciliation = {'outcome': 'not_due'}
        run = Run(
            run_id=uuid4().hex,
            source_id=source_id,
            config_version=config_version,
            started_at=started,
            finished_at=now or datetime.now(KST),
            outcome=outcome,
            new_count=0 if outcome == 'ok' else None,
            modified_count=0 if outcome == 'ok' else None,
            error=error,
        )
        if articles:
            run, counts = materials.save(
                path,
                articles,
                run,
                source['baseline_policy'],
                board=board,
                board_attempted=board_attempted,
            )
        registry.record_run(run)
    result = {
        'outcome': outcome,
        'run_id': run.run_id,
        'new_count': run.new_count,
        'modified_count': run.modified_count,
        **counts,
    }
    if error:
        result['error'] = error
    if reconciliation:
        result['reconciliation'] = reconciliation
    return result
