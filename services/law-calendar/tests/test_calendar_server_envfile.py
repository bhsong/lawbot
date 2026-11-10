"""Exercise calendar CLI environment loading before protected routes are built."""

import secrets
import sys

import pytest
from fastapi.testclient import TestClient

from law_calendar import server
from test_calendar_workflow import approval, import_one
from test_calendar_workflow import workflow as workflow

TOKEN_VAR = 'LAWBOT_CALENDAR_ADMIN_TOKEN'


@pytest.mark.parametrize('shell_token', [False, True], ids=['file-only', 'shell-overrides'])
def test_calendar_main_loads_service_env_for_review(workflow, tmp_path, monkeypatch, capsys, shell_token):
    database = workflow[0]
    decision = approval(database, import_one(workflow))
    service = tmp_path / 'calendar-service'
    module = service / 'src' / 'law_calendar' / 'server.py'
    module.parent.mkdir(parents=True)
    envfile = service / '.env'
    file_token = secrets.token_hex(32)
    token = secrets.token_hex(32) if shell_token else file_token
    envfile.write_text(f'export {TOKEN_VAR}={file_token}  # service credential\n', encoding='utf-8')
    envfile.chmod(0o600 if shell_token else 0o644)
    monkeypatch.delenv('LAWBOT_ENV_FILE', raising=False)
    # Track the key for teardown even when startup supplies it from the file.
    monkeypatch.setenv(TOKEN_VAR, token)
    if not shell_token:
        monkeypatch.delenv(TOKEN_VAR)
    monkeypatch.setattr(server, '__file__', str(module))
    monkeypatch.setattr(sys, 'argv', ['law-calendar', '--database', str(database), '--port', '8000'])
    monkeypatch.chdir(tmp_path)

    def run(app, **_):
        with TestClient(app) as client:
            hold = client.get('/api/law-holds').json()['items'][0]
            path = f'/api/admin/law-holds/effective/{hold["id"]}'
            changes = [change for change in decision['changes'] if change['id'] == hold['id']]
            payload = {**decision, 'changes': changes, 'reviewer': 'fixture-admin'}
            if shell_token:
                denied = client.post(path, json=payload, headers={'Authorization': f'Bearer {file_token}'})
                assert denied.status_code == 403
            response = client.post(path, json=payload, headers={'Authorization': f'Bearer {token}'})
            assert response.status_code == 200

    monkeypatch.setattr(server.uvicorn, 'run', run)
    server.main()
    captured = capsys.readouterr().out
    if file_token in captured or token in captured:
        pytest.fail('The calendar startup log contains the service token', pytrace=False)
    if shell_token:
        assert 'chmod 600' not in captured
    else:
        assert TOKEN_VAR in captured and 'chmod 600' in captured
