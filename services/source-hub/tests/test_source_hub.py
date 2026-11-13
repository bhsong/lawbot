import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from lawbot_common.law_scope import SCOPE_FILE
from source_hub import cli
from source_hub.config import Config, load
from source_hub.server import create_app
from source_hub.store import Registry, Run

AUTH = ('fixture-admin', 'fixture-secret')
ORIGIN = 'http://testserver'


@pytest.fixture
def registry(tmp_path):
    return Registry(load(), tmp_path / 'collector.db')


def test_seed_includes_all_agencies_and_unverified_boards(registry):
    sources = {row['source_id']: row for row in registry.snapshot()['sources']}
    assert {row['agency'] for row in sources.values()} == {'lawgo', 'assembly', 'moel', 'mofe', 'nts', 'hometax'}
    assert {'nts-tax-02', 'hometax-errata', 'existing-erp'} <= sources.keys()
    assert {key for key, row in sources.items() if row['agency'] == 'moel' and row['mode'] == 'rss'} == {
        'moel-rss-notice',
        'moel-rss-policy',
        'moel-rss-lawinfo',
    }
    assert all(not row['runnable'] for row in sources.values() if row['verification'] == 'not_tested')


def test_settings_are_hierarchical_audited_and_run_plans_are_frozen(registry):
    law = '근로기준법'
    initial_scope = SCOPE_FILE.read_bytes()
    before = registry.snapshot()
    assert registry.patch('law', law, {'enabled': False}, actor='operator', reason='fixture', version=0) == 1
    assert registry.patch('agency', 'lawgo', {'enabled': False}, actor='operator', reason='fixture', version=1) == 2
    snapshot = registry.snapshot()
    assert all(not row['effective_enabled'] for row in snapshot['sources'] if row['agency'] == 'lawgo')
    assert any(row['runnable'] for row in before['sources'] if row['agency'] == 'lawgo')
    assert registry.patch('agency', 'lawgo', {'enabled': True}, actor='operator', reason='fixture', version=2) == 3
    current = registry.snapshot()
    legalize = next(row for row in current['sources'] if row['source_id'] == 'legalize_law')
    assert not any(row['effective_enabled'] for row in legalize['laws'] if row['law_id'] == law)
    assert any(row['effective_enabled'] for row in legalize['laws'] if row['law_id'] != law)
    assert SCOPE_FILE.read_bytes() == initial_scope
    assert registry.history()[-1] | {'at': None} == {
        'target': law,
        'kind': 'law',
        'at': None,
        'actor': 'operator',
        'old': {'enabled': True},
        'new': {'enabled': False},
        'reason': 'fixture',
        'version': 1,
    }


@pytest.mark.parametrize(
    'fields',
    [
        {'mode': 'api', 'adapter': 'missing'},
        {'source_url': 'https://www.moel.go.kr.example.invalid/test'},
        {'source_url': 'https://user:pw@www.moel.go.kr/test'},
        {'source_url': 'https://www.moel.go.kr/test?api_key=fixture'},
        {'interval_minutes': 0},
    ],
)
def test_invalid_patch_does_not_change_the_registry(registry, fields):
    with pytest.raises(ValueError):
        registry.patch('source', 'moel-rss-notice', fields, actor='operator', reason='fixture', version=0)
    assert registry.snapshot()['config_version'] == 0
    assert registry.history() == []


def test_duplicate_ids_are_rejected():
    config = load().model_dump()
    config['sources'].append(config['sources'][0])
    with pytest.raises(ValueError, match='source_id'):
        Config.model_validate(config)


def test_admin_api_authorization_origin_and_concurrent_settings(registry):
    client = TestClient(create_app(registry, *AUTH, host='testserver'))
    payload = {'version': 0, 'reason': 'fixture', 'changes': {'enabled': False}}
    endpoint = '/api/collector/sources/legalize_law'
    assert client.get('/api/collector/sources').status_code == 401
    assert client.patch(endpoint, json=payload, auth=AUTH).status_code == 403
    assert (
        client.patch(endpoint, json=payload, auth=AUTH, headers={'Origin': 'https://elsewhere.invalid'}).status_code
        == 403
    )
    headers = {'Origin': ORIGIN}
    assert client.patch(endpoint, json=payload, auth=AUTH, headers=headers).json() == {'config_version': 1}
    assert client.patch(endpoint, json=payload, auth=AUTH, headers=headers).status_code == 409
    response = client.get('/api/collector/sources', auth=AUTH)
    assert response.headers['cache-control'] == 'no-store'
    assert AUTH[1] not in response.text
    assert client.get('/api/collector/audit', auth=AUTH).json()[0]['actor'] == AUTH[0]


def test_admin_screen_is_private_and_links_local_assets(registry):
    client = TestClient(create_app(registry, *AUTH, host='testserver'))
    assert client.get('/').status_code == 401
    response = client.get('/', auth=AUTH, headers={'Host': 'elsewhere.invalid'})
    assert response.status_code == 400
    response = client.get('/', auth=AUTH)
    assert response.status_code == 200 and '수집 설정' in response.text
    assert client.get('/admin.js', auth=AUTH).status_code == 200
    assert client.get('/admin.css', auth=AUTH).status_code == 200


def test_cli_validate_dry_run_and_change_share_the_same_registry(tmp_path, capsys):
    db = tmp_path / 'collector.db'
    base = ['--database', str(db)]
    assert cli.main([*base, 'validate']) == 0
    assert not db.exists()
    assert cli.main([*base, 'dry-run']) == 0
    assert not db.exists()
    assert cli.main([*base, 'set', 'group', 'erp', '{"enabled": false}', '--version', '0', '--reason', 'fixture']) == 0
    assert Path(db).exists()
    assert '"config_version": 1' in capsys.readouterr().out
    assert all(
        not row['effective_enabled'] for row in Registry(load(), db).snapshot()['sources'] if row['group'] == 'erp'
    )


def test_new_board_needs_verification_and_duplicate_ids_are_rejected(registry):
    new_board = {
        'group': 'erp',
        'agency': 'moel',
        'adapter': 'moel',
        'mode': 'html',
        'board_name': '합성 게시판',
        'official_homepage': 'https://www.moel.go.kr/',
        'source_url': 'https://www.moel.go.kr/test',
        'category_tags': ['합성'],
        'enabled': True,
        'verification': 'ready',
    }
    assert (
        registry.patch('source', 'moel-fixture', new_board, actor='operator', reason='fixture', version=0, create=True)
        == 1
    )
    row = next(source for source in registry.snapshot()['sources'] if source['source_id'] == 'moel-fixture')
    assert row['effective_enabled'] and row['verification'] == 'not_tested' and not row['runnable']
    with pytest.raises(ValueError, match='source_id'):
        registry.patch('source', 'moel-fixture', new_board, actor='operator', reason='fixture', version=1, create=True)


def test_registry_refuses_a_foreign_database_without_writing(registry):
    from contextlib import closing

    with closing(sqlite3.connect(registry.database)) as conn:
        conn.execute('CREATE TABLE corpus (body TEXT)')
        conn.commit()
    initial = registry.database.read_bytes()
    with pytest.raises(ValueError, match='수집 설정 DB'):
        registry.patch('group', 'law', {'enabled': False}, actor='operator', reason='fixture', version=0)
    assert registry.database.read_bytes() == initial
    with pytest.raises(ValueError, match='수집 설정 DB'):
        registry.snapshot()


def test_run_status_preserves_last_success_after_failed_run_and_disable(registry):
    first = Run(
        run_id='first',
        source_id='legalize_law',
        config_version=0,
        started_at='2026-11-12T06:30:00+09:00',
        finished_at='2026-11-12T06:32:00+09:00',
        outcome='ok',
        new_count=0,
        modified_count=0,
    )
    second = Run(
        run_id='second',
        source_id='legalize_law',
        config_version=0,
        started_at='2026-11-13T06:30:00+09:00',
        finished_at='2026-11-13T06:32:00+09:00',
        outcome='error',
        error='timeout',
    )
    registry.record_run(first)
    registry.record_run(second)
    registry.record_run(second)
    registry.patch('source', 'legalize_law', {'enabled': False}, actor='operator', reason='fixture', version=0)
    source = next(row for row in registry.snapshot()['sources'] if row['source_id'] == 'legalize_law')
    assert source['status'] == 'disabled'
    assert source['last_attempt_at'] == '2026-11-13T06:30:00+09:00'
    assert source['last_success_at'] == '2026-11-12T06:32:00+09:00'
    assert source['new_count'] is None and source['error'] == 'timeout'
    assert [row['run_id'] for row in registry.runs('legalize_law')] == ['second', 'first']


@contextmanager
def admin_browser(registry, *serve_args):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    env = dict(os.environ, SOURCE_HUB_ADMIN_USER=AUTH[0], SOURCE_HUB_ADMIN_PASSWORD=AUTH[1])
    command = [
        sys.executable,
        '-m',
        'source_hub.cli',
        '--database',
        str(registry.database),
        'serve',
        '--port',
        str(port),
        *serve_args,
    ]
    with subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as server:
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline and server.poll() is None:
                    try:
                        if client.get(url).status_code == 401:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail('Private collector administrator did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(http_credentials={'username': AUTH[0], 'password': AUTH[1]})
                    page.set_default_timeout(5000)
                    page.route(
                        '**/*',
                        lambda route: route.continue_() if route.request.url.startswith(url + '/') else route.abort(),
                    )
                    page.goto(url)
                    yield page
                finally:
                    browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


@pytest.mark.e2e_stub
def test_private_admin_browser_saves_settings_to_the_cli_registry(registry):
    with admin_browser(registry) as page:
        page.get_by_role('button', name='ERP 켜짐', exact=True).click()
        dialog = page.get_by_role('dialog')
        expect(dialog.get_by_text('과거 공개 자료', exact=False)).to_be_visible()
        dialog.get_by_label('변경 사유').fill('브라우저 대역 설정')
        dialog.get_by_role('button', name='저장').click()
        expect(page.get_by_role('button', name='ERP 꺼짐', exact=True)).to_be_visible()
        page.get_by_label('기관', exact=True).select_option('hometax')
        expect(page.get_by_role('row').filter(has_text='정오표·변경공지')).to_be_visible()
        expect(page.get_by_role('row').filter(has_text='법률·시행령·시행규칙')).to_have_count(0)
        expect(page.get_by_text('브라우저 대역 설정', exact=False)).to_be_visible()
    assert registry.snapshot()['groups']['erp'] is False
    assert registry.history()[0]['actor'] == AUTH[0]
