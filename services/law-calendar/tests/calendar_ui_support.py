"""Standalone local servers; no chatbot, monitor or model-server imports."""

import contextlib
import datetime
import os
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import httpx

from law_calendar.export import main as export_main
from law_calendar.export import write_site
from law_calendar.server import create_app

SERVICE = Path(__file__).resolve().parents[1]
FRONTEND = SERVICE / 'frontend'
ROOT = SERVICE.parents[1]


def export_calendar(tmp, database, *, daily_feed=None, **feeds):
    site = tmp / 'site'
    dist = FRONTEND / 'dist'
    assert (dist / 'index.html').is_file(), 'Build the independent calendar frontend first'
    # Playwright's synchronous facade already has an event loop on the test thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        if daily_feed is None:
            executor.submit(write_site, create_app(database, **feeds), dist, site).result(timeout=30)
        else:
            args = [
                '--database',
                str(database),
                '--erp-daily-feed',
                str(daily_feed),
                '--frontend-dist',
                str(dist),
                '--out',
                str(site),
            ]
            executor.submit(export_main, args).result(timeout=30)
    return site


@contextlib.contextmanager
def serve(tmp, *, development, database=None, site=None, **feeds):
    if not development:
        assert database is not None
        with serve_static(tmp, export_calendar(tmp, database, **feeds)) as server:
            yield server
        return

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    node = shutil.which('node')
    assert node, 'Node is required for Vite test servers'
    args = [node, str(FRONTEND / 'node_modules/vite/bin/vite.js')]
    args.extend(['--host', '127.0.0.1', '--port', str(port), '--strictPort'])
    env = {key: value for key, value in os.environ.items() if not key.startswith(('LAWBOT_', 'LAW_MONITOR_'))}
    if site is not None:
        env['LAW_CALENDAR_SITE'] = str(site)
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


@contextlib.contextmanager
def serve_static(tmp, site):
    rules = {}
    for line in (site / '_headers').read_text(encoding='utf-8').splitlines():
        if not line.startswith(' '):
            pattern = line
            rules[pattern] = []
        else:
            key, value = line.strip().split(': ', 1)
            rules[pattern].append((key, value))

    log = tmp / 'static-calendar.log'
    with log.open('w', encoding='utf-8', buffering=1) as stream:

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(site), **kwargs)

            def end_headers(self):
                for pattern, headers in rules.items():
                    if fnmatchcase(urlsplit(self.path).path, pattern):
                        for key, value in headers:
                            self.send_header(key, value)
                super().end_headers()

            def log_message(self, format, *args):
                stream.write(format % args + '\n')

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f'http://127.0.0.1:{server.server_port}', log
            finally:
                server.shutdown()
                thread.join(timeout=5)


def keep_on_failure(request, log, page):
    report = getattr(request.node, 'rep_call', None)
    if report is not None and report.failed:
        name = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d-%H%M%S') + '-' + request.node.name
        dest = ROOT / 'results/e2e_stub' / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log, dest / log.name)
        page.screenshot(path=str(dest / 'screen.png'), full_page=True)
        (dest / 'page.html').write_text(page.content(), encoding='utf-8')
