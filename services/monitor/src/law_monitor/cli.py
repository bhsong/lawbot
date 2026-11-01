"""law-monitor 명령: bootstrap / collect / replay (원 계획서 5.5).

종료 코드: 0 성공, 1 수집기 일부 실패(나머지는 저장됨), 2 실행 전 거부(설정 오류, 기준점 없음 등).
"""

import argparse
import logging
import sys
from pathlib import Path

from law_monitor import secrets
from law_monitor.app import AlreadyBootstrapped, BootstrapRequired, MissingSecret, Monitor
from law_monitor.config import CONFIG_FILE, REPOS, ConfigError, load
from law_monitor.gitrepo import GitError
from law_monitor.lawgo import LawGoClient
from law_monitor.paths import Paths, default_home


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='law-monitor', description='법령·행정규칙 변경을 이벤트 JSON으로 수집')
    p.add_argument('--home', type=Path, help='실행 데이터 폴더 (기본: LAW_MONITOR_HOME 또는 services/monitor)')
    p.add_argument('--config', type=Path, default=CONFIG_FILE, help='추적 대상 설정 (기본: config/targets.yaml)')
    sub = p.add_subparsers(dest='command', required=True)

    b = sub.add_parser('bootstrap', help='첫 실행: 현재 상태를 기준점으로 저장 (이벤트 없음)')
    b.add_argument('--only', help='이 수집기만 (legalize_law, legalize_admrule)')
    b.add_argument(
        '--force', action='store_true', help='이미 있는 기준점을 다시 잡음 (그 사이 변경은 이벤트가 안 생김)'
    )

    c = sub.add_parser('collect', help='평소 실행: 기준점 이후 변경을 이벤트로')
    c.add_argument('--only', help='이 수집기만 (디버깅용)')

    r = sub.add_parser('replay', help='과거 구간을 이벤트로 다시 만듦 (data/replay/, 상태 변경 없음)')
    r.add_argument('--repo', required=True, choices=REPOS)
    r.add_argument('--from', dest='since', required=True, help='시작 커밋 (이 커밋 다음부터)')
    r.add_argument('--to', dest='until', required=True, help='끝 커밋 (포함)')
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
    root = logging.getLogger('law_monitor')
    root.addHandler(console)
    try:
        oc = secrets.law_oc()
        lawgo = LawGoClient(oc) if oc else None
        monitor = Monitor(Paths(args.home or default_home()), load(args.config), lawgo=lawgo)
        if args.command == 'replay':
            events = monitor.replay(args.repo, args.since, args.until)
            print(f'replay events={len(events)} → {monitor.paths.replay}')
            return 0
        if args.command == 'bootstrap':
            summary = monitor.bootstrap(only=args.only, force=args.force)
        else:
            summary = monitor.collect(only=args.only)
        print(summary.line)
        return 1 if summary.failed else 0
    except (ConfigError, BootstrapRequired, AlreadyBootstrapped, MissingSecret, ValueError, GitError, OSError) as e:
        print(f'law-monitor: {e}', file=sys.stderr)
        return 2
    finally:
        root.removeHandler(console)


if __name__ == '__main__':
    sys.exit(main())
