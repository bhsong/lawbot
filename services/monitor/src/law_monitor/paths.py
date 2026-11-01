import os
from dataclasses import dataclass
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[2]  # services/monitor/src/law_monitor/paths.py → services/monitor


def default_home() -> Path:
    """실행 데이터(data/·logs/)를 둘 곳. LAW_MONITOR_HOME으로 바꿀 수 있고, 없으면 services/monitor."""
    env = os.environ.get('LAW_MONITOR_HOME')
    return Path(env).expanduser() if env else SERVICE_DIR


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def data(self) -> Path:
        return self.home / 'data'

    @property
    def repos(self) -> Path:  # legalize-kr·admrule-kr 부분 클론 (bare)
        return self.data / 'repos'

    @property
    def files(self) -> Path:  # 파일 내용 사본 (blob 해시별, 같은 내용은 한 번만)
        return self.data / 'files'

    @property
    def events(self) -> Path:  # 변경 이벤트 JSON (2단계 입력)
        return self.data / 'events'

    @property
    def replay(self) -> Path:  # replay 결과 (events 테이블·커서와 무관)
        return self.data / 'replay'

    @property
    def state_db(self) -> Path:
        return self.data / 'state.db'

    @property
    def lock(self) -> Path:  # 실행 잠금 (lock.run_lock)
        return self.data / '.lock'

    @property
    def logs(self) -> Path:
        return self.home / 'logs'

    def rel(self, path: Path) -> str:
        """이벤트 JSON에 적는 경로: HOME 기준 상대 경로."""
        return path.relative_to(self.home).as_posix()
