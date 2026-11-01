"""실행 잠금: 같은 home(state.db·저장소 클론)을 쓰는 law-monitor가 동시에 돌지 않게 data/.lock에 flock을 건다.

launchd의 daily와 수동 collect·summarize가 겹치면 두 프로세스가 같은 bare 저장소를 fetch하고 같은 state.db에 쓴다.
flock은 프로세스가 죽으면 OS가 풀어 주므로 남은 잠금 파일을 지울 필요가 없다.
"""

import fcntl
from contextlib import contextmanager

from law_monitor.paths import Paths


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(paths: Paths):
    paths.data.mkdir(parents=True, exist_ok=True)
    f = open(paths.lock, 'a')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise AlreadyRunning(f'다른 law-monitor 실행 중입니다 ({paths.lock}). 끝난 뒤 다시 실행하세요') from None
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
