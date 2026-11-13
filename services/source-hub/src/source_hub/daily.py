"""Run one managed law/ERP day and publish its immutable public snapshot."""

import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from lawbot_common.erp_daily import read_snapshot, replace_file, seal, snapshot_hash
from lawbot_common.erp_news import official_url
from lawbot_common.erp_rules import RuleSnapshot
from source_hub import moel, mofe, news, nts
from source_hub.collector import file_lock
from source_hub.store import KST, Registry

COLLECTORS = (
    dict.fromkeys(moel.CHANNELS, moel.collect)
    | dict.fromkeys(mofe.CHANNELS, mofe.collect)
    | dict.fromkeys(nts.CHANNELS, nts.collect)
)
LEGACY = ('legalize_law', 'legalize_admrule', 'assembly_bill', 'assembly_notice')
GOOD = {'ok', 'disabled', 'unverified', 'not_due'}


@dataclass(frozen=True)
class DailyConfig:
    root: Path
    monitor_home: Path
    erp_data: Path
    calendar_db: Path
    materials_db: Path
    public: Path
    uv: str = 'uv'
    erp_config: Path | None = None
    monitor_config: Path | None = None


@dataclass(frozen=True)
class FeedPaths:
    erp: Path
    rules: Path
    news: Path
    output: Path
    include_laws: bool = True


def command(config: DailyConfig, project: str, *args: str, timeout: int = 360) -> subprocess.CompletedProcess[str]:
    uv = [config.uv, 'run', '--frozen', '--no-sync']
    if project:
        uv.extend(('--project', str(config.root / 'services' / project)))
    return subprocess.run(
        [*uv, *args],
        cwd=config.root,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=os.environ | {'LAW_CALENDAR_DB': str(config.calendar_db), 'LAW_MONITOR_UV': config.uv},
    )


def legacy_rows(database: Path, run_id: str) -> tuple[dict, str | None, str | None]:
    if not database.is_file():
        return {}, None, None
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        found = conn.execute(
            "SELECT summary_json,finished_at,status FROM runs WHERE run_id=? AND mode='normal'", (run_id,)
        ).fetchone()
    if found is None:
        return {}, None, None
    return json.loads(found[0] or '{}').get('collectors', {}), found[1], found[2]


def run_legacy(config: DailyConfig, plan: dict, previous: dict) -> dict:
    database = config.monitor_home / 'data/state.db'
    flags = ['--config', str(config.monitor_config)] if config.monitor_config else []
    with NamedTemporaryFile('w', dir=config.public.parent, encoding='utf-8') as file:
        json.dump(plan, file, ensure_ascii=False)
        file.flush()
        result = command(
            config,
            '',
            'law-monitor',
            '--home',
            str(config.monitor_home),
            *flags,
            'daily',
            '--source-plan',
            file.name,
            timeout=3600,
        )
    if result.returncode == 3:
        return {'outcome': 'busy'}
    collectors, finished, status = legacy_rows(database, plan['run_id'])
    report = {}
    for name in plan['collectors']:
        item = collectors.get(name, {})
        prior = previous.get(name, {})
        ok = bool(finished and item.get('status') == 'ok')
        report[name] = {
            'outcome': 'ok' if ok else 'error',
            'run_id': plan['run_id'] if status else None,
            'new_count': item.get('events') if ok else None,
            'last_success_at': finished if ok else prior.get('lastSuccessAt'),
        }
    if result.returncode != 0:
        report['legacy_daily'] = {'outcome': 'partial' if finished else 'error', 'run_id': plan['run_id']}
    return report


def erp_args(config: DailyConfig) -> list[str]:
    args = ['erp-updates', '--data', str(config.erp_data)]
    if config.erp_config:
        args.extend(('--config', str(config.erp_config)))
    return args


def run_erp(config: DailyConfig, *, collect: bool) -> dict:
    result: dict = {'outcome': 'not_due'}
    if collect:
        proc = command(
            config, 'erp-updates', *erp_args(config), 'collect', '--repos', str(config.monitor_home / 'data/repos')
        )
        matched = re.fullmatch(r'collected=(\d+) new_versions=(\d+) errors=(\d+)', proc.stdout.strip())
        result = {
            'outcome': 'ok'
            if proc.returncode == 0 and matched
            else 'partial'
            if matched and int(matched[1])
            else 'error'
        }
        if matched and int(matched[2]) == 0 and result['outcome'] == 'ok':
            result |= {'new_count': 0, 'modified_count': 0}
    return result


def capture(config: DailyConfig, paths: FeedPaths, cutoff: datetime) -> dict:
    for name, path in (('export', paths.erp), ('export-rules', paths.rules)):
        if command(config, 'erp-updates', *erp_args(config), name, '--out', str(path)).returncode:
            raise ValueError('ERP approved feed export failed')
    result = command(
        config,
        'law-calendar',
        'python',
        '-m',
        'law_calendar.erp_capture',
        '--database',
        str(config.calendar_db),
        '--erp',
        str(paths.erp),
        '--rules',
        str(paths.rules),
        '--news',
        str(paths.news),
        '--out',
        str(paths.output),
        '--cutoff',
        cutoff.isoformat(),
        *([] if paths.include_laws else ['--without-law-news']),
    )
    if result.returncode:
        raise ValueError('ERP public snapshot capture failed')
    return json.loads(paths.output.read_bytes())


def old_snapshot(path: Path) -> dict:
    try:
        return read_snapshot(path)
    except FileNotFoundError:
        return {}


def due(source: dict, previous: dict, now: datetime) -> bool:
    stamp = previous.get('lastAttemptAt') or previous.get('lastSuccessAt')
    if not stamp:
        return True
    previous_start = datetime.fromisoformat(stamp)
    interval = timedelta(minutes=source['interval_minutes'])
    # Daily and longer cadences use the scheduled day rather than minute drift across days.
    if source['interval_minutes'] % 1440 == 0:
        return now.date() >= previous_start.astimezone(KST).date() + interval
    return now >= previous_start + interval


def skipped(source: dict, supported: bool) -> str | None:
    if not source['effective_enabled'] or (
        source['laws'] and not source['runnable'] and source['verification'] == 'ready'
    ):
        return 'disabled'
    if not source['runnable'] or not supported:
        return 'unverified'
    return None


def stage(state: dict, journal: Path, key: str, action: Callable[[], dict]) -> dict:
    if key not in state['steps']:
        try:
            state['steps'][key] = action()
        except OSError, ValueError, sqlite3.Error, subprocess.SubprocessError:
            state['steps'][key] = {'outcome': 'error'}
        if state['steps'][key].get('outcome') == 'busy':
            raise Busy('Another collection is running')
        replace_file(journal, state)
    return state['steps'][key]


def legacy_selection(rows: dict, previous: dict, state: dict, now: datetime) -> dict:
    selected = [
        key
        for key in LEGACY
        if key in rows
        and rows[key]['adapter'] == 'legacy'
        and skipped(rows[key], True) is None
        and due(rows[key], previous.get(key, {}), now)
    ]
    laws = rows.get('legalize_law', {}).get('laws', [])
    paths = [
        f'kr/{law["law_id"]}/{part["name"]}.md' for law in laws for part in law['parts'] if part['effective_enabled']
    ]
    registered = bool(laws)
    return {
        'run_id': state['run_id'],
        'collectors': selected,
        'law_paths': paths if registered else None,
    }


def public_status(key: str, row: dict, result: dict, prior: dict, now: datetime) -> dict:
    stamp = result.get('last_success_at') or (
        (result.get('finished_at') or now.isoformat())
        if result['outcome'] == 'ok'
        else row.get('last_success_at') or prior.get('lastSuccessAt')
    )
    attempt = result.get('started_at') or (
        prior.get('lastAttemptAt') if result['outcome'] == 'not_due' else now.isoformat()
    )
    stale = result['outcome'] not in GOOD or (
        result['outcome'] == 'not_due'
        and (not stamp or prior.get('stale') or result.get('previous_outcome') in ('error', 'partial', 'blocked'))
    )
    return {
        'sourceId': key,
        'outcome': result['outcome'],
        'runId': result.get('run_id'),
        'lastSuccessAt': stamp,
        'lastAttemptAt': attempt if result['outcome'] not in ('disabled', 'unverified') else prior.get('lastAttemptAt'),
        'stale': stale,
        'newCount': result.get('new_count'),
        'modifiedCount': result.get('modified_count'),
    }


def collect_sources(
    registry: Registry,
    config: DailyConfig,
    state: dict,
    journal: Path,
    previous: dict,
    now: datetime,
) -> list[dict]:
    plan = state['plan']
    rows = {row['source_id']: row for row in plan['sources']}
    legacy = {}
    if not state.get('publish_only'):
        selection = legacy_selection(rows, previous, state, now)
        legacy = stage(state, journal, 'legacy', lambda: run_legacy(config, selection, previous))
        if 'outcome' in legacy:
            legacy = dict.fromkeys(selection['collectors'], legacy) | {'legacy_daily': legacy}
    local = rows.get('existing-erp', {})
    local_enabled = bool(local and local['adapter'] == 'local' and skipped(local, True) is None)
    result = stage(
        state,
        journal,
        'erp',
        lambda: run_erp(
            config,
            collect=not state.get('publish_only')
            and local_enabled
            and due(local, previous.get('existing-erp', {}), now),
        ),
    )
    statuses = []
    for key, row in rows.items():
        supported = key in COLLECTORS or key in LEGACY or key == 'existing-erp'
        if outcome := skipped(row, supported):
            source_result = {'outcome': outcome}
        elif state.get('publish_only') and key in previous:
            statuses.append(previous[key])
            continue
        elif state.get('publish_only'):
            source_result = {'outcome': 'not_due'}
        elif key in LEGACY:
            source_result = legacy.get(key, {'outcome': 'not_due', 'run_id': previous.get(key, {}).get('runId')})
        elif key == 'existing-erp':
            source_result = result
        else:
            source_result = stage(
                state,
                journal,
                'agency:' + key,
                partial(COLLECTORS[key], registry, config.materials_db, key, reconcile=True, plan=plan),
            )
            latest = registry.runs(key)
            if latest and (source_result.get('run_id') == latest[0]['run_id'] or source_result['outcome'] == 'not_due'):
                run = latest[0]
                source_result = source_result | {
                    'run_id': run['run_id'],
                    'started_at': run['started_at'],
                    'finished_at': run['finished_at'],
                    'previous_outcome': run['outcome'],
                }
        statuses.append(public_status(key, row, source_result, previous.get(key, {}), now))
    if state.get('publish_only') and 'legacy_daily' in previous:
        statuses.append(previous['legacy_daily'])
    elif 'legacy_daily' in legacy:
        statuses.append(public_status('legacy_daily', {}, legacy['legacy_daily'], {}, now))
    return statuses


def news_plan(plan: dict, statuses: list[dict]) -> dict:
    status = {entry['sourceId']: entry for entry in statuses}
    return plan | {
        'sources': [
            row
            | {
                'last_success_at': status[row['source_id']]['lastSuccessAt'],
                'status': (
                    'delayed'
                    if status[row['source_id']]['stale'] and status[row['source_id']]['outcome'] in ('not_due', 'busy')
                    else row['status']
                    if status[row['source_id']]['outcome'] == 'not_due'
                    else status[row['source_id']]['outcome']
                ),
            }
            for row in plan['sources']
        ]
    }


def change_records(
    plan: dict,
    records: dict,
    feeds: dict,
    day: str,
    previous: dict,
) -> tuple[list[dict], dict]:
    """Track approved IDs outside the news drawer's top ten without republishing baseline lists."""
    ledger = dict(previous)
    visible = {}
    sources = news.review_sources_from_plan(plan)
    for source, entries in records.items():
        row = sources.get(source)
        if not row or not news.eligible(row):
            continue
        for upstream, item in entries:
            if official_url(item['canonical_url'], row['agency']):
                key = 'news:' + snapshot_hash({'source': source, 'upstream': upstream})
                visible[key] = {
                    'kind': 'news',
                    'sourceId': source,
                    'title': item['title'],
                    'url': item['canonical_url'],
                    'revision': item['revision'],
                    'event': item['event'],
                }
    for item in feeds['erp'].get('items', []):
        visible['erp:' + item['id']] = {
            'kind': 'erp',
            'title': item['title'],
            'url': item['sourceUrl'],
            'content': item['contentSha256'],
            'attachments': item['attachments'],
        }
    if feeds['rules']:
        for rule in RuleSnapshot.model_validate(feeds['rules']).rules:
            for scope in rule.scopes:
                for edition in scope.editions:
                    version = edition.version
                    if (
                        edition.reviews[-1].decision != 'confirmed'
                        or max(version.effectiveFrom, version.appliesFrom).isoformat() <= day
                    ):
                        continue
                    visible['rule:' + version.ruleVersionId] = {
                        'kind': 'scheduled',
                        'ruleId': rule.ruleId,
                        'title': rule.title,
                        'scope': scope.scope,
                        'version': version.model_dump(mode='json'),
                    }
    changes = []
    for key, value in sorted(visible.items()):
        digest = snapshot_hash(value)
        if digest != previous.get(key) and (value['kind'] != 'news' or value['event'] != 'baseline'):
            changes.append(
                value
                | {
                    'id': key,
                    'change': ('modified' if key in previous or value.get('event') == 'post_modified' else 'new'),
                }
            )
        ledger[key] = digest
    return changes, ledger


def report(snapshot: dict, *, reused: bool) -> dict:
    return {
        'status': snapshot['status'],
        'day_kst': snapshot['dayKst'],
        'run_id': snapshot['runId'],
        'snapshot_hash': snapshot['snapshotHash'],
        'reused': reused,
    }


class Busy(RuntimeError):
    """An existing collector holds the shared collection lock."""


def run(
    registry: Registry,
    config: DailyConfig,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(KST),
    republish: bool = False,
) -> dict:
    try:
        return _run(registry, config, now, republish)
    except Busy:
        return {'status': 'busy', 'day_kst': now().astimezone(KST).date().isoformat()}


def _run(registry: Registry, config: DailyConfig, now: Callable[[], datetime], republish: bool) -> dict:
    started = now().astimezone(KST)
    day = started.date().isoformat()
    public = config.public.absolute()
    private = registry.database.resolve()
    if (
        public.resolve()
        in {
            private,
            config.materials_db.resolve(),
            config.calendar_db.resolve(),
            (config.erp_data / 'erp.db').resolve(),
            (config.monitor_home / 'data/state.db').resolve(),
        }
        or public.is_symlink()
    ):
        raise ValueError('Use a distinct daily publication file')
    home = private.with_name(private.name + '.daily')
    with file_lock(home / '.lock') as locked:
        if not locked:
            return {'status': 'busy', 'day_kst': day}
        prior = old_snapshot(public)
        if prior.get('dayKst', '') > day:
            raise ValueError('KST clock precedes the published snapshot')
        working = home / day
        journal = working / 'run.json'
        archive = working / 'snapshot.json'
        if republish and prior.get('dayKst') != day:
            raise ValueError('Run the KST daily collection before republishing its snapshot')
        if not republish and prior.get('dayKst') == day:
            replace_file(working / 'published.json', prior)
            return report(prior, reused=True)
        if not republish and archive.is_file():
            saved = read_snapshot(archive)
            if prior.get('snapshotHash') != saved['snapshotHash']:
                replace_file(public, saved)
            replace_file(working / 'published.json', saved)
            return report(saved, reused=True)
        state = (
            json.loads(journal.read_bytes())
            if journal.is_file() and not republish
            else {
                'run_id': 'daily-' + uuid4().hex,
                'plan': registry.snapshot(),
                'steps': {},
                'started': started.isoformat(),
            }
        )
        if republish:
            working = working / ('review-' + uuid4().hex)
            journal = working / 'run.json'
            state |= {'publish_only': True, 'run_id': prior['runId']}
        replace_file(journal, state)
        public.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        plan = state['plan']
        previous = {entry['sourceId']: entry for entry in prior.get('sources', [])}
        paths = FeedPaths(
            working / 'erp.json',
            working / 'rules.json',
            working / 'news.json',
            working / 'capture.json',
            include_laws=any(row['source_id'] == 'legalize_law' and row['runnable'] for row in plan['sources']),
        )
        statuses = collect_sources(registry, config, state, journal, previous, datetime.fromisoformat(state['started']))
        cutoff = now().astimezone(KST)
        records = news.approved(config.materials_db)
        replace_file(
            paths.news,
            news.build(registry, config.materials_db, plan=news_plan(plan, statuses), records=records, now=cutoff),
        )
        feeds = capture(config, paths, cutoff)
        if any(value['state'] == 'unavailable' for value in feeds['news'].values()):
            old_law_news = prior.get('feeds', {}).get('news', {}).get('lawgo', {})
            if (
                old_law_news.get('items')
                and paths.include_laws
                and prior.get('configVersion') == plan['config_version']
                and prior.get('configHash') == plan['seed_sha256']
            ):
                feeds['news']['lawgo']['items'] = old_law_news['items']
        reference = prior
        if republish:
            before = sorted(path for path in home.glob('????-??-??/published.json') if path.parent.name < day)
            reference = read_snapshot(before[-1]) if before else {}
        ledger_file = home / 'ledger' / (reference.get('snapshotHash', '') + '.json')
        previous_ledger = json.loads(ledger_file.read_bytes()) if ledger_file.is_file() else {}
        changes, ledger = change_records(plan, records, feeds, day, previous_ledger)
        status = (
            'partial'
            if any(row['stale'] for row in statuses)
            or any(entry['state'] == 'unavailable' for entry in feeds['news'].values())
            else 'ok'
        )
        saved = seal(
            {
                'schemaVersion': 1,
                'dayKst': day,
                'cutoffAt': cutoff.isoformat(),
                'runId': state['run_id'],
                'configVersion': plan['config_version'],
                'configHash': plan['seed_sha256'],
                'status': status,
                'sources': statuses,
                'feeds': feeds,
                'changes': changes,
            }
        )
        replace_file(home / 'ledger' / (saved['snapshotHash'] + '.json'), ledger)
        replace_file(working / 'snapshot.json', saved)
        replace_file(public, saved)
        replace_file(home / day / 'published.json', saved)
        return report(saved, reused=False)
