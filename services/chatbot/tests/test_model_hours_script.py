import os
import plistlib
import subprocess
import time
from contextlib import suppress
from datetime import datetime
from signal import SIGKILL, SIGTERM

import pytest

from lawbot.paths import ROOT

SCRIPT = ROOT / 'deploy/model-hours/lawbot-model-hours'
MODEL, GENERATION = 'com.lawbot.model-server', 'local.qwen27b'
PROBE = 'uv run --frozen --no-sync python -m lawbot.check_deploy'


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
    date = tmp_path / 'date'
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
        [ "$FAIL_STOP" != "${2##*/}" ] || exit 0
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
    hang|leave-child)
        if [ "$UV_EXIT" = hang ]; then trap '' TERM; fi
        (trap '' TERM; exec sleep 30) &
        child=$!
        printf '%s %s\\n' "$$" "$child" > "$STATE/probe-pids"
        wait "$child" ;;
    retry|midnight)
        date +%s >> "$STATE/probe-times"
        [ -f "$STATE/probed" ] && exit 0
        : > "$STATE/probed"
        exit 1 ;;
esac
exit "$UV_EXIT"
""",
        encoding='utf-8',
    )
    date.write_text(
        """#!/bin/sh
if [ "$1" = +%s ] && [ "$UV_EXIT" = midnight ]; then
    if [ -f "$STATE/probed" ]; then printf '86400\\n'; else printf '85800\\n'; fi
else
    exec /bin/date "$@"
fi
""",
        encoding='utf-8',
    )
    for exe in (launchctl, uv, date):
        exe.chmod(0o700)

    def spawn(command, *args, uv_exit=0, fail_stop=''):
        return subprocess.Popen(
            ['/bin/sh', str(SCRIPT), command, '--prod-dir', str(prod), *args],
            env={
                'HOME': str(tmp_path),
                'PATH': f'{tmp_path}:/usr/bin:/bin',
                'CALLS': str(calls),
                'STATE': str(state),
                'FAKE_PROD': str(prod),
                'UV_EXIT': str(uv_exit),
                'FAIL_STOP': fail_stop,
            },
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    yield spawn, state, calls
    if (pids := state / 'probe-pids').is_file():
        for pid in map(int, pids.read_text().split()):
            with suppress(ProcessLookupError):
                os.kill(pid, SIGKILL)


def run(spawn, *args, **kwargs):
    with spawn(*args, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=6)
        finally:
            if process.poll() is None:
                process.kill()
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def assert_result(result, action, message, *, success=True):
    assert result.returncode == (0 if success else 1), (result.stdout, result.stderr)
    assert not (result.stderr if success else result.stdout)
    (line,) = (result.stdout if success else result.stderr).splitlines()
    timestamp, outcome = line.split(' ', 1)
    assert datetime.fromisoformat(timestamp).utcoffset() == datetime.now().astimezone().utcoffset()
    assert timestamp[10] == 'T'
    assert outcome == f'{action}: {message}'


def assert_probe_stopped(pids):
    deadline = time.monotonic() + 1
    for pid in map(int, pids.read_text().split()):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, f'Probe process {pid} is still alive'
            time.sleep(0.02)


def test_apply_at_open_then_at_close(schedule):
    spawn, state, calls = schedule
    (state / MODEL).touch()
    assert_result(run(spawn, 'apply', '--now', '06:00'), 'apply', 'Model servers ready.')
    domain = f'gui/{os.getuid()}'
    assert [line for line in calls.read_text().splitlines() if line.startswith('bootstrap ')] == [
        f'bootstrap {domain} {state.parent}/Library/LaunchAgents/{GENERATION}.plist'
    ]
    assert PROBE in calls.read_text()
    assert_result(run(spawn, 'apply', '--now', '21:30'), 'apply', 'Model servers unregistered.')
    assert not (state / MODEL).exists() and not (state / GENERATION).exists()
    assert [line for line in calls.read_text().splitlines() if line.startswith('bootout ')] == [
        f'bootout {domain}/{MODEL}',
        f'bootout {domain}/{GENERATION}',
    ]
    assert calls.read_text().count('uv ') == 1


def test_stop_succeeds_if_already_unloaded(schedule):
    spawn, _, calls = schedule
    assert_result(run(spawn, 'stop'), 'stop', 'Model servers unregistered.')
    assert 'uv ' not in calls.read_text()


def test_stop_checks_both_labels_after_bootout(schedule):
    spawn, state, _ = schedule
    (state / MODEL).touch()
    (state / GENERATION).touch()
    result = run(spawn, 'stop', fail_stop=MODEL)
    assert_result(result, 'stop', 'Model server bootout failed.', success=False)
    assert (state / MODEL).exists() and not (state / GENERATION).exists()


def test_start_reports_failure_at_deadline_without_printing_probe_output(schedule):
    spawn, _, calls = schedule
    result = run(spawn, 'start', '--now', '06:01', '--deadline', '06:01', uv_exit=1)
    assert_result(result, 'start', 'Model servers not ready (deadline 06:01).', success=False)
    assert calls.read_text().count(PROBE) == 1


def test_start_caps_a_running_check_at_the_deadline(schedule):
    spawn, state, calls = schedule
    t0 = time.monotonic()
    result = run(spawn, 'start', '--now', '06:24:58', uv_exit='hang')
    assert_result(result, 'start', 'Model servers not ready (deadline 06:25).', success=False)
    assert time.monotonic() - t0 < 5
    assert calls.read_text().count(PROBE) == 1
    assert_probe_stopped(state / 'probe-pids')


def test_interrupted_apply_cleans_up_probe_child(schedule):
    spawn, state, _ = schedule
    pids = state / 'probe-pids'
    with spawn('apply', '--now', '06:10', uv_exit='leave-child') as process:
        try:
            deadline = time.monotonic() + 3
            while not pids.is_file() or not pids.read_text().strip():
                assert time.monotonic() < deadline, 'Probe did not start'
                time.sleep(0.02)
            process.send_signal(SIGTERM)
            stdout, stderr = process.communicate(timeout=4)
        finally:
            if process.poll() is None:
                process.kill()
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    assert_result(result, 'apply', 'Model readiness check interrupted.', success=False)
    assert_probe_stopped(pids)


def test_start_retries_a_failed_check_before_the_deadline(schedule):
    spawn, _, calls = schedule
    result = run(spawn, 'start', '--now', '06:20', uv_exit='retry')
    assert_result(result, 'start', 'Model servers ready.')
    assert calls.read_text().count(PROBE) == 2


def test_apply_retries_after_midnight_before_the_next_day_deadline(schedule):
    spawn, state, _ = schedule
    result = run(spawn, 'apply', '--hours', '23:50-02:00', '--now', '23:50', '--deadline', '00:15', uv_exit='midnight')
    assert_result(result, 'apply', 'Model servers ready.')
    assert (state / 'probe-times').read_text().splitlines() == ['85800', '86400']


def test_apply_does_not_move_an_elapsed_deadline_to_the_next_day(schedule):
    spawn, _, calls = schedule
    result = run(spawn, 'apply', '--hours', '23:50-02:00', '--now', '00:01', '--deadline', '23:55', uv_exit='retry')
    assert_result(result, 'apply', 'Model servers not ready (deadline 23:55).', success=False)
    assert calls.read_text().count(PROBE) == 1


def test_status_only_reports_registration_and_running_state(schedule):
    spawn, state, calls = schedule
    (state / MODEL).touch()
    result = run(spawn, 'status')
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
