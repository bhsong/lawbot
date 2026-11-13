"""점검(checks): config/targets.yaml의 `checks`에 적은 명령을 하위 프로세스로 실행한다 (계획: docs/archives/monitor-article-check.md).

monitor가 다른 패키지를 import하지 않고 결과만 받는다. 예: 챗봇 조문 데이터 점검(lawbot-articles-check)이 monitor가
갱신한 legalize-kr 클론으로 '오늘 시행 중인 법'과 지금 데이터를 비교한다.

- 명령의 자리표시: {bin}(지금 Python의 bin 폴더 = law-monitor와 같은 venv), {home}, {data}, {repos}
- 종료 코드 약속: 0 이상 없음, 1 확인 필요, 그 밖·시간 초과·명령 없음은 점검 실패
- 결과 줄: 표준 출력의 마지막 줄 (없으면 표준 오류의 마지막 줄)
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from law_monitor.config import ConfigError
from law_monitor.paths import SERVICE_DIR, Paths

PLACEHOLDERS = ('bin', 'home', 'data', 'repos', 'root', 'uv')
TIMEOUT = 900  # 초. 조문 점검은 약 1분
STATUS = {0: '이상 없음', 1: '확인 필요'}
log = logging.getLogger('law_monitor')


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]

    @property
    def uses_article_repo(self) -> bool:
        return Path(self.command[0]).name in {'lawbot-articles-check', 'lawbot-articles-update'}


@dataclass(frozen=True)
class Result:
    name: str
    code: int | None  # None: 시간 초과·명령 없음
    line: str

    @property
    def status(self) -> str:
        return STATUS.get(self.code, '점검 실패') if self.code is not None else '점검 실패'

    @property
    def failed(self) -> bool:
        return self.code not in STATUS


def parse(items) -> tuple[Check, ...]:
    out = []
    for x in items or []:
        name = x.get('name') if isinstance(x, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ConfigError('checks: 이름이 비어 있습니다')
        command = x.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
            raise ConfigError(f'checks({name}): 명령은 비어 있지 않은 문자열 목록이어야 합니다')
        for arg in command:
            for key in _keys(arg):
                if key not in PLACEHOLDERS:
                    raise ConfigError(f'checks({name}): 알 수 없는 자리표시 {{{key}}} (쓸 수 있는 것: {PLACEHOLDERS})')
        out.append(Check(name.strip(), tuple(command)))
    names = [c.name for c in out]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ConfigError(f'checks에 중복된 이름: {", ".join(dup)}')
    return tuple(out)


def _keys(arg: str) -> list[str]:
    return re.findall(r'\{([^{}]*)\}', arg)


def expand(command: tuple[str, ...], paths: Paths) -> list[str]:
    values = {
        'bin': str(Path(sys.executable).parent),
        'home': str(paths.home),
        'data': str(paths.data),
        'repos': str(paths.repos),
        'root': str(SERVICE_DIR.parents[1]),
        'uv': os.environ.get('LAW_MONITOR_UV') or shutil.which('uv') or 'uv',
    }
    return [arg.format(**values) for arg in command]


def _last_line(text: str) -> str:
    lines = [x.strip() for x in (text or '').splitlines() if x.strip()]
    return lines[-1] if lines else ''


def run(checks: tuple[Check, ...] | list[Check], paths: Paths, *, timeout: float = TIMEOUT) -> list[Result]:
    results = []
    for c in checks:
        cmd = expand(c.command, paths)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            line = _last_line(p.stdout) or _last_line(p.stderr) or f'(출력 없음, 종료 코드 {p.returncode})'
            result = Result(c.name, p.returncode, line)
        except subprocess.TimeoutExpired:
            result = Result(c.name, None, f'시간 초과 ({timeout:g}초)')
        except (FileNotFoundError, PermissionError) as e:
            result = Result(c.name, None, f'명령 없음: {cmd[0]} ({type(e).__name__})')
        log.log(
            logging.ERROR if result.failed else logging.INFO, '점검 %s — %s: %s', c.name, result.status, result.line
        )
        results.append(result)
    return results
