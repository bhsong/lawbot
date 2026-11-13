"""Standalone local servers; no chatbot, monitor or model-server imports."""

import contextlib
import datetime
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

SERVICE = Path(__file__).resolve().parents[1]
FRONTEND = SERVICE / 'frontend'
ROOT = SERVICE.parents[1]


@contextlib.contextmanager
def serve(tmp, *, development, database=None, erp_feed=None, news_feed=None, rules_feed=None):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    node = shutil.which('node')
    assert node, 'Node is required for Vite test servers'
    args = [node, str(FRONTEND / 'node_modules/vite/bin/vite.js')]
    if not development:
        assert (FRONTEND / 'dist/index.html').is_file(), 'Build the independent calendar frontend first'
        args.append('preview')
    args.extend(['--host', '127.0.0.1', '--port', str(port), '--strictPort'])
    if database is not None:
        args = [
            sys.executable,
            '-m',
            'law_calendar.server',
            '--database',
            str(database),
            '--frontend',
            str(FRONTEND / 'dist'),
            '--port',
            str(port),
            *(['--erp-feed', str(erp_feed)] if erp_feed is not None else []),
            *(['--news-feed', str(news_feed)] if news_feed is not None else []),
            *(['--erp-rules-feed', str(rules_feed)] if rules_feed is not None else []),
        ]
    env = {key: value for key, value in os.environ.items() if not key.startswith(('LAWBOT_', 'LAW_MONITOR_'))}
    log = tmp / 'calendar-server.log'
    with log.open('w') as out:
        proc = subprocess.Popen(args, cwd=FRONTEND, env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            assert proc.poll() is None, log.read_text()
            try:
                if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, 'Calendar server startup timed out\n' + log.read_text()
            time.sleep(0.1)
        yield url, log
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def keep_on_failure(request, log, page):
    report = getattr(request.node, 'rep_call', None)
    if report is not None and report.failed:
        name = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d-%H%M%S') + '-' + request.node.name
        dest = ROOT / 'results/e2e_stub' / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log, dest / log.name)
        page.screenshot(path=str(dest / 'screen.png'), full_page=True)
        (dest / 'page.html').write_text(page.content(), encoding='utf-8')
