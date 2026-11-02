"""점검(checks): targets.yaml에 적은 명령을 daily에서 부르고 결과 줄을 요약 md에 (계획 docs/archives/monitor-article-check.md).

monitor는 점검 명령을 import하지 않고 하위 프로세스로 부른다 (예: 챗봇 조문 데이터 점검 lawbot-articles-check).
종료 코드 약속: 0 이상 없음, 1 확인 필요, 그 밖·시간 초과·명령 없음은 점검 실패.
"""

import sys
from pathlib import Path

import pytest

from law_monitor import checks, cli
from law_monitor.config import ConfigError
from law_monitor.paths import Paths

PY = sys.executable


def script(code: str) -> list[str]:
    return [PY, '-c', code]


def test_parse_checks_and_reject_bad_values():
    got = checks.parse([{'name': '조문', 'command': ['{bin}/lawbot-articles-check', '--repo', '{repos}/legalize-kr']}])
    assert got == (checks.Check('조문', ('{bin}/lawbot-articles-check', '--repo', '{repos}/legalize-kr')),)
    assert checks.parse(None) == ()
    for bad, message in [
        ([{'name': '', 'command': ['x']}], '이름'),
        ([{'name': 'a', 'command': []}], '명령'),
        ([{'name': 'a', 'command': 'x --y'}], '명령'),
        ([{'name': 'a', 'command': ['{nope}/x']}], '{nope}'),
        ([{'name': 'a', 'command': ['x']}, {'name': 'a', 'command': ['y']}], '중복'),
    ]:
        with pytest.raises(ConfigError, match=message):
            checks.parse(bad)


def test_repo_targets_yaml_has_article_check():
    from law_monitor.config import load

    assert [c.name for c in load().checks] == ['챗봇 조문 데이터']


def test_placeholders(tmp_path):
    paths = Paths(tmp_path / 'home')
    cmd = checks.expand(('{bin}/tool', '--repo', '{repos}/legalize-kr', '--out', '{data}/articles', '{home}'), paths)
    bin_dir = Path(sys.executable).parent
    assert cmd == [
        f'{bin_dir}/tool',
        '--repo',
        f'{paths.repos}/legalize-kr',
        '--out',
        f'{paths.data}/articles',
        str(paths.home),
    ]


def test_run_checks_exit_codes_and_last_line(tmp_path):
    paths = Paths(tmp_path / 'home')
    items = [
        checks.Check('같음', tuple(script('print("로그"); print("articles same=yes changed=0")'))),
        checks.Check('다름', tuple(script('import sys; print("articles same=no changed=3"); sys.exit(1)'))),
        checks.Check('실패', tuple(script('import sys; print("articles error=부칙", file=sys.stdout); sys.exit(2)'))),
        checks.Check('느림', tuple(script('import time; time.sleep(5)'))),
        checks.Check('없음', ('/nonexistent/lawbot-articles-check',)),
    ]
    got = checks.run(items, paths, timeout=1)
    assert [(r.name, r.code, r.status) for r in got] == [
        ('같음', 0, '이상 없음'),
        ('다름', 1, '확인 필요'),
        ('실패', 2, '점검 실패'),
        ('느림', None, '점검 실패'),
        ('없음', None, '점검 실패'),
    ]
    assert got[0].line == 'articles same=yes changed=0' and got[1].line == 'articles same=no changed=3'
    assert got[2].line == 'articles error=부칙'
    assert '시간 초과' in got[3].line and '명령 없음' in got[4].line
    assert [r.failed for r in got] == [False, False, True, True, True]


def test_summary_md_has_check_section(tmp_path):
    from law_monitor.summarize import check_lines

    results = [
        checks.Result('챗봇 조문 데이터', 1, 'articles same=no changed=6 → /x/2026-11-27.md'),
        checks.Result('다른 점검', None, '시간 초과 (900초)'),
    ]
    assert check_lines(results) == [
        '## 점검',
        '',
        '- 챗봇 조문 데이터 — 확인 필요: `articles same=no changed=6 → /x/2026-11-27.md`',
        '- 다른 점검 — 점검 실패: `시간 초과 (900초)`',
        '',
    ]
    assert check_lines([]) == []


def test_cli_check_command(tmp_path, capsys):
    cfg = tmp_path / 'targets.yaml'
    ok = repr(script('print("articles same=yes")'))
    cfg.write_text(
        'repos: {legalize-kr: "file:///none", admrule-kr: "file:///none"}\nlaws: []\n'
        f'admrules: {{names: [], keyword_rules: []}}\nchecks: [{{name: 조문, command: {ok}}}]\n',
        encoding='utf-8',
    )
    assert cli.main(['--home', str(tmp_path / 'h'), '--config', str(cfg), 'check']) == 0
    assert '조문 — 이상 없음: articles same=yes' in capsys.readouterr().out
    bad = repr(script('import sys; sys.exit(3)'))
    cfg.write_text(cfg.read_text(encoding='utf-8').replace(ok, bad), encoding='utf-8')
    assert cli.main(['--home', str(tmp_path / 'h'), '--config', str(cfg), 'check']) == 1
