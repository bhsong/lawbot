import os
import plistlib
import subprocess

import pytest

from lawbot.paths import ROOT

SCRIPT = ROOT / 'deploy/model-hours/lawbot-model-hours'
MODEL, GENERATION = 'com.lawbot.model-server', 'local.qwen27b'


@pytest.fixture
def schedule(tmp_path):
    state = tmp_path / 'state'
    prod = tmp_path / 'fake worktree'
    state.mkdir()
    prod.mkdir()
    calls = tmp_path / 'calls'
    calls.touch()
    launchctl = tmp_path / 'launchctl'
    uv = tmp_path / 'uv'
    launchctl.write_text(
        """#!/bin/sh
printf '%s\n' "$*" >> "$CALLS"
case "$1" in
    print)
        case "$2" in
            gui/*/*) [ -f "$STATE/${2##*/}" ] && printf 'state = running\n' ;;
            gui/*) exit 0 ;;
            *) exit 2 ;;
        esac ;;
    bootstrap)
        label=${3##*/}
        : > "$STATE/${label%.plist}" ;;
    bootout)
        [ -f "$STATE/${2##*/}" ] || exit 3
        mv "$STATE/${2##*/}" "$STATE/stopped-${2##*/}" ;;
    *) exit 2 ;;
esac
""",
        encoding='utf-8',
    )
    uv.write_text(
        """#!/bin/sh
printf '%s\n' "uv $*" >> "$CALLS"
[ "$PWD" = "$FAKE_PROD" ] || exit 2
printf 'private deploy check output\n' >&2
case "$UV_EXIT" in
    hang) exec sleep 30 ;;
    retry)
        [ -f "$STATE/probed" ] && exit 0
        : > "$STATE/probed"
        exit 1 ;;
esac
exit "$UV_EXIT"
""",
        encoding='utf-8',
    )
    for exe in (launchctl, uv):
        exe.chmod(0o700)

    def run(command, *args, uv_exit=0):
        return subprocess.run(
            ['/bin/sh', str(SCRIPT), command, '--prod-dir', str(prod), *args],
            env={
                'HOME': str(tmp_path),
                'PATH': f'{tmp_path}:/usr/bin:/bin',
                'CALLS': str(calls),
                'STATE': str(state),
                'FAKE_PROD': str(prod),
                'UV_EXIT': str(uv_exit),
            },
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )

    return run, state, calls


def test_apply_at_open_then_at_close(schedule):
    run, state, calls = schedule
    (state / MODEL).touch()
    result = run('apply', '--now', '06:00')
    assert result.returncode == 0, result.stderr
    domain = f'gui/{os.getuid()}'
    assert [line for line in calls.read_text().splitlines() if line.startswith('bootstrap ')] == [
        f'bootstrap {domain} {state.parent}/Library/LaunchAgents/{GENERATION}.plist'
    ]
    assert 'uv run python -m lawbot.check_deploy' in calls.read_text()
    assert 'private deploy check output' not in result.stdout + result.stderr
    result = run('apply', '--now', '21:30')
    assert result.returncode == 0, result.stderr
    assert not (state / MODEL).exists() and not (state / GENERATION).exists()
    assert [line for line in calls.read_text().splitlines() if line.startswith('bootout ')] == [
        f'bootout {domain}/{MODEL}',
        f'bootout {domain}/{GENERATION}',
    ]
    assert calls.read_text().count('uv ') == 1


def test_stop_succeeds_if_already_unloaded(schedule):
    run, _, calls = schedule
    result = run('stop')
    assert result.returncode == 0, result.stderr
    assert 'uv ' not in calls.read_text()


def test_start_reports_failure_at_deadline_without_printing_probe_output(schedule):
    run, _, calls = schedule
    result = run('start', '--now', '06:01', '--deadline', '06:01', uv_exit=1)
    assert result.returncode != 0
    assert calls.read_text().count('uv run python -m lawbot.check_deploy') == 1
    assert 'not ready' in result.stderr and '06:01' in result.stderr
    assert 'private deploy check output' not in result.stdout + result.stderr


def test_start_caps_a_running_check_at_the_deadline(schedule):
    run, _, calls = schedule
    result = run('start', '--now', '06:24:58', uv_exit='hang')
    assert result.returncode == 1 and 'not ready' in result.stderr
    assert calls.read_text().count('uv run python -m lawbot.check_deploy') == 1


def test_start_retries_a_failed_check_before_the_deadline(schedule):
    run, _, calls = schedule
    result = run('start', '--now', '06:20', uv_exit='retry')
    assert result.returncode == 0, result.stderr
    assert calls.read_text().count('uv run python -m lawbot.check_deploy') == 2


def test_status_only_reports_registration_and_running_state(schedule):
    run, state, calls = schedule
    (state / MODEL).touch()
    result = run('status')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f'{MODEL}: registered, running', f'{GENERATION}: not registered']
    assert all(line.startswith('print ') for line in calls.read_text().splitlines())


def test_launch_agent_runs_apply_at_login_open_and_close():
    path = ROOT / 'deploy/launchd/com.lawbot.model-hours.plist.example'
    config = plistlib.loads(path.read_bytes())
    assert config['RunAtLoad'] is True and not config.get('KeepAlive')
    assert config['ProgramArguments'] == [f'__PROD_DIR__/{SCRIPT.relative_to(ROOT)}', 'apply']
    assert config['StartCalendarInterval'] == [{'Hour': 6, 'Minute': 0}, {'Hour': 21, 'Minute': 30}]
    assert config['StandardOutPath'] == config['StandardErrorPath'] == '__LOG_DIR__/model-hours.log'
