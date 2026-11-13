"""law-monitor 명령: bootstrap / collect / replay / summarize / check / daily (원 계획서 5.5).

종료 코드: 0 성공, 1 수집기 일부 실패(나머지는 저장됨), 2 실행 전 거부(설정 오류, 기준점 없음, 다른 실행 중 등).
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from law_monitor import secrets
from law_monitor.app import AlreadyBootstrapped, BootstrapRequired, MissingSecret, Monitor
from law_monitor.assembly import SOURCES, AssemblyClient
from law_monitor.config import CONFIG_FILE, REPOS, ConfigError, load
from law_monitor.gitrepo import GitError
from law_monitor.lawgo import LawGoClient
from law_monitor.llm import ChatClient, load_config
from law_monitor.lock import AlreadyRunning, run_lock
from law_monitor.paths import Paths, default_home


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='law-monitor', description='법령·행정규칙 변경을 이벤트 JSON으로 수집하고 요약')
    p.add_argument('--home', type=Path, help='실행 데이터 폴더 (기본: LAW_MONITOR_HOME 또는 services/monitor)')
    p.add_argument('--config', type=Path, default=CONFIG_FILE, help='추적 대상 설정 (기본: config/targets.yaml)')
    sub = p.add_subparsers(dest='command', required=True)

    b = sub.add_parser('bootstrap', help='첫 실행: 현재 상태를 기준점으로 저장 (이벤트 없음)')
    b.add_argument('--only', help='이 수집기만 (legalize_law, legalize_admrule, assembly_bill, assembly_notice)')
    b.add_argument(
        '--force', action='store_true', help='이미 있는 기준점을 다시 잡음 (그 사이 변경은 이벤트가 안 생김)'
    )

    c = sub.add_parser('collect', help='평소 실행: 기준점 이후 변경을 이벤트로')
    c.add_argument('--only', help='이 수집기만 (디버깅용)')

    r = sub.add_parser('replay', help='과거 구간을 이벤트로 다시 만듦 (data/replay/, 상태 변경 없음)')
    r.add_argument('--repo', required=True, choices=REPOS)
    r.add_argument('--from', dest='since', required=True, help='시작 커밋 (이 커밋 다음부터)')
    r.add_argument('--to', dest='until', required=True, help='끝 커밋 (포함)')

    s = sub.add_parser('summarize', help='대기 이벤트를 모델로 요약 → data/summaries/YYYY-MM-DD.md')
    s.add_argument(
        '--events-dir', type=Path, help='이 폴더의 이벤트(replay 결과)를 요약해 그 폴더에 summary.md (상태 변경 없음)'
    )

    sub.add_parser('check', help='점검(checks)만 실행: 결과 줄 출력 (요약·상태 안 바뀜)')

    d = sub.add_parser(
        'daily', help='매일 실행(launchd): collect → 점검(checks) → summarize. collect가 일부 실패해도 점검·요약은 함'
    )
    d.add_argument('--only', help='이 수집기만 (디버깅용)')
    d.add_argument('--source-plan', type=Path, help='Source Hub 통합 실행 계획 JSON')
    return p


def _check(monitor: Monitor, *, articles_enabled: bool = True) -> tuple[list, int]:
    """점검 실행·출력. 종료 코드: 점검 실패가 있으면 1 (확인 필요는 0)"""
    results = monitor.check() if articles_enabled else monitor.check(articles_enabled=False)
    for r in results:
        print(f'check {r.name} — {r.status}: {r.line}')
    return results, (1 if any(r.failed for r in results) else 0)


def _summarize(
    monitor: Monitor, run_line: str | None = None, events_dir: Path | None = None, checks: list | None = None
) -> int:
    result = monitor.summarize(events_dir=events_dir, run_line=run_line, checks=checks)
    print(f'summarize events={result.events} calls={result.calls} pending={result.pending} → {result.path}')
    if result.failed:
        print(f'law-monitor: 요약 실패: {result.failed}', file=sys.stderr)
        return 1
    return 0


@dataclass(frozen=True)
class DailySelection:
    run_id: str
    collectors: list[str]
    law_paths: list[str] | None
    articles_enabled: bool
    disabled_law_paths: list[str] = field(default_factory=list)


def _daily_selection(path: Path) -> DailySelection:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Source Hub 실행 계획을 확인하세요')
    run_id, collectors, law_paths = (data.get(key) for key in ('run_id', 'collectors', 'law_paths'))
    disabled = data.get('disabled_law_paths', [])
    if not isinstance(run_id, str) or not re.fullmatch(r'daily-[a-zA-Z0-9-]{1,70}', run_id):
        raise ValueError('Source Hub 실행 ID를 확인하세요')
    if (
        not isinstance(collectors, list)
        or not all(item in ('legalize_law', 'legalize_admrule', *SOURCES) for item in collectors)
        or len(set(collectors)) != len(collectors)
    ):
        raise ValueError('Source Hub 수집기 선택을 확인하세요')
    for paths in (law_paths if law_paths is not None else [], disabled):
        if not isinstance(paths, list) or not all(
            isinstance(path, str) and re.fullmatch(r'kr/[^/\r\n]+/[^/\r\n]+\.md', path) for path in paths
        ):
            raise ValueError('Source Hub 법령 범위를 확인하세요')
    if disabled and (law_paths is None or set(law_paths).intersection(disabled)):
        raise ValueError('Source Hub 법령 선택과 비활성 범위가 겹칩니다')
    articles_enabled = data.get('articles_enabled')
    if not isinstance(articles_enabled, bool) or (not articles_enabled and 'legalize_law' in collectors):
        raise ValueError('Source Hub 조문 갱신 설정을 확인하세요')
    return DailySelection(run_id, collectors, law_paths, articles_enabled, disabled)


def _daily(monitor: Monitor, only: str | None, *, selection: DailySelection | None = None) -> int:
    code, line = 0, None
    articles_enabled = selection is None or selection.articles_enabled
    if selection is not None and articles_enabled and 'legalize_law' not in selection.collectors:
        try:
            monitor.refresh_article_repo()
        except GitError, OSError:
            print('law-monitor: 조문 입력 저장소 갱신 실패', file=sys.stderr)
            code = 1
    try:
        summary = (
            monitor.collect(only=only)
            if selection is None
            else monitor.collect_managed(
                selection.run_id,
                selection.collectors,
                selection.law_paths,
                disabled_law_paths=selection.disabled_law_paths,
            )
        )
        print(summary.line)
        code, line = max(code, 1 if summary.failed else 0), summary.line
    except (BootstrapRequired, MissingSecret) as e:
        print(f'law-monitor: {e}', file=sys.stderr)
        code = 2
    results, check_code = _check(monitor, articles_enabled=articles_enabled)
    code = max(code, check_code)
    try:
        code = max(code, _summarize(monitor, run_line=line, checks=results))
    except MissingSecret as e:
        print(f'law-monitor: {e}', file=sys.stderr)
        code = 2
    return code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
    root = logging.getLogger('law_monitor')
    root.addHandler(console)
    paths = Paths(args.home or default_home())
    try:
        with run_lock(paths):  # 같은 home의 다른 실행(launchd daily·수동)과 겹치지 않게
            return _run(args, paths)
    except (
        AlreadyRunning,
        ConfigError,
        BootstrapRequired,
        AlreadyBootstrapped,
        MissingSecret,
        ValueError,
        GitError,
        OSError,
    ) as e:
        print(f'law-monitor: {e}', file=sys.stderr)
        if isinstance(e, AlreadyRunning) and getattr(args, 'source_plan', None):
            return 3
        return 2
    finally:
        root.removeHandler(console)


def _run(args, paths: Paths) -> int:
    source_plan = getattr(args, 'source_plan', None)
    if getattr(args, 'only', None) and source_plan:
        raise ValueError('--only와 --source-plan은 함께 사용할 수 없습니다')
    selection = _daily_selection(source_plan) if source_plan else None
    oc = secrets.law_oc()
    lawgo = LawGoClient(oc) if oc else None
    llm_config = load_config()
    llm = ChatClient(llm_config) if llm_config else None
    config = load(args.config)
    enabled = secrets.get('LAW_ASSEMBLY_ENABLED')
    if enabled is not None:
        if enabled not in ('0', '1') or (enabled == '1' and not config.prelegislation_keywords):
            raise ConfigError('LAW_ASSEMBLY_ENABLED must be 0 or 1, with keywords required when enabled')
        config = replace(config, assembly_enabled=enabled == '1')
    assembly = None
    if (
        config.assembly_enabled
        or getattr(args, 'only', None) in SOURCES
        or (selection is not None and any(source in SOURCES for source in selection.collectors))
    ):
        assembly = AssemblyClient(secrets.get('LAW_ASSEMBLY_KEY'))
    monitor = Monitor(paths, config, lawgo=lawgo, llm=llm, assembly=assembly)
    if args.command == 'summarize':
        return _summarize(monitor, events_dir=args.events_dir)
    if args.command == 'daily':
        return _daily(monitor, args.only, selection=selection)
    if args.command == 'check':
        return _check(monitor)[1]
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


if __name__ == '__main__':
    sys.exit(main())
