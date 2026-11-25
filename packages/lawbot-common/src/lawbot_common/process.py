"""Minimal subprocess environment and POSIX process-group supervision."""

import os
import pwd
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

SYSTEM_PATH = '/usr/bin:/bin'
TOOL_PATH = f'/opt/homebrew/bin:/usr/local/bin:{SYSTEM_PATH}'
GROUP_GRACE = 10


def child_environment(*, path: str = SYSTEM_PATH) -> dict[str, str]:
    return {'PATH': path, 'HOME': pwd.getpwuid(os.getuid()).pw_dir}


def signal_group(pid: int, sig: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, sig)


def run_group(
    args: list[str], *, env: dict[str, str], timeout: float, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            try:
                signal_group(process.pid, signal.SIGTERM)
                process.communicate(timeout=GROUP_GRACE)
            except subprocess.TimeoutExpired:
                signal_group(process.pid, signal.SIGKILL)
                process.wait()
            raise
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
