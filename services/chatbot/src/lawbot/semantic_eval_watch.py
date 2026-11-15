"""Monitor the shared server and record only operational metadata during a live evaluation."""

import json
import os
import re
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import numpy as np

from lawbot.api_client import ModelServerError
from lawbot.exact_cache import canonical
from lawbot_common.api import HEALTH_PATH, HealthResponse

KST = ZoneInfo('Asia/Seoul')
LOG_FIELDS = {'ts', 'status', 'error_type', 'total_ms'}
STOP_ERRORS = {'model_server_busy', 'model_server_timeout'}
JSON_TOKEN = re.compile(rb'"(?:[^"\\]|\\.)*"|[{}\[\]:,]|[^{}\[\]:,"\s]+')


class EvaluationStopped(RuntimeError):
    """Stop immediately and preserve the current phase outputs."""


def append_record(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(canonical(data) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def log_fields(line: bytes):
    """Decode only the four allowed top-level scalars; skip every other value."""
    depth = 0
    key_position = False
    selected = None
    after_colon = False
    fields = {}
    for match in JSON_TOKEN.finditer(line):
        token = match[0]
        if depth == 1:
            if token == b',':
                key_position = True
                selected = None
            elif key_position and token.startswith(b'"'):
                key = json.loads(token)
                selected = key if key in LOG_FIELDS else None
                key_position = after_colon = False
            elif token == b':':
                after_colon = True
            elif selected and after_colon and token not in (b'{', b'['):
                fields[selected] = json.loads(token)
                selected = None
        if token in (b'{', b'['):
            depth += 1
            if depth == 1:
                key_position = True
        elif token in (b'}', b']'):
            depth -= 1
    return fields


class OperationsLogs:
    def __init__(self, directory: Path, started: datetime):
        self.directory = directory
        self.started = started
        self.offsets: dict[Path, int] = {}
        self.pending = []

    def read(self):
        pending, self.pending = self.pending, []
        yield from pending
        for path in sorted(self.directory.glob('requests-????-??.jsonl')):
            if path.name < f'requests-{self.started - timedelta(days=7):%Y-%m}.jsonl':
                continue
            with path.open('rb') as stream:
                stream.seek(self.offsets.get(path, 0))
                while line := stream.readline():
                    if not line.endswith(b'\n'):
                        break
                    self.offsets[path] = stream.tell()
                    try:
                        fields = log_fields(line)
                        ts = datetime.fromisoformat(fields.get('ts', ''))
                        if ts.tzinfo is not None:
                            yield ts, fields
                    except ValueError, TypeError:
                        continue

    def baseline(self):
        lower = self.started - timedelta(days=7)
        durations = []
        for ts, record in self.read():
            if ts >= self.started:
                self.pending.append((ts, record))
            elif (
                ts >= lower
                and record.get('status') == 'ok'
                and record.get('error_type') is None
                and type(record.get('total_ms')) in (int, float)
            ):
                durations.append(record['total_ms'])
        values = np.percentile(durations, [50, 95]).tolist() if len(durations) >= 20 else [None, None]
        return {'requests': len(durations), 'p50_ms': values[0], 'p95_ms': values[1]}


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout


def memory_sample():
    pressure = int(command('sysctl', '-n', 'kern.memorystatus_vm_pressure_level'))
    free = re.search(r'System-wide memory free percentage:\s*(\d+)%', command('memory_pressure'))
    swap = re.search(r'used\s*=\s*([\d.]+)([MG])', command('sysctl', '-n', 'vm.swapusage'))
    if free is None or swap is None:
        raise ValueError('memory counters were absent')
    scale = 1024 if swap[2] == 'G' else 1
    return {'pressure': pressure, 'free_percent': int(free[1]), 'swap_mib': float(swap[1]) * scale}


def memory_state(sample, baseline):
    growth = sample['swap_mib'] - baseline['swap_mib']
    if sample['pressure'] == 4 or sample['free_percent'] < 10 or growth > 3 * 1024:
        return 'pause'
    if sample['pressure'] <= 2 and sample['free_percent'] >= 15 and growth <= 3 * 1024:
        return 'ready'
    return 'watch'


class LiveMonitor:
    def __init__(self, client, directory, phase, ops_dir, expected_models, *, baseline=None):
        self.client = client
        self.path = directory / 'progress.jsonl'
        self.phase = phase
        self.expected_models = expected_models
        self.started = datetime.now(KST)
        existing = [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.is_file() else []
        original = next((row for row in existing if row['event'] == 'baseline'), baseline)
        when = datetime.fromisoformat(original['ts']) if original else self.started
        # Start each invocation at the last durable progress timestamp to cover a crash or phase transition.
        cursor = datetime.fromisoformat(existing[-1]['ts']) if existing else when
        self.ops = OperationsLogs(ops_dir, cursor)
        try:
            ops_baseline = self.ops.baseline()
        except OSError as exc:
            ops_baseline = {'requests': 0, 'p50_ms': None, 'p95_ms': None}
            self.record('ambiguous_operations_log', error=type(exc).__name__)

        self.memory = original['memory'] if original else memory_sample()
        self.p95 = original['operations']['p95_ms'] if original else ops_baseline['p95_ms']
        self.slow = deque()
        self.memory_pauses = deque(
            datetime.fromisoformat(row['ts'])
            for row in existing
            if row['event'] == 'pause' and row['cause'] == 'memory'
        )
        self.failures = deque(
            datetime.fromisoformat(row['ts']) for row in existing if row['event'] == 'request_failure'
        )
        self.rerank_times = [row['ms'] for row in existing if row['event'] == 'rerank_baseline_sample'][:20]
        self.next_memory = self.next_health = 0.0
        self.health_since = None
        self.pending = None
        config = next(
            (
                row['config']
                for row in reversed(existing)
                if row['event'] in ('phase_start', 'phase_complete') and 'config' in row
            ),
            {},
        )
        self.commit = config.get('server_commit')
        self.model_rows = config.get('model_revisions')
        self.pid = self.listener_pid()
        if original is None:
            self.record('baseline', operations=ops_baseline, memory=self.memory)
        self.record('monitor_started', memory=self.read_memory())

    def record(self, event, **details):
        append_record(self.path, {'ts': datetime.now(KST).isoformat(), 'phase': self.phase, 'event': event} | details)

    def listener_pid(self):
        port = urlsplit(self.client.url).port
        if port is None:
            return None
        try:
            output = command('lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t').splitlines()
        except subprocess.SubprocessError, OSError:
            return None
        return int(output[0]) if len(output) == 1 and output[0].isdigit() else None

    def stop(self, cause, **details):
        self.record('stop', cause=cause, **details)
        raise EvaluationStopped(cause)

    def read_operations(self):
        try:
            yield from self.ops.read()
        except OSError as exc:
            self.record('ambiguous_operations_log', error=type(exc).__name__)

    def check_operations(self):
        now = datetime.now(KST)
        for ts, fields in self.read_operations():
            if ts < self.ops.started:
                continue
            error = fields.get('error_type')
            if error in STOP_ERRORS:
                self.stop('production_model_error', operation_ts=ts.isoformat(), error_type=error)
            duration = fields.get('total_ms')
            if (
                self.p95 is not None
                and fields.get('status') == 'ok'
                and not error
                and ts >= now - timedelta(minutes=30)
                and type(duration) in (float, int)
                and duration > 1.5 * self.p95
            ):
                self.slow.append((ts, duration))
        while self.slow and self.slow[0][0] < now - timedelta(minutes=30):
            self.slow.popleft()

    def health(self):
        if self.pid is not None:
            try:
                os.kill(self.pid, 0)
            except ProcessLookupError:
                self.stop('model_server_process_gone')
        try:
            health = self.client._parsed(HealthResponse, 'GET', HEALTH_PATH, timeout=5)
            if not health.ready:
                return False
            if not health.commit or (self.commit is not None and health.commit != self.commit):
                self.stop('server_commit_changed_or_missing', previous=self.commit, current=health.commit)
            models = self.client.models(timeout=5)
        except ConnectionError, TimeoutError, ModelServerError:
            return False
        current = {f'{model["type"]}/{model["id"]}': model['revision'] for model in models if model['loaded']}
        revisions = {f'{model["type"]}/{model["id"]}': model['revision'] for model in models}
        if any(revisions.get(key) != revision for key, revision in self.expected_models.items()):
            self.stop('model_revision_changed_or_missing', expected=self.expected_models, current=revisions)
        if self.model_rows is not None and any(revisions.get(key) != value for key, value in self.model_rows.items()):
            self.stop('model_revision_changed', expected=self.model_rows, current=revisions)
        if self.expected_models.keys() - current.keys():
            return False
        self.commit, self.model_rows = health.commit, revisions
        return True

    def ready(self):
        while not self.health():
            self.check_operations()
            now = time.monotonic()
            if self.health_since is None:
                self.health_since = now
                self.record('health_wait')
            elif now - self.health_since >= 1800:
                self.stop('health_not_ready_30m')
            time.sleep(30)
        if self.health_since is not None:
            self.record('health_ready')
        self.health_since = None
        self.next_health = time.monotonic() + 60

    def rest(self, seconds, *, memory_pause=False):
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            self.check_operations()
            if time.monotonic() >= self.next_health:
                self.ready()
            if not memory_pause and time.monotonic() >= self.next_memory:
                self.check_memory()
            time.sleep(min(30, remaining))

    def read_memory(self):
        try:
            return memory_sample()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.record('ambiguous_memory_sample', error=type(exc).__name__)
            return None

    def check_memory(self):
        self.next_memory = time.monotonic() + 300
        sample = self.read_memory()
        if sample is None:
            return
        self.record('memory_sample', current=sample, baseline=self.memory)
        if memory_state(sample, self.memory) == 'pause':
            now = datetime.now(KST)
            while self.memory_pauses and now - self.memory_pauses[0] > timedelta(hours=2):
                self.memory_pauses.popleft()
            self.memory_pauses.append(now)
            self.record('pause', cause='memory', current=sample, baseline=self.memory)
            if len(self.memory_pauses) >= 3:
                self.stop('memory_pauses_3_in_2h', current=sample, baseline=self.memory)
            start = time.monotonic()
            while memory_state(sample, self.memory) != 'ready':
                self.rest(600, memory_pause=True)
                sample = self.read_memory() or sample
                self.record('memory_recheck', current=sample, baseline=self.memory)
                if time.monotonic() - start >= 3600 and memory_state(sample, self.memory) != 'ready':
                    self.stop('memory_pause_60m', current=sample, baseline=self.memory)
            self.record('resume', cause='memory', current=sample, baseline=self.memory)
        self.next_memory = time.monotonic() + 300

    def before_request(self):
        self.check_operations()
        if self.pending:
            cause, wait = self.pending
            self.pending = None
            self.record('pause', cause=cause)
            self.rest(wait)
            self.ready()
            self.record('resume', cause=cause)
        consecutive = 0
        while len(self.slow) >= 2:
            slow = [(ts.isoformat(), duration) for ts, duration in self.slow]
            self.slow.clear()
            self.record('pause', cause='production_latency', samples=slow, baseline_p95_ms=self.p95)
            self.rest(1800)
            consecutive += 1
            if consecutive >= 3 and len(self.slow) >= 2:
                self.stop('production_latency_persists', baseline_p95_ms=self.p95)
            self.record('resume', cause='production_latency')
        now = datetime.now(KST)
        closing, opening = (
            now.replace(hour=6, minute=15, second=0, microsecond=0),
            now.replace(hour=6, minute=55, second=0, microsecond=0),
        )
        if closing <= now < opening:
            self.record('pause', cause='corpus_update_window')
            self.rest((opening - now).total_seconds())
            self.ready()
            self.record('resume', cause='corpus_update_window')
        if time.monotonic() >= self.next_memory:
            self.check_memory()
        if time.monotonic() >= self.next_health:
            self.ready()

    def request_failure(self, kind, error):
        now = datetime.now(KST)
        while self.failures and now - self.failures[0] > timedelta(hours=1):
            self.failures.popleft()
        self.failures.append(now)
        self.record('request_failure', kind=kind, error=type(error).__name__, status=getattr(error, 'status', None))
        self.pending = ('request_failure', 600)

    def after_item(self):
        self.check_operations()
        now = datetime.now(KST)
        while self.failures and now - self.failures[0] > timedelta(hours=1):
            self.failures.popleft()
        if len(self.failures) >= 3:
            self.stop('requests_failed_3_in_1h', failures=[ts.isoformat() for ts in self.failures])

    def rerank_timing(self, duration_ms):
        if len(self.rerank_times) < 20:
            self.rerank_times.append(duration_ms)
            self.record('rerank_baseline_sample', ms=duration_ms)
            if len(self.rerank_times) == 20:
                self.record('rerank_baseline', calls=20, median_ms=median(self.rerank_times))
