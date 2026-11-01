"""실행기: 수집기를 차례로 돌리고, 결과를 이벤트 파일 + 한 트랜잭션으로 저장한다 (원 계획서 5.5·5.6).

- 한 수집기의 예외는 잡아서 runs.summary_json에 남기고 다음 수집기로 넘어간다.
- 연속 3회 실패하면 collector_failing 이벤트를 한 번 남긴다 (3단계의 "수집이 멈췄다" 알림용).
- 같은 날 여러 번 실행해도 이벤트는 내용 기반 event_id로 한 번만 저장된다.
"""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from law_monitor.collectors.base import Collector, CollectResult, RunContext
from law_monitor.collectors.legalize_git import LegalizeGitCollector, default_collectors
from law_monitor.config import Config
from law_monitor.events import EventStore, make_event_id
from law_monitor.paths import Paths
from law_monitor.state import State

KST = timezone(timedelta(hours=9), 'KST')
FAILING_AFTER = 3
log = logging.getLogger('law_monitor')


class BootstrapRequired(RuntimeError):
    pass


class AlreadyBootstrapped(RuntimeError):
    pass


@dataclass
class RunSummary:
    run_id: str
    mode: str
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    line: str = ''


class Monitor:
    def __init__(
        self,
        paths: Paths,
        config: Config,
        *,
        collectors: list[Collector] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.paths, self.config = paths, config
        self.collectors = collectors if collectors is not None else default_collectors(config)
        self.now = now or (lambda: datetime.now(KST))
        self.state = State(paths.state_db)

    def _select(self, only: str | None) -> list[Collector]:
        if only is None:
            return list(self.collectors)
        chosen = [c for c in self.collectors if c.name == only]
        if not chosen:
            raise ValueError(f'없는 수집기: {only} (있는 것: {", ".join(c.name for c in self.collectors)})')
        return chosen

    def bootstrap(self, only: str | None = None, force: bool = False) -> RunSummary:
        """현재 상태를 기준점으로 저장만 한다 (이벤트 없음)."""
        chosen = self._select(only)
        done = [c.name for c in chosen if c.has_baseline(self.state)]
        if done and not force:
            raise AlreadyBootstrapped(f'이미 기준점이 있습니다: {", ".join(done)}. 다시 잡으려면 --force')
        return self._run(chosen, 'bootstrap')

    def collect(self, only: str | None = None) -> RunSummary:
        chosen = self._select(only)
        missing = [c.name for c in chosen if not c.has_baseline(self.state)]
        if missing:
            raise BootstrapRequired(f'기준점이 없습니다: {", ".join(missing)}. 먼저 law-monitor bootstrap을 실행하세요')
        return self._run(chosen, 'normal')

    def replay(self, repo_key: str, since: str, until: str) -> list[dict]:
        """since..until 구간의 이벤트를 data/replay/<since>_<until>/에 쓴다. 상태는 바꾸지 않는다."""
        collector = next(
            (c for c in self.collectors if isinstance(c, LegalizeGitCollector) and c.repo_key == repo_key), None
        )
        if collector is None:
            raise ValueError(f'replay할 수 있는 저장소가 아닙니다: {repo_key}')
        ctx = RunContext(self.config, self.state, self.paths, 'replay', 'replay', self.now)
        with _file_log(self.paths, self.now()):
            result = collector.replay(ctx, since, until)
            store = EventStore(self.paths.home, self.paths.replay / f'{since[:8]}_{until[:8]}', dated=False)
            for ev in result.events:
                store.write(ev, result.diffs.get(ev['event_id']))
            log.info(
                'replay %s %s..%s events=%d → %s',
                repo_key,
                since[:8],
                until[:8],
                len(result.events),
                self.paths.rel(store.base),
            )
        return result.events

    def _run(self, chosen: list[Collector], mode: str) -> RunSummary:
        started = self.now()
        run_id = f'{started.strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:6]}'
        summary = RunSummary(run_id, mode)
        with _file_log(self.paths, started):
            self.state.start_run(run_id, mode, started.isoformat(timespec='seconds'))
            ctx = RunContext(self.config, self.state, self.paths, mode, run_id, self.now)
            results: dict[str, CollectResult] = {}
            per: dict[str, dict] = {}
            for c in chosen:
                try:
                    results[c.name] = c.collect(ctx)
                    summary.ok.append(c.name)
                    per[c.name] = {'status': 'ok', 'events': len(results[c.name].events)}
                except Exception as e:
                    log.exception('%s 실패', c.name)
                    summary.failed[c.name] = f'{type(e).__name__}: {e}'
                    per[c.name] = {'status': 'failed', 'error': summary.failed[c.name]}

            events, diffs = {}, {}
            if mode != 'bootstrap':
                for r in results.values():
                    for ev in r.events:
                        if ev['event_id'] not in events and not self.state.event_exists(ev['event_id']):
                            events[ev['event_id']] = ev
                            if ev['event_id'] in r.diffs:
                                diffs[ev['event_id']] = r.diffs[ev['event_id']]
            for name, error in summary.failed.items():
                n = self.state.consecutive_failures(name) + 1
                per[name]['consecutive_failures'] = n
                if n == FAILING_AFTER:
                    ev = self._failing_event(name, error, n, run_id)
                    events[ev['event_id']] = ev

            store = EventStore(self.paths.home, self.paths.events)
            paths = {eid: self.paths.rel(store.write(ev, diffs.get(eid))) for eid, ev in events.items()}
            summary.events = list(events.values())
            failed = f'({",".join(summary.failed)})' if summary.failed else ''
            summary.line = (
                f'collectors={len(chosen)} ok={len(summary.ok)} failed={len(summary.failed)}{failed} '
                f'events={len(summary.events)}'
            )
            status = 'ok' if not summary.failed else ('partial' if summary.ok else 'failed')
            finished = self.now().isoformat(timespec='seconds')
            self.state.apply(
                cursors={k: v for r in results.values() for k, v in r.cursors.items()},
                items=[it for r in results.values() for it in r.items],
                removed=[x for r in results.values() for x in r.removed],
                events=summary.events,
                event_paths=paths,
                replace_sources=[s for r in results.values() for s in r.replace_sources],
                now=finished,
                run=(
                    run_id,
                    finished,
                    status,
                    json.dumps({'collectors': per, 'events': len(events)}, ensure_ascii=False),
                ),
            )
            log.info('%s %s', mode, summary.line)
        return summary

    def _failing_event(self, name: str, error: str, n: int, run_id: str) -> dict:
        return {
            'event_id': make_event_id(name, 'collector_failing', run_id),
            'detected_at': self.now().isoformat(timespec='seconds'),
            'category': None,
            'source': name,
            'change_type': 'collector_failing',
            'target': {'item_id': name},
            'dates': {},
            'old': None,
            'new': None,
            'diff': {'kind': 'none', 'path': None, 'chunks': []},
            'context': {'error': error, 'consecutive_failures': n, 'run_id': run_id},
        }


class _file_log:
    """logs/collector-YYYYMMDD.log에 이 실행의 로그를 붙인다 (마지막 줄이 실행 요약)."""

    def __init__(self, paths: Paths, when: datetime):
        self.path = paths.logs / f'collector-{when.strftime("%Y%m%d")}.log'

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = logging.FileHandler(self.path, encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        log.addHandler(self.handler)
        if log.level == logging.NOTSET or log.level > logging.INFO:
            log.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        log.removeHandler(self.handler)
        self.handler.close()
        return False
